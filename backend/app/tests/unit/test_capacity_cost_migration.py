from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.tests.conftest import TEST_ALEMBIC_DATABASE_URL


def _alembic_config() -> Config:
    backend_root = Path(__file__).resolve().parents[3]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "app/db/migrations"))
    config.set_main_option("sqlalchemy.url", TEST_ALEMBIC_DATABASE_URL)
    return config


def _postgres_default_literal(expression: str | None) -> str | None:
    if expression is None:
        return None
    literal = expression.split("::", maxsplit=1)[0].strip().strip("()")
    if len(literal) >= 2 and literal[0] == literal[-1] == "'":
        return literal[1:-1].replace("''", "'")
    return literal


def _reset_schema() -> None:
    engine = create_engine(TEST_ALEMBIC_DATABASE_URL)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
    finally:
        engine.dispose()


def test_s002_round_trip_restores_unchanged_semantics_and_preserves_live_drift() -> None:
    config = _alembic_config()
    _reset_schema()
    try:
        command.upgrade(config, "20260716s001")
        engine = create_engine(TEST_ALEMBIC_DATABASE_URL)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO ai_providers "
                        "(id, name, provider_type, enabled, chat_model, "
                        "input_price_microunits_per_million_tokens, "
                        "output_price_microunits_per_million_tokens) VALUES "
                        "('00000000-0000-0000-0000-00000000a001', 'legacy-zero', "
                        "'mock', false, 'mock', 0, 0), "
                        "('00000000-0000-0000-0000-00000000a002', 'legacy-priced', "
                        "'mock', false, 'mock', 10, 20), "
                        "('00000000-0000-0000-0000-00000000a005', "
                        "'legacy-priced-to-zero', "
                        "'mock', false, 'mock', 11, 21), "
                        "('00000000-0000-0000-0000-00000000a006', "
                        "'legacy-currency-drift', "
                        "'mock', false, 'mock', 12, 22)"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO ai_usage_logs "
                        "(feature_name, status, prompt_tokens, completion_tokens, "
                        "estimated_cost_microunits, cost_currency) VALUES "
                        "('analysis', 'success', 10, 20, 0, 'USD'), "
                        "('analysis', 'failed', NULL, NULL, 0, 'USD'), "
                        "('analysis', 'success', 10, 20, 25, 'CNY')"
                    )
                )
        finally:
            engine.dispose()

        command.upgrade(config, "20260716s002")
        engine = create_engine(TEST_ALEMBIC_DATABASE_URL)
        try:
            inspector = inspect(engine)
            assert {"ragflow_api_calls", "storage_capacity_snapshots"} <= set(
                inspector.get_table_names()
            )
            ragflow_columns = {
                column["name"] for column in inspector.get_columns("ragflow_api_calls")
            }
            assert ragflow_columns == {
                "id",
                "department_id",
                "operation",
                "result",
                "failure_category",
                "started_at",
                "finished_at",
                "latency_ms",
            }
            ragflow_indexes = {item["name"] for item in inspector.get_indexes("ragflow_api_calls")}
            assert {
                "idx_ragflow_api_calls_finished_at",
                "idx_ragflow_api_calls_started_pending",
            } <= ragflow_indexes
            file_indexes = {item["name"] for item in inspector.get_indexes("files")}
            assert "idx_files_uploaded_at" in file_indexes
            assert {
                "url",
                "body",
                "file_id",
                "object_key",
                "response",
                "exception",
            }.isdisjoint(ragflow_columns)
            with engine.connect() as connection:
                provider_rows = connection.execute(
                    text(
                        "SELECT name, pricing_configured, "
                        "pricing_confirmed_input_microunits_per_million, "
                        "pricing_confirmed_output_microunits_per_million, "
                        "pricing_confirmed_currency FROM ai_providers "
                        "WHERE name LIKE 'legacy-%' ORDER BY name"
                    )
                ).all()
                usage_rows = connection.execute(
                    text(
                        "SELECT cost_status, estimated_cost_microunits "
                        "FROM ai_usage_logs ORDER BY id"
                    )
                ).all()
                defaults: dict[str, str | None] = {
                    str(row["column_name"]): row["column_default"]
                    for row in connection.execute(
                        text(
                            "SELECT column_name, column_default FROM information_schema.columns "
                            "WHERE table_schema = 'public' AND table_name = 'ai_usage_logs' "
                            "AND column_name IN ('cost_status', 'estimated_cost_microunits')"
                        )
                    ).mappings()
                }
                revision = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
            assert [tuple(row) for row in provider_rows] == [
                ("legacy-currency-drift", True, 12, 22, "USD"),
                ("legacy-priced", True, 10, 20, "USD"),
                ("legacy-priced-to-zero", True, 11, 21, "USD"),
                ("legacy-zero", False, None, None, None),
            ]
            assert [tuple(row) for row in usage_rows] == [
                ("legacy_unverifiable", 0),
                ("unknown_usage", 0),
                ("known", 25),
            ]
            assert _postgres_default_literal(defaults["estimated_cost_microunits"]) == "0"
            assert _postgres_default_literal(defaults["cost_status"]) == ("legacy_unverifiable")
            assert revision == "20260716s002"

            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE ai_providers SET pricing_configured = true, "
                        "pricing_confirmed_input_microunits_per_million = "
                        "input_price_microunits_per_million_tokens, "
                        "pricing_confirmed_output_microunits_per_million = "
                        "output_price_microunits_per_million_tokens, "
                        "pricing_confirmed_currency = pricing_currency "
                        "WHERE id = '00000000-0000-0000-0000-00000000a001'"
                    )
                )
                # Simulate rolling old writers: they can mutate/insert physical pricing fields but
                # cannot update the new declaration basis, so every such value must fail closed.
                connection.execute(
                    text(
                        "UPDATE ai_providers SET "
                        "input_price_microunits_per_million_tokens = 0, "
                        "output_price_microunits_per_million_tokens = 0 "
                        "WHERE id = '00000000-0000-0000-0000-00000000a005'"
                    )
                )
                connection.execute(
                    text(
                        "UPDATE ai_providers SET pricing_currency = 'CNY' "
                        "WHERE id = '00000000-0000-0000-0000-00000000a006'"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO ai_providers "
                        "(id, name, provider_type, enabled, chat_model, "
                        "input_price_microunits_per_million_tokens, "
                        "output_price_microunits_per_million_tokens) VALUES "
                        "('00000000-0000-0000-0000-00000000a007', 'rolling-old-insert', "
                        "'mock', false, 'mock', 13, 23)"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO ai_providers "
                        "(id, name, provider_type, enabled, chat_model, "
                        "input_price_microunits_per_million_tokens, "
                        "output_price_microunits_per_million_tokens, pricing_configured) VALUES "
                        "('00000000-0000-0000-0000-00000000a003', 'window-change', "
                        "'mock', false, 'mock', 0, 0, false)"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO departments (id, name, code, status) VALUES "
                        "('00000000-0000-0000-0000-00000000d001', "
                        "'治理测试部', 'governance-test', 'active')"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO users "
                        "(id, name, email, email_domain, password_hash, department_id, "
                        "department, role, status, email_verified) VALUES "
                        "('00000000-0000-0000-0000-00000000e001', 'owner', "
                        "'owner@company.com', 'company.com', 'x', "
                        "'00000000-0000-0000-0000-00000000d001', '治理测试部', "
                        "'employee', 'active', true)"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO files "
                        "(id, original_name, title, stored_name, extension, mime_type, size, "
                        "hash, storage_type, bucket, object_key, uploader_id, department_id, "
                        "department, visibility, status, review_status, "
                        "ai_analysis_enabled_at_upload, owner_id, series_id, version_number) "
                        "VALUES ('00000000-0000-0000-0000-00000000f001', 'governance.pdf', "
                        "'governance', 'governance.pdf', 'pdf', 'application/pdf', 128, "
                        "repeat('f', 64), 'minio', 'knowledge-files', "
                        "'uploads/governance.pdf', "
                        "'00000000-0000-0000-0000-00000000e001', "
                        "'00000000-0000-0000-0000-00000000d001', '治理测试部', "
                        "'department', 'uploaded', 'pending', false, "
                        "'00000000-0000-0000-0000-00000000e001', "
                        "'00000000-0000-0000-0000-00000000f001', 1)"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO files "
                        "(id, original_name, title, stored_name, extension, mime_type, size, "
                        "hash, storage_type, bucket, object_key, uploader_id, department_id, "
                        "department, visibility, status, review_status, "
                        "ai_analysis_enabled_at_upload, owner_id, series_id, version_number) "
                        "VALUES ('00000000-0000-0000-0000-00000000f002', "
                        "'legacy-writer.pdf', 'legacy-writer', 'legacy-writer.pdf', 'pdf', "
                        "'application/pdf', 128, repeat('e', 64), 'minio', 'knowledge-files', "
                        "'uploads/legacy-writer.pdf', "
                        "'00000000-0000-0000-0000-00000000e001', "
                        "'00000000-0000-0000-0000-00000000d001', '治理测试部', "
                        "'department', 'uploaded', 'pending', false, "
                        "'00000000-0000-0000-0000-00000000e001', "
                        "'00000000-0000-0000-0000-00000000f002', 1)"
                    )
                )
                # Simulate the previous application binary after the schema expand. It knows the
                # physical amount column but not cost_status; both inserts must remain valid.
                connection.execute(
                    text(
                        "INSERT INTO files "
                        "(id, original_name, title, stored_name, extension, mime_type, size, "
                        "hash, storage_type, bucket, object_key, uploader_id, department_id, "
                        "department, visibility, status, review_status, "
                        "ai_analysis_enabled_at_upload, owner_id, series_id, version_number) "
                        "VALUES ('00000000-0000-0000-0000-00000000f003', "
                        "'legacy-default.pdf', 'legacy-default', 'legacy-default.pdf', 'pdf', "
                        "'application/pdf', 128, repeat('d', 64), 'minio', 'knowledge-files', "
                        "'uploads/legacy-default.pdf', "
                        "'00000000-0000-0000-0000-00000000e001', "
                        "'00000000-0000-0000-0000-00000000d001', '治理测试部', "
                        "'department', 'uploaded', 'pending', false, "
                        "'00000000-0000-0000-0000-00000000e001', "
                        "'00000000-0000-0000-0000-00000000f003', 1)"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO document_analysis "
                        "(id, file_id, status, engine_type, prompt_tokens, completion_tokens, "
                        "latency_ms, cost_currency) VALUES "
                        "('10000000-0000-0000-0000-00000000f003', "
                        "'00000000-0000-0000-0000-00000000f003', 'succeeded', 'llm', "
                        "0, 0, 0, 'USD')"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO files "
                        "(id, original_name, title, stored_name, extension, mime_type, size, "
                        "hash, storage_type, bucket, object_key, uploader_id, department_id, "
                        "department, visibility, status, review_status, "
                        "ai_analysis_enabled_at_upload, owner_id, series_id, version_number) "
                        "SELECT '00000000-0000-0000-0000-00000000f004', "
                        "'downgrade-delete.pdf', 'downgrade-delete', "
                        "'downgrade-delete.pdf', extension, mime_type, size, repeat('c', 64), "
                        "storage_type, bucket, 'uploads/downgrade-delete.pdf', uploader_id, "
                        "department_id, department, visibility, status, review_status, "
                        "ai_analysis_enabled_at_upload, owner_id, "
                        "'00000000-0000-0000-0000-00000000f004', 1 "
                        "FROM files WHERE id = "
                        "'00000000-0000-0000-0000-00000000f003'"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO document_analysis "
                        "(id, file_id, status, engine_type, prompt_tokens, completion_tokens, "
                        "latency_ms, cost_status, estimated_cost_microunits, cost_currency) "
                        "VALUES ('10000000-0000-0000-0000-00000000f004', "
                        "'00000000-0000-0000-0000-00000000f004', 'succeeded', 'llm', "
                        "2, 2, 8, 'known', 31, 'USD')"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO document_analysis "
                        "(id, file_id, status, engine_type, prompt_tokens, completion_tokens, "
                        "latency_ms, estimated_cost_microunits, cost_currency) VALUES "
                        "('10000000-0000-0000-0000-00000000f002', "
                        "'00000000-0000-0000-0000-00000000f002', 'succeeded', 'llm', "
                        "3, 4, 12, 73, 'USD')"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO ai_usage_logs "
                        "(feature_name, status, prompt_tokens, completion_tokens, "
                        "estimated_cost_microunits, cost_currency) VALUES "
                        "('legacy-writer', 'success', 3, 4, 73, 'USD')"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO ai_usage_logs "
                        "(feature_name, status, prompt_tokens, completion_tokens, cost_currency) "
                        "VALUES ('legacy-writer-default', 'success', 0, 0, 'USD')"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO ai_usage_logs "
                        "(feature_name, status, prompt_tokens, completion_tokens, cost_status, "
                        "estimated_cost_microunits, cost_currency) VALUES "
                        "('new-writer-unknown', 'success', 5, 6, 'unknown_pricing', 0, 'USD')"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO document_analysis "
                        "(id, file_id, status, engine_type, prompt_tokens, completion_tokens, "
                        "latency_ms, cost_status, estimated_cost_microunits, cost_currency) "
                        "VALUES ('10000000-0000-0000-0000-00000000f001', "
                        "'00000000-0000-0000-0000-00000000f001', 'succeeded', 'llm', "
                        "1, 1, 10, 'known', 0, 'USD')"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO ai_usage_logs "
                        "(feature_name, status, prompt_tokens, completion_tokens, "
                        "cost_status, estimated_cost_microunits, cost_currency) VALUES "
                        "('roundtrip-free', 'success', 1, 1, 'known', 0, 'USD'), "
                        "('roundtrip-unknown', 'success', 7, 8, 'unknown_pricing', 0, 'USD'), "
                        "('downgrade-delete', 'success', 2, 2, 'known', 31, 'USD')"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO ragflow_api_calls "
                        "(id, department_id, operation, result, failure_category, started_at, "
                        "finished_at, latency_ms) VALUES "
                        "('00000000-0000-0000-0000-00000000b002', "
                        "'00000000-0000-0000-0000-00000000d001', 'upload_document', "
                        "'failure', 'timeout', '2026-07-17 00:00:00+00', "
                        "'2026-07-17 00:00:02+00', 2000)"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO storage_capacity_snapshots "
                        "(id, total_bytes, used_bytes, free_bytes, evidence_sha256, "
                        "captured_at, collected_at) VALUES "
                        "('00000000-0000-0000-0000-00000000c001', 100, 60, 40, "
                        "repeat('b', 64), '2026-07-17 00:00:00+00', "
                        "'2026-07-17 00:00:01+00')"
                    )
                )
            with engine.connect() as connection:
                rolling_providers = connection.execute(
                    text(
                        "SELECT name, pricing_configured, "
                        "input_price_microunits_per_million_tokens, "
                        "output_price_microunits_per_million_tokens, pricing_currency, "
                        "pricing_confirmed_input_microunits_per_million, "
                        "pricing_confirmed_output_microunits_per_million, "
                        "pricing_confirmed_currency FROM ai_providers WHERE name IN "
                        "('legacy-zero', 'legacy-priced-to-zero', "
                        "'legacy-currency-drift', 'rolling-old-insert') ORDER BY name"
                    )
                ).all()
                rolling_usage = connection.execute(
                    text(
                        "SELECT feature_name, cost_status, estimated_cost_microunits "
                        "FROM ai_usage_logs WHERE feature_name IN "
                        "('legacy-writer', 'legacy-writer-default', "
                        "'new-writer-unknown') ORDER BY feature_name"
                    )
                ).all()
                rolling_analysis = connection.execute(
                    text(
                        "SELECT id, cost_status, estimated_cost_microunits "
                        "FROM document_analysis WHERE id IN "
                        "('10000000-0000-0000-0000-00000000f002', "
                        "'10000000-0000-0000-0000-00000000f003') ORDER BY id"
                    )
                ).all()
            assert [tuple(row) for row in rolling_providers] == [
                ("legacy-currency-drift", True, 12, 22, "CNY", 12, 22, "USD"),
                ("legacy-priced-to-zero", True, 0, 0, "USD", 11, 21, "USD"),
                ("legacy-zero", True, 0, 0, "USD", 0, 0, "USD"),
                ("rolling-old-insert", False, 13, 23, "USD", None, None, None),
            ]
            assert [tuple(row) for row in rolling_usage] == [
                ("legacy-writer", "legacy_unverifiable", 73),
                ("legacy-writer-default", "legacy_unverifiable", 0),
                ("new-writer-unknown", "unknown_pricing", 0),
            ]
            assert [
                (str(row.id), row.cost_status, row.estimated_cost_microunits)
                for row in rolling_analysis
            ] == [
                ("10000000-0000-0000-0000-00000000f002", "legacy_unverifiable", 73),
                ("10000000-0000-0000-0000-00000000f003", "legacy_unverifiable", 0),
            ]
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO ai_usage_logs "
                            "(feature_name, status, prompt_tokens, completion_tokens, "
                            "cost_status, estimated_cost_microunits, cost_currency) VALUES "
                            "('known-without-usage', 'success', NULL, 1, "
                            "'known', 0, 'USD')"
                        )
                    )

            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "UPDATE ai_providers SET pricing_configured = true "
                            "WHERE id = '00000000-0000-0000-0000-00000000a003'"
                        )
                    )

            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "UPDATE ai_providers SET pricing_configured = false "
                            "WHERE id = '00000000-0000-0000-0000-00000000a002'"
                        )
                    )

            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO storage_capacity_snapshots "
                            "(id, total_bytes, used_bytes, free_bytes, evidence_sha256, "
                            "captured_at) VALUES "
                            "('00000000-0000-0000-0000-00000000b001', 100, 70, 40, "
                            "repeat('a', 64), now())"
                        )
                    )
        finally:
            engine.dispose()

        command.downgrade(config, "20260716s001")
        engine = create_engine(TEST_ALEMBIC_DATABASE_URL)
        try:
            inspector = inspect(engine)
            tables = set(inspector.get_table_names())
            shadow_tables = {
                "s002_ai_provider_pricing_backup",
                "s002_document_analysis_cost_backup",
                "s002_ai_usage_cost_backup",
                "s002_ragflow_api_calls_backup",
                "s002_storage_capacity_snapshots_backup",
            }
            assert shadow_tables <= tables
            assert "saved_views_rollback_backup" not in tables
            assert {"ragflow_api_calls", "storage_capacity_snapshots"}.isdisjoint(tables)
            assert "idx_files_uploaded_at" not in {
                item["name"] for item in inspector.get_indexes("files")
            }
            for table_name in shadow_tables:
                assert inspector.get_foreign_keys(table_name) == []
            provider_columns = {column["name"] for column in inspector.get_columns("ai_providers")}
            assert {
                "pricing_configured",
                "pricing_confirmed_input_microunits_per_million",
                "pricing_confirmed_output_microunits_per_million",
                "pricing_confirmed_currency",
            }.isdisjoint(provider_columns)
            usage_columns = {
                column["name"]: column for column in inspector.get_columns("ai_usage_logs")
            }
            assert "cost_status" not in usage_columns
            assert usage_columns["estimated_cost_microunits"]["nullable"] is False

            with engine.begin() as connection:
                provider_backup = connection.execute(
                    text(
                        "SELECT pricing_configured, "
                        "pricing_confirmed_input_microunits_per_million, "
                        "pricing_confirmed_output_microunits_per_million, "
                        "pricing_confirmed_currency, "
                        "input_price_microunits_per_million_tokens, "
                        "output_price_microunits_per_million_tokens, pricing_currency, "
                        "observed_reupgrade_pricing_configured, "
                        "observed_reupgrade_confirmed_input_microunits, "
                        "observed_reupgrade_confirmed_currency, "
                        "observed_reupgrade_pricing_currency "
                        "FROM s002_ai_provider_pricing_backup "
                        "WHERE id = '00000000-0000-0000-0000-00000000a001'"
                    )
                ).one()
                analysis_backup = connection.execute(
                    text(
                        "SELECT cost_status, estimated_cost_microunits, "
                        "expected_reupgrade_status, expected_reupgrade_cost_microunits "
                        ", observed_reupgrade_status "
                        "FROM s002_document_analysis_cost_backup "
                        "WHERE id = '10000000-0000-0000-0000-00000000f001'"
                    )
                ).one()
                ragflow_backup = connection.execute(
                    text(
                        "SELECT operation, result, failure_category, latency_ms "
                        "FROM s002_ragflow_api_calls_backup "
                        "WHERE id = '00000000-0000-0000-0000-00000000b002'"
                    )
                ).one()
                capacity_backup = connection.execute(
                    text(
                        "SELECT total_bytes, used_bytes, free_bytes, evidence_sha256 "
                        "FROM s002_storage_capacity_snapshots_backup "
                        "WHERE id = '00000000-0000-0000-0000-00000000c001'"
                    )
                ).one()
                assert provider_backup == (True, 0, 0, "USD", 0, 0, "USD", None, None, None, None)
                assert analysis_backup == ("known", 0, "legacy_unverifiable", 0, None)
                assert ragflow_backup == ("upload_document", "failure", "timeout", 2000)
                assert capacity_backup == (100, 60, 40, "b" * 64)

                # A legitimate delete in the downgrade window must remain deleted. New rows written
                # afterwards still receive the deterministic s002 inference on re-upgrade.
                connection.execute(
                    text(
                        "DELETE FROM ai_providers WHERE id = "
                        "'00000000-0000-0000-0000-00000000a002'"
                    )
                )
                connection.execute(
                    text(
                        "DELETE FROM document_analysis WHERE id = "
                        "'10000000-0000-0000-0000-00000000f004'"
                    )
                )
                connection.execute(
                    text("DELETE FROM ai_usage_logs WHERE feature_name = 'downgrade-delete'")
                )
                connection.execute(
                    text(
                        "INSERT INTO document_analysis "
                        "(id, file_id, status, engine_type, prompt_tokens, completion_tokens, "
                        "latency_ms, estimated_cost_microunits, cost_currency) VALUES "
                        "('10000000-0000-0000-0000-00000000f005', "
                        "'00000000-0000-0000-0000-00000000f004', 'succeeded', 'llm', "
                        "3, 4, 12, 19, 'USD')"
                    )
                )

                # Provider and cost rows can also drift while s002 is downgraded. Re-upgrade must
                # preserve these live values, while unchanged zero-cost semantics are restored.
                connection.execute(
                    text(
                        "UPDATE ai_providers SET "
                        "input_price_microunits_per_million_tokens = 50 "
                        "WHERE id = '00000000-0000-0000-0000-00000000a003'"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO ai_providers "
                        "(id, name, provider_type, enabled, chat_model, "
                        "input_price_microunits_per_million_tokens, "
                        "output_price_microunits_per_million_tokens) VALUES "
                        "('00000000-0000-0000-0000-00000000a004', 'window-new', "
                        "'mock', false, 'mock', 0, 0), "
                        "('00000000-0000-0000-0000-00000000a008', 'window-new-priced', "
                        "'mock', false, 'mock', 14, 24)"
                    )
                )
                connection.execute(
                    text(
                        "UPDATE ai_usage_logs SET prompt_tokens = 9, completion_tokens = 10, "
                        "estimated_cost_microunits = 40 "
                        "WHERE feature_name = 'roundtrip-unknown'"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO ai_usage_logs "
                        "(feature_name, status, prompt_tokens, completion_tokens, "
                        "estimated_cost_microunits, cost_currency) VALUES "
                        "('downgrade-reinsert', 'success', 3, 4, 19, 'USD'), "
                        "('window-new', 'success', 2, 3, 17, 'USD')"
                    )
                )
        finally:
            engine.dispose()

        command.upgrade(config, "20260716s002")
        engine = create_engine(TEST_ALEMBIC_DATABASE_URL)
        try:
            inspector = inspect(engine)
            restored_tables = set(inspector.get_table_names())
            assert {
                "s002_ai_provider_pricing_backup",
                "s002_document_analysis_cost_backup",
                "s002_ai_usage_cost_backup",
                "s002_ragflow_api_calls_backup",
                "s002_storage_capacity_snapshots_backup",
            }.isdisjoint(restored_tables)
            assert "saved_views_rollback_backup" not in restored_tables
            assert "idx_files_uploaded_at" in {
                item["name"] for item in inspector.get_indexes("files")
            }
            assert "idx_ragflow_api_calls_started_pending" in {
                item["name"] for item in inspector.get_indexes("ragflow_api_calls")
            }
            with engine.connect() as connection:
                revision = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                provider_rows = connection.execute(
                    text(
                        "SELECT name, pricing_configured, "
                        "input_price_microunits_per_million_tokens, "
                        "output_price_microunits_per_million_tokens, pricing_currency, "
                        "pricing_confirmed_input_microunits_per_million, "
                        "pricing_confirmed_output_microunits_per_million, "
                        "pricing_confirmed_currency "
                        "FROM ai_providers WHERE name IN "
                        "('legacy-currency-drift', 'legacy-priced-to-zero', 'legacy-zero', "
                        "'rolling-old-insert', 'window-change', 'window-new', "
                        "'window-new-priced') ORDER BY name"
                    )
                ).all()
                usage_rows = connection.execute(
                    text(
                        "SELECT feature_name, cost_status, estimated_cost_microunits "
                        "FROM ai_usage_logs WHERE feature_name IN "
                        "('legacy-writer', 'legacy-writer-default', "
                        "'downgrade-reinsert', 'new-writer-unknown', 'roundtrip-free', "
                        "'roundtrip-unknown', 'window-new') ORDER BY feature_name"
                    )
                ).all()
                analysis_rows = connection.execute(
                    text(
                        "SELECT id, cost_status, estimated_cost_microunits "
                        "FROM document_analysis WHERE id IN "
                        "('10000000-0000-0000-0000-00000000f001', "
                        "'10000000-0000-0000-0000-00000000f002', "
                        "'10000000-0000-0000-0000-00000000f003', "
                        "'10000000-0000-0000-0000-00000000f005') ORDER BY id"
                    )
                ).all()
                deleted_target_counts = connection.execute(
                    text(
                        "SELECT "
                        "(SELECT count(*) FROM ai_providers WHERE id = "
                        "'00000000-0000-0000-0000-00000000a002'), "
                        "(SELECT count(*) FROM document_analysis WHERE id = "
                        "'10000000-0000-0000-0000-00000000f004'), "
                        "(SELECT count(*) FROM ai_usage_logs "
                        "WHERE feature_name = 'downgrade-delete')"
                    )
                ).one()
                ragflow_row = connection.execute(
                    text(
                        "SELECT department_id, operation, result, failure_category, "
                        "started_at, finished_at, latency_ms FROM ragflow_api_calls "
                        "WHERE id = '00000000-0000-0000-0000-00000000b002'"
                    )
                ).one()
                capacity_row = connection.execute(
                    text(
                        "SELECT backend, scope, source_kind, total_bytes, used_bytes, "
                        "free_bytes, evidence_sha256, captured_at, collected_at "
                        "FROM storage_capacity_snapshots "
                        "WHERE id = '00000000-0000-0000-0000-00000000c001'"
                    )
                ).one()
                defaults = {
                    str(row["column_name"]): row["column_default"]
                    for row in connection.execute(
                        text(
                            "SELECT column_name, column_default FROM information_schema.columns "
                            "WHERE table_schema = 'public' AND table_name = 'ai_usage_logs' "
                            "AND column_name IN ('cost_status', 'estimated_cost_microunits')"
                        )
                    ).mappings()
                }

            assert revision == "20260716s002"
            assert [tuple(row) for row in provider_rows] == [
                ("legacy-currency-drift", True, 12, 22, "CNY", 12, 22, "USD"),
                ("legacy-priced-to-zero", True, 0, 0, "USD", 11, 21, "USD"),
                ("legacy-zero", True, 0, 0, "USD", 0, 0, "USD"),
                ("rolling-old-insert", False, 13, 23, "USD", None, None, None),
                ("window-change", False, 50, 0, "USD", None, None, None),
                ("window-new", False, 0, 0, "USD", None, None, None),
                ("window-new-priced", True, 14, 24, "USD", 14, 24, "USD"),
            ]
            assert [tuple(row) for row in usage_rows] == [
                ("downgrade-reinsert", "known", 19),
                ("legacy-writer", "legacy_unverifiable", 73),
                ("legacy-writer-default", "legacy_unverifiable", 0),
                ("new-writer-unknown", "unknown_pricing", 0),
                ("roundtrip-free", "known", 0),
                ("roundtrip-unknown", "known", 40),
                ("window-new", "known", 17),
            ]
            assert [
                (str(row.id), row.cost_status, row.estimated_cost_microunits)
                for row in analysis_rows
            ] == [
                ("10000000-0000-0000-0000-00000000f001", "known", 0),
                (
                    "10000000-0000-0000-0000-00000000f002",
                    "legacy_unverifiable",
                    73,
                ),
                (
                    "10000000-0000-0000-0000-00000000f003",
                    "legacy_unverifiable",
                    0,
                ),
                ("10000000-0000-0000-0000-00000000f005", "known", 19),
            ]
            assert deleted_target_counts == (0, 0, 0)
            assert str(ragflow_row.department_id) == "00000000-0000-0000-0000-00000000d001"
            assert ragflow_row.operation == "upload_document"
            assert ragflow_row.result == "failure"
            assert ragflow_row.failure_category == "timeout"
            assert ragflow_row.started_at == datetime(2026, 7, 17, tzinfo=UTC)
            assert ragflow_row.finished_at == datetime(2026, 7, 17, 0, 0, 2, tzinfo=UTC)
            assert ragflow_row.latency_ms == 2000
            assert capacity_row[:7] == (
                "minio",
                "cluster",
                "minio_cluster_metrics",
                100,
                60,
                40,
                "b" * 64,
            )
            assert capacity_row.captured_at == datetime(2026, 7, 17, tzinfo=UTC)
            assert capacity_row.collected_at == datetime(2026, 7, 17, 0, 0, 1, tzinfo=UTC)
            assert _postgres_default_literal(defaults["estimated_cost_microunits"]) == "0"
            assert _postgres_default_literal(defaults["cost_status"]) == ("legacy_unverifiable")
        finally:
            engine.dispose()
    finally:
        _reset_schema()
