from __future__ import annotations

from importlib import import_module

from sqlalchemy import MetaData


def _current_metadata() -> MetaData:
    from app.db.base import Base

    return Base.metadata


def test_phase1_auth_user_tables_are_registered_in_metadata() -> None:
    import_module("app.db.models")
    metadata = _current_metadata()

    expected_tables = {
        "users",
        "email_verification_tokens",
        "password_reset_tokens",
    }

    assert expected_tables <= set(metadata.tables)


def test_users_table_has_auth_constraints_and_indexes() -> None:
    import_module("app.modules.user.models")
    metadata = _current_metadata()

    table = metadata.tables["users"]

    assert {"email", "email_domain", "password_hash", "role", "status"} <= set(table.columns.keys())
    assert {column.name for column in table.primary_key.columns} == {"id"}
    assert {index.name for index in table.indexes if index.unique} >= {
        "uq_users_email",
    }
    constraint_names = {constraint.name for constraint in table.constraints}
    assert {
        "ck_users_role",
        "ck_users_status",
        "ck_users_auth_provider",
        "ck_users_email_lowercase",
        "ck_users_email_domain_lowercase",
        "ck_users_failed_login_count_non_negative",
    } <= constraint_names


def test_auth_token_tables_store_hashes_not_plain_tokens() -> None:
    import_module("app.modules.auth.models")
    metadata = _current_metadata()

    for table_name in ("email_verification_tokens", "password_reset_tokens"):
        table = metadata.tables[table_name]

        assert "token_hash" in table.columns
        assert "token" not in table.columns
        assert {"user_id", "expires_at", "used_at", "created_at"} <= set(table.columns.keys())
        assert any(index.name == f"idx_{table_name}_user_id" for index in table.indexes)
        assert f"ck_{table_name}_token_hash_sha256_hex" in {
            constraint.name for constraint in table.constraints
        }
