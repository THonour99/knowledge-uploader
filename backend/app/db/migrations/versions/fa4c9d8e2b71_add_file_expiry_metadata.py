"""add file expiry metadata

Revision ID: fa4c9d8e2b71
Revises: e8a9c1d2f3b4
Create Date: 2026-06-15 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "fa4c9d8e2b71"
down_revision: str | None = "e8a9c1d2f3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("files", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "files",
        sa.Column("expiry_status", sa.String(length=20), server_default="never", nullable=False),
    )
    op.add_column(
        "files",
        sa.Column("expiry_warning_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "files",
        sa.Column("expiry_expired_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_files_expiry_status",
        "files",
        "expiry_status IN ('never', 'active', 'expiring', 'expired')",
    )
    op.create_index("idx_files_expiry_status", "files", ["expiry_status"])
    op.create_index(
        "idx_files_expiry_scan",
        "files",
        ["expires_at", "expiry_warning_sent_at", "expiry_expired_sent_at"],
        postgresql_where=sa.text(
            "expires_at IS NOT NULL "
            "AND status NOT IN ('deleted', 'disabled', 'ragflow_cleanup_failed')"
        ),
    )


def downgrade() -> None:
    op.drop_index("idx_files_expiry_scan", table_name="files")
    op.drop_index("idx_files_expiry_status", table_name="files")
    op.drop_constraint("ck_files_expiry_status", "files", type_="check")
    op.drop_column("files", "expiry_expired_sent_at")
    op.drop_column("files", "expiry_warning_sent_at")
    op.drop_column("files", "expiry_status")
    op.drop_column("files", "expires_at")
