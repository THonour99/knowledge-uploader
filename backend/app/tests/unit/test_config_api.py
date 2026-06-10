from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncGenerator
from typing import cast
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from redis.asyncio import from_url
from sqlalchemy import select

pytestmark = pytest.mark.asyncio

EXPECTED_GROUP_KEYS: dict[str, set[str]] = {
    "upload": {
        "upload.allowed_extensions",
        "upload.max_file_size_mb",
        "upload.user_quota_mb",
        "upload.allow_multi_file",
        "upload.allow_user_delete",
        "upload.enable_duplicate_check",
    },
    "processing": {
        "processing.auto_parse_on_upload",
        "processing.auto_sync_after_parse",
        "processing.sync_after_ai_analysis",
        "processing.task_max_retries",
        "processing.task_timeout_seconds",
        "processing.parse_max_pages",
        "processing.parse_max_chars",
    },
    "security": {
        "security.allowed_email_domains",
        "security.password_min_length",
        "security.login_max_failed_attempts",
        "security.login_lock_minutes",
        "security.require_email_verification",
        "security.require_review_before_sync",
        "security.block_critical_sensitive_sync",
    },
    "basic": {
        "basic.system_name",
        "basic.system_logo_url",
        "basic.default_language",
        "basic.default_timezone",
        "basic.notification_channels",
        "basic.admin_contact_email",
    },
    "ragflow": {
        "ragflow.base_url",
        "ragflow.api_key",
        "ragflow.default_dataset_id",
        "ragflow.auto_sync_enabled",
        "ragflow.sync_max_retries",
        "ragflow.sync_timeout_seconds",
        "ragflow.allow_high_risk_sync",
        "ragflow.delete_remote_on_file_delete",
        "ragflow.keep_remote_on_archive",
    },
}


async def _reset_database() -> None:
    from importlib import import_module

    import_module("app.db.models")

    from app.core.database import engine
    from app.db.base import Base

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()

    redis_client = from_url(  # type: ignore[no-untyped-call]
        os.environ["CACHE_REDIS_URL"],
        encoding="utf-8",
        decode_responses=True,
    )
    try:
        await redis_client.flushdb()
    finally:
        await redis_client.aclose()


@pytest.fixture(autouse=True)
async def clean_database() -> AsyncGenerator[None, None]:
    await _reset_database()
    yield
    from app.core.database import engine
    from app.db.base import Base

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def config_client() -> AsyncGenerator[AsyncClient, None]:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.config import Settings
    from app.core.database import AsyncSessionFactory, get_session
    from app.core.deps import get_app_settings
    from app.main import app

    settings = Settings(
        allowed_email_domains="company.com",
        jwt_secret="test-jwt-secret-with-more-than-32-bytes",
        cache_redis_url=os.environ["CACHE_REDIS_URL"],
        require_email_verification=False,
    )

    async def override_session() -> AsyncGenerator[AsyncSession, None]:
        async with AsyncSessionFactory() as session:
            yield session

    app.dependency_overrides[get_app_settings] = lambda: settings
    app.dependency_overrides[get_session] = override_session
    transport = ASGITransport(app=app)  # type: ignore[arg-type]
    async with AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client
    app.dependency_overrides.clear()


async def _create_user(*, email: str, password: str, role: str = "employee") -> UUID:
    from app.core.database import AsyncSessionFactory
    from app.core.security import hash_password
    from app.modules.user.models import User

    normalized_email = email.lower()
    user = User(
        name=email.split("@", 1)[0],
        email=normalized_email,
        email_domain=normalized_email.rsplit("@", 1)[1],
        password_hash=hash_password(password),
        role=role,
        status="active",
        email_verified=True,
    )
    async with AsyncSessionFactory() as session:
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user.id


async def _login(client: AsyncClient, *, email: str, password: str) -> str:
    response = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    return str(response.json()["data"]["access_token"])


