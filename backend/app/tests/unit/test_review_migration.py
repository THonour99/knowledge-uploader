from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

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


def test_r001_normalizes_legacy_review_state_and_adds_claim_consistency() -> None:
    config = _alembic_config()
    department_id = uuid.uuid4()
    uploader_id = uuid.uuid4()
    file_id = uuid.uuid4()
    non_pending_file_id = uuid.uuid4()
    _reset_schema()
    try:
        command.upgrade(config, "20260708d001")
        engine = create_engine(TEST_ALEMBIC_DATABASE_URL)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO departments (id, name, code, status)
                        VALUES (:department_id, '迁移测试部', 'review-migration', 'active')
                        """
                    ),
                    {"department_id": department_id},
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO users (
                            id, name, email, email_domain, password_hash, department_id,
                            department, role, status, email_verified
                        )
                        VALUES (
                            :uploader_id, 'uploader', 'migration@company.com', 'company.com',
                            'x', :department_id, '迁移测试部', 'employee', 'active', true
                        )
                        """
                    ),
                    {"uploader_id": uploader_id, "department_id": department_id},
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO files (
                            id, original_name, stored_name, extension, mime_type, size, hash,
                            storage_type, bucket, object_key, uploader_id, department_id,
                            department, visibility, status, review_status,
                            ai_analysis_enabled_at_upload
                        )
                        VALUES (
                            :file_id, 'legacy.pdf', 'legacy.pdf', 'pdf', 'application/pdf',
                            128, :hash_value, 'minio', 'knowledge-files', 'uploads/legacy.pdf',
                            :uploader_id, :department_id, '迁移测试部', 'private',
                            'pending_review', 'in_review', false
                        ), (
                            :non_pending_file_id, 'legacy-draft.pdf', 'legacy-draft.pdf',
                            'pdf', 'application/pdf', 128, :second_hash_value, 'minio',
                            'knowledge-files', 'uploads/legacy-draft.pdf', :uploader_id,
                            :department_id, '迁移测试部', 'private', 'uploaded',
                            'in_review', false
                        )
                        """
                    ),
                    {
                        "file_id": file_id,
                        "non_pending_file_id": non_pending_file_id,
                        "hash_value": "a" * 64,
                        "second_hash_value": "b" * 64,
                        "uploader_id": uploader_id,
                        "department_id": department_id,
                    },
                )
        finally:
            engine.dispose()

        command.upgrade(config, "20260716r001")

        engine = create_engine(TEST_ALEMBIC_DATABASE_URL)
        try:
            with engine.connect() as connection:
                migrated: dict[str, Any] = dict(
                    connection.execute(
                        text(
                            """
                            SELECT review_status, submitted_at, review_due_at,
                                   claimed_by, claimed_at, claim_expires_at
                            FROM files
                            WHERE id = :file_id
                            """
                        ),
                        {"file_id": file_id},
                    )
                    .mappings()
                    .one()
                )
            assert migrated["review_status"] == "pending"
            assert migrated["submitted_at"] is not None
            assert migrated["review_due_at"] is not None
            assert migrated["claimed_by"] is None
            assert migrated["claimed_at"] is None
            assert migrated["claim_expires_at"] is None
            with engine.connect() as connection:
                worker_columns_at_r001 = set(
                    connection.execute(
                        text(
                            """
                            SELECT table_name || '.' || column_name
                            FROM information_schema.columns
                            WHERE (table_name = 'document_analysis'
                                   AND column_name = 'lease_token')
                               OR (table_name = 'sync_tasks' AND column_name IN (
                                   'lease_token', 'lease_heartbeat_at',
                                   'reconcile_attempt_count',
                                   'reconcile_not_before', 'recovery_probe_due_at'
                               ))
                            """
                        )
                    ).scalars()
                )
            assert worker_columns_at_r001 == set()
            with engine.connect() as connection:
                non_pending_review_status = connection.execute(
                    text("SELECT review_status FROM files WHERE id = :file_id"),
                    {"file_id": non_pending_file_id},
                ).scalar_one()
            assert non_pending_review_status == "pending"

            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "UPDATE files SET review_status = 'in_review' WHERE id = :file_id"
                        ),
                        {"file_id": file_id},
                    )
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            """
                            UPDATE files
                            SET submitted_at = NULL,
                                review_due_at = NULL
                            WHERE id = :file_id
                            """
                        ),
                        {"file_id": file_id},
                    )
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            """
                            UPDATE files
                            SET review_due_at = submitted_at
                            WHERE id = :file_id
                            """
                        ),
                        {"file_id": file_id},
                    )
        finally:
            engine.dispose()

        command.downgrade(config, "20260708d001")
        engine = create_engine(TEST_ALEMBIC_DATABASE_URL)
        try:
            with engine.connect() as connection:
                remaining_review_columns = set(
                    connection.execute(
                        text(
                            """
                            SELECT column_name
                            FROM information_schema.columns
                            WHERE table_name = 'files' AND column_name IN (
                                'submitted_at', 'review_due_at', 'claimed_by',
                                'claimed_at', 'claim_expires_at', 'review_version'
                            )
                            """
                        )
                    ).scalars()
                )
            assert remaining_review_columns == set()
        finally:
            engine.dispose()
    finally:
        _reset_schema()


def test_r002_adds_and_downgrades_worker_execution_lease_metadata() -> None:
    config = _alembic_config()
    _reset_schema()
    try:
        command.upgrade(config, "20260716r001")
        command.upgrade(config, "20260716r002")
        engine = create_engine(TEST_ALEMBIC_DATABASE_URL)
        try:
            with engine.connect() as connection:
                worker_columns = set(
                    connection.execute(
                        text(
                            """
                            SELECT table_name || '.' || column_name
                            FROM information_schema.columns
                            WHERE (table_name = 'document_analysis'
                                   AND column_name = 'lease_token')
                               OR (table_name = 'sync_tasks' AND column_name IN (
                                   'lease_token', 'lease_heartbeat_at',
                                   'reconcile_attempt_count',
                                   'reconcile_not_before', 'recovery_probe_due_at'
                               ))
                            """
                        )
                    ).scalars()
                )
            assert worker_columns == {
                "document_analysis.lease_token",
                "sync_tasks.lease_token",
                "sync_tasks.lease_heartbeat_at",
                "sync_tasks.reconcile_attempt_count",
                "sync_tasks.reconcile_not_before",
                "sync_tasks.recovery_probe_due_at",
            }
        finally:
            engine.dispose()

        command.downgrade(config, "20260716r001")
        engine = create_engine(TEST_ALEMBIC_DATABASE_URL)
        try:
            with engine.connect() as connection:
                remaining = set(
                    connection.execute(
                        text(
                            """
                            SELECT table_name || '.' || column_name
                            FROM information_schema.columns
                            WHERE (table_name = 'document_analysis'
                                   AND column_name = 'lease_token')
                               OR (table_name = 'sync_tasks' AND column_name IN (
                                   'lease_token', 'lease_heartbeat_at',
                                   'reconcile_attempt_count',
                                   'reconcile_not_before', 'recovery_probe_due_at'
                               ))
                            """
                        )
                    ).scalars()
                )
            assert remaining == set()
        finally:
            engine.dispose()
    finally:
        _reset_schema()


def test_d002_backfills_original_name_as_non_null_title() -> None:
    config = _alembic_config()
    department_id = uuid.uuid4()
    uploader_id = uuid.uuid4()
    file_id = uuid.uuid4()
    _reset_schema()
    try:
        command.upgrade(config, "20260708d001")
        engine = create_engine(TEST_ALEMBIC_DATABASE_URL)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO departments (id, name, code, status)
                        VALUES (:department_id, '草稿迁移部', 'draft-migration', 'active')
                        """
                    ),
                    {"department_id": department_id},
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO users (
                            id, name, email, email_domain, password_hash, department_id,
                            department, role, status, email_verified
                        )
                        VALUES (
                            :uploader_id, 'uploader', 'draft-migration@company.com',
                            'company.com', 'x', :department_id, '草稿迁移部', 'employee',
                            'active', true
                        )
                        """
                    ),
                    {"uploader_id": uploader_id, "department_id": department_id},
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO files (
                            id, original_name, stored_name, extension, mime_type, size, hash,
                            storage_type, bucket, object_key, uploader_id, department_id,
                            department, visibility, status, review_status,
                            ai_analysis_enabled_at_upload
                        )
                        VALUES (
                            :file_id, 'legacy-title.pdf', 'legacy-title.pdf', 'pdf',
                            'application/pdf', 128, :hash_value, 'minio', 'knowledge-files',
                            'uploads/legacy-title.pdf', :uploader_id, :department_id,
                            '草稿迁移部', 'private', 'uploaded', 'pending', false
                        )
                        """
                    ),
                    {
                        "file_id": file_id,
                        "hash_value": "c" * 64,
                        "uploader_id": uploader_id,
                        "department_id": department_id,
                    },
                )
        finally:
            engine.dispose()

        command.upgrade(config, "20260716d002")

        engine = create_engine(TEST_ALEMBIC_DATABASE_URL)
        try:
            with engine.connect() as connection:
                title = connection.execute(
                    text("SELECT title FROM files WHERE id = :file_id"),
                    {"file_id": file_id},
                ).scalar_one()
                nullable = connection.execute(
                    text(
                        """
                        SELECT is_nullable
                        FROM information_schema.columns
                        WHERE table_name = 'files' AND column_name = 'title'
                        """
                    )
                ).scalar_one()
                column_default = connection.execute(
                    text(
                        """
                        SELECT column_default
                        FROM information_schema.columns
                        WHERE table_name = 'files' AND column_name = 'title'
                        """
                    )
                ).scalar_one_or_none()
            assert title == "legacy-title.pdf"
            assert nullable == "NO"
            assert column_default is None
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.execute(
                        text("UPDATE files SET title = NULL WHERE id = :file_id"),
                        {"file_id": file_id},
                    )
        finally:
            engine.dispose()

        command.downgrade(config, "20260716r002")
        engine = create_engine(TEST_ALEMBIC_DATABASE_URL)
        try:
            with engine.connect() as connection:
                title_column = connection.execute(
                    text(
                        """
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_name = 'files' AND column_name = 'title'
                        """
                    )
                ).scalar_one_or_none()
            assert title_column is None
        finally:
            engine.dispose()
    finally:
        _reset_schema()
