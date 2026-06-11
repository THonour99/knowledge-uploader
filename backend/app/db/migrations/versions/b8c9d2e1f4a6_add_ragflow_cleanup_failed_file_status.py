"""add ragflow cleanup failed file status

Revision ID: b8c9d2e1f4a6
Revises: f7a2d5c8b3e1
Create Date: 2026-06-11 16:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b8c9d2e1f4a6"
down_revision: str | None = "f7a2d5c8b3e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT_NAME = "ck_files_status"
TABLE_NAME = "files"
OLD_STATUSES = (
    "'uploaded', 'extracting_text', 'analysis_queued', 'analyzing', "
    "'analysis_failed', 'analyzed', 'pending_review', 'sensitive_review_required', "
    "'approved', 'rejected', 'queued', 'syncing', 'uploaded_to_ragflow', "
    "'parsing', 'parsed', 'failed', 'disabled', 'deleted'"
)
NEW_STATUSES = f"{OLD_STATUSES}, 'ragflow_cleanup_failed'"


def upgrade() -> None:
    op.drop_constraint(CONSTRAINT_NAME, TABLE_NAME, type_="check")
    op.create_check_constraint(
        CONSTRAINT_NAME,
        TABLE_NAME,
        f"status IN ({NEW_STATUSES})",
    )


def downgrade() -> None:
    op.execute("UPDATE files SET status = 'deleted' WHERE status = 'ragflow_cleanup_failed'")
    op.drop_constraint(CONSTRAINT_NAME, TABLE_NAME, type_="check")
    op.create_check_constraint(
        CONSTRAINT_NAME,
        TABLE_NAME,
        f"status IN ({OLD_STATUSES})",
    )
