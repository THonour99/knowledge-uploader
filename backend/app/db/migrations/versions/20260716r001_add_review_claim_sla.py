"""add review claim and SLA metadata

Revision ID: 20260716r001
Revises: 20260708d001
Create Date: 2026-07-16 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260716r001"
down_revision: str | None = "20260708d001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("files", sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("files", sa.Column("review_due_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "files",
        sa.Column(
            "claimed_by",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.add_column("files", sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "files",
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "files",
        sa.Column("review_version", sa.Integer(), server_default="0", nullable=False),
    )
    op.execute(
        """
        UPDATE files
        SET review_status = CASE
                WHEN status = 'pending_review' THEN 'pending'
                WHEN status = 'approved' THEN 'approved'
                WHEN status = 'rejected' THEN 'rejected'
                WHEN review_status = 'in_review' THEN 'pending'
                ELSE review_status
            END,
            claimed_by = NULL,
            claimed_at = NULL,
            claim_expires_at = NULL
        """
    )
    op.execute(
        """
        UPDATE files
        SET claimed_by = NULL,
            claimed_at = NULL,
            claim_expires_at = NULL,
            review_status = CASE
                WHEN status = 'pending_review' THEN 'pending'
                ELSE review_status
            END
        WHERE NOT (
            (claimed_by IS NULL AND claimed_at IS NULL AND claim_expires_at IS NULL)
            OR
            (claimed_by IS NOT NULL AND claimed_at IS NOT NULL
             AND claim_expires_at IS NOT NULL AND claim_expires_at > claimed_at)
        )
        """
    )
    op.execute(
        """
        UPDATE files
        SET submitted_at = COALESCE(submitted_at, updated_at, uploaded_at, now()),
            review_due_at = CASE
                WHEN review_due_at IS NULL
                     OR review_due_at <= COALESCE(
                         submitted_at,
                         updated_at,
                         uploaded_at,
                         now()
                     )
                THEN COALESCE(submitted_at, updated_at, uploaded_at, now())
                     + interval '24 hours'
                ELSE review_due_at
            END
        WHERE status = 'pending_review'
        """
    )
    op.execute(
        """
        UPDATE files
        SET submitted_at = NULL,
            review_due_at = NULL
        WHERE status <> 'pending_review'
          AND NOT (
              (submitted_at IS NULL AND review_due_at IS NULL)
              OR
              (submitted_at IS NOT NULL AND review_due_at IS NOT NULL
               AND review_due_at > submitted_at)
          )
        """
    )
    op.create_check_constraint(
        "ck_files_review_version_non_negative",
        "files",
        "review_version >= 0",
    )
    op.create_check_constraint(
        "ck_files_review_sla_pair_valid",
        "files",
        "(submitted_at IS NULL AND review_due_at IS NULL) OR "
        "(submitted_at IS NOT NULL AND review_due_at IS NOT NULL "
        "AND review_due_at > submitted_at)",
    )
    op.create_check_constraint(
        "ck_files_pending_review_requires_sla",
        "files",
        "status <> 'pending_review' OR "
        "(submitted_at IS NOT NULL AND review_due_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_files_claim_expiry_after_claim",
        "files",
        "(claimed_by IS NULL AND claimed_at IS NULL AND claim_expires_at IS NULL) OR "
        "(claimed_by IS NOT NULL AND claimed_at IS NOT NULL "
        "AND claim_expires_at IS NOT NULL AND claim_expires_at > claimed_at)",
    )
    op.create_check_constraint(
        "ck_files_claim_review_status_consistent",
        "files",
        "(status = 'pending_review' AND ("
        "(review_status = 'pending' AND claimed_by IS NULL "
        "AND claimed_at IS NULL AND claim_expires_at IS NULL) OR "
        "(review_status = 'in_review' AND claimed_by IS NOT NULL "
        "AND claimed_at IS NOT NULL AND claim_expires_at IS NOT NULL))) OR "
        "(status <> 'pending_review' AND claimed_by IS NULL "
        "AND claimed_at IS NULL AND claim_expires_at IS NULL "
        "AND review_status <> 'in_review')",
    )
    op.create_index(
        "idx_files_review_queue",
        "files",
        ["review_due_at", "submitted_at"],
        postgresql_where=sa.text("status = 'pending_review'"),
    )
    op.create_index(
        "idx_files_review_claim",
        "files",
        ["claimed_by", "claim_expires_at"],
        postgresql_where=sa.text("claimed_by IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_files_review_claim", table_name="files")
    op.drop_index("idx_files_review_queue", table_name="files")
    op.drop_constraint("ck_files_claim_review_status_consistent", "files", type_="check")
    op.drop_constraint("ck_files_claim_expiry_after_claim", "files", type_="check")
    op.drop_constraint("ck_files_pending_review_requires_sla", "files", type_="check")
    op.drop_constraint("ck_files_review_sla_pair_valid", "files", type_="check")
    op.drop_constraint("ck_files_review_version_non_negative", "files", type_="check")
    op.drop_column("files", "review_version")
    op.drop_column("files", "claim_expires_at")
    op.drop_column("files", "claimed_at")
    op.drop_column("files", "claimed_by")
    op.drop_column("files", "review_due_at")
    op.drop_column("files", "submitted_at")
