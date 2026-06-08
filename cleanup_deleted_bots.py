"""
cleanup_deleted_bots.py
=======================
Bazadagi barcha worker botlarni Telegram API orqali tekshiradi.
O'chirilgan yoki yaroqsiz tokenli botlarni bazadan o'chiradi.

Ishlatish:
    python cleanup_deleted_bots.py [--dry-run]

--dry-run: faqat hisobot, hech narsa o'chirilmaydi
"""
import asyncio
import argparse
import logging
import sys
import os

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# project root
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from aiogram import Bot
from aiogram.exceptions import TelegramUnauthorizedError
from sqlalchemy import select, delete

from database import get_session, init_db
from models import Worker, ChannelWorker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("cleanup")

CONCURRENT = 10          # bir vaqtda nechta bot tekshiriladi
RETRY_DELAY = 2.0        # xato bo'lganda kutish (soniya)


async def check_token(token: str) -> tuple[bool, str | None]:
    """
    Returns (is_valid, username_or_None).
    is_valid=False => token yaroqsiz (bot o'chirilgan yoki ban yegan).
    """
    bot = Bot(token=token)
    try:
        me = await bot.get_me()
        return True, me.username
    except TelegramUnauthorizedError:
        return False, None
    except Exception as exc:
        error_text = str(exc).lower()
        if "unauthorized" in error_text or "invalid token" in error_text or "token is invalid" in error_text:
            return False, None
        # Network yoki boshqa xato — ishonchsiz, saqlab turamiz
        logger.warning("Noaniq xato '%s' uchun: %s — saqlab qolinadi", token[:20], exc)
        return True, None   # ehtiyotkorlik bilan True
    finally:
        await bot.session.close()


async def check_all_workers(workers: list[Worker]) -> dict[int, tuple[bool, str | None]]:
    """Semaphore bilan parallel tekshiradi."""
    sem = asyncio.Semaphore(CONCURRENT)
    results: dict[int, tuple[bool, str | None]] = {}

    async def task(w: Worker):
        async with sem:
            valid, username = await check_token(w.token)
            results[w.id] = (valid, username)
            status = f"✅ @{username}" if valid else "❌ YAROQSIZ / O'CHIRILGAN"
            logger.info("Worker #%d  @%-30s  %s", w.id, w.username or "?", status)

    await asyncio.gather(*[task(w) for w in workers])
    return results


async def main(dry_run: bool) -> None:
    logger.info("🔍 Bazadan workerlar yuklanmoqda...")
    await init_db()

    async with get_session() as session:
        all_workers: list[Worker] = list(
            (await session.execute(select(Worker))).scalars().all()
        )

    logger.info("📊 Jami worker: %d ta", len(all_workers))
    logger.info("🌐 Telegram API orqali tekshirilmoqda (parallel=%d)...\n", CONCURRENT)

    results = await check_all_workers(all_workers)

    invalid_ids = [wid for wid, (valid, _) in results.items() if not valid]
    valid_count = len(all_workers) - len(invalid_ids)

    print()
    print("═" * 55)
    print(f"  ✅ Yaroqli botlar   : {valid_count}")
    print(f"  ❌ Yaroqsiz botlar  : {len(invalid_ids)}")
    print("═" * 55)

    if not invalid_ids:
        logger.info("🎉 Hech qanday yaroqsiz bot topilmadi. Baza toza!")
        return

    print("\n🗑  O'chiriladigan workerlar:")
    for w in all_workers:
        if w.id in invalid_ids:
            print(f"   • #%d  @%s  (token: %s...)" % (w.id, w.username or "?", w.token[:20]))

    if dry_run:
        logger.info("\n⚠️  DRY-RUN rejimi — hech narsa o'chirilmadi.")
        return

    # Tasdiqlash
    confirm = input(f"\n❓ {len(invalid_ids)} ta botni bazadan o'chirasizmi? [y/N]: ").strip().lower()
    if confirm != "y":
        logger.info("❌ Bekor qilindi.")
        return

    async with get_session() as session:
        # ChannelWorker yozuvlarini oldin o'chir (CASCADE bo'lmasligi uchun)
        await session.execute(
            delete(ChannelWorker).where(ChannelWorker.worker_id.in_(invalid_ids))
        )
        # Worker larni o'chir
        await session.execute(
            delete(Worker).where(Worker.id.in_(invalid_ids))
        )

    logger.info("✅ %d ta yaroqsiz bot bazadan muvaffaqiyatli o'chirildi!", len(invalid_ids))
    logger.info("🗄  Qolgan yaroqli botlar: %d ta", valid_count)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bazadagi o'chirilgan botlarni tozalash")
    parser.add_argument("--dry-run", action="store_true", help="Faqat hisobot, o'chirmaslik")
    args = parser.parse_args()

    asyncio.run(main(dry_run=args.dry_run))
