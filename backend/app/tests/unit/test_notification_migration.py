from __future__ import annotations

import json
import uuid
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


def test_n001_normalizes_legacy_metadata_and_enforces_delivery_idempotency() -> None:
    config = _alembic_config()
    _reset_schema()
    try:
        command.upgrade(config, "20260716o001")
        department_id = uuid.uuid4()
        user_id = uuid.uuid4()
        legacy_file_id = uuid.uuid4()
        structured_task_id = uuid.uuid4()
        legacy_notification_id = uuid.uuid4()
        structured_notification_id = uuid.uuid4()
        malformed_notification_id = uuid.uuid4()
        non_object_notification_id = uuid.uuid4()

        engine = create_engine(TEST_ALEMBIC_DATABASE_URL)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO departments (id, name, code, status) "
                        "VALUES (:id, 'Notification migration', 'notification-migration', 'active')"
                    ),
                    {"id": department_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO users "
                        "(id, name, email, email_domain, password_hash, department_id, "
                        "role, status, email_verified) "
                        "VALUES (:id, 'Migration user', 'migration@company.com', "
                        "'company.com', 'hash', :department_id, 'employee', 'active', true)"
                    ),
                    {"id": user_id, "department_id": department_id},
                )
                rows = [
                    (
                        legacy_notification_id,
                        "in_app",
                        {
                            "file_id": str(legacy_file_id),
                            "review_status": "pending",
                            "url": "https://attacker.invalid/redirect",
                            "arbitrary": {"secret": "must-not-survive"},
                        },
                    ),
                    (
                        structured_notification_id,
                        "email",
                        {
                            "resource_type": "sync_task",
                            "resource_id": str(structured_task_id),
                            "status": "x" * 100,
                            "expiry_status": "expired",
                            "expires_at": "2026-07-16T12:00:00+00:00",
                            "path": "/admin/task-logs?token=secret",
                        },
                    ),
                    (
                        malformed_notification_id,
                        "in_app",
                        {
                            "resource_type": "file",
                            "resource_id": "not-a-uuid",
                            "file_id": str(legacy_file_id),
                            "status": "rejected",
                        },
                    ),
                    (non_object_notification_id, "in_app", []),
                ]
                for notification_id, channel, metadata in rows:
                    connection.execute(
                        text(
                            "INSERT INTO notifications "
                            "(id, user_id, type, channel, title, body, metadata_json) "
                            "VALUES (:id, :user_id, 'legacy', :channel, 'Legacy', "
                            "'Legacy body', CAST(:metadata AS jsonb))"
                        ),
                        {
                            "id": notification_id,
                            "user_id": user_id,
                            "channel": channel,
                            "metadata": json.dumps(metadata),
                        },
                    )
        finally:
            engine.dispose()

        command.upgrade(config, "20260716n001")
        engine = create_engine(TEST_ALEMBIC_DATABASE_URL)
        try:
            with engine.connect() as connection:
                revision = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                migrated_rows = {
                    row.id: row
                    for row in connection.execute(
                        text(
                            "SELECT id, channel, metadata_json, delivery_status, "
                            "delivery_attempts FROM notifications"
                        )
                    ).mappings()
                }
                columns = set(
                    connection.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_name = 'notifications'"
                        )
                    ).scalars()
                )
                constraints = set(
                    connection.execute(
                        text(
                            "SELECT conname FROM pg_constraint "
                            "WHERE conrelid = 'notifications'::regclass"
                        )
                    ).scalars()
                )
                indexes = set(
                    connection.execute(
                        text(
                            "SELECT indexname FROM pg_indexes "
                            "WHERE schemaname = 'public' AND tablename = 'notifications'"
                        )
                    ).scalars()
                )

            assert revision == "20260716n001"
            assert migrated_rows[legacy_notification_id].metadata_json == {
                "resource_type": "file",
                "resource_id": str(legacy_file_id),
                "status": "pending",
            }
            assert migrated_rows[structured_notification_id].metadata_json == {
                "resource_type": "sync_task",
                "resource_id": str(structured_task_id),
                "status": "x" * 80,
                "expiry_status": "expired",
                "expires_at": "2026-07-16T12:00:00+00:00",
            }
            assert migrated_rows[malformed_notification_id].metadata_json == {"status": "rejected"}
            assert migrated_rows[non_object_notification_id].metadata_json == {}
            assert migrated_rows[legacy_notification_id].delivery_status == "not_applicable"
            assert migrated_rows[structured_notification_id].delivery_status == "pending"
            assert all(row.delivery_attempts == 0 for row in migrated_rows.values())
            assert {
                "source_event_id",
                "delivery_status",
                "delivery_attempts",
                "last_delivery_error",
                "delivered_at",
            } <= columns
            assert {
                "ck_notifications_delivery_status",
                "ck_notifications_delivery_attempts_nonnegative",
                "ck_notifications_channel_delivery_status",
                "fk_notifications_source_event_id_event_outbox",
                "uq_notifications_source_recipient_channel",
            } <= constraints
            assert {
                "idx_notifications_source_event_id",
                "idx_notifications_email_pending",
            } <= indexes

            with engine.begin() as connection:
                source_event_id = int(
                    connection.execute(
                        text(
                            "INSERT INTO event_outbox "
                            "(event_type, aggregate_type, aggregate_id, payload) "
                            "VALUES ('review.file.approved', 'file', :aggregate_id, "
                            "'{}'::jsonb) RETURNING id"
                        ),
                        {"aggregate_id": str(legacy_file_id)},
                    ).scalar_one()
                )
                connection.execute(
                    text(
                        "INSERT INTO notifications "
                        "(id, user_id, source_event_id, type, channel, title, body, "
                        "metadata_json, delivery_status) "
                        "VALUES (:id, :user_id, :source_event_id, 'approved', 'in_app', "
                        "'Approved', 'Approved', '{}'::jsonb, 'not_applicable')"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "user_id": user_id,
                        "source_event_id": source_event_id,
                    },
                )

            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO notifications "
                            "(id, user_id, source_event_id, type, channel, title, body, "
                            "metadata_json, delivery_status) "
                            "VALUES (:id, :user_id, :source_event_id, 'duplicate', "
                            "'in_app', 'Duplicate', 'Duplicate', '{}'::jsonb, "
                            "'not_applicable')"
                        ),
                        {
                            "id": uuid.uuid4(),
                            "user_id": user_id,
                            "source_event_id": source_event_id,
                        },
                    )

            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO notifications "
                            "(id, user_id, type, channel, title, body, metadata_json, "
                            "delivery_status) VALUES (:id, :user_id, 'invalid', 'email', "
                            "'Invalid', 'Invalid', '{}'::jsonb, 'not_applicable')"
                        ),
                        {"id": uuid.uuid4(), "user_id": user_id},
                    )
        finally:
            engine.dispose()

        command.downgrade(config, "20260716o001")
        engine = create_engine(TEST_ALEMBIC_DATABASE_URL)
        try:
            with engine.connect() as connection:
                downgraded_columns = set(
                    connection.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_name = 'notifications'"
                        )
                    ).scalars()
                )
                normalized_metadata = connection.execute(
                    text("SELECT metadata_json FROM notifications WHERE id = :id"),
                    {"id": legacy_notification_id},
                ).scalar_one()
            assert "source_event_id" not in downgraded_columns
            assert "delivery_status" not in downgraded_columns
            assert normalized_metadata == {
                "resource_type": "file",
                "resource_id": str(legacy_file_id),
                "status": "pending",
            }
        finally:
            engine.dispose()

        command.upgrade(config, "20260716n001")
    finally:
        _reset_schema()
