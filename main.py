import asyncio
import logging
from pathlib import Path

import uvicorn
from aiogram import Bot

from bot_registry import ensure_default_settings
from bulk_import_service import process_bulk_import_from_file
from config import settings
from database import init_db
from api_server import app
from master_bot import dp

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
CREATED_TOKENS_FILE = BASE_DIR / "created_tokens.txt"


async def start_bot(bot: Bot):
    logger.info("Starting master bot polling...")
    await dp.start_polling(bot)


async def start_server():
    logger.info("Starting FastAPI server on port %s...", settings.PORT)
    config = uvicorn.Config(
        app=app,
        host="0.0.0.0",
        port=settings.PORT,
        log_level=settings.LOG_LEVEL.lower(),
        loop="asyncio",
    )
    server = uvicorn.Server(config)
    await server.serve()


async def seed_initial_workers(master_bot: Bot):
    if CREATED_TOKENS_FILE.exists():
        logger.info("Importing workers from %s", CREATED_TOKENS_FILE)
        await process_bulk_import_from_file(
            master_bot=master_bot,
            owner_id=settings.OWNER_ID,
            file_path=CREATED_TOKENS_FILE,
        )
    else:
        logger.info("created_tokens.txt not found, skipping startup import.")


async def main():
    logger.info("Initializing Telegram Reaction Master...")
    await init_db()
    await ensure_default_settings()

    master_bot = Bot(token=settings.MASTER_TOKEN)
    try:
        await seed_initial_workers(master_bot)
        await asyncio.gather(
            start_bot(master_bot),
            start_server(),
        )
    finally:
        await master_bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Application shut down gracefully.")
