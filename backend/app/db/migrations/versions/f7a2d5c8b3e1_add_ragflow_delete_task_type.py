"""add ragflow_delete task type

Revision ID: f7a2d5c8b3e1
Revises: d4e7a9b2c5f8
Create Date: 2026-06-11 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "f7a2d5c8b3e1"
down_revision: str | None = "d4e7a9b2c5f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT_NAME = "ck_sync_tasks_task_type"
TABLE_NAME = "sync_tasks"
OLD_TASK_TYPES = "('ragflow_upload', 'ragflow_parse', 'ragflow_status_check')"
NEW_TASK_TYPES = "('ragflow_upload', 'ragflow_parse', 'ragflow_status_check', 'ragflow_delete')"


def upgrade() -> None:
    op.drop_constraint(CONSTRAINT_NAME, TABLE_NAME, type_="check")
    op.create_check_constraint(
        CONSTRAINT_NAME,
        TABLE_NAME,
        f"task_type IN {NEW_TASK_TYPES}",
    )


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT_NAME, TABLE_NAME, type_="check")
    # 旧约束不允许 ragflow_delete, 回滚前先清理该类型任务 (日志随 FK CASCADE 删除)
    op.execute("DELETE FROM sync_tasks WHERE task_type = 'ragflow_delete'")
    op.create_check_constraint(
        CONSTRAINT_NAME,
        TABLE_NAME,
        f"task_type IN {OLD_TASK_TYPES}",
    )
