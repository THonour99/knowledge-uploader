from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

SYNC_TASK_TYPES = ("ragflow_upload", "ragflow_parse", "ragflow_status_check", "ragflow_delete")
SYNC_TASK_STATUSES = ("queued", "running", "succeeded", "failed", "canceled")
ACTIVE_SYNC_TASK_STATUSES = ("queued", "running")


class SyncTask(Base):
    __tablename__ = "sync_tasks"
    __table_args__ = (
        CheckConstraint(
            "task_type IN ("
            "'ragflow_upload', 'ragflow_parse', 'ragflow_status_check', 'ragflow_delete'"
            ")",
            name="ck_sync_tasks_task_type",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'canceled')",
            name="ck_sync_tasks_status",
        ),
        CheckConstraint("retry_count >= 0", name="ck_sync_tasks_retry_count_non_negative"),
        CheckConstraint("max_retry_count >= 0", name="ck_sync_tasks_max_retry_count_non_negative"),
        Index("idx_sync_tasks_file_id", "file_id"),
        Index("idx_sync_tasks_status", "status"),
        Index("idx_sync_tasks_task_type", "task_type"),
        Index("idx_sync_tasks_created_at", "created_at"),
        Index(
            "uq_sync_tasks_active_ragflow_upload_per_file",
            "file_id",
            unique=True,
            postgresql_where=text(
                "task_type = 'ragflow_upload' AND status IN ('queued', 'running')"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    file_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("files.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_type: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, server_default="queued")
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    max_retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="3")
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class SyncTaskLog(Base):
    __tablename__ = "sync_task_logs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'canceled')",
            name="ck_sync_task_logs_status",
        ),
        Index("idx_sync_task_logs_task_id", "task_id"),
        Index("idx_sync_task_logs_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sync_tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
