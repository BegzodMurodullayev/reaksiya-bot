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

    MASTER_TOKEN: str
    OWNER_ID: int
    ADMIN_IDS: str = ""
    API_ID: int = 0
    API_HASH: str = ""

    @property
    def admin_ids_list(self) -> list[int]:
        ids = {self.OWNER_ID}
        if self.ADMIN_IDS:
            for x in self.ADMIN_IDS.split(","):
                x = x.strip()
                if x.isdigit() or (x.startswith("-") and x[1:].isdigit()):
                    ids.add(int(x))
        return list(ids)

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
