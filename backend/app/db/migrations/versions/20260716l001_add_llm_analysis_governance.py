"""add auditable llm analysis governance

Revision ID: 20260716l001
Revises: 20260716n001
Create Date: 2026-07-16 22:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "20260716l001"
down_revision: str | None = "20260716n001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ai_providers",
        sa.Column(
            "input_price_microunits_per_million_tokens",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "ai_providers",
        sa.Column(
            "output_price_microunits_per_million_tokens",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "ai_providers",
        sa.Column(
            "pricing_currency",
            sa.String(length=3),
            server_default=sa.text("'USD'"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_ai_providers_input_price_non_negative",
        "ai_providers",
        "input_price_microunits_per_million_tokens >= 0",
    )
    op.create_check_constraint(
        "ck_ai_providers_input_price_max",
        "ai_providers",
        "input_price_microunits_per_million_tokens <= 1000000000000",
    )
    op.create_check_constraint(
        "ck_ai_providers_output_price_non_negative",
        "ai_providers",
        "output_price_microunits_per_million_tokens >= 0",
    )
    op.create_check_constraint(
        "ck_ai_providers_output_price_max",
        "ai_providers",
        "output_price_microunits_per_million_tokens <= 1000000000000",
    )
    op.create_check_constraint(
        "ck_ai_providers_pricing_currency",
        "ai_providers",
        "pricing_currency ~ '^[A-Z]{3}$'",
    )

    op.execute(
        sa.text(
            "UPDATE ai_providers SET "
            "timeout_seconds = LEAST(timeout_seconds, 240), "
            "max_retry_count = LEAST(GREATEST(max_retry_count, 0), 10), "
            "max_input_tokens = CASE WHEN max_input_tokens IS NULL THEN NULL "
            "ELSE LEAST(GREATEST(max_input_tokens, 1), 1000000000) END, "
            "max_output_tokens = CASE WHEN max_output_tokens IS NULL THEN NULL "
            "ELSE LEAST(GREATEST(max_output_tokens, 1), 4096) END, "
            "temperature = LEAST(GREATEST(temperature, 0), 2), "
            "top_p = CASE WHEN top_p IS NULL THEN NULL ELSE LEAST(GREATEST(top_p, 0), 1) END"
        )
    )
    op.execute(
        sa.text(
            "UPDATE ai_providers SET enabled = false "
            "WHERE enabled AND provider_type <> 'disabled' "
            "AND (chat_model IS NULL OR length(btrim(chat_model)) = 0)"
        )
    )
    op.create_check_constraint(
        "ck_ai_providers_enabled_chat_model",
        "ai_providers",
        "provider_type = 'disabled' OR NOT enabled OR "
        "(chat_model IS NOT NULL AND length(btrim(chat_model)) > 0)",
    )
    provider_constraints = (
        ("ck_ai_providers_timeout_max", "timeout_seconds <= 240"),
        ("ck_ai_providers_retry_max", "max_retry_count <= 10"),
        (
            "ck_ai_providers_max_input_tokens_range",
            "max_input_tokens IS NULL OR (max_input_tokens > 0 AND max_input_tokens <= 1000000000)",
        ),
        (
            "ck_ai_providers_max_output_tokens_range",
            "max_output_tokens IS NULL OR (max_output_tokens > 0 AND max_output_tokens <= 4096)",
        ),
        (
            "ck_ai_providers_temperature_range",
            "temperature >= 0 AND temperature <= 2",
        ),
        (
            "ck_ai_providers_top_p_range",
            "top_p IS NULL OR (top_p >= 0 AND top_p <= 1)",
        ),
    )
    for name, condition in provider_constraints:
        op.create_check_constraint(name, "ai_providers", condition)

    op.drop_column("ai_providers", "vision_model")
    op.drop_column("ai_providers", "embedding_model")
    _add_document_analysis_columns()
    _add_usage_log_columns()
    op.execute(sa.text("DELETE FROM ai_feature_configs WHERE feature_name = 'ocr'"))


def _add_document_analysis_columns() -> None:
    columns: tuple[sa.Column[Any], ...] = (
        sa.Column("engine_type", sa.String(length=20), server_default="rule", nullable=False),
        sa.Column("provider_name", sa.String(length=120), nullable=True),
        sa.Column("model_name", sa.String(length=120), nullable=True),
        sa.Column("prompt_template_id", sa.Uuid(), nullable=True),
        sa.Column("prompt_template_key", sa.String(length=80), nullable=True),
        sa.Column("prompt_version", sa.Integer(), nullable=True),
        sa.Column("input_char_count", sa.Integer(), nullable=True),
        sa.Column("input_sha256", sa.String(length=64), nullable=True),
        sa.Column("category_count", sa.Integer(), nullable=True),
        sa.Column("input_truncated", sa.Boolean(), nullable=True),
        sa.Column("attempt_number", sa.Integer(), server_default="1", nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("completion_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("latency_ms", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failure_category", sa.String(length=40), nullable=True),
        sa.Column(
            "estimated_cost_microunits",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "cost_currency",
            sa.String(length=3),
            server_default=sa.text("'USD'"),
            nullable=False,
        ),
    )
    for column in columns:
        op.add_column("document_analysis", column)
    op.create_foreign_key(
        "fk_document_analysis_prompt_template_id",
        "document_analysis",
        "prompt_templates",
        ["prompt_template_id"],
        ["id"],
        ondelete="SET NULL",
    )
    constraints = (
        ("ck_document_analysis_engine_type", "engine_type IN ('rule', 'llm', 'hybrid')"),
        ("ck_document_analysis_attempt_number_positive", "attempt_number > 0"),
        (
            "ck_document_analysis_prompt_version_positive",
            "prompt_version IS NULL OR prompt_version > 0",
        ),
        (
            "ck_document_analysis_input_char_count_non_negative",
            "input_char_count IS NULL OR input_char_count >= 0",
        ),
        (
            "ck_document_analysis_input_sha256",
            "input_sha256 IS NULL OR input_sha256 ~ '^[0-9a-f]{64}$'",
        ),
        (
            "ck_document_analysis_category_count_non_negative",
            "category_count IS NULL OR category_count >= 0",
        ),
        ("ck_document_analysis_prompt_tokens_non_negative", "prompt_tokens >= 0"),
        ("ck_document_analysis_completion_tokens_non_negative", "completion_tokens >= 0"),
        ("ck_document_analysis_latency_non_negative", "latency_ms >= 0"),
        ("ck_document_analysis_cost_non_negative", "estimated_cost_microunits >= 0"),
        ("ck_document_analysis_cost_currency", "cost_currency ~ '^[A-Z]{3}$'"),
    )
    for name, condition in constraints:
        op.create_check_constraint(name, "document_analysis", condition)


def _add_usage_log_columns() -> None:
    columns: tuple[sa.Column[Any], ...] = (
        sa.Column("analysis_id", sa.Uuid(), nullable=True),
        sa.Column("provider_name", sa.String(length=120), nullable=True),
        sa.Column("model_name", sa.String(length=120), nullable=True),
        sa.Column("prompt_template_id", sa.Uuid(), nullable=True),
        sa.Column("prompt_template_key", sa.String(length=80), nullable=True),
        sa.Column("prompt_version", sa.Integer(), nullable=True),
        sa.Column("input_char_count", sa.Integer(), nullable=True),
        sa.Column("input_sha256", sa.String(length=64), nullable=True),
        sa.Column("category_count", sa.Integer(), nullable=True),
        sa.Column("input_truncated", sa.Boolean(), nullable=True),
        sa.Column("analysis_attempt", sa.Integer(), server_default="1", nullable=False),
        sa.Column("call_sequence", sa.Integer(), server_default="1", nullable=False),
        sa.Column("failure_category", sa.String(length=40), nullable=True),
        sa.Column(
            "estimated_cost_microunits",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "cost_currency",
            sa.String(length=3),
            server_default=sa.text("'USD'"),
            nullable=False,
        ),
    )
    for column in columns:
        op.add_column("ai_usage_logs", column)
    op.create_foreign_key(
        "fk_ai_usage_logs_analysis_id",
        "ai_usage_logs",
        "document_analysis",
        ["analysis_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_ai_usage_logs_prompt_template_id",
        "ai_usage_logs",
        "prompt_templates",
        ["prompt_template_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("idx_ai_usage_logs_analysis_id", "ai_usage_logs", ["analysis_id"])
    op.create_index(
        "uq_ai_usage_logs_analysis_attempt_call",
        "ai_usage_logs",
        ["analysis_id", "analysis_attempt", "call_sequence"],
        unique=True,
    )
    constraints = (
        ("ck_ai_usage_logs_analysis_attempt_positive", "analysis_attempt > 0"),
        ("ck_ai_usage_logs_call_sequence_positive", "call_sequence > 0"),
        (
            "ck_ai_usage_logs_prompt_version_positive",
            "prompt_version IS NULL OR prompt_version > 0",
        ),
        (
            "ck_ai_usage_logs_input_char_count_non_negative",
            "input_char_count IS NULL OR input_char_count >= 0",
        ),
        (
            "ck_ai_usage_logs_input_sha256",
            "input_sha256 IS NULL OR input_sha256 ~ '^[0-9a-f]{64}$'",
        ),
        (
            "ck_ai_usage_logs_category_count_non_negative",
            "category_count IS NULL OR category_count >= 0",
        ),
        ("ck_ai_usage_logs_cost_non_negative", "estimated_cost_microunits >= 0"),
        ("ck_ai_usage_logs_cost_currency", "cost_currency ~ '^[A-Z]{3}$'"),
    )
    for name, condition in constraints:
        op.create_check_constraint(name, "ai_usage_logs", condition)


def downgrade() -> None:
    op.execute(
        sa.text(
            "INSERT INTO ai_feature_configs "
            "(id, feature_name, enabled, config_json) VALUES "
            "('00000000-0000-0000-0000-0000000000c1', 'ocr', false, "
            '\'{"name":"OCR","description":"retired placeholder"}\'::jsonb) '
            "ON CONFLICT (feature_name) DO NOTHING"
        )
    )

    usage_constraints = (
        "ck_ai_usage_logs_cost_currency",
        "ck_ai_usage_logs_cost_non_negative",
        "ck_ai_usage_logs_category_count_non_negative",
        "ck_ai_usage_logs_input_sha256",
        "ck_ai_usage_logs_input_char_count_non_negative",
        "ck_ai_usage_logs_prompt_version_positive",
        "ck_ai_usage_logs_call_sequence_positive",
        "ck_ai_usage_logs_analysis_attempt_positive",
    )
    for name in usage_constraints:
        op.drop_constraint(name, "ai_usage_logs", type_="check")
    op.drop_index("uq_ai_usage_logs_analysis_attempt_call", table_name="ai_usage_logs")
    op.drop_index("idx_ai_usage_logs_analysis_id", table_name="ai_usage_logs")
    op.drop_constraint(
        "fk_ai_usage_logs_prompt_template_id",
        "ai_usage_logs",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_ai_usage_logs_analysis_id",
        "ai_usage_logs",
        type_="foreignkey",
    )
    usage_columns = (
        "cost_currency",
        "estimated_cost_microunits",
        "input_truncated",
        "category_count",
        "input_sha256",
        "input_char_count",
        "failure_category",
        "call_sequence",
        "analysis_attempt",
        "prompt_version",
        "prompt_template_key",
        "prompt_template_id",
        "model_name",
        "provider_name",
        "analysis_id",
    )
    for column in usage_columns:
        op.drop_column("ai_usage_logs", column)

    analysis_constraints = (
        "ck_document_analysis_cost_currency",
        "ck_document_analysis_cost_non_negative",
        "ck_document_analysis_category_count_non_negative",
        "ck_document_analysis_input_sha256",
        "ck_document_analysis_input_char_count_non_negative",
        "ck_document_analysis_latency_non_negative",
        "ck_document_analysis_completion_tokens_non_negative",
        "ck_document_analysis_prompt_tokens_non_negative",
        "ck_document_analysis_prompt_version_positive",
        "ck_document_analysis_attempt_number_positive",
        "ck_document_analysis_engine_type",
    )
    for name in analysis_constraints:
        op.drop_constraint(name, "document_analysis", type_="check")
    op.drop_constraint(
        "fk_document_analysis_prompt_template_id",
        "document_analysis",
        type_="foreignkey",
    )
    analysis_columns = (
        "cost_currency",
        "estimated_cost_microunits",
        "input_truncated",
        "category_count",
        "input_sha256",
        "input_char_count",
        "failure_category",
        "latency_ms",
        "completion_tokens",
        "prompt_tokens",
        "attempt_number",
        "prompt_version",
        "prompt_template_key",
        "prompt_template_id",
        "model_name",
        "provider_name",
        "engine_type",
    )
    for column in analysis_columns:
        op.drop_column("document_analysis", column)

    for constraint_name in (
        "ck_ai_providers_top_p_range",
        "ck_ai_providers_temperature_range",
        "ck_ai_providers_max_output_tokens_range",
        "ck_ai_providers_max_input_tokens_range",
        "ck_ai_providers_retry_max",
        "ck_ai_providers_timeout_max",
        "ck_ai_providers_enabled_chat_model",
    ):
        op.drop_constraint(constraint_name, "ai_providers", type_="check")

    op.drop_constraint("ck_ai_providers_pricing_currency", "ai_providers", type_="check")
    op.drop_constraint("ck_ai_providers_output_price_max", "ai_providers", type_="check")
    op.drop_constraint("ck_ai_providers_input_price_max", "ai_providers", type_="check")
    op.drop_constraint("ck_ai_providers_output_price_non_negative", "ai_providers", type_="check")
    op.drop_constraint("ck_ai_providers_input_price_non_negative", "ai_providers", type_="check")
    op.drop_column("ai_providers", "pricing_currency")
    op.drop_column("ai_providers", "output_price_microunits_per_million_tokens")
    op.drop_column("ai_providers", "input_price_microunits_per_million_tokens")
    op.add_column("ai_providers", sa.Column("embedding_model", sa.String(length=120)))
    op.add_column("ai_providers", sa.Column("vision_model", sa.String(length=120)))
