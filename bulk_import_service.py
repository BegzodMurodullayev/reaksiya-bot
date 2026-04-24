import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from aiogram import Bot

from bot_registry import read_tokens_file, register_worker
from database import get_session
from models import BulkImportLog

logger = logging.getLogger(__name__)

bulk_import_lock = asyncio.Lock()


async def process_bulk_import(
    master_bot: Bot,
    owner_id: int,
    tokens: Iterable[str],
    status_message=None,
):
    token_list = [token.strip() for token in tokens if token and token.strip()]
    if bulk_import_lock.locked():
        if status_message:
            await status_message.edit_text("Boshqa ommaviy yuklash jarayoni ketmoqda. Biroz kuting.")
        return

    async with bulk_import_lock:
        total_tokens = len(token_list)
        if total_tokens == 0:
            if status_message:
                await status_message.edit_text("Import uchun token topilmadi.")
            return

        async with get_session() as session:
            import_log = BulkImportLog(
                owner_id=owner_id,
                total_tokens=total_tokens,
                status="running",
            )
            session.add(import_log)
            await session.flush()
            log_id = import_log.id

        success_count = 0
        failed_count = 0
        duplicate_count = 0
        promoted_count = 0
        semaphore = asyncio.Semaphore(3)

        async def check_and_add_token(token: str):
            nonlocal success_count, failed_count, duplicate_count, promoted_count
            async with semaphore:
                result = await register_worker(master_bot, token)
                status = result.get("status")
                if status in {"added", "reactivated"}:
                    success_count += 1
                    promoted_count += result.get("promoted_count", 0)
                elif status == "existing":
                    duplicate_count += 1
                    promoted_count += result.get("promoted_count", 0)
                else:
                    failed_count += 1

        tasks = [asyncio.create_task(check_and_add_token(token)) for token in token_list]

        for index, task in enumerate(tasks, start=1):
            await task
            if status_message and (index == total_tokens or index % 5 == 0):
                try:
                    await status_message.edit_text(
                        "Import davom etmoqda...\n"
                        f"Tekshirildi: {index}/{total_tokens}\n"
                        f"Yangi: {success_count}\n"
                        f"Takror: {duplicate_count}\n"
                        f"Xato: {failed_count}"
                    )
                except Exception:
                    logger.debug("Could not update bulk import status message.")

        async with get_session() as session:
            import_log = await session.get(BulkImportLog, log_id)
            if import_log:
                import_log.success_count = success_count
                import_log.failed_count = failed_count
                import_log.duplicate_count = duplicate_count
                import_log.status = "completed"
                import_log.completed_at = datetime.now(timezone.utc)

        final_text = (
            "Import tugadi.\n\n"
            f"Jami token: {total_tokens}\n"
            f"Yangi qo'shildi: {success_count}\n"
            f"Allaqachon bor edi: {duplicate_count}\n"
            f"Xato: {failed_count}\n"
            f"Admin qilish urinishlari muvaffaqiyatli: {promoted_count}"
        )
        if status_message:
            try:
                await status_message.edit_text(final_text)
            except Exception:
                await status_message.answer(final_text)


async def process_bulk_import_from_file(
    master_bot: Bot,
    owner_id: int,
    file_path: str | Path,
    status_message=None,
):
    tokens = read_tokens_file(file_path)
    await process_bulk_import(master_bot, owner_id, tokens, status_message)
