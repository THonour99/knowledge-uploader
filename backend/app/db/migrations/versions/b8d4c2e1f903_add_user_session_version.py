"""add user session version

Revision ID: b8d4c2e1f903
Revises: 9c1f4d2a6b7e
Create Date: 2026-06-05 10:05:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8d4c2e1f903"
down_revision: str | None = "9c1f4d2a6b7e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("session_version", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_check_constraint(
        "ck_users_session_version_non_negative",
        "users",
        "session_version >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_users_session_version_non_negative",
        "users",
        type_="check",
    )
    op.drop_column("users", "session_version")
