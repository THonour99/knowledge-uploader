from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

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


def _table_exists(table_name: str) -> bool:
    engine = create_engine(TEST_ALEMBIC_DATABASE_URL)
    try:
        with engine.connect() as connection:
            return bool(
                connection.execute(
                    text("SELECT to_regclass('public.' || :table_name) IS NOT NULL"),
                    {"table_name": table_name},
                ).scalar_one()
            )
    finally:
        engine.dispose()


def _config_value(key: str) -> object | None:
    engine = create_engine(TEST_ALEMBIC_DATABASE_URL)
    try:
        with engine.connect() as connection:
            return connection.execute(
                text("SELECT value FROM system_configs WHERE key = :key"),
                {"key": key},
            ).scalar_one_or_none()
    finally:
        engine.dispose()


def _config_row(key: str) -> dict[str, object] | None:
    engine = create_engine(TEST_ALEMBIC_DATABASE_URL)
    try:
        with engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        'SELECT id, key, "group", value, value_type, is_secret, description, '
                        "updated_by, created_at, updated_at "
                        "FROM system_configs WHERE key = :key"
                    ),
                    {"key": key},
                )
                .mappings()
                .one_or_none()
            )
            return dict(row) if row is not None else None
    finally:
        engine.dispose()


def _insert_legacy_file(
    connection: object,
    *,
    file_id: uuid.UUID,
    uploader_id: uuid.UUID,
    department_id: uuid.UUID,
    status: str,
    hash_value: str,
    ragflow_document_id: str,
) -> None:
    connection.execute(  # type: ignore[attr-defined]
        text(
            """
            INSERT INTO files (
                id, original_name, title, stored_name, extension, mime_type, size, hash,
                storage_type, bucket, object_key, uploader_id, department_id, department,
                visibility, status, review_status, ragflow_document_id,
                ragflow_parse_status, ai_analysis_enabled_at_upload
            ) VALUES (
                :file_id, :name, :name, :name, 'pdf', 'application/pdf', 128, :hash_value,
                'minio', 'knowledge-files', :object_key, :uploader_id, :department_id,
                '迁移测试部', 'department', :status, 'approved', :ragflow_document_id,
                'DONE', false
            )
            """
        ),
        {
            "file_id": file_id,
            "name": f"{file_id}.pdf",
            "hash_value": hash_value,
            "object_key": f"uploads/{file_id}.pdf",
            "uploader_id": uploader_id,
            "department_id": department_id,
            "status": status,
            "ragflow_document_id": ragflow_document_id,
        },
    )


