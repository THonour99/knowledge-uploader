from __future__ import annotations

import uuid
from collections.abc import Generator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from app.tests.conftest import TEST_ALEMBIC_DATABASE_URL

_O001_CONFIG_KEYS = frozenset(
    {
        "upload.enabled",
        "upload.allowed_extensions",
        "upload.max_file_size_mb",
        "upload.user_quota_mb",
        "upload.allow_multi_file",
        "upload.allow_user_delete",
        "outbox.publish_max_retries",
        "processing.parse_max_pages",
        "processing.parse_max_chars",
        "security.allowed_email_domains",
        "security.password_min_length",
        "security.login_max_failed_attempts",
        "security.login_lock_minutes",
        "security.require_email_verification",
        "security.block_critical_sensitive_sync",
        "review.claim_timeout_minutes",
        "review.sla_hours",
        "ragflow.base_url",
        "ragflow.api_key",
        "ragflow.sync_max_retries",
        "ragflow.parse_poll_timeout_seconds",
        "ragflow.sync_timeout_seconds",
        "ragflow.allow_high_risk_sync",
        "ragflow.delete_remote_on_file_delete",
        "ragflow.keep_remote_on_archive",
    }
)


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


@pytest.fixture
def isolated_migration_schema() -> Generator[None, None, None]:
    _reset_schema()
    yield
    _reset_schema()


def test_o001_is_single_head_after_d002_and_reconciles_operational_schema(
    isolated_migration_schema: None,
) -> None:
    config = _alembic_config()
    command.upgrade(config, "20260716d002")
    engine = create_engine(TEST_ALEMBIC_DATABASE_URL)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE system_configs SET value = '73'::jsonb "
                    "WHERE key = 'upload.max_file_size_mb'"
                )
            )
            connection.execute(
                text(
                    "UPDATE system_configs SET value = 'false'::jsonb "
                    "WHERE key = 'security.block_critical_sensitive_sync'"
                )
            )
            connection.execute(
                text(
                    "UPDATE system_configs SET value = "
                    "'\"private-admin@example.invalid\"'::jsonb, "
                    "description = 'preserve exact legacy value' "
                    "WHERE key = 'basic.admin_contact_email'"
                )
            )
            original_deleted_rows = list(
                connection.execute(
                    text(
                        'SELECT id, key, "group", value::text, value_type, is_secret, '
                        "description, updated_by, created_at, updated_at "
                        "FROM system_configs WHERE key IN "
                        "('basic.admin_contact_email', 'ragflow.default_dataset_id') "
                        "ORDER BY key"
                    )
                ).tuples()
            )
    finally:
        engine.dispose()

    command.upgrade(config, "20260716o001")
    engine = create_engine(TEST_ALEMBIC_DATABASE_URL)
    try:
        with engine.connect() as connection:
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            config_keys = set(connection.execute(text("SELECT key FROM system_configs")).scalars())
            preserved_value = connection.execute(
                text("SELECT value::text FROM system_configs WHERE key = 'upload.max_file_size_mb'")
            ).scalar_one()
            critical_sync_block = connection.execute(
                text(
                    "SELECT value::text FROM system_configs "
                    "WHERE key = 'security.block_critical_sensitive_sync'"
                )
            ).scalar_one()
            backup_count = connection.execute(
                text("SELECT count(*) FROM o001_deleted_system_configs_backup")
            ).scalar_one()
            outbox_columns = set(
                connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'event_outbox' AND column_name IN "
                        "('first_publish_failed_at', 'last_publish_failed_at')"
                    )
                ).scalars()
            )
            dead_letter_columns = set(
                connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'outbox_dead_letters'"
                    )
                ).scalars()
            )
        assert revision == "20260716o001"
        assert config_keys == _O001_CONFIG_KEYS
        assert preserved_value == "73"
        assert critical_sync_block == "true"
        assert int(backup_count) == 15
        assert outbox_columns == {
            "first_publish_failed_at",
            "last_publish_failed_at",
        }
        assert {
            "event_id",
            "status",
            "payload_summary",
            "last_replayed_by",
            "resolved_at",
        } <= dead_letter_columns

        event_id = 0
        with engine.begin() as connection:
            event_id = int(
                connection.execute(
                    text(
                        "INSERT INTO event_outbox "
                        "(event_type, aggregate_type, aggregate_id, payload) "
                        "VALUES ('test.event', 'test', 'target', '{}'::jsonb) RETURNING id"
                    )
                ).scalar_one()
            )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO outbox_dead_letters "
                        "(id, event_id, status, attempts, error_type, correlation_id, "
                        "payload_summary) VALUES "
                        "(:id, :event_id, 'unsafe', 1, 'RuntimeError', 'test', '{}'::jsonb)"
                    ),
                    {"id": uuid.uuid4(), "event_id": event_id},
                )
    finally:
        engine.dispose()

    command.downgrade(config, "20260716d002")
    engine = create_engine(TEST_ALEMBIC_DATABASE_URL)
    try:
        with engine.connect() as connection:
            dead_letter_table = connection.execute(
                text("SELECT to_regclass('public.outbox_dead_letters')")
            ).scalar_one_or_none()
            new_keys = set(
                connection.execute(
                    text(
                        "SELECT key FROM system_configs WHERE key IN "
                        "('upload.enabled', 'outbox.publish_max_retries', "
                        "'review.claim_timeout_minutes', 'review.sla_hours', "
                        "'ragflow.parse_poll_timeout_seconds')"
                    )
                ).scalars()
            )
            restored_basic = connection.execute(
                text("SELECT count(*) FROM system_configs WHERE \"group\" = 'basic'")
            ).scalar_one()
            restored_deleted_rows = list(
                connection.execute(
                    text(
                        'SELECT id, key, "group", value::text, value_type, is_secret, '
                        "description, updated_by, created_at, updated_at "
                        "FROM system_configs WHERE key IN "
                        "('basic.admin_contact_email', 'ragflow.default_dataset_id') "
                        "ORDER BY key"
                    )
                ).tuples()
            )
            backup_table = connection.execute(
                text("SELECT to_regclass('public.o001_deleted_system_configs_backup')")
            ).scalar_one_or_none()
        assert dead_letter_table is None
        assert new_keys == set()
        assert int(restored_basic) == 6
        assert restored_deleted_rows == original_deleted_rows
        assert backup_table is None
    finally:
        engine.dispose()


