"""add announcements

Revision ID: 20260720a001
Revises: 20260720r001
Create Date: 2026-07-20 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260720a001"
down_revision: str | None = "20260720r001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "announcements",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body_markdown", sa.Text(), nullable=False),
        sa.Column("audience_type", sa.String(length=20), server_default="all", nullable=False),
        sa.Column("lifecycle_state", sa.String(length=20), server_default="draft", nullable=False),
        sa.Column("visible_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_pinned", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("published_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("withdrawn_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("withdraw_reason", sa.String(length=500), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "audience_type IN ('all', 'departments', 'roles')",
            name="ck_announcements_audience_type",
        ),
        sa.CheckConstraint(
            "lifecycle_state IN ('draft', 'released', 'withdrawn')",
            name="ck_announcements_lifecycle_state",
        ),
        sa.CheckConstraint(
            "char_length(btrim(title)) BETWEEN 1 AND 200", name="ck_announcements_title"
        ),
        sa.CheckConstraint(
            "char_length(btrim(body_markdown)) BETWEEN 1 AND 50000",
            name="ck_announcements_body_markdown",
        ),
        sa.CheckConstraint("row_version >= 1", name="ck_announcements_row_version"),
        sa.CheckConstraint(
            "expires_at IS NULL OR (visible_from IS NOT NULL AND expires_at > visible_from)",
            name="ck_announcements_time_window",
        ),
        sa.CheckConstraint(
            "lifecycle_state = 'draft' OR visible_from IS NOT NULL",
            name="ck_announcements_released_visible_from",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["published_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["withdrawn_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_announcements_public_window",
        "announcements",
        ["lifecycle_state", "visible_from", "expires_at"],
    )
    op.create_index("idx_announcements_pinned", "announcements", ["is_pinned", "visible_from"])
    op.create_index("idx_announcements_created_at", "announcements", ["created_at"])
    op.create_table(
        "announcement_departments",
        sa.Column("announcement_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("department_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["announcement_id"], ["announcements.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["department_id"], ["departments.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("announcement_id", "department_id"),
    )
    op.create_index(
        "idx_announcement_departments_department_id", "announcement_departments", ["department_id"]
    )
    op.create_table(
        "announcement_roles",
        sa.Column("announcement_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=40), nullable=False),
        sa.CheckConstraint(
            "role IN ('employee', 'dept_admin', 'system_admin')", name="ck_announcement_roles_role"
        ),
        sa.ForeignKeyConstraint(["announcement_id"], ["announcements.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("announcement_id", "role"),
    )
    op.create_index("idx_announcement_roles_role", "announcement_roles", ["role"])
    op.create_table(
        "announcement_reads",
        sa.Column("announcement_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "read_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["announcement_id"], ["announcements.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("announcement_id", "user_id"),
    )
    op.create_index("idx_announcement_reads_user_id", "announcement_reads", ["user_id", "read_at"])


def downgrade() -> None:
    op.drop_index("idx_announcement_reads_user_id", table_name="announcement_reads")
    op.drop_table("announcement_reads")
    op.drop_index("idx_announcement_roles_role", table_name="announcement_roles")
    op.drop_table("announcement_roles")
    op.drop_index(
        "idx_announcement_departments_department_id", table_name="announcement_departments"
    )
    op.drop_table("announcement_departments")
    op.drop_index("idx_announcements_created_at", table_name="announcements")
    op.drop_index("idx_announcements_pinned", table_name="announcements")
    op.drop_index("idx_announcements_public_window", table_name="announcements")
    op.drop_table("announcements")
