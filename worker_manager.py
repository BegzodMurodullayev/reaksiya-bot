import asyncio
import logging
import random
from typing import List

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import ReactionTypeCustomEmoji, ReactionTypeEmoji
from sqlalchemy.orm import selectinload

from bot_registry import DEFAULT_REACTIONS, parse_reactions_text, _split_emoji_string
from database import get_session
from sqlalchemy import select
from models import Channel, TaskLog, Worker, ChannelWorker

logger = logging.getLogger(__name__)


def _is_custom_emoji_id(value: str) -> bool:
    """Premium emoji ID larini aniqlash.
    
    Telegram custom emoji ID lari 17-19 xonali raqamdan iborat bo'ladi.
    Masalan: 5368324170671202286
    Oddiy emojidan farqi: faqat raqamlardan iborat va 10 belgidan uzun.
    """
    stripped = value.strip()
    return stripped.isdigit() and len(stripped) > 10


def _build_reaction_object(emoji_value: str):
    """Reaksiya turini avtomatik aniqlaydi.
    
    - Premium custom emoji ID (uzun raqam) => ReactionTypeCustomEmoji
    - Oddiy unicode emoji             => ReactionTypeEmoji
    """
    if _is_custom_emoji_id(emoji_value):
        return ReactionTypeCustomEmoji(type="custom_emoji", custom_emoji_id=emoji_value.strip())
    return ReactionTypeEmoji(type="emoji", emoji=emoji_value)


def _sanitize_emoji_list(raw_list: list[str] | None) -> list[str]:
    """Bazadan kelgan emoji ro'yxatini tozalaydi.
    
    Ba'zan bazada ['❤👍🔥...'] kabi bitta katta
    string saqlanib qolishi mumkin. Buni to'g'ri alohida emojilarga ajratadi.
    """
    if not raw_list:
        return DEFAULT_REACTIONS.copy()
    
    result: list[str] = []
    for item in raw_list:
        item = str(item).strip()
        if not item:
            continue
        # Custom emoji ID — to'g'ridan qo'sh
        if _is_custom_emoji_id(item):
            result.append(item)
            continue
        # Bitta qisqa emoji (1-5 codepoint)
        if len(item) <= 5:
            result.append(item)
            continue
        # Uzun string — emoji library bilan ajrat
        parts = _split_emoji_string(item)
        if parts:
            logger.info("Sanitized emoji string '%s' → %s", item[:20], parts)
            result.extend(parts)
        else:
            result.append(item)  # fallback
    
    return result if result else DEFAULT_REACTIONS.copy()


def _build_reaction_plan(available_emojis: list[str], worker_count: int) -> list[str]:
    emoji_pool = list(available_emojis or DEFAULT_REACTIONS.copy())
    random.shuffle(emoji_pool)
    plan: list[str] = []
    cursor = 0

    for _ in range(worker_count):
        plan.append(emoji_pool[cursor])
        cursor += 1
        if cursor >= len(emoji_pool):
            cursor = 0
            random.shuffle(emoji_pool)

    random.shuffle(plan)
    return plan


