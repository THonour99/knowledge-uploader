from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from app.tests.conftest import TEST_ALEMBIC_DATABASE_URL


def _alembic_config() -> Config:
    backend_root = Path(__file__).resolve().parents[3]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "app/db/migrations"))
    config.set_main_option("sqlalchemy.url", TEST_ALEMBIC_DATABASE_URL)
    return config


def _reset_schema() -> None:
    engine = create_engine(TEST_ALEMBIC_DATABASE_URL)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
    finally:
        engine.dispose()


def _columns(table_name: str) -> set[str]:
    engine = create_engine(TEST_ALEMBIC_DATABASE_URL)
    try:
        with engine.connect() as connection:
            return set(
                connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND table_name = :table_name"
                    ),
                    {"table_name": table_name},
                ).scalars()
            )
    finally:
        engine.dispose()


def _feature_enabled(feature_name: str) -> bool | None:
    engine = create_engine(TEST_ALEMBIC_DATABASE_URL)
    try:
        with engine.connect() as connection:
            return connection.execute(
                text(
                    "SELECT enabled FROM ai_feature_configs " "WHERE feature_name = :feature_name"
                ),
                {"feature_name": feature_name},
            ).scalar_one_or_none()
    finally:
        engine.dispose()


def test_l001_llm_governance_upgrade_downgrade_upgrade_round_trip() -> None:
    config = _alembic_config()
    _reset_schema()
    try:
        command.upgrade(config, "20260716n001")
        legacy_engine = create_engine(TEST_ALEMBIC_DATABASE_URL)
        try:
            with legacy_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO ai_providers "
                        "(id, name, provider_type, timeout_seconds, max_retry_count, "
                        "max_input_tokens, max_output_tokens) "
                        "VALUES ('00000000-0000-0000-0000-0000000000a0', "
                        "'legacy-limits', 'mock', 999, 99, 2000000000, 99999)"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO ai_feature_configs "
                        "(id, feature_name, enabled) VALUES "
                        "('00000000-0000-0000-0000-0000000000c0', 'ocr', true)"
                    )
                )
        finally:
            legacy_engine.dispose()
        command.upgrade(config, "20260716l001")
        assert _feature_enabled("ocr") is None

        assert {
            "input_price_microunits_per_million_tokens",
            "output_price_microunits_per_million_tokens",
            "pricing_currency",
        } <= _columns("ai_providers")
        assert {"embedding_model", "vision_model"}.isdisjoint(_columns("ai_providers"))
        provenance_columns = {
            "engine_type",
            "provider_name",
            "model_name",
            "prompt_template_id",
            "prompt_template_key",
            "prompt_version",
            "input_char_count",
            "input_sha256",
            "category_count",
            "input_truncated",
            "attempt_number",
            "prompt_tokens",
            "completion_tokens",
            "latency_ms",
            "failure_category",
            "estimated_cost_microunits",
            "cost_currency",
        }
        assert provenance_columns <= _columns("document_analysis")
        assert provenance_columns - {"engine_type", "attempt_number"} <= _columns("ai_usage_logs")

        engine = create_engine(TEST_ALEMBIC_DATABASE_URL)
        try:
            with engine.connect() as connection:
                revision = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                constraints = set(
                    connection.execute(
                        text(
                            "SELECT conname FROM pg_constraint "
                            "WHERE connamespace = 'public'::regnamespace"
                        )
                    ).scalars()
                )
                indexes = set(
                    connection.execute(
                        text(
                            "SELECT indexname FROM pg_indexes "
                            "WHERE schemaname = 'public' AND tablename = 'ai_usage_logs'"
                        )
                    ).scalars()
                )
                normalized_limits = connection.execute(
                    text(
                        "SELECT timeout_seconds, max_retry_count, max_input_tokens, "
                        "max_output_tokens FROM ai_providers WHERE name = 'legacy-limits'"
                    )
                ).one()
            assert revision == "20260716l001"
            assert tuple(normalized_limits) == (240, 10, 1_000_000_000, 4_096)
            assert {
                "ck_ai_providers_input_price_max",
                "ck_ai_providers_output_price_max",
                "ck_ai_providers_retry_max",
                "ck_ai_providers_timeout_max",
                "ck_ai_providers_enabled_chat_model",
                "ck_ai_providers_max_input_tokens_range",
                "ck_ai_providers_max_output_tokens_range",
                "ck_document_analysis_input_sha256",
                "ck_ai_usage_logs_input_sha256",
            } <= constraints
            assert "uq_ai_usage_logs_analysis_attempt_call" in indexes

            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO ai_providers "
                            "(id, name, provider_type, input_price_microunits_per_million_tokens) "
                            "VALUES ('00000000-0000-0000-0000-0000000000a1', "
                            "'overflow', 'mock', 1000000000001)"
                        )
                    )
        finally:
            engine.dispose()

        command.downgrade(config, "20260716n001")
        assert "input_sha256" not in _columns("document_analysis")
        assert "analysis_id" not in _columns("ai_usage_logs")
        assert {"embedding_model", "vision_model"} <= _columns("ai_providers")
        assert _feature_enabled("ocr") is False

        command.upgrade(config, "20260716l001")
        assert "input_sha256" in _columns("document_analysis")
        assert "analysis_id" in _columns("ai_usage_logs")
        assert {"embedding_model", "vision_model"}.isdisjoint(_columns("ai_providers"))
        assert _feature_enabled("ocr") is None
    finally:
        _reset_schema()
