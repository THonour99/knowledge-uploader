"""disable email verification default

Revision ID: 20260708d001
Revises: 20260701d001
Create Date: 2026-07-08 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260708d001"
down_revision: str | None = "20260701d001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE users
        SET status = 'active',
            email_verified = true
        WHERE status = 'pending_email_verification'
        """
    )
    op.execute(
        """
        UPDATE system_configs
        SET value = 'false'::jsonb,
            description = '注册后是否要求邮箱验证 - 当前默认关闭',
            updated_at = now()
        WHERE key = 'security.require_email_verification'
          AND value = 'true'::jsonb
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE system_configs
        SET value = 'true'::jsonb,
            description = '注册后是否要求邮箱验证',
            updated_at = now()
        WHERE key = 'security.require_email_verification'
          AND value = 'false'::jsonb
        """
    )
