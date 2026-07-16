"""add editable file draft title

Revision ID: 20260716d002
Revises: 20260716r002
Create Date: 2026-07-16 00:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260716d002"
down_revision: str | None = "20260716r002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("files", sa.Column("title", sa.String(length=255), nullable=True))
    op.execute("UPDATE files SET title = original_name WHERE title IS NULL")
    op.alter_column(
        "files",
        "title",
        existing_type=sa.String(length=255),
        nullable=False,
    )
    op.alter_column(
        "files",
        "title",
        existing_type=sa.String(length=255),
        server_default=None,
    )


def downgrade() -> None:
    op.drop_column("files", "title")
