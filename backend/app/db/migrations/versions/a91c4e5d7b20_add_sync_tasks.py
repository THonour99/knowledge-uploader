"""add sync tasks

Revision ID: a91c4e5d7b20
Revises: 3f9a1c7d2b84
Create Date: 2026-06-05 18:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a91c4e5d7b20"
down_revision: str | None = "3f9a1c7d2b84"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sync_tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("file_id", sa.Uuid(), nullable=False),
        sa.Column("task_type", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=40), server_default="queued", nullable=False),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_retry_count", sa.Integer(), server_default="3", nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "task_type IN ('ragflow_upload', 'ragflow_parse', 'ragflow_status_check')",
            name="ck_sync_tasks_task_type",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'canceled')",
            name="ck_sync_tasks_status",
        ),
        sa.CheckConstraint("retry_count >= 0", name="ck_sync_tasks_retry_count_non_negative"),
        sa.CheckConstraint(
            "max_retry_count >= 0",
            name="ck_sync_tasks_max_retry_count_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["file_id"],
            ["files.id"],
            name="fk_sync_tasks_file_id_files",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_sync_tasks_file_id", "sync_tasks", ["file_id"], unique=False)
    op.create_index("idx_sync_tasks_status", "sync_tasks", ["status"], unique=False)
    op.create_index("idx_sync_tasks_task_type", "sync_tasks", ["task_type"], unique=False)
    op.create_index("idx_sync_tasks_created_at", "sync_tasks", ["created_at"], unique=False)
    op.create_index(
        "uq_sync_tasks_active_ragflow_upload_per_file",
        "sync_tasks",
        ["file_id"],
        unique=True,
        postgresql_where=sa.text(
            "task_type = 'ragflow_upload' AND status IN ('queued', 'running')"
        ),
    )

    op.create_table(
        "sync_task_logs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'canceled')",
            name="ck_sync_task_logs_status",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["sync_tasks.id"],
            name="fk_sync_task_logs_task_id_sync_tasks",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_sync_task_logs_task_id",
        "sync_task_logs",
        ["task_id"],
        unique=False,
    )
    op.create_index(
        "idx_sync_task_logs_created_at",
        "sync_task_logs",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_sync_task_logs_created_at", table_name="sync_task_logs")
    op.drop_index("idx_sync_task_logs_task_id", table_name="sync_task_logs")
    op.drop_table("sync_task_logs")
    op.drop_index("uq_sync_tasks_active_ragflow_upload_per_file", table_name="sync_tasks")
    op.drop_index("idx_sync_tasks_created_at", table_name="sync_tasks")
    op.drop_index("idx_sync_tasks_task_type", table_name="sync_tasks")
    op.drop_index("idx_sync_tasks_status", table_name="sync_tasks")
    op.drop_index("idx_sync_tasks_file_id", table_name="sync_tasks")
    op.drop_table("sync_tasks")