def test_v001_version_governance_upgrade_constraints_and_round_trip() -> None:
    config = _alembic_config()
    department_id = uuid.uuid4()
    second_department_id = uuid.uuid4()
    uploader_id = uuid.uuid4()
    parsed_file_id = uuid.uuid4()
    second_uploader_id = uuid.uuid4()
    disabled_file_id = uuid.uuid4()
    replacement_id = uuid.uuid4()
    operation_id = uuid.uuid4()
    compatibility_file_id = uuid.uuid4()
    _reset_schema()
    try:
        command.upgrade(config, "20260716s001")
        engine = create_engine(TEST_ALEMBIC_DATABASE_URL)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO departments (id, name, code, status) VALUES
                        (:department_id, '迁移测试部', 'version-migration', 'active'),
                        (:second_department_id, '另一部门', 'version-migration-2', 'active')
                        """
                    ),
                    {
                        "department_id": department_id,
                        "second_department_id": second_department_id,
                    },
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO users (
                            id, name, email, email_domain, password_hash, department_id,
                            department, role, status, email_verified
                        ) VALUES
                        (
                            :uploader_id, 'owner', 'owner@company.com', 'company.com', 'x',
                            :department_id, '迁移测试部', 'employee', 'active', true
                        ), (
                            :second_uploader_id, 'other', 'other@company.com', 'company.com',
                            'x', :department_id, '迁移测试部', 'employee', 'active', true
                        )
                        """
                    ),
                    {
                        "uploader_id": uploader_id,
                        "second_uploader_id": second_uploader_id,
                        "department_id": department_id,
                    },
                )
                _insert_legacy_file(
                    connection,
                    file_id=parsed_file_id,
                    uploader_id=uploader_id,
                    department_id=department_id,
                    status="parsed",
                    hash_value="a" * 64,
                    ragflow_document_id="remote-current",
                )
                _insert_legacy_file(
                    connection,
                    file_id=disabled_file_id,
                    uploader_id=uploader_id,
                    department_id=department_id,
                    status="disabled",
                    hash_value="b" * 64,
                    ragflow_document_id="remote-disabled-unknown",
                )
        finally:
            engine.dispose()

        command.upgrade(config, "20260716v001")
        governance_columns = {
            "owner_id",
            "series_id",
            "version_number",
            "replaces_file_id",
            "replacement_remote_action",
            "is_current_version",
            "remote_visibility",
            "version_switch_status",
            "version_switch_error",
            "version_switch_attempt_count",
            "predecessor_remote_deactivated_at",
            "local_version_activated_at",
            "remote_version_activated_at",
        }
        assert governance_columns <= _columns("files")
        assert _table_exists("ragflow_version_operations")
        assert _config_value("ragflow.keep_replaced_remote") is False

        engine = create_engine(TEST_ALEMBIC_DATABASE_URL)
        try:
            with engine.connect() as connection:
                rows = {
                    row.id: row
                    for row in connection.execute(
                        text(
                            """
                            SELECT id, owner_id, series_id, version_number,
                                   is_current_version, remote_visibility
                            FROM files
                            WHERE id IN (:parsed_file_id, :disabled_file_id)
                            """
                        ),
                        {
                            "parsed_file_id": parsed_file_id,
                            "disabled_file_id": disabled_file_id,
                        },
                    )
                }
            assert rows[parsed_file_id].owner_id == uploader_id
            assert rows[parsed_file_id].series_id == parsed_file_id
            assert rows[parsed_file_id].version_number == 1
            assert rows[parsed_file_id].is_current_version is True
            assert rows[parsed_file_id].remote_visibility == "current"
            assert rows[disabled_file_id].remote_visibility == "unknown"

            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO files (
                            id, original_name, title, stored_name, extension, mime_type,
                            size, hash, storage_type, bucket, object_key, uploader_id,
                            owner_id, department_id, department, visibility, status,
                            review_status, ragflow_document_id, ragflow_parse_status,
                            ai_analysis_enabled_at_upload, series_id, version_number,
                            replaces_file_id, replacement_remote_action,
                            is_current_version, remote_visibility,
                            version_switch_status
                        ) VALUES (
                            :file_id, 'replacement.pdf', 'replacement.pdf',
                            'replacement.pdf', 'pdf', 'application/pdf', 128, :hash_value,
                            'minio', 'knowledge-files', 'uploads/replacement.pdf',
                            :uploader_id, :uploader_id, :department_id, '迁移测试部',
                            'department', 'parsed', 'approved', 'remote-replacement',
                            'DONE', false, :series_id, 2, :replaces_file_id, 'archive',
                            false,
                            'candidate', 'pending'
                        )
                        """
                    ),
                    {
                        "file_id": replacement_id,
                        "hash_value": "c" * 64,
                        "uploader_id": uploader_id,
                        "department_id": department_id,
                        "series_id": parsed_file_id,
                        "replaces_file_id": parsed_file_id,
                    },
                )

            with pytest.raises(DBAPIError):
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            """
                            UPDATE files SET department_id = :department_id
                            WHERE id = :file_id
                            """
                        ),
                        {
                            "department_id": second_department_id,
                            "file_id": parsed_file_id,
                        },
                    )
            with pytest.raises(DBAPIError):
                with engine.begin() as connection:
                    connection.execute(
                        text("UPDATE files SET uploader_id = :uploader_id WHERE id = :file_id"),
                        {
                            "uploader_id": second_uploader_id,
                            "file_id": parsed_file_id,
                        },
                    )
            with pytest.raises(DBAPIError):
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            """
                            INSERT INTO files (
                                id, original_name, title, stored_name, extension, mime_type,
                                size, hash, storage_type, bucket, object_key, uploader_id,
                                owner_id, department_id, department, visibility, status,
                                review_status, ai_analysis_enabled_at_upload, series_id,
                                version_number, replaces_file_id, replacement_remote_action,
                                is_current_version,
                                remote_visibility, version_switch_status
                            ) VALUES (
                                :file_id, 'cross-owner.pdf', 'cross-owner.pdf',
                                'cross-owner.pdf', 'pdf', 'application/pdf', 128,
                                :hash_value, 'minio', 'knowledge-files',
                                'uploads/cross-owner.pdf', :uploader_id, :uploader_id,
                                :department_id, '迁移测试部', 'department', 'uploaded',
                                'pending', false, :series_id, 2, :replaces_file_id, 'delete',
                                false,
                                'candidate', 'pending'
                            )
                            """
                        ),
                        {
                            "file_id": uuid.uuid4(),
                            "hash_value": "e" * 64,
                            "uploader_id": second_uploader_id,
                            "department_id": department_id,
                            "series_id": disabled_file_id,
                            "replaces_file_id": disabled_file_id,
                        },
                    )
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.execute(
                        text("UPDATE files SET is_current_version = true WHERE id = :file_id"),
                        {"file_id": replacement_id},
                    )
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            """
                            INSERT INTO files (
                                id, original_name, title, stored_name, extension, mime_type,
                                size, hash, storage_type, bucket, object_key, uploader_id,
                                owner_id, department_id, department, visibility, status,
                                review_status, ai_analysis_enabled_at_upload, series_id,
                                version_number, replaces_file_id, replacement_remote_action,
                                is_current_version,
                                remote_visibility, version_switch_status
                            ) VALUES (
                                :file_id, 'duplicate.pdf', 'duplicate.pdf', 'duplicate.pdf',
                                'pdf', 'application/pdf', 128, :hash_value, 'minio',
                                'knowledge-files', 'uploads/duplicate.pdf', :uploader_id,
                                :uploader_id, :department_id, '迁移测试部', 'department',
                                'uploaded', 'pending', false, :series_id, 3,
                                :replaces_file_id, 'delete', false, 'candidate', 'pending'
                            )
                            """
                        ),
                        {
                            "file_id": uuid.uuid4(),
                            "hash_value": "d" * 64,
                            "uploader_id": uploader_id,
                            "department_id": department_id,
                            "series_id": parsed_file_id,
                            "replaces_file_id": parsed_file_id,
                        },
                    )

            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE files SET is_current_version = false WHERE id = :file_id"),
                    {"file_id": parsed_file_id},
                )
                connection.execute(
                    text("UPDATE files SET is_current_version = true WHERE id = :file_id"),
                    {"file_id": replacement_id},
                )
                connection.execute(
                    text(
                        """
                        UPDATE files
                        SET owner_id = :owner_id,
                            remote_visibility = 'current',
                            version_switch_status = 'completed',
                            version_switch_error = 'preserved-evidence',
                            version_switch_attempt_count = 7,
                            predecessor_remote_deactivated_at =
                                TIMESTAMPTZ '2026-07-17 01:02:03+00',
                            local_version_activated_at =
                                TIMESTAMPTZ '2026-07-17 01:03:04+00',
                            remote_version_activated_at =
                                TIMESTAMPTZ '2026-07-17 01:04:05+00'
                        WHERE id = :file_id
                        """
                    ),
                    {"owner_id": second_uploader_id, "file_id": replacement_id},
                )
                connection.execute(
                    text(
                        """
                        UPDATE files SET remote_visibility = 'not_current'
                        WHERE id = :file_id
                        """
                    ),
                    {"file_id": parsed_file_id},
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO ragflow_version_operations (
                            id, file_id, target_file_id, operation, status,
                            attempt_count, last_error, started_at, finished_at
                        ) VALUES (
                            :id, :file_id, :target_file_id, 'deactivate_predecessor',
                            'unknown', 4, 'OutcomeUnknown',
                            TIMESTAMPTZ '2026-07-17 01:00:00+00',
                            TIMESTAMPTZ '2026-07-17 01:01:00+00'
                        )
                        """
                    ),
                    {
                        "id": operation_id,
                        "file_id": replacement_id,
                        "target_file_id": parsed_file_id,
                    },
                )
                connection.execute(
                    text(
                        """
                        UPDATE system_configs
                        SET value = 'true'::jsonb,
                            description = 'preserved replacement policy',
                            updated_by = :updated_by,
                            updated_at = TIMESTAMPTZ '2026-07-17 02:03:04+00'
                        WHERE key = 'ragflow.keep_replaced_remote'
                        """
                    ),
                    {"updated_by": second_uploader_id},
                )
        finally:
            engine.dispose()
        preserved_config = _config_row("ragflow.keep_replaced_remote")
        assert preserved_config is not None
        assert preserved_config["value"] is True
        assert preserved_config["updated_by"] == second_uploader_id

        command.downgrade(config, "20260716s001")
        assert governance_columns.isdisjoint(_columns("files"))
        assert not _table_exists("ragflow_version_operations")
        assert _config_value("ragflow.keep_replaced_remote") is None
        assert _table_exists("document_version_governance_config_shadow")
        engine = create_engine(TEST_ALEMBIC_DATABASE_URL)
        try:
            with engine.begin() as connection:
                _insert_legacy_file(
                    connection,
                    file_id=compatibility_file_id,
                    uploader_id=uploader_id,
                    department_id=department_id,
                    status="uploaded",
                    hash_value="f" * 64,
                    ragflow_document_id="compatibility-window-root",
                )
        finally:
            engine.dispose()

        command.upgrade(config, "20260716v001")
        assert governance_columns <= _columns("files")
        assert not _table_exists("document_version_governance_file_shadow")
        assert _config_row("ragflow.keep_replaced_remote") == preserved_config
        assert not _table_exists("document_version_governance_config_shadow")
        assert not _table_exists("document_version_governance_operation_shadow")
        engine = create_engine(TEST_ALEMBIC_DATABASE_URL)
        try:
            with engine.connect() as connection:
                restored = connection.execute(
                    text(
                        """
                        SELECT owner_id, series_id, version_number, replaces_file_id,
                               replacement_remote_action,
                               is_current_version, remote_visibility,
                               version_switch_status, version_switch_error,
                               version_switch_attempt_count,
                               predecessor_remote_deactivated_at,
                               local_version_activated_at,
                               remote_version_activated_at
                        FROM files WHERE id = :file_id
                        """
                    ),
                    {"file_id": replacement_id},
                ).one()
                assert restored.owner_id == second_uploader_id
                assert restored.series_id == parsed_file_id
                assert restored.version_number == 2
                assert restored.replaces_file_id == parsed_file_id
                assert restored.replacement_remote_action == "archive"
                assert restored.is_current_version is True
                assert restored.remote_visibility == "current"
                assert restored.version_switch_status == "completed"
                assert restored.version_switch_error == "preserved-evidence"
                assert restored.version_switch_attempt_count == 7
                assert restored.predecessor_remote_deactivated_at is not None
                assert restored.local_version_activated_at is not None
                assert restored.remote_version_activated_at is not None
                operation = connection.execute(
                    text(
                        """
                        SELECT file_id, target_file_id, operation, status,
                               attempt_count, last_error, started_at, finished_at
                        FROM ragflow_version_operations WHERE id = :id
                        """
                    ),
                    {"id": operation_id},
                ).one()
                assert operation.file_id == replacement_id
                assert operation.target_file_id == parsed_file_id
                assert operation.operation == "deactivate_predecessor"
                assert operation.status == "unknown"
                assert operation.attempt_count == 4
                assert operation.last_error == "OutcomeUnknown"
                assert operation.started_at is not None
                assert operation.finished_at is not None
                compatibility = connection.execute(
                    text(
                        """
                        SELECT owner_id, series_id, version_number, replaces_file_id
                        FROM files WHERE id = :file_id
                        """
                    ),
                    {"file_id": compatibility_file_id},
                ).one()
                assert compatibility.owner_id == uploader_id
                assert compatibility.series_id == compatibility_file_id
                assert compatibility.version_number == 1
                assert compatibility.replaces_file_id is None
        finally:
            engine.dispose()
    finally:
        _reset_schema()


