"""Helpers for worker registration, token import and reaction defaults."""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from sqlalchemy import select
from database import get_session
from models import AppSetting, Channel, ChannelWorker, Worker

logger = logging.getLogger(__name__)

DEFAULT_REACTIONS = [
    "❤",
    "👍",
    "🔥",
    "👏",
    "🎉",
    "🤩",
    "👌",
    "❤\ufe0f\u200d🔥",  # ❤️‍🔥 correct ZWJ sequence (U+2764 + U+FE0F + ZWJ + U+1F525)
    "💯",
    "⚡",
    "🏆",
]
DEFAULT_REACTIONS_KEY = "default_reactions"
TOKEN_PATTERN = re.compile(r"(?P<token>\d{6,}:[A-Za-z0-9_-]{30,})")
TRACKABLE_CHAT_TYPES = {"group", "supergroup", "channel"}
PROMOTABLE_CHAT_TYPES = {"supergroup", "channel"}
ACTIVE_MEMBER_STATUSES = {"member", "administrator", "creator", "restricted"}


async def _get_master_bot_user_id(master_bot: Bot) -> int:
    cached_user_id = getattr(master_bot, "_cached_user_id", None)
    if cached_user_id is not None:
        return cached_user_id

    me = await master_bot.get_me()
    setattr(master_bot, "_cached_user_id", me.id)
    return me.id


async def inspect_target_chat(master_bot: Bot, chat_id: int) -> dict[str, Any]:
    chat = await master_bot.get_chat(chat_id)
    chat_type = getattr(chat, "type", None)
    title = getattr(chat, "title", None) or getattr(chat, "full_name", None) or str(chat.id)

    if chat_type not in TRACKABLE_CHAT_TYPES:
        return {
            "ok": False,
            "chat_id": chat.id,
            "title": title,
            "chat_type": chat_type,
            "reason": "Faqat kanal, supergroup va group qo'llab-quvvatlanadi.",
            "promotion_supported": False,
        }

    master_bot_user_id = await _get_master_bot_user_id(master_bot)
    try:
        member = await master_bot.get_chat_member(chat.id, master_bot_user_id)
        member_status = getattr(member, "status", None)
    except TelegramAPIError as exc:
        return {
            "ok": False,
            "chat_id": chat.id,
            "title": title,
            "chat_type": chat_type,
            "reason": f"Main bot chat holatini tekshirib bo'lmadi: {exc}",
            "promotion_supported": False,
        }

    is_admin = member_status in {"administrator", "creator"}
    if not is_admin:
        return {
            "ok": False,
            "chat_id": chat.id,
            "title": title,
            "chat_type": chat_type,
            "reason": "Main bot bu chatda admin bo'lishi kerak.",
            "promotion_supported": False,
        }

    warning = None
    if chat_type == "group":
        warning = (
            "Basic group aniqlandi. Worker botlarni avtomatik admin qilish Bot API'da "
            "supergroup/channel kabi ishlamaydi, ularni qo'lda qo'shish kerak bo'ladi."
        )

    return {
        "ok": True,
        "chat_id": chat.id,
        "title": title,
        "chat_type": chat_type,
        "reason": None,
        "warning": warning,
        "promotion_supported": chat_type in PROMOTABLE_CHAT_TYPES,
    }


def parse_reactions_text(raw_text: str | None) -> list[str]:
    """Reaksiyalarni parse qiladi.

    Qo'llab-quvvatlanadigan formatlar:
    - Oddiy emoji: ❤ 👍 🔥
    - Premium custom emoji ID: 5368324170671202286 (Telegram dan olingan uzun raqam)
    - Aralash: 👍 5368324170671202286 🔥
    """
    if not raw_text:
        return []
    reactions: list[str] = []
    for item in raw_text.replace(",", " ").split():
        emoji = item.strip()
        if emoji and emoji not in reactions:
            reactions.append(emoji)
    return reactions


def parse_tokens_blob(raw_text: str) -> list[str]:
    tokens: list[str] = []
    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            line = line.split("=", 1)[1].strip()
        match = TOKEN_PATTERN.search(line)
        if match:
            token = match.group("token")
            if token not in tokens:
                tokens.append(token)
    return tokens


def read_tokens_file(path: str | Path) -> list[str]:
    return parse_tokens_blob(Path(path).read_text(encoding="utf-8"))


