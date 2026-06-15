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
    SmallInteger,
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
            "'parsing', 'parsed', 'failed', 'disabled', 'deleted', 'ragflow_cleanup_failed'"
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
        Index("idx_files_simhash", "simhash"),
        Index("idx_files_simhash_band_0", "simhash_band_0"),
        Index("idx_files_simhash_band_1", "simhash_band_1"),
        Index("idx_files_simhash_band_2", "simhash_band_2"),
        Index("idx_files_simhash_band_3", "simhash_band_3"),
        CheckConstraint(
            "expiry_status IN ('never', 'active', 'expiring', 'expired')",
            name="ck_files_expiry_status",
        ),
        Index(
            "idx_files_expiry_scan",
            "expires_at",
            "expiry_warning_sent_at",
            "expiry_expired_sent_at",
            postgresql_where=sql_text(
                "expires_at IS NOT NULL "
                "AND status NOT IN ('deleted', 'disabled', 'ragflow_cleanup_failed')"
            ),
        ),
        Index("idx_files_expiry_status", "expiry_status"),
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
    # 语义降级: 仅保存 AI 建议标签快照; 正式标签以 review 模块 tags/file_tags 关联为准。
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
    simhash: Mapped[int | None] = mapped_column(BigInteger)
    simhash_band_0: Mapped[int | None] = mapped_column(SmallInteger)
    simhash_band_1: Mapped[int | None] = mapped_column(SmallInteger)
    simhash_band_2: Mapped[int | None] = mapped_column(SmallInteger)
    simhash_band_3: Mapped[int | None] = mapped_column(SmallInteger)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expiry_status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="never")
    expiry_warning_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expiry_expired_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
