"""add ai analysis tables

Revision ID: c7f1a2b9d6e4
Revises: a91c4e5d7b20
Create Date: 2026-06-06 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c7f1a2b9d6e4"
down_revision: str | None = "a91c4e5d7b20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_providers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("provider_type", sa.String(length=40), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=True),
        sa.Column("api_key_encrypted", sa.Text(), nullable=True),
        sa.Column("chat_model", sa.String(length=120), nullable=True),
        sa.Column("embedding_model", sa.String(length=120), nullable=True),
        sa.Column("vision_model", sa.String(length=120), nullable=True),
        sa.Column("is_internal", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("priority", sa.Integer(), server_default="100", nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), server_default="60", nullable=False),
        sa.Column("max_retry_count", sa.Integer(), server_default="2", nullable=False),
        sa.Column("max_input_tokens", sa.Integer(), nullable=True),
        sa.Column("max_output_tokens", sa.Integer(), nullable=True),
        sa.Column("temperature", sa.Float(), server_default="0.2", nullable=False),
        sa.Column("top_p", sa.Float(), nullable=True),
        sa.Column("last_test_status", sa.String(length=20), nullable=True),
        sa.Column("last_test_latency_ms", sa.Integer(), nullable=True),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "provider_type IN ("
            "'openai_compatible', 'local_openai_compatible', 'ollama', "
            "'vllm', 'lmstudio', 'custom', 'mock', 'disabled'"
            ")",
            name="ck_ai_providers_provider_type",
        ),
        sa.CheckConstraint("priority >= 0", name="ck_ai_providers_priority_non_negative"),
        sa.CheckConstraint("timeout_seconds > 0", name="ck_ai_providers_timeout_positive"),
        sa.CheckConstraint("max_retry_count >= 0", name="ck_ai_providers_retry_non_negative"),
        sa.CheckConstraint(
            "last_test_status IS NULL OR last_test_status IN ('success', 'failed')",
            name="ck_ai_providers_last_test_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_ai_providers_enabled_priority", "ai_providers", ["enabled", "priority"])
    op.create_index("uq_ai_providers_name", "ai_providers", ["name"], unique=True)

    op.create_table(
        "ai_feature_configs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("feature_name", sa.String(length=80), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "config_json",
            postgresql.JSONB(astext_type=sa.Text()),  # type: ignore[no-untyped-call]
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_ai_feature_configs_feature_name",
        "ai_feature_configs",
        ["feature_name"],
        unique=True,
    )

    op.create_table(
        "prompt_templates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("template_key", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column(
            "variables",
            postgresql.JSONB(astext_type=sa.Text()),  # type: ignore[no-untyped-call]
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("version > 0", name="ck_prompt_templates_version_positive"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_prompt_templates_enabled", "prompt_templates", ["enabled"])
    op.create_index(
        "uq_prompt_templates_template_key", "prompt_templates", ["template_key"], unique=True
    )

    op.create_table(
        "sensitive_rules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("rule_type", sa.String(length=20), nullable=False),
        sa.Column("pattern", sa.Text(), nullable=True),
        sa.Column(
            "keywords",
            postgresql.JSONB(astext_type=sa.Text()),  # type: ignore[no-untyped-call]
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("risk_level", sa.String(length=20), nullable=False),
        sa.Column("action", sa.String(length=30), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("hit_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "rule_type IN ('keyword', 'regex')", name="ck_sensitive_rules_rule_type"
        ),
        sa.CheckConstraint(
            "risk_level IN ('low', 'medium', 'high', 'critical')",
            name="ck_sensitive_rules_risk_level",
        ),
        sa.CheckConstraint(
            "action IN ('flag', 'require_review', 'block_sync')",
            name="ck_sensitive_rules_action",
        ),
        sa.CheckConstraint("hit_count >= 0", name="ck_sensitive_rules_hit_count_non_negative"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_sensitive_rules_enabled", "sensitive_rules", ["enabled"])
    op.create_index("idx_sensitive_rules_risk_level", "sensitive_rules", ["risk_level"])

    op.create_table(
        "document_analysis",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("file_id", sa.Uuid(), nullable=False),
        sa.Column("provider_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="running", nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("suggested_category_id", sa.Uuid(), nullable=True),
        sa.Column("suggested_category_name", sa.String(length=120), nullable=True),
        sa.Column(
            "suggested_tags",
            postgresql.JSONB(astext_type=sa.Text()),  # type: ignore[no-untyped-call]
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "sensitive_risk_level", sa.String(length=20), server_default="none", nullable=False
        ),
        sa.Column(
            "sensitive_hits",
            postgresql.JSONB(astext_type=sa.Text()),  # type: ignore[no-untyped-call]
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')", name="ck_document_analysis_status"
        ),
        sa.CheckConstraint(
            "sensitive_risk_level IN ('none', 'low', 'medium', 'high', 'critical')",
            name="ck_document_analysis_sensitive_risk_level",
        ),
        sa.ForeignKeyConstraint(["file_id"], ["files.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["provider_id"], ["ai_providers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["suggested_category_id"], ["categories.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_document_analysis_sensitive_risk_level", "document_analysis", ["sensitive_risk_level"]
    )
    op.create_index("idx_document_analysis_status", "document_analysis", ["status"])
    op.create_index("uq_document_analysis_file_id", "document_analysis", ["file_id"], unique=True)

    op.create_table(
        "ai_usage_logs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("provider_id", sa.Uuid(), nullable=True),
        sa.Column("file_id", sa.Uuid(), nullable=True),
        sa.Column("feature_name", sa.String(length=80), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("status IN ('success', 'failed')", name="ck_ai_usage_logs_status"),
        sa.CheckConstraint(
            "prompt_tokens IS NULL OR prompt_tokens >= 0",
            name="ck_ai_usage_logs_prompt_tokens_non_negative",
        ),
        sa.CheckConstraint(
            "completion_tokens IS NULL OR completion_tokens >= 0",
            name="ck_ai_usage_logs_completion_tokens_non_negative",
        ),
        sa.ForeignKeyConstraint(["file_id"], ["files.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["provider_id"], ["ai_providers.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_ai_usage_logs_created_at", "ai_usage_logs", ["created_at"])
    op.create_index("idx_ai_usage_logs_file_id", "ai_usage_logs", ["file_id"])
    op.create_index("idx_ai_usage_logs_provider_id", "ai_usage_logs", ["provider_id"])


def downgrade() -> None:
    op.drop_index("idx_ai_usage_logs_provider_id", table_name="ai_usage_logs")
    op.drop_index("idx_ai_usage_logs_file_id", table_name="ai_usage_logs")
    op.drop_index("idx_ai_usage_logs_created_at", table_name="ai_usage_logs")
    op.drop_table("ai_usage_logs")

    op.drop_index("uq_document_analysis_file_id", table_name="document_analysis")
    op.drop_index("idx_document_analysis_status", table_name="document_analysis")
    op.drop_index("idx_document_analysis_sensitive_risk_level", table_name="document_analysis")
    op.drop_table("document_analysis")

    op.drop_index("idx_sensitive_rules_risk_level", table_name="sensitive_rules")
    op.drop_index("idx_sensitive_rules_enabled", table_name="sensitive_rules")
    op.drop_table("sensitive_rules")

    op.drop_index("uq_prompt_templates_template_key", table_name="prompt_templates")
    op.drop_index("idx_prompt_templates_enabled", table_name="prompt_templates")
    op.drop_table("prompt_templates")

    op.drop_index("uq_ai_feature_configs_feature_name", table_name="ai_feature_configs")
    op.drop_table("ai_feature_configs")

    op.drop_index("uq_ai_providers_name", table_name="ai_providers")
    op.drop_index("idx_ai_providers_enabled_priority", table_name="ai_providers")
    op.drop_table("ai_providers")