async def ensure_default_settings() -> None:
    async with get_session() as session:
        setting = await session.get(AppSetting, DEFAULT_REACTIONS_KEY)
        if setting is None:
            session.add(AppSetting(key=DEFAULT_REACTIONS_KEY, value=DEFAULT_REACTIONS.copy()))


async def get_default_reactions(session) -> list[str]:
    setting = await session.get(AppSetting, DEFAULT_REACTIONS_KEY)
    if not setting or not isinstance(setting.value, list) or not setting.value:
        return DEFAULT_REACTIONS.copy()
    return [str(item) for item in setting.value if str(item).strip()]


async def set_default_reactions(reactions: list[str]) -> list[str]:
    clean_reactions = reactions or DEFAULT_REACTIONS.copy()
    async with get_session() as session:
        setting = await session.get(AppSetting, DEFAULT_REACTIONS_KEY)
        if setting is None:
            session.add(AppSetting(key=DEFAULT_REACTIONS_KEY, value=clean_reactions))
        else:
            setting.value = clean_reactions
    return clean_reactions


async def _fetch_worker_identity(token: str) -> tuple[int, str | None]:
    worker_bot = Bot(token=token)
    try:
        me = await worker_bot.get_me()
        return me.id, me.username
    finally:
        await worker_bot.session.close()


async def _promote_worker_in_chat(
    master_bot: Bot,
    chat_id: int,
    worker_user_id: int,
    worker_username: str | None,
) -> tuple[bool, str | None]:
    for attempt in range(1, 4):
        try:
            await master_bot.promote_chat_member(
                chat_id=chat_id,
                user_id=worker_user_id,
                can_manage_chat=True,
            )
            return True, None
        except TelegramRetryAfter as exc:
            wait_seconds = getattr(exc, "retry_after", 1) or 1
            logger.warning(
                "Promotion retry for @%s in chat %s after %ss.",
                worker_username or worker_user_id,
                chat_id,
                wait_seconds,
            )
            await asyncio.sleep(wait_seconds)
        except TelegramAPIError as exc:
            error_text = str(exc)
            logger.warning(
                "Could not promote worker @%s in chat %s: %s",
                worker_username or worker_user_id,
                chat_id,
                error_text,
            )
            return False, error_text
    return False, "Promotion retry limit reached."


async def _check_worker_membership(master_bot: Bot, chat_id: int, worker_user_id: int) -> tuple[bool, str | None]:
    try:
        member = await master_bot.get_chat_member(chat_id, worker_user_id)
    except TelegramAPIError as exc:
        error_text = str(exc)
        lowered = error_text.lower()
        if any(marker in lowered for marker in ("user not found", "participant", "member not found")):
            return False, error_text
        return True, error_text

    status = getattr(member, "status", None)
    return status in ACTIVE_MEMBER_STATUSES, status


async def register_worker(master_bot: Bot, token: str) -> dict[str, Any]:
    token_match = TOKEN_PATTERN.search(token.strip())
    if not token_match:
        return {"status": "invalid", "reason": "Token formati noto'g'ri."}

    clean_token = token_match.group("token")
    try:
        worker_user_id, username = await _fetch_worker_identity(clean_token)
    except Exception as exc:
        logger.warning("Worker token validation failed: %s", exc)
        return {"status": "invalid", "reason": str(exc)}

    async with get_session() as session:
        result = await session.execute(select(Worker).where(Worker.token == clean_token))
        worker = result.scalar_one_or_none()
        status = "existing"
        if worker is None:
            worker = Worker(token=clean_token, username=username, is_active=True)
            session.add(worker)
            await session.flush()
            status = "added"
        else:
            if not worker.is_active:
                status = "reactivated"
            worker.username = username
            worker.is_active = True

        chats_result = await session.execute(
            select(Channel).where(Channel.is_active.is_(True)).order_by(Channel.id)
        )
        chats = chats_result.scalars().all()

        linked_chat_ids = set(
            (
                await session.execute(
                    select(ChannelWorker.channel_id).where(ChannelWorker.worker_id == worker.id)
                )
            ).scalars().all()
        )

        linked_count = 0
        for chat in chats:
            if chat.id not in linked_chat_ids:
                session.add(ChannelWorker(channel_id=chat.id, worker_id=worker.id))
                linked_count += 1

        worker_id = worker.id
        chat_refs = [(chat.id, chat.channel_id, chat.title) for chat in chats]

    promoted_count = 0
    pending_titles: list[str] = []
    skipped_titles: list[str] = []
    for chat_db_id, telegram_chat_id, title in chat_refs:
        chat_info = await inspect_target_chat(master_bot, telegram_chat_id)
        if not chat_info["ok"]:
            skipped_titles.append(f"{title}: {chat_info['reason']}")
            continue
        if not chat_info["promotion_supported"]:
            skipped_titles.append(f"{title}: {chat_info['warning']}")
            continue

        worker_is_member, membership_status = await _check_worker_membership(
            master_bot,
            telegram_chat_id,
            worker_user_id,
        )
        if not worker_is_member:
            pending_titles.append(title)
            continue

        if membership_status in ("administrator", "creator"):
            promoted = True
            error_text = None
        else:
            promoted = False
            error_text = "Qo'lda admin qilinishi kutilmoqda"
        if promoted:
            promoted_count += 1
            async with get_session() as session:
                cw = (await session.execute(
                    select(ChannelWorker).where(
                        ChannelWorker.channel_id == chat_db_id,
                        ChannelWorker.worker_id == worker_id
                    )
                )).scalar_one_or_none()
                if cw:
                    cw.is_admin = True
        elif error_text and any(
            marker in error_text.lower()
            for marker in ("not enough rights", "not a member", "participant", "user not found")
        ):
            pending_titles.append(title)
        elif membership_note and membership_note not in ACTIVE_MEMBER_STATUSES:
            pending_titles.append(title)

    return {
        "status": status,
        "username": username,
        "worker_id": worker_id,
        "linked_count": linked_count,
        "promoted_count": promoted_count,
        "pending_titles": pending_titles,
        "skipped_titles": skipped_titles,
    }


