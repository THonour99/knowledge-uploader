"""add document ownership and recoverable version governance

Revision ID: 20260716v001
Revises: 20260716s001
Create Date: 2026-07-17 08:00:00.000000
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260716v001"
down_revision: str | None = "20260716s001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
_VERSION_CONFIG_KEY = "ragflow.keep_replaced_remote"
_VERSION_CONFIG_DESCRIPTION = "新版本生效时是否保留旧远端文档并将其标记为非当前版本"
_VERSION_CONFIG_SHADOW = "document_version_governance_config_shadow"
_VERSION_CONFIG_COLUMNS = (
    "id",
    "key",
    "group",
    "value",
    "value_type",
    "is_secret",
    "description",
    "updated_by",
    "created_at",
    "updated_at",
)


def _system_config_table(name: str = "system_configs") -> sa.TableClause:
    return sa.table(
        name,
        sa.column("id", sa.Uuid()),
        sa.column("key", sa.String()),
        sa.column("group", sa.String()),
        sa.column("value", postgresql.JSONB(astext_type=sa.Text())),
        sa.column("value_type", sa.String()),
        sa.column("is_secret", sa.Boolean()),
        sa.column("description", sa.Text()),
        sa.column("updated_by", sa.Uuid()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )


def _restore_version_config() -> None:
    connection = op.get_bind()
    shadow_exists = connection.execute(
        sa.text("SELECT to_regclass('public.' || :table_name) IS NOT NULL"),
        {"table_name": _VERSION_CONFIG_SHADOW},
    ).scalar_one()
    if not shadow_exists:
        return
    table = _system_config_table()
    shadow = _system_config_table(_VERSION_CONFIG_SHADOW)
    shadow_rows = (
        connection.execute(sa.select(*(shadow.c[name] for name in _VERSION_CONFIG_COLUMNS)))
        .mappings()
        .all()
    )
    if len(shadow_rows) != 1 or shadow_rows[0]["key"] != _VERSION_CONFIG_KEY:
        raise RuntimeError("document version config shadow is incomplete")
    shadow_values = dict(shadow_rows[0])
    target = (
        connection.execute(
            sa.select(*(table.c[name] for name in _VERSION_CONFIG_COLUMNS)).where(
                table.c.key == _VERSION_CONFIG_KEY
            )
        )
        .mappings()
        .one_or_none()
    )
    if target is None:
        connection.execute(sa.insert(table), shadow_values)
    elif tuple(target[name] for name in _VERSION_CONFIG_COLUMNS) != tuple(
        shadow_values[name] for name in _VERSION_CONFIG_COLUMNS
    ):
        raise RuntimeError("document version config restore conflicts with an existing row")
    restored = (
        connection.execute(
            sa.select(*(table.c[name] for name in _VERSION_CONFIG_COLUMNS)).where(
                table.c.key == _VERSION_CONFIG_KEY
            )
        )
        .mappings()
        .one_or_none()
    )
    if restored is None or tuple(restored[name] for name in _VERSION_CONFIG_COLUMNS) != tuple(
        shadow_values[name] for name in _VERSION_CONFIG_COLUMNS
    ):
        raise RuntimeError("document version config shadow was not fully restored")


def _insert_version_config() -> None:
    table = _system_config_table()
    connection = op.get_bind()
    exists = connection.execute(
        sa.select(table.c.key).where(table.c.key == _VERSION_CONFIG_KEY)
    ).scalar_one_or_none()
    if exists is None:
        connection.execute(
            sa.insert(table),
            {
                "id": uuid.uuid4(),
                "key": _VERSION_CONFIG_KEY,
                "group": "ragflow",
                "value": False,
                "value_type": "bool",
                "is_secret": False,
                "description": _VERSION_CONFIG_DESCRIPTION,
            },
        )


def _backup_version_config_for_downgrade() -> None:
    op.execute(
        sa.text(
            f"""
            CREATE TABLE IF NOT EXISTS {_VERSION_CONFIG_SHADOW} (
                id uuid NOT NULL UNIQUE,
                key varchar(120) PRIMARY KEY,
                "group" varchar(20) NOT NULL,
                value jsonb,
                value_type varchar(20) NOT NULL,
                is_secret boolean NOT NULL,
                description text NOT NULL,
                updated_by uuid,
                created_at timestamp with time zone NOT NULL,
                updated_at timestamp with time zone NOT NULL
            )
            """
        )
    )
    connection = op.get_bind()
    table = _system_config_table()
    shadow = _system_config_table(_VERSION_CONFIG_SHADOW)
    connection.execute(sa.delete(shadow))
    connection.execute(
        sa.insert(shadow).from_select(
            list(_VERSION_CONFIG_COLUMNS),
            sa.select(*(table.c[name] for name in _VERSION_CONFIG_COLUMNS)).where(
                table.c.key == _VERSION_CONFIG_KEY
            ),
        )
    )
    rows = connection.execute(sa.select(shadow.c.key)).scalars().all()
    if rows != [_VERSION_CONFIG_KEY]:
        raise RuntimeError("document version config could not be backed up")


def _delete_version_config() -> None:
    table = _system_config_table()
    op.get_bind().execute(sa.delete(table).where(table.c.key == _VERSION_CONFIG_KEY))


def upgrade() -> None:
    _restore_version_config()
    _insert_version_config()
    _add_file_governance_columns()
    _backfill_file_governance()
    _restore_file_governance()
    _add_file_governance_constraints()
    _create_version_operation_table()
    _restore_version_operations()
    _drop_governance_shadow_tables()


def _add_file_governance_columns() -> None:
    columns: tuple[sa.Column[Any], ...] = (
        sa.Column("owner_id", sa.Uuid(), nullable=True),
        sa.Column("series_id", sa.Uuid(), nullable=True),
        sa.Column("version_number", sa.Integer(), nullable=True),
        sa.Column("replaces_file_id", sa.Uuid(), nullable=True),
        sa.Column("replacement_remote_action", sa.String(length=20), nullable=True),
        sa.Column(
            "is_current_version",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "remote_visibility",
            sa.String(length=20),
            server_default=sa.text("'candidate'"),
            nullable=False,
        ),
        sa.Column(
            "version_switch_status",
            sa.String(length=40),
            server_default=sa.text("'not_required'"),
            nullable=False,
        ),
        sa.Column("version_switch_error", sa.String(length=120), nullable=True),
        sa.Column(
            "version_switch_attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("predecessor_remote_deactivated_at", sa.DateTime(timezone=True)),
        sa.Column("local_version_activated_at", sa.DateTime(timezone=True)),
        sa.Column("remote_version_activated_at", sa.DateTime(timezone=True)),
    )
    for column in columns:
        op.add_column("files", column)


def _backfill_file_governance() -> None:
    op.execute(
        sa.text(
            "UPDATE files SET owner_id = uploader_id, series_id = id, version_number = 1, "
            "remote_visibility = CASE "
            "WHEN status = 'parsed' AND ragflow_document_id IS NOT NULL THEN 'current' "
            "WHEN ragflow_document_id IS NOT NULL THEN 'unknown' "
            "ELSE 'candidate' END, "
            "version_switch_status = 'not_required'"
        )
    )
    op.alter_column("files", "series_id", existing_type=sa.Uuid(), nullable=False)
    op.alter_column("files", "version_number", existing_type=sa.Integer(), nullable=False)


def _restore_file_governance() -> None:
    op.execute(
        sa.text(
            """
            DO $migration$
            BEGIN
                IF to_regclass(
                    'public.document_version_governance_file_shadow'
                ) IS NOT NULL THEN
                    IF EXISTS (
                        SELECT 1
                        FROM document_version_governance_file_shadow AS shadow
                        LEFT JOIN files AS current_file
                            ON current_file.id = shadow.file_id
                        WHERE current_file.id IS NULL
                    ) THEN
                        RAISE EXCEPTION
                            'document governance restore is missing a shadow file';
                    END IF;
                    IF EXISTS (
                        SELECT 1
                        FROM document_version_governance_file_shadow AS shadow
                        JOIN files AS current_file
                            ON current_file.id = shadow.file_id
                        WHERE ROW(
                            current_file.status,
                            current_file.review_status,
                            current_file.uploader_id,
                            current_file.department_id,
                            current_file.ragflow_dataset_id,
                            current_file.ragflow_document_id,
                            current_file.ragflow_parse_status
                        ) IS DISTINCT FROM ROW(
                            shadow.baseline_status,
                            shadow.baseline_review_status,
                            shadow.baseline_uploader_id,
                            shadow.baseline_department_id,
                            shadow.baseline_ragflow_dataset_id,
                            shadow.baseline_ragflow_document_id,
                            shadow.baseline_ragflow_parse_status
                        )
                    ) THEN
                        RAISE EXCEPTION
                            'document governance baseline drifted during downgrade';
                    END IF;
                    UPDATE files AS current_file
                    SET owner_id = shadow.owner_id,
                        series_id = shadow.series_id,
                        version_number = shadow.version_number,
                        replaces_file_id = shadow.replaces_file_id,
                        replacement_remote_action = shadow.replacement_remote_action,
                        is_current_version = shadow.is_current_version,
                        remote_visibility = shadow.remote_visibility,
                        version_switch_status = shadow.version_switch_status,
                        version_switch_error = shadow.version_switch_error,
                        version_switch_attempt_count = shadow.version_switch_attempt_count,
                        predecessor_remote_deactivated_at =
                            shadow.predecessor_remote_deactivated_at,
                        local_version_activated_at = shadow.local_version_activated_at,
                        remote_version_activated_at = shadow.remote_version_activated_at
                    FROM document_version_governance_file_shadow AS shadow
                    WHERE current_file.id = shadow.file_id;
                    IF EXISTS (
                        SELECT 1
                        FROM document_version_governance_file_shadow AS shadow
                        JOIN files AS current_file
                            ON current_file.id = shadow.file_id
                        WHERE ROW(
                            current_file.owner_id,
                            current_file.series_id,
                            current_file.version_number,
                            current_file.replaces_file_id,
                            current_file.replacement_remote_action,
                            current_file.is_current_version,
                            current_file.remote_visibility,
                            current_file.version_switch_status,
                            current_file.version_switch_error,
                            current_file.version_switch_attempt_count,
                            current_file.predecessor_remote_deactivated_at,
                            current_file.local_version_activated_at,
                            current_file.remote_version_activated_at
                        ) IS DISTINCT FROM ROW(
                            shadow.owner_id,
                            shadow.series_id,
                            shadow.version_number,
                            shadow.replaces_file_id,
                            shadow.replacement_remote_action,
                            shadow.is_current_version,
                            shadow.remote_visibility,
                            shadow.version_switch_status,
                            shadow.version_switch_error,
                            shadow.version_switch_attempt_count,
                            shadow.predecessor_remote_deactivated_at,
                            shadow.local_version_activated_at,
                            shadow.remote_version_activated_at
                        )
                    ) THEN
                        RAISE EXCEPTION
                            'document governance shadow fields were not restored';
                    END IF;
                END IF;
            END
            $migration$;
            """
        )
    )


def _restore_version_operations() -> None:
    op.execute(
        sa.text(
            """
            DO $migration$
            BEGIN
                IF to_regclass(
                    'public.document_version_governance_operation_shadow'
                ) IS NOT NULL THEN
                    IF EXISTS (
                        SELECT 1
                        FROM document_version_governance_operation_shadow AS shadow
                        LEFT JOIN files AS candidate ON candidate.id = shadow.file_id
                        LEFT JOIN files AS target ON target.id = shadow.target_file_id
                        WHERE candidate.id IS NULL OR target.id IS NULL
                    ) THEN
                        RAISE EXCEPTION
                            'version operation restore is missing a candidate or target';
                    END IF;
                    INSERT INTO ragflow_version_operations (
                        id,
                        file_id,
                        target_file_id,
                        operation,
                        status,
                        attempt_count,
                        last_error,
                        started_at,
                        finished_at,
                        created_at,
                        updated_at
                    )
                    SELECT
                        shadow.id,
                        shadow.file_id,
                        shadow.target_file_id,
                        shadow.operation,
                        shadow.status,
                        shadow.attempt_count,
                        shadow.last_error,
                        shadow.started_at,
                        shadow.finished_at,
                        shadow.created_at,
                        shadow.updated_at
                    FROM document_version_governance_operation_shadow AS shadow
                    ON CONFLICT (id) DO UPDATE
                    SET file_id = EXCLUDED.file_id,
                        target_file_id = EXCLUDED.target_file_id,
                        operation = EXCLUDED.operation,
                        status = EXCLUDED.status,
                        attempt_count = EXCLUDED.attempt_count,
                        last_error = EXCLUDED.last_error,
                        started_at = EXCLUDED.started_at,
                        finished_at = EXCLUDED.finished_at,
                        created_at = EXCLUDED.created_at,
                        updated_at = EXCLUDED.updated_at;
                    IF (
                        SELECT COUNT(*)
                        FROM document_version_governance_operation_shadow
                    ) <> (
                        SELECT COUNT(*)
                        FROM document_version_governance_operation_shadow AS shadow
                        JOIN ragflow_version_operations AS restored
                            ON restored.id = shadow.id
                        WHERE ROW(
                            restored.file_id,
                            restored.target_file_id,
                            restored.operation,
                            restored.status,
                            restored.attempt_count,
                            restored.last_error,
                            restored.started_at,
                            restored.finished_at,
                            restored.created_at,
                            restored.updated_at
                        ) IS NOT DISTINCT FROM ROW(
                            shadow.file_id,
                            shadow.target_file_id,
                            shadow.operation,
                            shadow.status,
                            shadow.attempt_count,
                            shadow.last_error,
                            shadow.started_at,
                            shadow.finished_at,
                            shadow.created_at,
                            shadow.updated_at
                        )
                    ) THEN
                        RAISE EXCEPTION
                            'version operation shadow rows were not fully restored';
                    END IF;
                END IF;
            END
            $migration$;
            """
        )
    )


def _drop_governance_shadow_tables() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS document_version_governance_operation_shadow"))
    op.execute(sa.text("DROP TABLE IF EXISTS document_version_governance_file_shadow"))
    op.execute(sa.text(f"DROP TABLE IF EXISTS {_VERSION_CONFIG_SHADOW}"))


def _backup_governance_for_downgrade() -> None:
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS document_version_governance_file_shadow (
                file_id uuid PRIMARY KEY,
                baseline_status varchar(40),
                baseline_review_status varchar(40),
                baseline_uploader_id uuid,
                baseline_department_id uuid,
                baseline_ragflow_dataset_id varchar(120),
                baseline_ragflow_document_id varchar(120),
                baseline_ragflow_parse_status varchar(40),
                owner_id uuid,
                series_id uuid NOT NULL,
                version_number integer NOT NULL,
                replaces_file_id uuid,
                replacement_remote_action varchar(20),
                is_current_version boolean NOT NULL,
                remote_visibility varchar(20) NOT NULL,
                version_switch_status varchar(40) NOT NULL,
                version_switch_error varchar(120),
                version_switch_attempt_count integer NOT NULL,
                predecessor_remote_deactivated_at timestamp with time zone,
                local_version_activated_at timestamp with time zone,
                remote_version_activated_at timestamp with time zone
            )
            """
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE document_version_governance_file_shadow "
            "ADD COLUMN IF NOT EXISTS replacement_remote_action varchar(20)"
        )
    )
    op.execute(
        sa.text(
            """
            ALTER TABLE document_version_governance_file_shadow
                ADD COLUMN IF NOT EXISTS baseline_status varchar(40),
                ADD COLUMN IF NOT EXISTS baseline_review_status varchar(40),
                ADD COLUMN IF NOT EXISTS baseline_uploader_id uuid,
                ADD COLUMN IF NOT EXISTS baseline_department_id uuid,
                ADD COLUMN IF NOT EXISTS baseline_ragflow_dataset_id varchar(120),
                ADD COLUMN IF NOT EXISTS baseline_ragflow_document_id varchar(120),
                ADD COLUMN IF NOT EXISTS baseline_ragflow_parse_status varchar(40)
            """
        )
    )
    op.execute(sa.text("TRUNCATE document_version_governance_file_shadow"))
    op.execute(
        sa.text(
            """
            INSERT INTO document_version_governance_file_shadow (
                file_id,
                baseline_status,
                baseline_review_status,
                baseline_uploader_id,
                baseline_department_id,
                baseline_ragflow_dataset_id,
                baseline_ragflow_document_id,
                baseline_ragflow_parse_status,
                owner_id,
                series_id,
                version_number,
                replaces_file_id,
                replacement_remote_action,
                is_current_version,
                remote_visibility,
                version_switch_status,
                version_switch_error,
                version_switch_attempt_count,
                predecessor_remote_deactivated_at,
                local_version_activated_at,
                remote_version_activated_at
            )
            SELECT
                id,
                status,
                review_status,
                uploader_id,
                department_id,
                ragflow_dataset_id,
                ragflow_document_id,
                ragflow_parse_status,
                owner_id,
                series_id,
                version_number,
                replaces_file_id,
                replacement_remote_action,
                is_current_version,
                remote_visibility,
                version_switch_status,
                version_switch_error,
                version_switch_attempt_count,
                predecessor_remote_deactivated_at,
                local_version_activated_at,
                remote_version_activated_at
            FROM files
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS document_version_governance_operation_shadow (
                id uuid PRIMARY KEY,
                file_id uuid NOT NULL,
                target_file_id uuid NOT NULL,
                operation varchar(40) NOT NULL,
                status varchar(20) NOT NULL,
                attempt_count integer NOT NULL,
                last_error varchar(120),
                started_at timestamp with time zone,
                finished_at timestamp with time zone,
                created_at timestamp with time zone NOT NULL,
                updated_at timestamp with time zone NOT NULL
            )
            """
        )
    )
    op.execute(sa.text("TRUNCATE document_version_governance_operation_shadow"))
    op.execute(
        sa.text(
            """
            INSERT INTO document_version_governance_operation_shadow (
                id,
                file_id,
                target_file_id,
                operation,
                status,
                attempt_count,
                last_error,
                started_at,
                finished_at,
                created_at,
                updated_at
            )
            SELECT
                id,
                file_id,
                target_file_id,
                operation,
                status,
                attempt_count,
                last_error,
                started_at,
                finished_at,
                created_at,
                updated_at
            FROM ragflow_version_operations
            """
        )
    )


