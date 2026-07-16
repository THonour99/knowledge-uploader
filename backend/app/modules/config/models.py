from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, String, Text, func
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SystemConfig(Base):
    __tablename__ = "system_configs"
    __table_args__ = (
        CheckConstraint(
            "\"group\" IN ('upload', 'processing', 'security', 'review', 'ragflow', 'outbox')",
            name="ck_system_configs_group",
        ),
        CheckConstraint(
            "value_type IN ('string', 'int', 'bool', 'list', 'secret')",
            name="ck_system_configs_value_type",
        ),
        Index("uq_system_configs_key", "key", unique=True),
        Index("idx_system_configs_group", "group"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(String(120), nullable=False)
    group: Mapped[str] = mapped_column(String(20), nullable=False)
    value: Mapped[object | None] = mapped_column(JSONB, nullable=True)
    value_type: Mapped[str] = mapped_column(String(20), nullable=False)
    is_secret: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=sql_text("''"),
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
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
