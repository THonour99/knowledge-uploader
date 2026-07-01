"""drop external auth columns

Revision ID: 20260701d001
Revises: 20260623d003
Create Date: 2026-07-01 10:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260701d001"
down_revision: str | None = "20260623d003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 移除外部/钉钉登录预留字段: 项目仅支持本地邮箱+密码登录, 这些列从未被业务代码读取。
    op.drop_constraint("ck_users_auth_provider", "users", type_="check")
    op.drop_column("users", "ding_user_id")
    op.drop_column("users", "external_user_id")
    op.drop_column("users", "auth_provider")


def downgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "auth_provider",
            sa.String(length=40),
            server_default="local",
            nullable=False,
        ),
    )
    op.add_column(
        "users",
        sa.Column("external_user_id", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("ding_user_id", sa.String(length=120), nullable=True),
    )
    op.create_check_constraint(
        "ck_users_auth_provider",
        "users",
        "auth_provider IN ('local', 'dingtalk', 'external')",
    )