def _add_file_governance_constraints() -> None:
    op.create_foreign_key(
        "fk_files_owner_id_users",
        "files",
        "users",
        ["owner_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_files_series_id_files",
        "files",
        "files",
        ["series_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_files_replaces_file_id_files",
        "files",
        "files",
        ["replaces_file_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    checks = (
        ("ck_files_version_number_positive", "version_number > 0"),
        (
            "ck_files_remote_visibility",
            "remote_visibility IN ('candidate', 'current', 'not_current', 'unknown')",
        ),
        (
            "ck_files_version_switch_status",
            "version_switch_status IN ('not_required', 'pending', "
            "'old_remote_deactivated', 'local_switched', 'completed', "
            "'failed_old_deactivate', 'failed_new_activate')",
        ),
        (
            "ck_files_version_switch_attempt_count_non_negative",
            "version_switch_attempt_count >= 0",
        ),
        (
            "ck_files_replacement_version_consistent",
            "(replaces_file_id IS NULL AND version_number = 1) OR "
            "(replaces_file_id IS NOT NULL AND version_number > 1)",
        ),
        (
            "ck_files_replacement_remote_action",
            "(replaces_file_id IS NULL AND replacement_remote_action IS NULL) OR "
            "(replaces_file_id IS NOT NULL AND "
            "replacement_remote_action IN ('delete', 'archive'))",
        ),
        (
            "ck_files_replacement_not_self",
            "replaces_file_id IS NULL OR replaces_file_id <> id",
        ),
    )
    for name, condition in checks:
        op.create_check_constraint(name, "files", condition)
    op.create_index("idx_files_owner_id", "files", ["owner_id"])
    op.create_index(
        "idx_files_series_version",
        "files",
        ["series_id", "version_number"],
        unique=True,
    )
    op.create_index(
        "uq_files_replaces_file_id",
        "files",
        ["replaces_file_id"],
        unique=True,
        postgresql_where=sa.text(
            "replaces_file_id IS NOT NULL AND "
            "status NOT IN ('deleted', 'disabled', 'ragflow_cleanup_failed')"
        ),
    )
    op.create_index(
        "uq_files_current_version_per_series",
        "files",
        ["series_id"],
        unique=True,
        postgresql_where=sa.text("is_current_version"),
    )
    op.execute(
        sa.text(
            """
            CREATE FUNCTION enforce_file_version_chain() RETURNS trigger AS $$
            DECLARE
                predecessor_series uuid;
                predecessor_version integer;
                predecessor_department uuid;
                predecessor_uploader uuid;
                series_max integer;
            BEGIN
                IF TG_OP = 'UPDATE' AND (
                    NEW.series_id IS DISTINCT FROM OLD.series_id OR
                    NEW.version_number IS DISTINCT FROM OLD.version_number OR
                    NEW.replaces_file_id IS DISTINCT FROM OLD.replaces_file_id OR
                    NEW.replacement_remote_action IS DISTINCT FROM OLD.replacement_remote_action OR
                    NEW.department_id IS DISTINCT FROM OLD.department_id OR
                    NEW.uploader_id IS DISTINCT FROM OLD.uploader_id
                ) THEN
                    RAISE EXCEPTION 'file version identity is immutable';
                END IF;
                IF NEW.replaces_file_id IS NULL THEN
                    IF NEW.series_id <> NEW.id OR NEW.version_number <> 1 OR
                       NEW.replacement_remote_action IS NOT NULL THEN
                        RAISE EXCEPTION 'root version identity is invalid';
                    END IF;
                    RETURN NEW;
                END IF;
                SELECT series_id, version_number, department_id, uploader_id
                INTO predecessor_series, predecessor_version,
                     predecessor_department, predecessor_uploader
                FROM files WHERE id = NEW.replaces_file_id FOR SHARE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'predecessor does not exist';
                END IF;
                IF NEW.replacement_remote_action NOT IN ('delete', 'archive') THEN
                    RAISE EXCEPTION 'replacement remote action is invalid';
                END IF;
                IF predecessor_series <> NEW.series_id OR
                   predecessor_version >= NEW.version_number OR
                   predecessor_department <> NEW.department_id OR
                   predecessor_uploader <> NEW.uploader_id THEN
                    RAISE EXCEPTION 'replacement chain identity is invalid';
                END IF;
                IF TG_OP = 'INSERT' THEN
                    SELECT COALESCE(MAX(version_number), 0)
                    INTO series_max FROM files WHERE series_id = NEW.series_id;
                    IF NEW.version_number <> series_max + 1 THEN
                        RAISE EXCEPTION 'replacement version must append to series';
                    END IF;
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_files_version_chain BEFORE INSERT OR UPDATE OF "
            "series_id, version_number, replaces_file_id, replacement_remote_action, "
            "department_id, uploader_id ON files FOR EACH ROW "
            "EXECUTE FUNCTION enforce_file_version_chain()"
        )
    )


def _create_version_operation_table() -> None:
    op.create_table(
        "ragflow_version_operations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "file_id",
            sa.Uuid(),
            sa.ForeignKey("files.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_file_id",
            sa.Uuid(),
            sa.ForeignKey("files.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("operation", sa.String(length=40), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("last_error", sa.String(length=120)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "operation IN ('deactivate_predecessor', 'activate_candidate')",
            name="ck_ragflow_version_operations_operation",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'unknown')",
            name="ck_ragflow_version_operations_status",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_ragflow_version_operations_attempt_count_non_negative",
        ),
        sa.UniqueConstraint(
            "file_id",
            "operation",
            name="uq_ragflow_version_operations_file_operation",
        ),
    )
    op.create_index(
        "idx_ragflow_version_operations_file_id",
        "ragflow_version_operations",
        ["file_id"],
    )
    op.create_index(
        "idx_ragflow_version_operations_status",
        "ragflow_version_operations",
        ["status"],
    )


def downgrade() -> None:
    _backup_governance_for_downgrade()
    _backup_version_config_for_downgrade()
    op.drop_index(
        "idx_ragflow_version_operations_status",
        table_name="ragflow_version_operations",
    )
    op.drop_index(
        "idx_ragflow_version_operations_file_id",
        table_name="ragflow_version_operations",
    )
    op.drop_table("ragflow_version_operations")
    op.execute(sa.text("DROP TRIGGER trg_files_version_chain ON files"))
    op.execute(sa.text("DROP FUNCTION enforce_file_version_chain()"))
    op.drop_index("uq_files_current_version_per_series", table_name="files")
    op.drop_index("uq_files_replaces_file_id", table_name="files")
    op.drop_index("idx_files_series_version", table_name="files")
    op.drop_index("idx_files_owner_id", table_name="files")
    for name in (
        "ck_files_replacement_not_self",
        "ck_files_replacement_remote_action",
        "ck_files_replacement_version_consistent",
        "ck_files_version_switch_attempt_count_non_negative",
        "ck_files_version_switch_status",
        "ck_files_remote_visibility",
        "ck_files_version_number_positive",
    ):
        op.drop_constraint(name, "files", type_="check")
    for name in (
        "fk_files_replaces_file_id_files",
        "fk_files_series_id_files",
        "fk_files_owner_id_users",
    ):
        op.drop_constraint(name, "files", type_="foreignkey")
    for column in (
        "remote_version_activated_at",
        "local_version_activated_at",
        "predecessor_remote_deactivated_at",
        "version_switch_attempt_count",
        "version_switch_error",
        "version_switch_status",
        "remote_visibility",
        "is_current_version",
        "replaces_file_id",
        "replacement_remote_action",
        "version_number",
        "series_id",
        "owner_id",
    ):
        op.drop_column("files", column)
    _delete_version_config()
