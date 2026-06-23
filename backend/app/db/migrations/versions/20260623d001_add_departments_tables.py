"""add departments tables

Revision ID: 20260623d001
Revises: fa4c9d8e2b71
Create Date: 2026-06-23 00:00:00.000000
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260623d001"
down_revision: str | None = "fa4c9d8e2b71"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UNASSIGNED_DEPARTMENT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def upgrade() -> None:
    op.create_table(
        "departments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_departments_status"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_departments_name", "departments", ["name"], unique=True)
    op.create_index("uq_departments_code", "departments", ["code"], unique=True)
    op.create_index("idx_departments_status", "departments", ["status"])
    op.create_index("idx_departments_created_at", "departments", ["created_at"])
    op.execute(
        sa.text(
            "INSERT INTO departments (id, name, code, status) "
            "VALUES (:id, '未分配', 'unassigned', 'active') "
            "ON CONFLICT (code) DO NOTHING"
        ).bindparams(sa.bindparam("id", value=UNASSIGNED_DEPARTMENT_ID, type_=sa.Uuid()))
    )
    op.create_table(
        "user_managed_departments",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["department_id"], ["departments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "department_id"),
    )
    op.create_index(
        "idx_user_managed_departments_department_id",
        "user_managed_departments",
        ["department_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_user_managed_departments_department_id",
        table_name="user_managed_departments",
    )
    op.drop_table("user_managed_departments")
    op.drop_index("idx_departments_created_at", table_name="departments")
    op.drop_index("idx_departments_status", table_name="departments")
    op.drop_index("uq_departments_code", table_name="departments")
    op.drop_index("uq_departments_name", table_name="departments")
    op.drop_table("departments")
