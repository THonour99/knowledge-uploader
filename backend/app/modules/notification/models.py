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
    UniqueConstraint,
    func,
)
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint("channel IN ('in_app', 'email')", name="ck_notifications_channel"),
        CheckConstraint(
            "delivery_status IN ('not_applicable', 'pending', 'sent', 'failed')",
            name="ck_notifications_delivery_status",
        ),
        CheckConstraint(
            "delivery_attempts >= 0",
            name="ck_notifications_delivery_attempts_nonnegative",
        ),
        CheckConstraint(
            "(channel = 'in_app' AND delivery_status = 'not_applicable') OR "
            "(channel = 'email' AND delivery_status IN ('pending', 'sent', 'failed'))",
            name="ck_notifications_channel_delivery_status",
        ),
        UniqueConstraint(
            "source_event_id",
            "user_id",
            "channel",
            name="uq_notifications_source_recipient_channel",
        ),
        Index("idx_notifications_user_created_at", "user_id", "created_at"),
        Index(
            "idx_notifications_unread",
            "user_id",
            postgresql_where=sql_text("read_at IS NULL"),
        ),
        Index("idx_notifications_type", "type"),
        Index("idx_notifications_source_event_id", "source_event_id"),
        Index(
            "idx_notifications_email_pending",
            "created_at",
            postgresql_where=sql_text("channel = 'email' AND delivery_status = 'pending'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_event_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("event_outbox.id", ondelete="RESTRICT"),
    )
    type: Mapped[str] = mapped_column(String(80), nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False, server_default="in_app")
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sql_text("'{}'::jsonb"),
    )
    delivery_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default="not_applicable",
    )
    delivery_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_delivery_error: Mapped[str | None] = mapped_column(String(120))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
