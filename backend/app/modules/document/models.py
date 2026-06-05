from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class File(Base):
    __tablename__ = "files"
    __table_args__ = (
        CheckConstraint("size > 0", name="ck_files_size_positive"),
        CheckConstraint("hash ~ '^[0-9a-f]{64}$'", name="ck_files_hash_sha256_hex"),
        CheckConstraint("storage_type IN ('minio')", name="ck_files_storage_type"),
        CheckConstraint(
            "visibility IN ('private', 'department', 'company')",
            name="ck_files_visibility",
        ),
        CheckConstraint(
            "status IN ("
            "'uploaded', 'extracting_text', 'analysis_queued', 'analyzing', "
            "'analysis_failed', 'analyzed', 'pending_review', 'sensitive_review_required', "
            "'approved', 'rejected', 'queued', 'syncing', 'uploaded_to_ragflow', "
            "'parsing', 'parsed', 'failed', 'disabled', 'deleted'"
            ")",
            name="ck_files_status",
        ),
        CheckConstraint(
            "review_status IN ('pending', 'in_review', 'approved', 'rejected')",
            name="ck_files_review_status",
        ),
        Index("idx_files_uploader_uploaded_at", "uploader_id", "uploaded_at"),
        Index("idx_files_hash", "hash"),
        Index("idx_files_status", "status"),
        Index("idx_files_review_status", "review_status"),
        Index("idx_files_category_id", "category_id"),
        Index("idx_files_dataset_mapping_id", "dataset_mapping_id"),
        Index("idx_files_object_key", "object_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_name: Mapped[str] = mapped_column(String(255), nullable=False)
    extension: Mapped[str] = mapped_column(String(20), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_type: Mapped[str] = mapped_column(String(20), nullable=False, server_default="minio")
    bucket: Mapped[str] = mapped_column(String(100), nullable=False)
    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    uploader_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    department: Mapped[str | None] = mapped_column(String(100))
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"),
    )
    dataset_mapping_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("dataset_mappings.id", ondelete="SET NULL"),
    )
    visibility: Mapped[str] = mapped_column(String(20), nullable=False, server_default="private")
    description: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sql_text("'[]'::jsonb"),
    )
    status: Mapped[str] = mapped_column(String(40), nullable=False, server_default="uploaded")
    review_status: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        server_default="pending",
    )
    ragflow_dataset_id: Mapped[str | None] = mapped_column(String(120))
    ragflow_document_id: Mapped[str | None] = mapped_column(String(120))
    ragflow_parse_status: Mapped[str | None] = mapped_column(String(40))
    ragflow_error_message: Mapped[str | None] = mapped_column(Text)
    ai_analysis_enabled_at_upload: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="false",
    )
    ai_config_snapshot: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
