import asyncio
import logging
import random
from typing import List

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import ReactionTypeEmoji
from sqlalchemy.orm import selectinload

from bot_registry import DEFAULT_REACTIONS
from database import get_session
from sqlalchemy import select
from models import Channel, TaskLog, Worker, ChannelWorker

logger = logging.getLogger(__name__)


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
):
    delay = random.uniform(5, 45)
    logger.info(
        "Worker %s waiting %.2fs before reacting %s on msg %s in chat %s.",
        worker_id,
        delay,
        reaction_emoji,
        message_id,
        channel_id,
    )
    await asyncio.sleep(delay)

    bot = Bot(token=bot_token)
    max_retries = 3
    success = False
    invalid_token = False
    attempts_used = 0

    try:
        for attempt in range(1, max_retries + 1):
            attempts_used = attempt
            try:
                await bot.set_message_reaction(
                    chat_id=channel_id,
                    message_id=message_id,
                    reaction=[ReactionTypeEmoji(type="emoji", emoji=reaction_emoji)],
                )
                success = True
                logger.info(
                    "Worker %s successfully reacted %s on message %s.",
                    worker_id,
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
                    await asyncio.sleep(2**attempt)
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
            else:
                task_log.status = "failed"

            if invalid_token:
                worker = await session.get(Worker, worker_id)
                if worker:
                    worker.is_active = False
                    logger.error("Worker %s token is invalid. Deactivated.", worker_id)


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

        available_emojis = (channel.reactions or []) or DEFAULT_REACTIONS.copy()
        active_workers: List[Worker] = [
            worker for worker in channel.workers 
            if worker.is_active and worker.id in admin_worker_ids
        ]

        if not active_workers:
            logger.info("No active workers found for chat %s.", telegram_channel_id)
            return

        reaction_plan = _build_reaction_plan(available_emojis, len(active_workers))
        logger.info(
            "Scheduling %s reactions for chat %s message %s.",
            len(active_workers),
            telegram_channel_id,
            message_id,
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
                )
            )
