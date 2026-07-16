"""add reliable notification delivery and idempotency

Revision ID: 20260716n001
Revises: 20260716o001
Create Date: 2026-07-16 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260716n001"
down_revision: str | None = "20260716o001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("notifications", sa.Column("source_event_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "notifications",
        sa.Column(
            "delivery_status",
            sa.String(length=20),
            server_default=sa.text("'not_applicable'"),
            nullable=False,
        ),
    )
    op.add_column(
        "notifications",
        sa.Column(
            "delivery_attempts",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "notifications",
        sa.Column("last_delivery_error", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "notifications",
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )

    # A pre-release build allowed email rows without delivery state. Keep them
    # retryable before enforcing the channel/status invariant.
    op.execute(
        """
        UPDATE notifications
        SET delivery_status = 'pending'
        WHERE channel = 'email'
        """
    )
    _normalize_legacy_metadata()

    op.create_check_constraint(
        "ck_notifications_delivery_status",
        "notifications",
        "delivery_status IN ('not_applicable', 'pending', 'sent', 'failed')",
    )
    op.create_check_constraint(
        "ck_notifications_delivery_attempts_nonnegative",
        "notifications",
        "delivery_attempts >= 0",
    )
    op.create_check_constraint(
        "ck_notifications_channel_delivery_status",
        "notifications",
        "(channel = 'in_app' AND delivery_status = 'not_applicable') OR "
        "(channel = 'email' AND delivery_status IN ('pending', 'sent', 'failed'))",
    )
    op.create_foreign_key(
        "fk_notifications_source_event_id_event_outbox",
        "notifications",
        "event_outbox",
        ["source_event_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_notifications_source_recipient_channel",
        "notifications",
        ["source_event_id", "user_id", "channel"],
    )
    op.create_index(
        "idx_notifications_source_event_id",
        "notifications",
        ["source_event_id"],
    )
    op.create_index(
        "idx_notifications_email_pending",
        "notifications",
        ["created_at"],
        postgresql_where=sa.text("channel = 'email' AND delivery_status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("idx_notifications_email_pending", table_name="notifications")
    op.drop_index("idx_notifications_source_event_id", table_name="notifications")
    op.drop_constraint(
        "uq_notifications_source_recipient_channel",
        "notifications",
        type_="unique",
    )
    op.drop_constraint(
        "fk_notifications_source_event_id_event_outbox",
        "notifications",
        type_="foreignkey",
    )
    op.drop_constraint(
        "ck_notifications_channel_delivery_status",
        "notifications",
        type_="check",
    )
    op.drop_constraint(
        "ck_notifications_delivery_attempts_nonnegative",
        "notifications",
        type_="check",
    )
    op.drop_constraint(
        "ck_notifications_delivery_status",
        "notifications",
        type_="check",
    )
    op.drop_column("notifications", "delivered_at")
    op.drop_column("notifications", "last_delivery_error")
    op.drop_column("notifications", "delivery_attempts")
    op.drop_column("notifications", "delivery_status")
    op.drop_column("notifications", "source_event_id")


def _normalize_legacy_metadata() -> None:
    """Replace executable/free-form metadata with the strict allowlisted shape."""
    op.execute(
        r"""
        WITH safe_rows AS (
            SELECT
                id,
                CASE
                    WHEN jsonb_typeof(metadata_json) = 'object' THEN metadata_json
                    ELSE '{}'::jsonb
                END AS metadata
            FROM notifications
        ),
        normalized AS (
            SELECT
                id,
                CASE
                    WHEN (
                        metadata ? 'resource_type'
                        OR metadata ? 'resource_id'
                    )
                    AND metadata->>'resource_type' IN ('file', 'sync_task')
                    AND metadata->>'resource_id' ~*
                        '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                    THEN metadata->>'resource_type'
                    WHEN NOT (
                        metadata ? 'resource_type'
                        OR metadata ? 'resource_id'
                    )
                    AND metadata->>'file_id' ~*
                        '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                    THEN 'file'
                    WHEN NOT (
                        metadata ? 'resource_type'
                        OR metadata ? 'resource_id'
                    )
                    AND metadata->>'sync_task_id' ~*
                        '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                    THEN 'sync_task'
                    ELSE NULL
                END AS resource_type,
                CASE
                    WHEN (
                        metadata ? 'resource_type'
                        OR metadata ? 'resource_id'
                    )
                    AND metadata->>'resource_type' IN ('file', 'sync_task')
                    AND metadata->>'resource_id' ~*
                        '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                    THEN metadata->>'resource_id'
                    WHEN NOT (
                        metadata ? 'resource_type'
                        OR metadata ? 'resource_id'
                    )
                    AND metadata->>'file_id' ~*
                        '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                    THEN metadata->>'file_id'
                    WHEN NOT (
                        metadata ? 'resource_type'
                        OR metadata ? 'resource_id'
                    )
                    AND metadata->>'sync_task_id' ~*
                        '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                    THEN metadata->>'sync_task_id'
                    ELSE NULL
                END AS resource_id,
                left(
                    NULLIF(
                        btrim(
                            COALESCE(
                                NULLIF(metadata->>'status', ''),
                                NULLIF(metadata->>'review_status', '')
                            )
                        ),
                        ''
                    ),
                    80
                ) AS status,
                CASE
                    WHEN metadata->>'expiry_status' IN ('expiring', 'expired')
                    THEN metadata->>'expiry_status'
                    ELSE NULL
                END AS expiry_status,
                CASE
                    WHEN metadata->>'expires_at' ~
                        '^[0-9]{4}-[0-9]{2}-[0-9]{2}T.+(Z|[+-][0-9]{2}:[0-9]{2})$'
                    THEN metadata->>'expires_at'
                    ELSE NULL
                END AS expires_at
            FROM safe_rows
        )
        UPDATE notifications AS notification
        SET metadata_json = jsonb_strip_nulls(
            jsonb_build_object(
                'resource_type', normalized.resource_type,
                'resource_id', normalized.resource_id,
                'status', normalized.status,
                'expiry_status', normalized.expiry_status,
                'expires_at', normalized.expires_at
            )
        )
        FROM normalized
        WHERE notification.id = normalized.id
        """
    )
