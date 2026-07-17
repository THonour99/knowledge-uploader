from __future__ import annotations

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


def _reset_schema() -> None:
    engine = create_engine(TEST_ALEMBIC_DATABASE_URL)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
    finally:
        engine.dispose()


def test_saved_views_upgrade_downgrade_from_l001() -> None:
    config = _alembic_config()
    _reset_schema()
    try:
        command.upgrade(config, "20260716l001")
        engine = create_engine(TEST_ALEMBIC_DATABASE_URL)
        try:
            assert "saved_views" not in inspect(engine).get_table_names()
        finally:
            engine.dispose()

        command.upgrade(config, "20260716s001")
        engine = create_engine(TEST_ALEMBIC_DATABASE_URL)
        try:
            inspector = inspect(engine)
            assert "saved_views" in inspector.get_table_names()
            assert {
                "id",
                "owner_id",
                "scope",
                "department_id",
                "page_key",
                "name",
                "definition_schema_version",
                "query_definition",
                "column_preferences",
                "row_version",
                "created_at",
                "updated_at",
            } == {column["name"] for column in inspector.get_columns("saved_views")}
            check_names = {
                constraint["name"] for constraint in inspector.get_check_constraints("saved_views")
            }
            assert "ck_saved_views_department_page_scope" in check_names
            index_names = {index["name"] for index in inspector.get_indexes("saved_views")}
            assert {
                "idx_saved_views_owner_page",
                "idx_saved_views_department_page",
                "uq_saved_views_private_name",
                "uq_saved_views_department_name",
            } <= index_names

            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO users "
                        "(id, name, email, email_domain, password_hash, role, status, "
                        "email_verified) VALUES "
                        "('00000000-0000-0000-0000-000000000099', 'saved-view-test', "
                        "'saved-view-test@company.com', 'company.com', 'hash', "
                        "'employee', 'active', true)"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO departments (id, name, code, status) VALUES "
                        "('00000000-0000-0000-0000-000000000098', "
                        "'saved-view-department', 'saved-view-department', 'active')"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO saved_views "
                        "(id, owner_id, scope, page_key, name, definition_schema_version, "
                        "query_definition, column_preferences) VALUES "
                        "('00000000-0000-0000-0000-000000000101', "
                        "'00000000-0000-0000-0000-000000000099', 'private', "
                        "'my_files', 'default', 2, '{}'::jsonb, '{}'::jsonb)"
                    )
                )
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO saved_views "
                            "(id, owner_id, scope, page_key, name, definition_schema_version, "
                            "query_definition, column_preferences) VALUES "
                            "('00000000-0000-0000-0000-000000000102', "
                            "'00000000-0000-0000-0000-000000000099', 'private', "
                            "'my_files', 'invalid-json-shape', 2, '[]'::jsonb, '{}'::jsonb)"
                        )
                    )
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO saved_views "
                            "(id, owner_id, scope, department_id, page_key, name, "
                            "definition_schema_version, query_definition, "
                            "column_preferences) VALUES "
                            "('00000000-0000-0000-0000-000000000103', "
                            "'00000000-0000-0000-0000-000000000099', 'department', "
                            "'00000000-0000-0000-0000-000000000098', "
                            "'my_files', 'invalid-department-page', 2, "
                            "'{}'::jsonb, '{}'::jsonb)"
                        )
                    )
            with engine.connect() as connection:
                original = (
                    connection.execute(
                        text(
                            "SELECT id, owner_id, scope, department_id, page_key, name, "
                            "definition_schema_version, query_definition, "
                            "column_preferences, row_version, created_at, updated_at "
                            "FROM saved_views"
                        )
                    )
                    .mappings()
                    .one()
                )
        finally:
            engine.dispose()

        command.downgrade(config, "20260716l001")
        engine = create_engine(TEST_ALEMBIC_DATABASE_URL)
        try:
            table_names = set(inspect(engine).get_table_names())
            assert "saved_views" not in table_names
            assert "saved_views_rollback_backup" in table_names
            with engine.connect() as connection:
                backup = (
                    connection.execute(
                        text(
                            "SELECT id, owner_id, scope, page_key, name, "
                            "definition_schema_version, query_definition, "
                            "column_preferences, row_version, department_id, "
                            "created_at, updated_at "
                            "FROM saved_views_rollback_backup"
                        )
                    )
                    .mappings()
                    .one()
                )
            assert str(backup["id"]) == "00000000-0000-0000-0000-000000000101"
            assert str(backup["owner_id"]) == "00000000-0000-0000-0000-000000000099"
            assert backup["scope"] == "private"
            assert backup["page_key"] == "my_files"
            assert backup["name"] == "default"
            assert backup["definition_schema_version"] == 2
            assert backup["query_definition"] == {}
            assert backup["column_preferences"] == {}
            assert backup["row_version"] == 1
            assert backup["department_id"] == original["department_id"]
            assert backup["created_at"] == original["created_at"]
            assert backup["updated_at"] == original["updated_at"]
        finally:
            engine.dispose()
        command.upgrade(config, "20260716s001")
        engine = create_engine(TEST_ALEMBIC_DATABASE_URL)
        try:
            table_names = set(inspect(engine).get_table_names())
            assert "saved_views" in table_names
            assert "saved_views_rollback_backup" not in table_names
            with engine.connect() as connection:
                revision = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                restored = (
                    connection.execute(
                        text(
                            "SELECT id, owner_id, scope, page_key, name, "
                            "definition_schema_version, query_definition, "
                            "column_preferences, row_version, department_id, "
                            "created_at, updated_at "
                            "FROM saved_views"
                        )
                    )
                    .mappings()
                    .one()
                )
            assert revision == "20260716s001"
            assert str(restored["id"]) == "00000000-0000-0000-0000-000000000101"
            assert str(restored["owner_id"]) == "00000000-0000-0000-0000-000000000099"
            assert restored["scope"] == "private"
            assert restored["page_key"] == "my_files"
            assert restored["name"] == "default"
            assert restored["definition_schema_version"] == 2
            assert restored["query_definition"] == {}
            assert restored["column_preferences"] == {}
            assert restored["row_version"] == 1
            assert restored["department_id"] == original["department_id"]
            assert restored["created_at"] == original["created_at"]
            assert restored["updated_at"] == original["updated_at"]
        finally:
            engine.dispose()
    finally:
        _reset_schema()