def test_o001_fails_closed_without_deleting_unknown_or_sensitive_rows(
    isolated_migration_schema: None,
) -> None:
    config = _alembic_config()
    command.upgrade(config, "20260716d002")
    engine = create_engine(TEST_ALEMBIC_DATABASE_URL)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO system_configs "
                    '(id, key, "group", value, value_type, is_secret, description) VALUES '
                    "(:id, 'custom.secret_endpoint', 'basic', "
                    "'\"must-not-appear-in-errors\"'::jsonb, 'string', true, "
                    "'custom protected value')"
                ),
                {"id": uuid.uuid4()},
            )
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match=r"blocked by 1 unknown row") as captured:
        command.upgrade(config, "20260716o001")

    message = str(captured.value)
    assert "custom.secret_endpoint" not in message
    assert "must-not-appear-in-errors" not in message
    engine = create_engine(TEST_ALEMBIC_DATABASE_URL)
    try:
        with engine.connect() as connection:
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            preserved = connection.execute(
                text(
                    "SELECT value::text FROM system_configs " "WHERE key = 'custom.secret_endpoint'"
                )
            ).scalar_one()
            backup_table = connection.execute(
                text("SELECT to_regclass('public.o001_deleted_system_configs_backup')")
            ).scalar_one_or_none()
        assert revision == "20260716d002"
        assert preserved == '"must-not-appear-in-errors"'
        assert backup_table is None
    finally:
        engine.dispose()