async def execute_reaction_task(
    worker_id: int,
    bot_token: str,
    channel_id: int,
    message_id: int,
    reaction_emoji: str,
    task_log_id: int,
    channel_db_id: int | None = None,
):
    delay = random.uniform(2, 3)
    reaction_type = "premium ⭐" if _is_custom_emoji_id(reaction_emoji) else "oddiy"
    logger.info(
        "Worker %s waiting %.2fs before reacting [%s] %s on msg %s in chat %s.",
        worker_id,
        delay,
        reaction_type,
        reaction_emoji,
        message_id,
        channel_id,
    )
    await asyncio.sleep(delay)

    bot = Bot(token=bot_token)
    max_retries = 3
    success = False
    invalid_token = False
    kicked_from_chat = False
    reaction_invalid = False
    attempts_used = 0

    try:
        for attempt in range(1, max_retries + 1):
            attempts_used = attempt
            try:
                await bot.set_message_reaction(
                    chat_id=channel_id,
                    message_id=message_id,
                    reaction=[_build_reaction_object(reaction_emoji)],
                )
                success = True
                logger.info(
                    "Worker %s successfully reacted [%s] %s on message %s.",
                    worker_id,
                    reaction_type,
                    reaction_emoji,
                    message_id,
                )
                break
            except TelegramRetryAfter as exc:
                wait_seconds = getattr(exc, "retry_after", 1) or 1
                logger.warning(
                    "Worker %s hit flood control on message %s, retrying in %ss.",
                    worker_id,
                    message_id,
                    wait_seconds,
                )
                await asyncio.sleep(wait_seconds)
            except Exception as exc:
                error_text = str(exc).lower()

                # Non-retryable: invalid emoji — will never succeed, skip immediately
                if "reaction_invalid" in error_text:
                    reaction_invalid = True
                    logger.warning(
                        "Worker %s: REACTION_INVALID for emoji '%s' on msg %s — skipping retries.",
                        worker_id,
                        reaction_emoji,
                        message_id,
                    )
                    break

                # Non-retryable: bot was kicked from the channel
                if "forbidden" in error_text and ("kicked" in error_text or "bot was blocked" in error_text):
                    kicked_from_chat = True
                    logger.warning(
                        "Worker %s was kicked from chat %s — marking as non-admin.",
                        worker_id,
                        channel_id,
                    )
                    break

                if "message_id_invalid" not in error_text and "chat not found" not in error_text:
                    logger.warning(
                        "Worker %s failed to react on message %s (attempt %s): %s",
                        worker_id,
                        message_id,
                        attempt,
                        exc,
                    )

                if any(marker in error_text for marker in ("unauthorized", "invalid token", "token is invalid")):
                    invalid_token = True
                    break

                if attempt < max_retries:
                    await asyncio.sleep(2 ** attempt)
    finally:
        await bot.session.close()

    async with get_session() as session:
        task_log = await session.get(TaskLog, task_log_id)
        if task_log:
            task_log.retry_count = max(0, attempts_used - 1)
            if success:
                task_log.status = "success"
            elif invalid_token:
                task_log.status = "invalid_token"
            elif kicked_from_chat:
                task_log.status = "forbidden"
            elif reaction_invalid:
                task_log.status = "reaction_invalid"
            else:
                task_log.status = "failed"

            if invalid_token:
                worker = await session.get(Worker, worker_id)
                if worker:
                    worker.is_active = False
                    logger.error("Worker %s token is invalid. Deactivated.", worker_id)

            # Demote worker from this channel so it's excluded from future scheduling
            if kicked_from_chat and channel_db_id is not None:
                from sqlalchemy import select as sa_select
                cw = (
                    await session.execute(
                        sa_select(ChannelWorker).where(
                            ChannelWorker.channel_id == channel_db_id,
                            ChannelWorker.worker_id == worker_id,
                        )
                    )
                ).scalar_one_or_none()
                if cw:
                    cw.is_admin = False
                    logger.warning(
                        "Worker %s removed from admin list for channel_db_id=%s.",
                        worker_id,
                        channel_db_id,
                    )


async def schedule_reactions(channel_db_id: int, telegram_channel_id: int, message_id: int):
    async with get_session() as session:
        channel = await session.get(
            Channel,
            channel_db_id,
            options=[selectinload(Channel.workers)],
        )
        if not channel or not channel.is_active:
            logger.warning("Channel %s not found or inactive. Cannot schedule reactions.", channel_db_id)
            return

        cw_stmt = select(ChannelWorker.worker_id).where(
            ChannelWorker.channel_id == channel_db_id,
            ChannelWorker.is_admin == True
        )
        admin_worker_ids = (await session.execute(cw_stmt)).scalars().all()

        raw_emojis = channel.reactions or []
        available_emojis = _sanitize_emoji_list(raw_emojis)
        
        active_workers: List[Worker] = [
            worker for worker in channel.workers
            if worker.is_active and worker.id in admin_worker_ids
        ]

        if not active_workers:
            logger.info("No active workers found for chat %s.", telegram_channel_id)
            return

        reaction_plan = _build_reaction_plan(available_emojis, len(active_workers))

        premium_count = sum(1 for e in available_emojis if _is_custom_emoji_id(e))
        logger.info(
            "Scheduling %s reactions (%s premium) for chat %s message %s. Emojis: %s",
            len(active_workers),
            premium_count,
            telegram_channel_id,
            message_id,
            available_emojis[:5],
        )

        for worker, selected_emoji in zip(active_workers, reaction_plan, strict=False):
            task_log = TaskLog(
                channel_id=channel_db_id,
                message_id=message_id,
                worker_id=worker.id,
                reaction_emoji=selected_emoji,
                status="pending",
            )
            session.add(task_log)
            await session.flush()

            asyncio.create_task(
                execute_reaction_task(
                    worker_id=worker.id,
                    bot_token=worker.token,
                    channel_id=telegram_channel_id,
                    message_id=message_id,
                    reaction_emoji=selected_emoji,
                    task_log_id=task_log.id,
                    channel_db_id=channel_db_id,
                )
            )
