from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from redis.asyncio import from_url
from sqlalchemy import select

pytestmark = pytest.mark.asyncio

EXPECTED_GROUP_KEYS: dict[str, set[str]] = {
    "upload": {
        "upload.enabled",
        "upload.allowed_extensions",
        "upload.max_file_size_mb",
        "upload.user_quota_mb",
        "upload.allow_multi_file",
        "upload.allow_user_delete",
    },
    "processing": {
        "processing.parse_max_pages",
        "processing.parse_max_chars",
    },
    "outbox": {"outbox.publish_max_retries"},
    "security": {
        "security.allowed_email_domains",
        "security.password_min_length",
        "security.login_max_failed_attempts",
        "security.login_lock_minutes",
        "security.require_email_verification",
        "security.block_critical_sensitive_sync",
    },
    "review": {
        "review.claim_timeout_minutes",
        "review.sla_hours",
    },
    "ragflow": {
        "ragflow.base_url",
        "ragflow.api_key",
        "ragflow.allowed_dataset_ids",
        "ragflow.sync_max_retries",
        "ragflow.parse_poll_timeout_seconds",
        "ragflow.sync_timeout_seconds",
        "ragflow.allow_high_risk_sync",
        "ragflow.delete_remote_on_file_delete",
        "ragflow.keep_remote_on_archive",
        "ragflow.keep_replaced_remote",
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
    assert upload_items["upload.enabled"]["value"] is True

    security_items = _items_by_key(await _get_group(config_client, token, "security"))
    assert security_items["security.allowed_email_domains"]["value"] == ["company.com"]
    assert security_items["security.login_max_failed_attempts"]["value"] == 5
    assert security_items["security.require_email_verification"]["value"] is False
    assert security_items["security.block_critical_sensitive_sync"]["immutable"] is True

    review_items = _items_by_key(await _get_group(config_client, token, "review"))
    assert review_items["review.claim_timeout_minutes"]["value"] == 30
    assert review_items["review.sla_hours"]["value"] == 24
    assert "已有领取不追溯缩短" in cast(
        str,
        review_items["review.claim_timeout_minutes"]["description"],
    )
    assert "已有截止时间不追溯缩短" in cast(
        str,
        review_items["review.sla_hours"]["description"],
    )

    ragflow_items = _items_by_key(await _get_group(config_client, token, "ragflow"))
    api_key_item = ragflow_items["ragflow.api_key"]
    assert api_key_item["is_secret"] is True
    assert api_key_item["value"] is None
    assert api_key_item["masked_value"] is None


async def test_review_config_enforces_snapshot_contract_bounds(
    config_client: AsyncClient,
) -> None:
    await _create_user(
        email="config-review-admin@company.com",
        password="password123",
        role="system_admin",
    )
    token = await _login(
        config_client,
        email="config-review-admin@company.com",
        password="password123",
    )

    response = await config_client.put(
        "/api/admin/configs/review",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "items": {
                "review.claim_timeout_minutes": 5,
                "review.sla_hours": 720,
            }
        },
    )

    assert response.status_code == 200
    items = _items_by_key(response.json()["data"])
    assert items["review.claim_timeout_minutes"]["value"] == 5
    assert items["review.sla_hours"]["value"] == 720

    for key, invalid_value in (
        ("review.claim_timeout_minutes", 4),
        ("review.claim_timeout_minutes", 1441),
        ("review.sla_hours", 0),
        ("review.sla_hours", 721),
    ):
        invalid_response = await config_client.put(
            "/api/admin/configs/review",
            headers={"Authorization": f"Bearer {token}"},
            json={"items": {key: invalid_value}},
        )
        assert invalid_response.status_code == 400
        assert invalid_response.json()["error_code"] == "VALIDATION_ERROR"


async def test_update_response_does_not_query_database_after_commit(
    config_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.config.schemas import ConfigGroupResponse
    from app.modules.config.service import ConfigService  # noqa: TID251 - config service contract

    await _create_user(
        email="config-commit-boundary-admin@company.com",
        password="password123",
        role="system_admin",
    )
    token = await _login(
        config_client,
        email="config-commit-boundary-admin@company.com",
        password="password123",
    )

    async def fail_post_commit_query(
        _service: ConfigService,
        _group: str,
    ) -> ConfigGroupResponse:
        raise AssertionError("update_group queried its response after commit")

    monkeypatch.setattr(ConfigService, "_group_response", fail_post_commit_query)

    response = await config_client.put(
        "/api/admin/configs/upload",
        headers={"Authorization": f"Bearer {token}"},
        json={"items": {"upload.max_file_size_mb": 73}},
    )

    assert response.status_code == 200
    assert _items_by_key(response.json()["data"])["upload.max_file_size_mb"]["value"] == 73


async def test_dept_admin_and_employee_cannot_read_configs(
    config_client: AsyncClient,
) -> None:
    await _create_user(
        email="config-knowledge-admin@company.com",
        password="password123",
        role="dept_admin",
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

    assert admin_response.status_code == 403
    assert admin_response.json()["error_code"] == "PERMISSION_DENIED"
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


async def test_dept_admin_cannot_update_configs(config_client: AsyncClient) -> None:
    await _create_user(
        email="config-verify-admin@company.com",
        password="password123",
        role="system_admin",
    )
    await _create_user(
        email="config-readonly-admin@company.com",
        password="password123",
        role="dept_admin",
    )
    system_token = await _login(
        config_client,
        email="config-verify-admin@company.com",
        password="password123",
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

    items = _items_by_key(await _get_group(config_client, system_token, "upload"))
    assert items["upload.max_file_size_mb"]["value"] == 50


async def test_system_admin_can_set_ragflow_api_key_before_runtime_dataset_allowlist(
    config_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import get_settings

    monkeypatch.delenv("RAGFLOW_ALLOWED_DATASET_IDS", raising=False)
    get_settings.cache_clear()
    await _create_user(
        email="config-no-allowlist-admin@company.com",
        password="password123",
        role="system_admin",
    )
    token = await _login(
        config_client,
        email="config-no-allowlist-admin@company.com",
        password="password123",
    )

    response = await config_client.put(
        "/api/admin/configs/ragflow",
        headers={"Authorization": f"Bearer {token}"},
        json={"items": {"ragflow.api_key": "sk-runtime-secret-abcd"}},
    )

    assert response.status_code == 200
    items = _items_by_key(response.json()["data"])
    assert items["ragflow.api_key"]["masked_value"] == "sk-****abcd"
    assert items["ragflow.allowed_dataset_ids"]["value"] == []


async def test_secret_config_masked_in_response_and_encrypted_in_db(
    config_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import get_settings
    from app.core.database import AsyncSessionFactory
    from app.core.security import decrypt_secret
    from app.modules.audit.models import AuditLog
    from app.modules.config.models import SystemConfig

    plaintext = "sk-config-secret-abcd"
    fallback_secret = "sk-env-fallback-abcd"
    monkeypatch.setenv("RAGFLOW_ALLOWED_DATASET_IDS", "config-dataset")
    monkeypatch.setenv("RAGFLOW_API_KEY", fallback_secret)
    get_settings.cache_clear()
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

    endpoint_response = await config_client.put(
        "/api/admin/configs/ragflow",
        headers={"Authorization": f"Bearer {token}"},
        json={"items": {"ragflow.base_url": "https://attacker.invalid/capture"}},
    )
    assert endpoint_response.status_code == 400
    assert endpoint_response.json()["error_code"] == "VALIDATION_ERROR"
    assert plaintext not in json.dumps(endpoint_response.json())

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

    from app.core import runtime_config

    assert await runtime_config.get_config("ragflow.api_key") == fallback_secret

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


async def test_critical_sync_block_is_exposed_as_immutable(
    config_client: AsyncClient,
) -> None:
    await _create_user(
        email="config-invariant-admin@company.com",
        password="password123",
        role="system_admin",
    )
    token = await _login(
        config_client,
        email="config-invariant-admin@company.com",
        password="password123",
    )

    for value in (True, False):
        response = await config_client.put(
            "/api/admin/configs/security",
            headers={"Authorization": f"Bearer {token}"},
            json={"items": {"security.block_critical_sensitive_sync": value}},
        )
        assert response.status_code == 400
        assert response.json()["message"] == (
            "config key is immutable: security.block_critical_sensitive_sync"
        )


async def test_email_verification_environment_floor_is_effective_and_cannot_be_relaxed(
    config_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core import runtime_config
    from app.core.config import Settings
    from app.core.database import AsyncSessionFactory
    from app.modules.audit.models import AuditLog
    from app.modules.config import service as config_service  # noqa: TID251 - same module
    from app.modules.config.models import SystemConfig

    await _create_user(
        email="config-email-floor-admin@company.com",
        password="password123",
        role="system_admin",
    )
    token = await _login(
        config_client,
        email="config-email-floor-admin@company.com",
        password="password123",
    )
    async with AsyncSessionFactory() as session:
        session.add(
            SystemConfig(
                key="security.require_email_verification",
                group="security",
                value=False,
                value_type="bool",
                is_secret=False,
                description="Require verified email",
            )
        )
        await session.commit()

    protected_settings = Settings(
        allowed_email_domains="company.com",
        jwt_secret="test-jwt-secret-with-more-than-32-bytes",
        cache_redis_url=os.environ["CACHE_REDIS_URL"],
        require_email_verification=True,
    )
    monkeypatch.setattr(config_service, "get_settings", lambda: protected_settings)
    monkeypatch.setattr(runtime_config, "get_settings", lambda: protected_settings)
    runtime_config.invalidate(forget_last_known_good=True)

    effective_items = _items_by_key(await _get_group(config_client, token, "security"))
    assert effective_items["security.require_email_verification"]["value"] is True

    response = await config_client.put(
        "/api/admin/configs/security",
        headers={"Authorization": f"Bearer {token}"},
        json={"items": {"security.require_email_verification": False}},
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "VALIDATION_ERROR"
    assert "security.require_email_verification" in response.json()["message"]
    async with AsyncSessionFactory() as session:
        stored = await session.scalar(
            select(SystemConfig.value).where(
                SystemConfig.key == "security.require_email_verification"
            )
        )
        update_audits = list(
            (
                await session.execute(select(AuditLog).where(AuditLog.action == "config.update"))
            ).scalars()
        )
    assert stored is False
    assert update_audits == []


async def test_system_admin_lists_and_idempotently_replays_dead_letter(
    config_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox, OutboxDeadLetter, OutboxRepository
    from app.modules.audit.models import AuditLog

    actor_id = await _create_user(
        email="dlq-system-admin@company.com",
        password="password123",
        role="system_admin",
    )
    token = await _login(
        config_client,
        email="dlq-system-admin@company.com",
        password="password123",
    )
    async with AsyncSessionFactory() as session:
        event = EventOutbox(
            event_type="document.file.uploaded",
            aggregate_type="file",
            aggregate_id="file-dlq-1",
            payload={
                "file_id": "file-dlq-1",
                "secret": "must-not-leak-through-dlq-api",
            },
            publish_attempts=4,
            last_error="RuntimeError",
        )
        session.add(event)
        await session.flush()
        dead_letter = OutboxDeadLetter(
            event_id=event.id,
            first_failed_at=datetime.now(UTC),
            last_failed_at=datetime.now(UTC),
            attempts=4,
            error_type="RuntimeError",
            correlation_id=f"outbox:{event.id}",
            trace_id="trace-dlq-api",
            payload_summary={
                "field_names": [
                    "file_id",
                    "secret",
                    {"api_key": "must-not-leak-summary-value"},
                    "x" * 65,
                ],
                "field_count": "must-not-leak-count-value",
                "encoded_bytes": -80,
                "hmac_sha256": "must-not-leak-hmac-value",
                "payload": "must-not-leak-dirty-payload",
                "api_key": "must-not-leak-api-key",
            },
        )
        session.add(dead_letter)
        await session.commit()
        dead_letter_id = dead_letter.id
        event_id = event.id

    list_response = await config_client.get(
        "/api/admin/outbox/dead-letters",
        params={"status": "pending"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert list_response.status_code == 200
    list_payload = list_response.json()["data"]
    assert list_payload["total"] == 1
    assert list_payload["items"][0]["id"] == str(dead_letter_id)
    assert list_payload["items"][0]["event_id"] == event_id
    assert list_payload["items"][0]["status"] == "pending"
    assert list_payload["items"][0]["correlation_id"] == f"outbox:{event_id}"
    assert list_payload["items"][0]["payload_summary"]["field_names"] == [
        "file_id",
        "secret",
    ]
    assert list_payload["items"][0]["payload_summary"] == {
        "field_names": ["file_id", "secret"],
        "field_count": 0,
        "encoded_bytes": 0,
        "hmac_sha256": "0" * 64,
    }
    serialized_list = json.dumps(list_payload, ensure_ascii=False)
    assert "must-not-leak-through-dlq-api" not in serialized_list
    assert "must-not-leak-summary-value" not in serialized_list
    assert "must-not-leak-count-value" not in serialized_list
    assert "must-not-leak-hmac-value" not in serialized_list
    assert "must-not-leak-dirty-payload" not in serialized_list
    assert "must-not-leak-api-key" not in serialized_list

    detail_response = await config_client.get(
        f"/api/admin/outbox/dead-letters/{dead_letter_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert detail_response.status_code == 200
    assert detail_response.json()["data"]["status"] == "pending"

    first_replay = await config_client.post(
        f"/api/admin/outbox/dead-letters/{dead_letter_id}/replay",
        headers={"Authorization": f"Bearer {token}"},
        json={"reason": "确认消息代理恢复后重放"},
    )
    second_replay = await config_client.post(
        f"/api/admin/outbox/dead-letters/{dead_letter_id}/replay",
        headers={"Authorization": f"Bearer {token}"},
        json={"reason": "重复请求用于验证幂等"},
    )

    assert first_replay.status_code == 200
    assert first_replay.json()["data"]["replay_queued"] is True
    assert first_replay.json()["data"]["item"]["status"] == "requeued"
    assert second_replay.status_code == 200
    assert second_replay.json()["data"]["replay_queued"] is False

    async with AsyncSessionFactory() as session:
        replayed_event = await session.get(EventOutbox, event_id)
        assert replayed_event is not None
        await OutboxRepository(session).mark_published(replayed_event)
        await session.commit()

    resolved_response = await config_client.get(
        "/api/admin/outbox/dead-letters",
        params={"status": "resolved"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resolved_response.status_code == 200
    assert resolved_response.json()["data"]["items"][0]["status"] == "resolved"

    async with AsyncSessionFactory() as session:
        stored_event = await session.get(EventOutbox, event_id)
        stored_dead_letter = await session.get(OutboxDeadLetter, dead_letter_id)
        audit_result = await session.execute(
            select(AuditLog).where(AuditLog.action.like("outbox.dead_letter.%"))
        )
        audit_logs = list(audit_result.scalars())

    assert stored_event is not None
    assert stored_event.publish_attempts == 0
    assert stored_event.last_error is None
    assert stored_event.published_at is not None
    assert stored_dead_letter is not None
    assert stored_dead_letter.replay_count == 1
    assert stored_dead_letter.last_replayed_by == actor_id
    assert stored_dead_letter.last_replay_reason == "确认消息代理恢复后重放"
    assert [log.action for log in audit_logs].count("outbox.dead_letter.replay") == 2
    assert "must-not-leak-through-dlq-api" not in json.dumps(
        [log.metadata_json for log in audit_logs],
        ensure_ascii=False,
    )


async def test_dead_letter_endpoints_require_system_admin(
    config_client: AsyncClient,
) -> None:
    await _create_user(
        email="dlq-employee@company.com",
        password="password123",
        role="employee",
    )
    token = await _login(
        config_client,
        email="dlq-employee@company.com",
        password="password123",
    )

    response = await config_client.get(
        "/api/admin/outbox/dead-letters",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert response.json()["error_code"] == "PERMISSION_DENIED"
