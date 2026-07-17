"""add permission-scoped saved views

Revision ID: 20260716s001
Revises: 20260716l001
Create Date: 2026-07-17 10:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260716s001"
down_revision: str | None = "20260716l001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROLLBACK_BACKUP_TABLE = "saved_views_rollback_backup"


def upgrade() -> None:
    op.create_table(
        "saved_views",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "owner_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("scope", sa.String(length=20), nullable=False),
        sa.Column(
            "department_id",
            sa.Uuid(),
            sa.ForeignKey("departments.id", ondelete="RESTRICT"),
        ),
        sa.Column("page_key", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("definition_schema_version", sa.SmallInteger(), nullable=False),
        sa.Column("query_definition", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("column_preferences", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "row_version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "scope IN ('private', 'department')",
            name="ck_saved_views_scope",
        ),
        sa.CheckConstraint(
            "page_key IN ('my_files', 'review_files', 'task_logs', 'statistics')",
            name="ck_saved_views_page_key",
        ),
        sa.CheckConstraint(
            "scope = 'private' OR page_key IN ('review_files', 'task_logs')",
            name="ck_saved_views_department_page_scope",
        ),
        sa.CheckConstraint(
            "(scope = 'private' AND department_id IS NULL) OR "
            "(scope = 'department' AND department_id IS NOT NULL)",
            name="ck_saved_views_scope_department",
        ),
        sa.CheckConstraint(
            "definition_schema_version > 0",
            name="ck_saved_views_schema_version_positive",
        ),
        sa.CheckConstraint("row_version > 0", name="ck_saved_views_row_version_positive"),
        sa.CheckConstraint(
            "length(btrim(name)) BETWEEN 1 AND 80",
            name="ck_saved_views_name_length",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(query_definition) = 'object'",
            name="ck_saved_views_query_definition_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(column_preferences) = 'object'",
            name="ck_saved_views_column_preferences_object",
        ),
        sa.CheckConstraint(
            "octet_length(query_definition::text) <= 8192",
            name="ck_saved_views_query_definition_size",
        ),
        sa.CheckConstraint(
            "octet_length(column_preferences::text) <= 4096",
            name="ck_saved_views_column_preferences_size",
        ),
    )
    op.create_index(
        "idx_saved_views_owner_page",
        "saved_views",
        ["owner_id", "page_key"],
    )
    op.create_index(
        "idx_saved_views_department_page",
        "saved_views",
        ["department_id", "page_key"],
    )
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX uq_saved_views_private_name "
            "ON saved_views (owner_id, page_key, lower(name)) WHERE scope = 'private'"
        )
    )
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX uq_saved_views_department_name "
            "ON saved_views (department_id, page_key, lower(name)) "
            "WHERE scope = 'department'"
        )
    )
    _restore_rollback_backup()


def _restore_rollback_backup() -> None:
    if not sa.inspect(op.get_bind()).has_table(_ROLLBACK_BACKUP_TABLE):
        return
    columns = (
        "id, owner_id, scope, department_id, page_key, name, "
        "definition_schema_version, query_definition, column_preferences, "
        "row_version, created_at, updated_at"
    )
    op.execute(
        sa.text(
            f"INSERT INTO saved_views ({columns}) SELECT {columns} FROM {_ROLLBACK_BACKUP_TABLE}"
        )
    )
    op.drop_table(_ROLLBACK_BACKUP_TABLE)


def downgrade() -> None:
    _create_rollback_backup()
    op.drop_index("uq_saved_views_department_name", table_name="saved_views")
    op.drop_index("uq_saved_views_private_name", table_name="saved_views")
    op.drop_index("idx_saved_views_department_page", table_name="saved_views")
    op.drop_index("idx_saved_views_owner_page", table_name="saved_views")
    op.drop_table("saved_views")


def _create_rollback_backup() -> None:
    if sa.inspect(op.get_bind()).has_table(_ROLLBACK_BACKUP_TABLE):
        raise RuntimeError("saved views rollback backup already exists")
    op.create_table(
        _ROLLBACK_BACKUP_TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("scope", sa.String(length=20), nullable=False),
        sa.Column("department_id", sa.Uuid()),
        sa.Column("page_key", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("definition_schema_version", sa.SmallInteger(), nullable=False),
        sa.Column("query_definition", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("column_preferences", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    columns = (
        "id, owner_id, scope, department_id, page_key, name, "
        "definition_schema_version, query_definition, column_preferences, "
        "row_version, created_at, updated_at"
    )
    op.execute(
        sa.text(
            f"INSERT INTO {_ROLLBACK_BACKUP_TABLE} ({columns}) SELECT {columns} FROM saved_views"
        )
    )
