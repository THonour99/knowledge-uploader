"""add department ids to users files

Revision ID: 20260623d002
Revises: 20260623d001
Create Date: 2026-06-23 00:10:00.000000
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260623d002"
down_revision: str | None = "20260623d001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UNASSIGNED_DEPARTMENT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
UNASSIGNED_DEFAULT = sa.text("'00000000-0000-0000-0000-000000000001'::uuid")


def upgrade() -> None:
    op.add_column("users", sa.Column("department_id", sa.Uuid(), nullable=True))
    op.execute(
        sa.text(
            "UPDATE users SET department_id = :department_id " "WHERE department_id IS NULL"
        ).bindparams(sa.bindparam("department_id", value=UNASSIGNED_DEPARTMENT_ID, type_=sa.Uuid()))
    )
    op.alter_column("users", "department_id", server_default=UNASSIGNED_DEFAULT)
    op.alter_column("users", "department_id", nullable=False)
    op.create_foreign_key(
        "fk_users_department_id_departments",
        "users",
        "departments",
        ["department_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("idx_users_department_id", "users", ["department_id"])
    op.create_index(
        "idx_users_department_role_status",
        "users",
        ["department_id", "role", "status"],
    )

    op.add_column("files", sa.Column("department_id", sa.Uuid(), nullable=True))
    op.execute(
        sa.text(
            "UPDATE files SET department_id = COALESCE(users.department_id, :department_id) "
            "FROM users WHERE files.uploader_id = users.id AND files.department_id IS NULL"
        ).bindparams(sa.bindparam("department_id", value=UNASSIGNED_DEPARTMENT_ID, type_=sa.Uuid()))
    )
    op.execute(
        sa.text(
            "UPDATE files SET department_id = :department_id " "WHERE department_id IS NULL"
        ).bindparams(sa.bindparam("department_id", value=UNASSIGNED_DEPARTMENT_ID, type_=sa.Uuid()))
    )
    op.alter_column("files", "department_id", server_default=UNASSIGNED_DEFAULT)
    op.alter_column("files", "department_id", nullable=False)
    op.create_foreign_key(
        "fk_files_department_id_departments",
        "files",
        "departments",
        ["department_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("idx_files_department_id", "files", ["department_id"])
    op.create_index("idx_files_department_uploaded_at", "files", ["department_id", "uploaded_at"])
    op.create_index(
        "idx_files_department_review_status",
        "files",
        ["department_id", "review_status"],
    )
    op.create_index("idx_files_uploader_status", "files", ["uploader_id", "status"])


def downgrade() -> None:
    op.drop_index("idx_files_uploader_status", table_name="files")
    op.drop_index("idx_files_department_review_status", table_name="files")
    op.drop_index("idx_files_department_uploaded_at", table_name="files")
    op.drop_index("idx_files_department_id", table_name="files")
    op.drop_constraint("fk_files_department_id_departments", "files", type_="foreignkey")
    op.drop_column("files", "department_id")
    op.drop_index("idx_users_department_role_status", table_name="users")
    op.drop_index("idx_users_department_id", table_name="users")
    op.drop_constraint("fk_users_department_id_departments", "users", type_="foreignkey")
    op.drop_column("users", "department_id")
