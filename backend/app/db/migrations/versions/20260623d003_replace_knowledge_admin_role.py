"""replace knowledge admin role

Revision ID: 20260623d003
Revises: 20260623d002
Create Date: 2026-06-23 00:20:00.000000

Downgrade can restore the old role check constraint and maps dept_admin back to
knowledge_admin, but system_admin rows that were promoted from knowledge_admin
cannot be distinguished from pre-existing system_admin rows.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260623d003"
down_revision: str | None = "20260623d002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE users SET role = 'system_admin' WHERE role = 'knowledge_admin'")
    op.drop_constraint("ck_users_role", "users", type_="check")
    op.create_check_constraint(
        "ck_users_role",
        "users",
        "role IN ('employee', 'dept_admin', 'system_admin')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_users_role", "users", type_="check")
    op.execute("UPDATE users SET role = 'knowledge_admin' WHERE role = 'dept_admin'")
    op.create_check_constraint(
        "ck_users_role",
        "users",
        "role IN ('employee', 'knowledge_admin', 'system_admin')",
    )
