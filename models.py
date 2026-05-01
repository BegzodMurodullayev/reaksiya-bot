"""SQLAlchemy ORM models."""
from datetime import datetime
from typing import List, Optional

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class ChannelWorker(Base):
    __tablename__ = "channel_workers"

    channel_id: Mapped[int] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), primary_key=True
    )
    worker_id: Mapped[int] = mapped_column(
        ForeignKey("workers.id", ondelete="CASCADE"), primary_key=True
    )
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    reactions: Mapped[Optional[list[str]]] = mapped_column(JSONB, nullable=True, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    workers: Mapped[List["Worker"]] = relationship(
        "Worker",
        secondary="channel_workers",
        back_populates="channels",
        lazy="selectin",
    )
    task_logs: Mapped[List["TaskLog"]] = relationship(
        "TaskLog",
        back_populates="channel",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Channel {self.channel_id} '{self.title}'>"


class Worker(Base):
    __tablename__ = "workers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_seen: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    channels: Mapped[List["Channel"]] = relationship(
        "Channel",
        secondary="channel_workers",
        back_populates="workers",
        lazy="selectin",
    )
    task_logs: Mapped[List["TaskLog"]] = relationship(
        "TaskLog",
        back_populates="worker",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Worker @{self.username} active={self.is_active}>"


class TaskLog(Base):
    __tablename__ = "task_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("channels.id", ondelete="SET NULL"),
        nullable=True,
    )
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    worker_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("workers.id", ondelete="SET NULL"),
        nullable=True,
    )
    reaction_emoji: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    channel: Mapped[Optional["Channel"]] = relationship("Channel", back_populates="task_logs")
    worker: Mapped[Optional["Worker"]] = relationship("Worker", back_populates="task_logs")

    def __repr__(self) -> str:
        return f"<TaskLog ch={self.channel_id} msg={self.message_id} status={self.status}>"


class BulkImportLog(Base):
    __tablename__ = "bulk_import_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    success_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    def __repr__(self) -> str:
        return f"<BulkImportLog {self.id} status={self.status}>"


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict | list | str | int | bool | None] = mapped_column(JSONB, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<AppSetting {self.key}>"