async def _get_group(client: AsyncClient, token: str, group: str) -> dict[str, object]:
    response = await client.get(
        "/api/admin/configs",
        params={"group": group},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    return cast(dict[str, object], response.json()["data"])


def _items_by_key(group_data: dict[str, object]) -> dict[str, dict[str, object]]:
    items = cast(list[dict[str, object]], group_data["items"])
    return {cast(str, item["key"]): item for item in items}


async def test_system_admin_reads_all_config_groups_with_seed_items(
    config_client: AsyncClient,
) -> None:
    await _create_user(
        email="config-system-admin@company.com",
        password="password123",
        role="system_admin",
    )
    token = await _login(
        config_client,
        email="config-system-admin@company.com",
        password="password123",
    )

    for group, expected_keys in EXPECTED_GROUP_KEYS.items():
        group_data = await _get_group(config_client, token, group)
        assert group_data["group"] == group
        items = _items_by_key(group_data)
        assert set(items) == expected_keys

    upload_items = _items_by_key(await _get_group(config_client, token, "upload"))
    assert upload_items["upload.max_file_size_mb"]["value"] == 50
    assert upload_items["upload.max_file_size_mb"]["value_type"] == "int"
    assert upload_items["upload.allowed_extensions"]["value"] == [
        "pdf",
        "docx",
        "xlsx",
        "pptx",
        "txt",
        "md",
        "csv",
    ]
    assert upload_items["upload.allow_user_delete"]["value"] is False

    security_items = _items_by_key(await _get_group(config_client, token, "security"))
    assert security_items["security.allowed_email_domains"]["value"] == ["company.com"]
    assert security_items["security.login_max_failed_attempts"]["value"] == 5

    ragflow_items = _items_by_key(await _get_group(config_client, token, "ragflow"))
    api_key_item = ragflow_items["ragflow.api_key"]
    assert api_key_item["is_secret"] is True
    assert api_key_item["value"] is None
    assert api_key_item["masked_value"] is None


async def test_knowledge_admin_can_read_and_employee_cannot(
    config_client: AsyncClient,
) -> None:
    await _create_user(
        email="config-knowledge-admin@company.com",
        password="password123",
        role="knowledge_admin",
    )
    await _create_user(email="config-employee@company.com", password="password123")
    admin_token = await _login(
        config_client,
        email="config-knowledge-admin@company.com",
        password="password123",
    )
    employee_token = await _login(
        config_client,
        email="config-employee@company.com",
        password="password123",
    )

    admin_response = await config_client.get(
        "/api/admin/configs",
        params={"group": "upload"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    employee_response = await config_client.get(
        "/api/admin/configs",
        params={"group": "upload"},
        headers={"Authorization": f"Bearer {employee_token}"},
    )

    assert admin_response.status_code == 200
    assert set(_items_by_key(admin_response.json()["data"])) == EXPECTED_GROUP_KEYS["upload"]
    assert employee_response.status_code == 403
    assert employee_response.json()["error_code"] == "PERMISSION_DENIED"


async def test_system_admin_updates_upload_group_writes_audit_and_outbox(
    config_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.audit.models import AuditLog

    actor_id = await _create_user(
        email="config-update-admin@company.com",
        password="password123",
        role="system_admin",
    )
    token = await _login(
        config_client,
        email="config-update-admin@company.com",
        password="password123",
    )

    response = await config_client.put(
        "/api/admin/configs/upload",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "items": {
                "upload.max_file_size_mb": 120,
                "upload.allow_user_delete": True,
            }
        },
    )

    assert response.status_code == 200
    updated_items = _items_by_key(response.json()["data"])
    assert updated_items["upload.max_file_size_mb"]["value"] == 120
    assert updated_items["upload.allow_user_delete"]["value"] is True

    reread_items = _items_by_key(await _get_group(config_client, token, "upload"))
    assert reread_items["upload.max_file_size_mb"]["value"] == 120
    assert reread_items["upload.allow_user_delete"]["value"] is True

    async with AsyncSessionFactory() as session:
        audit_result = await session.execute(
            select(AuditLog).where(AuditLog.action == "config.update")
        )
        audit_logs = list(audit_result.scalars())
        event_result = await session.execute(
            select(EventOutbox).where(EventOutbox.event_type == "config.settings.updated")
        )
        outbox_events = list(event_result.scalars())

    assert len(audit_logs) == 1
    audit_log = audit_logs[0]
    assert audit_log.actor_id == actor_id
    assert audit_log.target_id == uuid.uuid5(uuid.NAMESPACE_URL, "system-config-group:upload")
    assert audit_log.metadata_json["group"] == "upload"
    assert sorted(cast(list[str], audit_log.metadata_json["keys"])) == [
        "upload.allow_user_delete",
        "upload.max_file_size_mb",
    ]

    assert len(outbox_events) == 1
    outbox_event = outbox_events[0]
    assert outbox_event.aggregate_type == "config"
    assert outbox_event.aggregate_id == "upload"
    assert outbox_event.payload["group"] == "upload"
    assert sorted(cast(list[str], outbox_event.payload["keys"])) == [
        "upload.allow_user_delete",
        "upload.max_file_size_mb",
    ]


async def test_knowledge_admin_cannot_update_configs(config_client: AsyncClient) -> None:
    await _create_user(
        email="config-readonly-admin@company.com",
        password="password123",
        role="knowledge_admin",
    )
    token = await _login(
        config_client,
        email="config-readonly-admin@company.com",
        password="password123",
    )

    response = await config_client.put(
        "/api/admin/configs/upload",
        headers={"Authorization": f"Bearer {token}"},
        json={"items": {"upload.max_file_size_mb": 200}},
    )

    assert response.status_code == 403
    assert response.json()["error_code"] == "PERMISSION_DENIED"

    items = _items_by_key(await _get_group(config_client, token, "upload"))
    assert items["upload.max_file_size_mb"]["value"] == 50


async def test_secret_config_masked_in_response_and_encrypted_in_db(
    config_client: AsyncClient,
) -> None:
    from app.core.config import get_settings
    from app.core.database import AsyncSessionFactory
    from app.core.security import decrypt_secret
    from app.modules.audit.models import AuditLog
    from app.modules.config.models import SystemConfig

    plaintext = "sk-config-secret-abcd"
    await _create_user(
        email="config-secret-admin@company.com",
        password="password123",
        role="system_admin",
    )
    token = await _login(
        config_client,
        email="config-secret-admin@company.com",
        password="password123",
    )

    response = await config_client.put(
        "/api/admin/configs/ragflow",
        headers={"Authorization": f"Bearer {token}"},
        json={"items": {"ragflow.api_key": plaintext}},
    )

    assert response.status_code == 200
    updated_item = _items_by_key(response.json()["data"])["ragflow.api_key"]
    assert updated_item["value"] is None
    assert updated_item["masked_value"] == "sk-****abcd"

    reread_item = _items_by_key(await _get_group(config_client, token, "ragflow"))[
        "ragflow.api_key"
    ]
    assert reread_item["value"] is None
    assert reread_item["masked_value"] == "sk-****abcd"

    async with AsyncSessionFactory() as session:
        row_result = await session.execute(
            select(SystemConfig).where(SystemConfig.key == "ragflow.api_key")
        )
        row = row_result.scalar_one()
        audit_result = await session.execute(
            select(AuditLog).where(AuditLog.action == "config.update")
        )
        audit_logs = list(audit_result.scalars())

    stored_value = cast(str, row.value)
    assert isinstance(stored_value, str)
    assert stored_value != plaintext
    assert plaintext not in stored_value
    assert decrypt_secret(stored_value, get_settings().encryption_key) == plaintext

    assert len(audit_logs) == 1
    metadata_dump = json.dumps(audit_logs[0].metadata_json, ensure_ascii=False)
    assert plaintext not in metadata_dump
    assert "ragflow.api_key" in cast(list[str], audit_logs[0].metadata_json["keys"])

    clear_response = await config_client.put(
        "/api/admin/configs/ragflow",
        headers={"Authorization": f"Bearer {token}"},
        json={"items": {"ragflow.api_key": ""}},
    )

    assert clear_response.status_code == 200
    cleared_item = _items_by_key(clear_response.json()["data"])["ragflow.api_key"]
    assert cleared_item["value"] is None
    assert cleared_item["masked_value"] is None

    async with AsyncSessionFactory() as session:
        cleared_result = await session.execute(
            select(SystemConfig).where(SystemConfig.key == "ragflow.api_key")
        )
        cleared_row = cleared_result.scalar_one()

    assert cleared_row.value is None


async def test_update_rejects_invalid_values_and_unknown_keys(
    config_client: AsyncClient,
) -> None:
    await _create_user(
        email="config-validation-admin@company.com",
        password="password123",
        role="system_admin",
    )
    token = await _login(
        config_client,
        email="config-validation-admin@company.com",
        password="password123",
    )

    wrong_type_response = await config_client.put(
        "/api/admin/configs/upload",
        headers={"Authorization": f"Bearer {token}"},
        json={"items": {"upload.max_file_size_mb": "abc"}},
    )
    negative_response = await config_client.put(
        "/api/admin/configs/upload",
        headers={"Authorization": f"Bearer {token}"},
        json={"items": {"upload.max_file_size_mb": -5}},
    )
    over_max_response = await config_client.put(
        "/api/admin/configs/upload",
        headers={"Authorization": f"Bearer {token}"},
        json={"items": {"upload.max_file_size_mb": 20000}},
    )
    unknown_key_response = await config_client.put(
        "/api/admin/configs/upload",
        headers={"Authorization": f"Bearer {token}"},
        json={"items": {"upload.not_a_real_key": 1}},
    )
    cross_group_key_response = await config_client.put(
        "/api/admin/configs/upload",
        headers={"Authorization": f"Bearer {token}"},
        json={"items": {"security.password_min_length": 10}},
    )
    unknown_group_put_response = await config_client.put(
        "/api/admin/configs/not-a-group",
        headers={"Authorization": f"Bearer {token}"},
        json={"items": {"upload.max_file_size_mb": 80}},
    )
    unknown_group_get_response = await config_client.get(
        "/api/admin/configs",
        params={"group": "not-a-group"},
        headers={"Authorization": f"Bearer {token}"},
    )

    for response in (
        wrong_type_response,
        negative_response,
        over_max_response,
        unknown_key_response,
        cross_group_key_response,
    ):
        assert response.status_code == 400
        assert response.json()["error_code"] == "VALIDATION_ERROR"

    assert unknown_group_put_response.status_code in {404, 422}
    assert unknown_group_get_response.status_code in {404, 422}

    items = _items_by_key(await _get_group(config_client, token, "upload"))
    assert items["upload.max_file_size_mb"]["value"] == 50
