from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (
        CheckConstraint(
            "default_visibility IN ('private', 'department', 'company')",
            name="ck_categories_default_visibility",
        ),
        Index("uq_categories_code", "code", unique=True),
        Index("idx_categories_parent_id", "parent_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"),
    )
    require_review: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="true",
    )
    default_dataset_id: Mapped[str | None] = mapped_column(String(120))
    allow_employee_select: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="true",
    )
    allow_ai_recommend: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="true",
    )
    default_visibility: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default="private",
    )
    keywords: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sql_text("'[]'::jsonb"),
    )
    classification_prompt: Mapped[str | None] = mapped_column(Text)
    ai_analysis_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="true",
    )
    sensitive_detection_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="true",
    )
    auto_sync_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="false",
    )
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


class DatasetMapping(Base):
    __tablename__ = "dataset_mappings"
    __table_args__ = (
        Index("idx_dataset_mappings_category_id", "category_id"),
        Index("idx_dataset_mappings_enabled", "enabled"),
        Index("uq_dataset_mappings_ragflow_dataset_id", "ragflow_dataset_id", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    category_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("categories.id", ondelete="RESTRICT"),
        nullable=False,
    )
    ragflow_dataset_id: Mapped[str] = mapped_column(String(120), nullable=False)
    ragflow_dataset_name: Mapped[str] = mapped_column(String(120), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
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
