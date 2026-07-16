"""add worker execution leases and upload reconciliation metadata

Revision ID: 20260716r002
Revises: 20260716r001
Create Date: 2026-07-16 00:05:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260716r002"
down_revision: str | None = "20260716r001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "document_analysis",
        sa.Column("lease_token", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "sync_tasks",
        sa.Column("lease_token", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "sync_tasks",
        sa.Column("lease_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "sync_tasks",
        sa.Column(
            "reconcile_attempt_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "sync_tasks",
        sa.Column("reconcile_not_before", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "sync_tasks",
        sa.Column("recovery_probe_due_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_sync_tasks_reconcile_attempt_count_non_negative",
        "sync_tasks",
        "reconcile_attempt_count >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_sync_tasks_reconcile_attempt_count_non_negative",
        "sync_tasks",
        type_="check",
    )
    op.drop_column("sync_tasks", "recovery_probe_due_at")
    op.drop_column("sync_tasks", "reconcile_not_before")
    op.drop_column("sync_tasks", "reconcile_attempt_count")
    op.drop_column("sync_tasks", "lease_heartbeat_at")
    op.drop_column("sync_tasks", "lease_token")
    op.drop_column("document_analysis", "lease_token")