async def sync_workers_for_channel(master_bot: Bot, channel_db_id: int) -> dict[str, Any]:
    async with get_session() as session:
        channel = await session.get(
            Channel,
            channel_db_id,
        )
        if channel is None:
            return {"status": "missing"}

        workers_result = await session.execute(
            select(Worker).where(Worker.is_active.is_(True)).order_by(Worker.id)
        )
        workers = workers_result.scalars().all()

        existing_link_ids = set(
            (
                await session.execute(
                    select(ChannelWorker.worker_id).where(ChannelWorker.channel_id == channel.id)
                )
            ).scalars().all()
        )
        linked_count = 0

        for worker in workers:
            if worker.id not in existing_link_ids:
                session.add(ChannelWorker(channel_id=channel.id, worker_id=worker.id))
                linked_count += 1

        workers_for_promotion = workers
        telegram_chat_id = channel.channel_id
        channel_title = channel.title

    chat_info = await inspect_target_chat(master_bot, telegram_chat_id)
    if not chat_info["ok"]:
        return {
            "status": "blocked",
            "title": channel_title,
            "linked_count": linked_count,
            "promoted_count": 0,
            "pending_usernames": [],
            "warning": chat_info["reason"],
        }
    if not chat_info["promotion_supported"]:
        return {
            "status": "limited",
            "title": channel_title,
            "linked_count": linked_count,
            "promoted_count": 0,
            "pending_usernames": [],
            "warning": chat_info["warning"],
        }

    promoted_count = 0
    pending_usernames: list[str] = []
    for worker in workers_for_promotion:
        try:
            worker_user_id, username = await _fetch_worker_identity(worker.token)
        except Exception as exc:
            logger.warning("Worker %s identity fetch failed during sync: %s", worker.id, exc)
            continue

        worker_is_member, membership_status = await _check_worker_membership(master_bot, telegram_chat_id, worker_user_id)
        if not worker_is_member:
            pending_usernames.append(f"@{username}" if username else f"id:{worker.id}")
            continue

        if membership_status in ("administrator", "creator"):
            promoted = True
            error_text = None
        else:
            promoted = False
            error_text = "Qo'lda admin qilinishi kutilmoqda"
        if promoted:
            promoted_count += 1
            async with get_session() as session:
                cw = (await session.execute(
                    select(ChannelWorker).where(
                        ChannelWorker.channel_id == channel_db_id,
                        ChannelWorker.worker_id == worker.id
                    )
                )).scalar_one_or_none()
                if cw:
                    cw.is_admin = True
        elif error_text and any(
            marker in error_text.lower()
            for marker in ("not enough rights", "not a member", "participant", "user not found")
        ):
            pending_usernames.append(f"@{username}" if username else f"id:{worker.id}")

    return {
        "status": "ok",
        "title": channel_title,
        "linked_count": linked_count,
        "promoted_count": promoted_count,
        "pending_usernames": pending_usernames,
        "warning": chat_info.get("warning"),
    }
