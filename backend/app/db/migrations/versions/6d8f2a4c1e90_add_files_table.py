"""add files table

Revision ID: 6d8f2a4c1e90
Revises: b8d4c2e1f903
Create Date: 2026-06-05 14:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "6d8f2a4c1e90"
down_revision: str | None = "b8d4c2e1f903"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "files",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column("stored_name", sa.String(length=255), nullable=False),
        sa.Column("extension", sa.String(length=20), nullable=False),
        sa.Column("mime_type", sa.String(length=120), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.Column("storage_type", sa.String(length=20), server_default="minio", nullable=False),
        sa.Column("bucket", sa.String(length=100), nullable=False),
        sa.Column("object_key", sa.String(length=512), nullable=False),
        sa.Column("uploader_id", sa.Uuid(), nullable=False),
        sa.Column("department", sa.String(length=100), nullable=True),
        sa.Column("category_id", sa.Uuid(), nullable=True),
        sa.Column("dataset_mapping_id", sa.Uuid(), nullable=True),
        sa.Column("visibility", sa.String(length=20), server_default="private", nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "tags",
            postgresql.JSONB(astext_type=sa.Text()),  # type: ignore[no-untyped-call]
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=40), server_default="uploaded", nullable=False),
        sa.Column(
            "review_status",
            sa.String(length=40),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("ragflow_dataset_id", sa.String(length=120), nullable=True),
        sa.Column("ragflow_document_id", sa.String(length=120), nullable=True),
        sa.Column("ragflow_parse_status", sa.String(length=40), nullable=True),
        sa.Column("ragflow_error_message", sa.Text(), nullable=True),
        sa.Column(
            "ai_analysis_enabled_at_upload",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
        sa.Column(
            "ai_config_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),  # type: ignore[no-untyped-call]
            nullable=True,
        ),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("hash ~ '^[0-9a-f]{64}$'", name="ck_files_hash_sha256_hex"),
        sa.CheckConstraint(
            "review_status IN ('pending', 'in_review', 'approved', 'rejected')",
            name="ck_files_review_status",
        ),
        sa.CheckConstraint("size > 0", name="ck_files_size_positive"),
        sa.CheckConstraint(
            "status IN ("
            "'uploaded', 'extracting_text', 'analysis_queued', 'analyzing', "
            "'analysis_failed', 'analyzed', 'pending_review', 'sensitive_review_required', "
            "'approved', 'rejected', 'queued', 'syncing', 'uploaded_to_ragflow', "
            "'parsing', 'parsed', 'failed', 'disabled', 'deleted'"
            ")",
            name="ck_files_status",
        ),
        sa.CheckConstraint("storage_type IN ('minio')", name="ck_files_storage_type"),
        sa.CheckConstraint(
            "visibility IN ('private', 'department', 'company')",
            name="ck_files_visibility",
        ),
        sa.ForeignKeyConstraint(["uploader_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_files_hash", "files", ["hash"], unique=False)
    op.create_index("idx_files_object_key", "files", ["object_key"], unique=False)
    op.create_index("idx_files_review_status", "files", ["review_status"], unique=False)
    op.create_index("idx_files_status", "files", ["status"], unique=False)
    op.create_index(
        "idx_files_uploader_uploaded_at",
        "files",
        ["uploader_id", "uploaded_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_files_uploader_uploaded_at", table_name="files")
    op.drop_index("idx_files_status", table_name="files")
    op.drop_index("idx_files_review_status", table_name="files")
    op.drop_index("idx_files_object_key", table_name="files")
    op.drop_index("idx_files_hash", table_name="files")
    op.drop_table("files")