def test_v001_reupgrade_fails_closed_when_legacy_fields_drift_during_downgrade() -> None:
    config = _alembic_config()
    department_id = uuid.uuid4()
    second_department_id = uuid.uuid4()
    uploader_id = uuid.uuid4()
    second_uploader_id = uuid.uuid4()
    file_id = uuid.uuid4()
    _reset_schema()
    try:
        command.upgrade(config, "20260716s001")
        engine = create_engine(TEST_ALEMBIC_DATABASE_URL)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO departments (id, name, code, status) VALUES
                        (:department_id, '基线部门', 'baseline-origin', 'active'),
                        (:second_department_id, '漂移部门', 'baseline-drift', 'active')
                        """
                    ),
                    {
                        "department_id": department_id,
                        "second_department_id": second_department_id,
                    },
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO users (
                            id, name, email, email_domain, password_hash, department_id,
                            department, role, status, email_verified
                        ) VALUES
                        (
                            :uploader_id, 'origin', 'baseline-origin@company.com',
                            'company.com', 'x', :department_id, '基线部门',
                            'employee', 'active', true
                        ), (
                            :second_uploader_id, 'drift', 'baseline-drift@company.com',
                            'company.com', 'x', :second_department_id, '漂移部门',
                            'employee', 'active', true
                        )
                        """
                    ),
                    {
                        "uploader_id": uploader_id,
                        "second_uploader_id": second_uploader_id,
                        "department_id": department_id,
                        "second_department_id": second_department_id,
                    },
                )
                _insert_legacy_file(
                    connection,
                    file_id=file_id,
                    uploader_id=uploader_id,
                    department_id=department_id,
                    status="parsed",
                    hash_value="9" * 64,
                    ragflow_document_id="baseline-document",
                )
                connection.execute(
                    text(
                        """
                        UPDATE files
                        SET ragflow_dataset_id = 'baseline-dataset'
                        WHERE id = :file_id
                        """
                    ),
                    {"file_id": file_id},
                )
        finally:
            engine.dispose()

        command.upgrade(config, "20260716v001")
        command.downgrade(config, "20260716s001")
        assert {
            "baseline_status",
            "baseline_review_status",
            "baseline_uploader_id",
            "baseline_department_id",
            "baseline_ragflow_dataset_id",
            "baseline_ragflow_document_id",
            "baseline_ragflow_parse_status",
        } <= _columns("document_version_governance_file_shadow")

        engine = create_engine(TEST_ALEMBIC_DATABASE_URL)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE files
                        SET status = 'uploaded',
                            review_status = 'pending',
                            uploader_id = :uploader_id,
                            department_id = :department_id,
                            ragflow_dataset_id = 'drifted-dataset',
                            ragflow_document_id = 'drifted-document',
                            ragflow_parse_status = 'FAILED'
                        WHERE id = :file_id
                        """
                    ),
                    {
                        "uploader_id": second_uploader_id,
                        "department_id": second_department_id,
                        "file_id": file_id,
                    },
                )
        finally:
            engine.dispose()

        with pytest.raises(DBAPIError, match="baseline drifted during downgrade"):
            command.upgrade(config, "20260716v001")
        assert _table_exists("document_version_governance_file_shadow")
    finally:
        _reset_schema()


@pytest.mark.parametrize("missing_role", ["candidate", "target"])
def test_v001_reupgrade_fails_closed_when_shadow_operation_file_is_missing(
    missing_role: str,
) -> None:
    config = _alembic_config()
    department_id = uuid.uuid4()
    uploader_id = uuid.uuid4()
    root_id = uuid.uuid4()
    candidate_id = uuid.uuid4()
    operation_id = uuid.uuid4()
    _reset_schema()
    try:
        command.upgrade(config, "20260716s001")
        engine = create_engine(TEST_ALEMBIC_DATABASE_URL)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO departments (id, name, code, status)
                        VALUES (:id, '恢复阻断测试部', 'version-restore-block', 'active')
                        """
                    ),
                    {"id": department_id},
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO users (
                            id, name, email, email_domain, password_hash, department_id,
                            department, role, status, email_verified
                        ) VALUES (
                            :id, 'restore-owner', 'restore-owner@company.com',
                            'company.com', 'x', :department_id, '恢复阻断测试部',
                            'employee', 'active', true
                        )
                        """
                    ),
                    {"id": uploader_id, "department_id": department_id},
                )
                _insert_legacy_file(
                    connection,
                    file_id=root_id,
                    uploader_id=uploader_id,
                    department_id=department_id,
                    status="parsed",
                    hash_value="7" * 64,
                    ragflow_document_id="remote-restore-root",
                )
        finally:
            engine.dispose()

        command.upgrade(config, "20260716v001")
        engine = create_engine(TEST_ALEMBIC_DATABASE_URL)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO files (
                            id, original_name, title, stored_name, extension, mime_type,
                            size, hash, storage_type, bucket, object_key, uploader_id,
                            owner_id, department_id, department, visibility, status,
                            review_status, ragflow_document_id, ragflow_parse_status,
                            ai_analysis_enabled_at_upload, series_id, version_number,
                            replaces_file_id, replacement_remote_action,
                            is_current_version, remote_visibility,
                            version_switch_status
                        ) VALUES (
                            :id, 'restore-candidate.pdf', 'restore-candidate.pdf',
                            'restore-candidate.pdf', 'pdf', 'application/pdf', 128,
                            :hash_value, 'minio', 'knowledge-files',
                            'uploads/restore-candidate.pdf', :uploader_id, :uploader_id,
                            :department_id, '恢复阻断测试部', 'department', 'parsed', 'approved',
                            'remote-restore-candidate', 'DONE', false, :series_id, 2,
                            :replaces_file_id, 'delete', false, 'candidate', 'pending'
                        )
                        """
                    ),
                    {
                        "id": candidate_id,
                        "hash_value": "8" * 64,
                        "uploader_id": uploader_id,
                        "department_id": department_id,
                        "series_id": root_id,
                        "replaces_file_id": root_id,
                    },
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO ragflow_version_operations (
                            id, file_id, target_file_id, operation, status, attempt_count
                        ) VALUES (
                            :id, :file_id, :target_file_id,
                            'deactivate_predecessor', 'running', 1
                        )
                        """
                    ),
                    {
                        "id": operation_id,
                        "file_id": candidate_id,
                        "target_file_id": root_id,
                    },
                )
        finally:
            engine.dispose()

        command.downgrade(config, "20260716s001")
        engine = create_engine(TEST_ALEMBIC_DATABASE_URL)
        try:
            with engine.begin() as connection:
                missing_id = candidate_id if missing_role == "candidate" else root_id
                connection.execute(
                    text("DELETE FROM files WHERE id = :id"),
                    {"id": missing_id},
                )
        finally:
            engine.dispose()

        with pytest.raises(DBAPIError):
            command.upgrade(config, "20260716v001")

        assert _table_exists("document_version_governance_file_shadow")
        assert _table_exists("document_version_governance_operation_shadow")
        assert not _table_exists("ragflow_version_operations")
        assert "owner_id" not in _columns("files")
        engine = create_engine(TEST_ALEMBIC_DATABASE_URL)
        try:
            with engine.connect() as connection:
                assert (
                    connection.execute(
                        text("SELECT COUNT(*) FROM document_version_governance_file_shadow")
                    ).scalar_one()
                    == 2
                )
                assert (
                    connection.execute(
                        text("SELECT COUNT(*) FROM document_version_governance_operation_shadow")
                    ).scalar_one()
                    == 1
                )
        finally:
            engine.dispose()
    finally:
        _reset_schema()


@pytest.mark.parametrize("tamper", ["missing", "conflict"])
def test_v001_reupgrade_fails_closed_for_incomplete_or_conflicting_config_shadow(
    tamper: str,
) -> None:
    config = _alembic_config()
    _reset_schema()
    try:
        command.upgrade(config, "20260716v001")
        command.downgrade(config, "20260716s001")
        assert _table_exists("document_version_governance_config_shadow")
        assert _config_value("ragflow.keep_replaced_remote") is None

        engine = create_engine(TEST_ALEMBIC_DATABASE_URL)
        try:
            with engine.begin() as connection:
                if tamper == "missing":
                    connection.execute(
                        text(
                            "DELETE FROM document_version_governance_config_shadow "
                            "WHERE key = 'ragflow.keep_replaced_remote'"
                        )
                    )
                else:
                    connection.execute(
                        text(
                            """
                            INSERT INTO system_configs (
                                id,
                                key,
                                "group",
                                value,
                                value_type,
                                is_secret,
                                description
                            ) VALUES (
                                :id,
                                'ragflow.keep_replaced_remote',
                                'ragflow',
                                'true'::jsonb,
                                'bool',
                                false,
                                'conflicting replacement policy'
                            )
                            """
                        ),
                        {"id": uuid.uuid4()},
                    )
        finally:
            engine.dispose()

        expected_error = (
            "document version config shadow is incomplete"
            if tamper == "missing"
            else "document version config restore conflicts with an existing row"
        )
        with pytest.raises(RuntimeError, match=expected_error):
            command.upgrade(config, "20260716v001")

        assert _table_exists("document_version_governance_config_shadow")
        assert "owner_id" not in _columns("files")
        if tamper == "missing":
            assert _config_value("ragflow.keep_replaced_remote") is None
        else:
            assert _config_value("ragflow.keep_replaced_remote") is True
    finally:
        _reset_schema()
