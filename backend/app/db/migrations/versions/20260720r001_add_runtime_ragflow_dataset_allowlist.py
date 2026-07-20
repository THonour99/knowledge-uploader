"""add runtime RAGFlow dataset allowlist

Revision ID: 20260720r001
Revises: 20260716s002
Create Date: 2026-07-20 10:00:00.000000
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260720r001"
down_revision: str | None = "20260716s002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OPERATIONS_BEFORE = (
    "'delete_document', 'find_document_by_name', 'get_document_status', 'ping', "
    "'start_parse', 'update_document_metadata', 'upload_document'"
)
_OPERATIONS_AFTER = (
    "'delete_document', 'find_document_by_name', 'get_document_status', "
    "'list_datasets', 'ping', 'start_parse', 'update_document_metadata', "
    "'upload_document'"
)


def _system_configs_table() -> sa.TableClause:
    return sa.table(
        "system_configs",
        sa.column("id", sa.Uuid()),
        sa.column("key", sa.String()),
        sa.column("group", sa.String()),
        sa.column("value", postgresql.JSONB(astext_type=sa.Text())),  # type: ignore[no-untyped-call]
        sa.column("value_type", sa.String()),
        sa.column("is_secret", sa.Boolean()),
        sa.column("description", sa.Text()),
    )


def _replace_operation_constraint(operations: str) -> None:
    op.drop_constraint(
        "ck_ragflow_api_calls_operation",
        "ragflow_api_calls",
        type_="check",
    )
    op.create_check_constraint(
        "ck_ragflow_api_calls_operation",
        "ragflow_api_calls",
        f"operation IN ({operations})",
    )


def upgrade() -> None:
    _replace_operation_constraint(_OPERATIONS_AFTER)
    op.bulk_insert(
        _system_configs_table(),
        [
            {
                "id": uuid.uuid5(uuid.NAMESPACE_URL, "system-config:ragflow.allowed_dataset_ids"),
                "key": "ragflow.allowed_dataset_ids",
                "group": "ragflow",
                "value": None,
                "value_type": "list",
                "is_secret": False,
                "description": "允许同步的 RAGFlow Dataset ID 白名单 空列表时禁止同步",
            }
        ],
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM system_configs WHERE key = 'ragflow.allowed_dataset_ids'"))
    op.execute(sa.text("DELETE FROM ragflow_api_calls WHERE operation = 'list_datasets'"))
    _replace_operation_constraint(_OPERATIONS_BEFORE)
