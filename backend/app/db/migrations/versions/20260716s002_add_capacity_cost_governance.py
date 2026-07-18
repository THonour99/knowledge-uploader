"""add capacity and cost governance

Revision ID: 20260716s002
Revises: 20260716v001
Create Date: 2026-07-17 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "20260716s002"
down_revision: str | None = "20260716v001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COST_STATUSES = "'known', 'unknown_pricing', 'unknown_usage', 'legacy_unverifiable'"
_RAGFLOW_OPERATIONS = (
    "'delete_document', 'find_document_by_name', 'get_document_status', 'ping', "
    "'start_parse', 'update_document_metadata', 'upload_document'"
)
_RAGFLOW_FAILURE_CATEGORIES = (
    "'authentication', 'authorization', 'configuration', 'conflict', 'network', "
    "'not_found', 'protocol', 'rate_limited', 'timeout', 'unknown', 'upstream_5xx'"
)
_PROVIDER_SHADOW = "s002_ai_provider_pricing_backup"
_DOCUMENT_COST_SHADOW = "s002_document_analysis_cost_backup"
_USAGE_COST_SHADOW = "s002_ai_usage_cost_backup"
_RAGFLOW_SHADOW = "s002_ragflow_api_calls_backup"
_CAPACITY_SHADOW = "s002_storage_capacity_snapshots_backup"
_SHADOW_TABLES = (
    _PROVIDER_SHADOW,
    _DOCUMENT_COST_SHADOW,
    _USAGE_COST_SHADOW,
    _RAGFLOW_SHADOW,
    _CAPACITY_SHADOW,
)


def upgrade() -> None:
    restore_pending = _has_complete_shadow_backup()
    op.create_index("idx_files_uploaded_at", "files", ["uploaded_at"])
    _add_pricing_configuration_semantics()
    _add_cost_status("document_analysis", has_nullable_usage=False)
    _add_cost_status("ai_usage_logs", has_nullable_usage=True)
    _create_ragflow_api_calls()
    _create_storage_capacity_snapshots()
    if restore_pending:
        _restore_shadow_backup()


def _has_complete_shadow_backup() -> bool:
    connection = op.get_bind()
    states = [
        bool(
            connection.execute(
                sa.text("SELECT to_regclass(:qualified_name) IS NOT NULL"),
                {"qualified_name": f"public.{table_name}"},
            ).scalar_one()
        )
        for table_name in _SHADOW_TABLES
    ]
    if any(states) and not all(states):
        raise RuntimeError("capacity governance shadow backup set is incomplete")
    return all(states)


def _restore_shadow_backup() -> None:
    connection = op.get_bind()
    _capture_reupgrade_observations()
    connection.execute(
        sa.text(
            f"UPDATE ai_providers AS target SET "
            "pricing_configured = backup.pricing_configured, "
            "pricing_confirmed_input_microunits_per_million = "
            "backup.pricing_confirmed_input_microunits_per_million, "
            "pricing_confirmed_output_microunits_per_million = "
            "backup.pricing_confirmed_output_microunits_per_million, "
            "pricing_confirmed_currency = backup.pricing_confirmed_currency "
            f"FROM {_PROVIDER_SHADOW} AS backup "
            "WHERE target.id = backup.id"
        )
    )
    for table_name, shadow_name in (
        ("document_analysis", _DOCUMENT_COST_SHADOW),
        ("ai_usage_logs", _USAGE_COST_SHADOW),
    ):
        connection.execute(
            sa.text(
                f"UPDATE {table_name} AS target SET "
                "cost_status = backup.cost_status, "
                "estimated_cost_microunits = backup.estimated_cost_microunits "
                f"FROM {shadow_name} AS backup "
                "WHERE target.id = backup.id "
                "AND target.cost_status = backup.expected_reupgrade_status "
                "AND target.estimated_cost_microunits IS NOT DISTINCT FROM "
                "backup.expected_reupgrade_cost_microunits "
                "AND target.cost_status = backup.observed_reupgrade_status "
                "AND target.estimated_cost_microunits IS NOT DISTINCT FROM "
                "backup.observed_reupgrade_cost_microunits"
            )
        )
    connection.execute(
        sa.text(
            f"INSERT INTO ragflow_api_calls "
            "(id, department_id, operation, result, failure_category, started_at, "
            "finished_at, latency_ms) "
            "SELECT backup.id, CASE WHEN backup.department_id IS NULL OR EXISTS "
            "(SELECT 1 FROM departments WHERE departments.id = backup.department_id) "
            "THEN backup.department_id ELSE NULL END, backup.operation, backup.result, "
            "backup.failure_category, backup.started_at, backup.finished_at, backup.latency_ms "
            f"FROM {_RAGFLOW_SHADOW} AS backup"
        )
    )
    connection.execute(
        sa.text(
            "INSERT INTO storage_capacity_snapshots "
            "(id, backend, scope, source_kind, total_bytes, used_bytes, free_bytes, "
            "evidence_sha256, captured_at, collected_at) "
            "SELECT id, backend, scope, source_kind, total_bytes, used_bytes, free_bytes, "
            f"evidence_sha256, captured_at, collected_at FROM {_CAPACITY_SHADOW}"
        )
    )
    _assert_shadow_rows_restored("ragflow_api_calls", _RAGFLOW_SHADOW)
    _assert_semantic_shadow_restored()
    _assert_shadow_rows_restored("storage_capacity_snapshots", _CAPACITY_SHADOW)
    for table_name in reversed(_SHADOW_TABLES):
        op.drop_table(table_name)


def _capture_reupgrade_observations() -> None:
    """Capture every still-live shadow target before restoring the richer s002 semantics.

    Provider rows recover their original declaration and confirmation basis while retaining the
    current downgrade-window price triple, so any old-writer drift becomes ineffective. Cost rows
    unchanged during the downgrade window recover their richer status; drifted cost rows keep the
    deterministic semantics inferred from their current values. Persisting observations lets the
    post-restore audit prove both branches instead of silently ignoring rows. A nullable observation
    left empty marks a target deleted during the downgrade window and lets the audit prove that the
    rollback evidence did not resurrect it.
    """
    connection = op.get_bind()
    connection.execute(
        sa.text(
            f"UPDATE {_PROVIDER_SHADOW} AS backup SET "
            "observed_reupgrade_pricing_configured = target.pricing_configured, "
            "observed_reupgrade_confirmed_input_microunits = "
            "target.pricing_confirmed_input_microunits_per_million, "
            "observed_reupgrade_confirmed_output_microunits = "
            "target.pricing_confirmed_output_microunits_per_million, "
            "observed_reupgrade_confirmed_currency = target.pricing_confirmed_currency, "
            "observed_reupgrade_input_price_microunits = "
            "target.input_price_microunits_per_million_tokens, "
            "observed_reupgrade_output_price_microunits = "
            "target.output_price_microunits_per_million_tokens, "
            "observed_reupgrade_pricing_currency = target.pricing_currency "
            "FROM ai_providers AS target WHERE target.id = backup.id"
        )
    )
    for table_name, shadow_name in (
        ("document_analysis", _DOCUMENT_COST_SHADOW),
        ("ai_usage_logs", _USAGE_COST_SHADOW),
    ):
        connection.execute(
            sa.text(
                f"UPDATE {shadow_name} AS backup SET "
                "observed_reupgrade_status = target.cost_status, "
                "observed_reupgrade_cost_microunits = target.estimated_cost_microunits "
                f"FROM {table_name} AS target WHERE target.id = backup.id"
            )
        )


def _assert_shadow_rows_restored(table_name: str, shadow_name: str) -> None:
    connection = op.get_bind()
    live_count = int(connection.execute(sa.text(f"SELECT count(*) FROM {table_name}")).scalar_one())
    shadow_count = int(
        connection.execute(sa.text(f"SELECT count(*) FROM {shadow_name}")).scalar_one()
    )
    if live_count != shadow_count:
        raise RuntimeError("capacity governance shadow evidence restoration was incomplete")


def _assert_semantic_shadow_restored() -> None:
    connection = op.get_bind()
    _assert_missing_shadow_targets_remain_deleted(
        "ai_providers",
        _PROVIDER_SHADOW,
        observation_column="observed_reupgrade_pricing_configured",
    )
    provider_mismatch = int(
        connection.execute(
            sa.text(
                f"SELECT count(*) FROM ai_providers AS target "
                f"JOIN {_PROVIDER_SHADOW} AS backup ON backup.id = target.id WHERE "
                "backup.observed_reupgrade_pricing_configured IS NULL OR "
                "backup.observed_reupgrade_input_price_microunits IS NULL OR "
                "backup.observed_reupgrade_output_price_microunits IS NULL OR "
                "backup.observed_reupgrade_pricing_currency IS NULL OR "
                "backup.observed_reupgrade_pricing_configured IS DISTINCT FROM "
                "(backup.observed_reupgrade_input_price_microunits <> 0 OR "
                "backup.observed_reupgrade_output_price_microunits <> 0) OR "
                "backup.observed_reupgrade_confirmed_input_microunits IS DISTINCT FROM "
                "CASE WHEN backup.observed_reupgrade_input_price_microunits <> 0 OR "
                "backup.observed_reupgrade_output_price_microunits <> 0 THEN "
                "backup.observed_reupgrade_input_price_microunits ELSE NULL END OR "
                "backup.observed_reupgrade_confirmed_output_microunits IS DISTINCT FROM "
                "CASE WHEN backup.observed_reupgrade_input_price_microunits <> 0 OR "
                "backup.observed_reupgrade_output_price_microunits <> 0 THEN "
                "backup.observed_reupgrade_output_price_microunits ELSE NULL END OR "
                "backup.observed_reupgrade_confirmed_currency IS DISTINCT FROM "
                "CASE WHEN backup.observed_reupgrade_input_price_microunits <> 0 OR "
                "backup.observed_reupgrade_output_price_microunits <> 0 THEN "
                "backup.observed_reupgrade_pricing_currency ELSE NULL END OR "
                "target.input_price_microunits_per_million_tokens IS DISTINCT FROM "
                "backup.observed_reupgrade_input_price_microunits OR "
                "target.output_price_microunits_per_million_tokens IS DISTINCT FROM "
                "backup.observed_reupgrade_output_price_microunits OR "
                "target.pricing_currency IS DISTINCT FROM "
                "backup.observed_reupgrade_pricing_currency OR "
                "target.pricing_configured IS DISTINCT FROM backup.pricing_configured OR "
                "target.pricing_confirmed_input_microunits_per_million IS DISTINCT FROM "
                "backup.pricing_confirmed_input_microunits_per_million OR "
                "target.pricing_confirmed_output_microunits_per_million IS DISTINCT FROM "
                "backup.pricing_confirmed_output_microunits_per_million OR "
                "target.pricing_confirmed_currency IS DISTINCT FROM "
                "backup.pricing_confirmed_currency"
            )
        ).scalar_one()
    )
    if provider_mismatch:
        raise RuntimeError("capacity governance provider pricing restoration was incomplete")
    for table_name, shadow_name in (
        ("document_analysis", _DOCUMENT_COST_SHADOW),
        ("ai_usage_logs", _USAGE_COST_SHADOW),
    ):
        _assert_missing_shadow_targets_remain_deleted(
            table_name,
            shadow_name,
            observation_column="observed_reupgrade_status",
        )
        cost_mismatch = int(
            connection.execute(
                sa.text(
                    f"SELECT count(*) FROM {table_name} AS target "
                    f"JOIN {shadow_name} AS backup ON backup.id = target.id "
                    "WHERE target.cost_status IS DISTINCT FROM CASE WHEN "
                    "backup.observed_reupgrade_status = backup.expected_reupgrade_status "
                    "AND backup.observed_reupgrade_cost_microunits IS NOT DISTINCT FROM "
                    "backup.expected_reupgrade_cost_microunits THEN backup.cost_status "
                    "ELSE backup.observed_reupgrade_status END OR "
                    "target.estimated_cost_microunits IS DISTINCT FROM CASE WHEN "
                    "backup.observed_reupgrade_status = backup.expected_reupgrade_status "
                    "AND backup.observed_reupgrade_cost_microunits IS NOT DISTINCT FROM "
                    "backup.expected_reupgrade_cost_microunits "
                    "THEN backup.estimated_cost_microunits "
                    "ELSE backup.observed_reupgrade_cost_microunits END"
                )
            ).scalar_one()
        )
        if cost_mismatch:
            raise RuntimeError("capacity governance cost restoration was incomplete")


def _assert_missing_shadow_targets_remain_deleted(
    table_name: str,
    shadow_name: str,
    *,
    observation_column: str,
) -> None:
    """Ensure rows absent at re-upgrade were not resurrected from rollback evidence."""
    connection = op.get_bind()
    resurrected_count = int(
        connection.execute(
            sa.text(
                f"SELECT count(*) FROM {shadow_name} AS backup "
                f"JOIN {table_name} AS target ON target.id = backup.id "
                f"WHERE backup.{observation_column} IS NULL"
            )
        ).scalar_one()
    )
    if resurrected_count:
        raise RuntimeError("capacity governance deleted shadow targets were resurrected")


def _add_pricing_configuration_semantics() -> None:
    op.add_column(
        "ai_providers",
        sa.Column(
            "pricing_configured",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "ai_providers",
        sa.Column("pricing_confirmed_input_microunits_per_million", sa.BigInteger()),
    )
    op.add_column(
        "ai_providers",
        sa.Column("pricing_confirmed_output_microunits_per_million", sa.BigInteger()),
    )
    op.add_column(
        "ai_providers",
        sa.Column("pricing_confirmed_currency", sa.String(length=3)),
    )
    op.execute(
        sa.text(
            "UPDATE ai_providers SET pricing_configured = true, "
            "pricing_confirmed_input_microunits_per_million = "
            "input_price_microunits_per_million_tokens, "
            "pricing_confirmed_output_microunits_per_million = "
            "output_price_microunits_per_million_tokens, "
            "pricing_confirmed_currency = pricing_currency "
            "WHERE input_price_microunits_per_million_tokens <> 0 "
            "OR output_price_microunits_per_million_tokens <> 0"
        )
    )
    op.create_check_constraint(
        "ck_ai_providers_pricing_confirmation_basis",
        "ai_providers",
        "(pricing_configured AND "
        "pricing_confirmed_input_microunits_per_million IS NOT NULL AND "
        "pricing_confirmed_output_microunits_per_million IS NOT NULL AND "
        "pricing_confirmed_currency IS NOT NULL) OR "
        "(NOT pricing_configured AND "
        "pricing_confirmed_input_microunits_per_million IS NULL AND "
        "pricing_confirmed_output_microunits_per_million IS NULL AND "
        "pricing_confirmed_currency IS NULL)",
    )


def _add_cost_status(table_name: str, *, has_nullable_usage: bool) -> None:
    # Expand-only compatibility window: the previous application version still relies on the
    # non-null amount column and its server default. Keep both until a later contract migration.
    # Do not add a status-to-amount constraint here: an old writer can legitimately produce a
    # positive physical amount with the legacy status default. A separate contract revision may
    # tighten this only after every old writer is retired and the observation window is complete.
    op.add_column(
        table_name,
        sa.Column(
            "cost_status",
            sa.String(length=40),
            server_default=sa.text("'legacy_unverifiable'"),
            nullable=False,
        ),
    )
    if has_nullable_usage:
        op.execute(
            sa.text(
                f"UPDATE {table_name} SET cost_status = CASE "
                "WHEN prompt_tokens IS NULL OR completion_tokens IS NULL "
                "THEN 'unknown_usage' "
                "WHEN estimated_cost_microunits > 0 THEN 'known' "
                "ELSE 'legacy_unverifiable' END"
            )
        )
    else:
        op.execute(
            sa.text(
                f"UPDATE {table_name} SET cost_status = CASE "
                "WHEN estimated_cost_microunits > 0 THEN 'known' "
                "ELSE 'legacy_unverifiable' END"
            )
        )
    op.create_check_constraint(
        f"ck_{table_name}_cost_status",
        table_name,
        f"cost_status IN ({_COST_STATUSES})",
    )
    if has_nullable_usage:
        op.create_check_constraint(
            f"ck_{table_name}_known_cost_usage",
            table_name,
            "cost_status <> 'known' OR "
            "(prompt_tokens IS NOT NULL AND completion_tokens IS NOT NULL)",
        )


def _create_ragflow_api_calls() -> None:
    op.create_table(
        "ragflow_api_calls",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "department_id",
            sa.Uuid(),
            sa.ForeignKey("departments.id", ondelete="SET NULL"),
        ),
        sa.Column("operation", sa.String(length=40), nullable=False),
        sa.Column(
            "result",
            sa.String(length=20),
            server_default=sa.text("'started'"),
            nullable=False,
        ),
        sa.Column("failure_category", sa.String(length=40)),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("latency_ms", sa.BigInteger()),
        sa.CheckConstraint(
            f"operation IN ({_RAGFLOW_OPERATIONS})",
            name="ck_ragflow_api_calls_operation",
        ),
        sa.CheckConstraint(
            "result IN ('started', 'success', 'failure')",
            name="ck_ragflow_api_calls_result",
        ),
        sa.CheckConstraint(
            f"failure_category IS NULL OR failure_category IN ({_RAGFLOW_FAILURE_CATEGORIES})",
            name="ck_ragflow_api_calls_failure_category",
        ),
        sa.CheckConstraint(
            "(result = 'failure' AND failure_category IS NOT NULL) OR "
            "(result <> 'failure' AND failure_category IS NULL)",
            name="ck_ragflow_api_calls_failure_result",
        ),
        sa.CheckConstraint(
            "(result = 'started' AND finished_at IS NULL AND latency_ms IS NULL) OR "
            "(result IN ('success', 'failure') AND finished_at IS NOT NULL "
            "AND latency_ms IS NOT NULL AND latency_ms >= 0)",
            name="ck_ragflow_api_calls_lifecycle",
        ),
    )
    op.create_index("idx_ragflow_api_calls_started_at", "ragflow_api_calls", ["started_at"])
    op.create_index("idx_ragflow_api_calls_finished_at", "ragflow_api_calls", ["finished_at"])
    op.create_index(
        "idx_ragflow_api_calls_operation_result",
        "ragflow_api_calls",
        ["operation", "result"],
    )
    op.create_index(
        "idx_ragflow_api_calls_department_started",
        "ragflow_api_calls",
        ["department_id", "started_at"],
    )
    op.create_index(
        "idx_ragflow_api_calls_started_pending",
        "ragflow_api_calls",
        ["started_at", "id"],
        postgresql_where=sa.text("result = 'started'"),
    )


def _create_storage_capacity_snapshots() -> None:
    op.create_table(
        "storage_capacity_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "backend",
            sa.String(length=20),
            server_default=sa.text("'minio'"),
            nullable=False,
        ),
        sa.Column(
            "scope",
            sa.String(length=20),
            server_default=sa.text("'cluster'"),
            nullable=False,
        ),
        sa.Column(
            "source_kind",
            sa.String(length=40),
            server_default=sa.text("'minio_cluster_metrics'"),
            nullable=False,
        ),
        sa.Column("total_bytes", sa.BigInteger(), nullable=False),
        sa.Column("used_bytes", sa.BigInteger(), nullable=False),
        sa.Column("free_bytes", sa.BigInteger(), nullable=False),
        sa.Column("evidence_sha256", sa.String(length=64), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "collected_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("backend = 'minio'", name="ck_storage_capacity_snapshots_backend"),
        sa.CheckConstraint("scope = 'cluster'", name="ck_storage_capacity_snapshots_scope"),
        sa.CheckConstraint(
            "source_kind = 'minio_cluster_metrics'",
            name="ck_storage_capacity_snapshots_source_kind",
        ),
        sa.CheckConstraint(
            "total_bytes > 0",
            name="ck_storage_capacity_snapshots_total_positive",
        ),
        sa.CheckConstraint(
            "used_bytes >= 0",
            name="ck_storage_capacity_snapshots_used_non_negative",
        ),
        sa.CheckConstraint(
            "free_bytes >= 0",
            name="ck_storage_capacity_snapshots_free_non_negative",
        ),
        sa.CheckConstraint(
            "used_bytes <= total_bytes AND free_bytes <= total_bytes "
            "AND used_bytes + free_bytes <= total_bytes",
            name="ck_storage_capacity_snapshots_bytes_consistent",
        ),
        sa.CheckConstraint(
            "evidence_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_storage_capacity_snapshots_evidence_sha256",
        ),
        sa.CheckConstraint(
            "collected_at >= captured_at",
            name="ck_storage_capacity_snapshots_collection_order",
        ),
    )
    op.create_index(
        "uq_storage_capacity_snapshots_source_capture",
        "storage_capacity_snapshots",
        ["source_kind", "captured_at"],
        unique=True,
    )
    op.create_index(
        "idx_storage_capacity_snapshots_captured_at",
        "storage_capacity_snapshots",
        ["captured_at"],
    )


def _create_shadow_backup() -> None:
    if _has_complete_shadow_backup():
        raise RuntimeError("capacity governance shadow backup already exists")
    op.create_table(
        _PROVIDER_SHADOW,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pricing_configured", sa.Boolean(), nullable=False),
        sa.Column("pricing_confirmed_input_microunits_per_million", sa.BigInteger()),
        sa.Column("pricing_confirmed_output_microunits_per_million", sa.BigInteger()),
        sa.Column("pricing_confirmed_currency", sa.String(length=3)),
        sa.Column("input_price_microunits_per_million_tokens", sa.BigInteger(), nullable=False),
        sa.Column("output_price_microunits_per_million_tokens", sa.BigInteger(), nullable=False),
        sa.Column("pricing_currency", sa.String(length=3), nullable=False),
        sa.Column("observed_reupgrade_pricing_configured", sa.Boolean()),
        sa.Column("observed_reupgrade_confirmed_input_microunits", sa.BigInteger()),
        sa.Column("observed_reupgrade_confirmed_output_microunits", sa.BigInteger()),
        sa.Column("observed_reupgrade_confirmed_currency", sa.String(length=3)),
        sa.Column("observed_reupgrade_input_price_microunits", sa.BigInteger()),
        sa.Column("observed_reupgrade_output_price_microunits", sa.BigInteger()),
        sa.Column("observed_reupgrade_pricing_currency", sa.String(length=3)),
        sa.PrimaryKeyConstraint("id"),
    )
    _create_cost_shadow_table(_DOCUMENT_COST_SHADOW, sa.Uuid())
    _create_cost_shadow_table(_USAGE_COST_SHADOW, sa.BigInteger())
    op.create_table(
        _RAGFLOW_SHADOW,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("department_id", sa.Uuid()),
        sa.Column("operation", sa.String(length=40), nullable=False),
        sa.Column("result", sa.String(length=20), nullable=False),
        sa.Column("failure_category", sa.String(length=40)),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("latency_ms", sa.BigInteger()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        _CAPACITY_SHADOW,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("backend", sa.String(length=20), nullable=False),
        sa.Column("scope", sa.String(length=20), nullable=False),
        sa.Column("source_kind", sa.String(length=40), nullable=False),
        sa.Column("total_bytes", sa.BigInteger(), nullable=False),
        sa.Column("used_bytes", sa.BigInteger(), nullable=False),
        sa.Column("free_bytes", sa.BigInteger(), nullable=False),
        sa.Column("evidence_sha256", sa.String(length=64), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    connection = op.get_bind()
    connection.execute(
        sa.text(
            f"INSERT INTO {_PROVIDER_SHADOW} "
            "(id, pricing_configured, pricing_confirmed_input_microunits_per_million, "
            "pricing_confirmed_output_microunits_per_million, pricing_confirmed_currency, "
            "input_price_microunits_per_million_tokens, "
            "output_price_microunits_per_million_tokens, pricing_currency) "
            "SELECT id, pricing_configured, "
            "pricing_confirmed_input_microunits_per_million, "
            "pricing_confirmed_output_microunits_per_million, pricing_confirmed_currency, "
            "input_price_microunits_per_million_tokens, "
            "output_price_microunits_per_million_tokens, pricing_currency FROM ai_providers"
        )
    )
    connection.execute(
        sa.text(
            f"INSERT INTO {_DOCUMENT_COST_SHADOW} "
            "(id, cost_status, estimated_cost_microunits, expected_reupgrade_status, "
            "expected_reupgrade_cost_microunits) "
            "SELECT id, cost_status, estimated_cost_microunits, "
            "CASE WHEN estimated_cost_microunits > 0 THEN 'known' "
            "ELSE 'legacy_unverifiable' END, "
            "estimated_cost_microunits FROM document_analysis"
        )
    )
    connection.execute(
        sa.text(
            f"INSERT INTO {_USAGE_COST_SHADOW} "
            "(id, cost_status, estimated_cost_microunits, expected_reupgrade_status, "
            "expected_reupgrade_cost_microunits) "
            "SELECT id, cost_status, estimated_cost_microunits, "
            "CASE WHEN prompt_tokens IS NULL OR completion_tokens IS NULL "
            "THEN 'unknown_usage' "
            "WHEN estimated_cost_microunits > 0 THEN 'known' "
            "ELSE 'legacy_unverifiable' END, "
            "estimated_cost_microunits FROM ai_usage_logs"
        )
    )
    connection.execute(
        sa.text(
            f"INSERT INTO {_RAGFLOW_SHADOW} "
            "(id, department_id, operation, result, failure_category, started_at, "
            "finished_at, latency_ms) SELECT id, department_id, operation, result, "
            "failure_category, started_at, finished_at, latency_ms FROM ragflow_api_calls"
        )
    )
    connection.execute(
        sa.text(
            f"INSERT INTO {_CAPACITY_SHADOW} "
            "(id, backend, scope, source_kind, total_bytes, used_bytes, free_bytes, "
            "evidence_sha256, captured_at, collected_at) "
            "SELECT id, backend, scope, source_kind, total_bytes, used_bytes, free_bytes, "
            "evidence_sha256, captured_at, collected_at FROM storage_capacity_snapshots"
        )
    )


def _create_cost_shadow_table(table_name: str, id_type: sa.types.TypeEngine[Any]) -> None:
    op.create_table(
        table_name,
        sa.Column("id", id_type, nullable=False),
        sa.Column("cost_status", sa.String(length=40), nullable=False),
        sa.Column("estimated_cost_microunits", sa.BigInteger(), nullable=False),
        sa.Column("expected_reupgrade_status", sa.String(length=40), nullable=False),
        sa.Column("expected_reupgrade_cost_microunits", sa.BigInteger(), nullable=False),
        sa.Column("observed_reupgrade_status", sa.String(length=40)),
        sa.Column("observed_reupgrade_cost_microunits", sa.BigInteger()),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    _create_shadow_backup()
    op.drop_index(
        "idx_storage_capacity_snapshots_captured_at",
        table_name="storage_capacity_snapshots",
    )
    op.drop_index(
        "uq_storage_capacity_snapshots_source_capture",
        table_name="storage_capacity_snapshots",
    )
    op.drop_table("storage_capacity_snapshots")

    op.drop_index("idx_ragflow_api_calls_started_pending", table_name="ragflow_api_calls")
    op.drop_index("idx_ragflow_api_calls_department_started", table_name="ragflow_api_calls")
    op.drop_index("idx_ragflow_api_calls_operation_result", table_name="ragflow_api_calls")
    op.drop_index("idx_ragflow_api_calls_finished_at", table_name="ragflow_api_calls")
    op.drop_index("idx_ragflow_api_calls_started_at", table_name="ragflow_api_calls")
    op.drop_table("ragflow_api_calls")

    _drop_cost_status("ai_usage_logs")
    _drop_cost_status("document_analysis")
    op.drop_constraint(
        "ck_ai_providers_pricing_confirmation_basis",
        "ai_providers",
        type_="check",
    )
    op.drop_column("ai_providers", "pricing_confirmed_currency")
    op.drop_column("ai_providers", "pricing_confirmed_output_microunits_per_million")
    op.drop_column("ai_providers", "pricing_confirmed_input_microunits_per_million")
    op.drop_column("ai_providers", "pricing_configured")
    op.drop_index("idx_files_uploaded_at", table_name="files")


def _drop_cost_status(table_name: str) -> None:
    if table_name == "ai_usage_logs":
        op.drop_constraint(f"ck_{table_name}_known_cost_usage", table_name, type_="check")
    op.drop_constraint(f"ck_{table_name}_cost_status", table_name, type_="check")
    op.drop_column(table_name, "cost_status")
