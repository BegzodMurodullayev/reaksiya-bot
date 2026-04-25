"""
config.py — Pydantic Settings for Telegram Reaction Master
"""
from pydantic_settings import BaseSettings, SettingsConfigDict
import os

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Telegram
    MASTER_TOKEN: str
    OWNER_ID: int
    API_ID: int = 0
    API_HASH: str = ""

    # Database
    DATABASE_URL: str

    # Server
    PORT: int = 8000

    # Logging
    LOG_LEVEL: str = "INFO"

    # Seed
    SEED_WORKERS: str = ""

    # DB Pool settings optimized for Render free tier
    DB_POOL_SIZE: int = 3
    DB_MAX_OVERFLOW: int = 5
    DB_POOL_TIMEOUT: int = 30
    DB_POOL_RECYCLE: int = 1800  # 30 min — prevent stale connections


settings = Settings()
