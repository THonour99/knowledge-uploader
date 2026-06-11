from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Database / App fixtures (mirror test_review_api.py pattern)
# ---------------------------------------------------------------------------


async def _reset_database() -> None:
    from importlib import import_module

    import_module("app.db.models")

    from app.core.database import engine
    from app.db.base import Base

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()

    from redis.asyncio import from_url

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
async def audit_client() -> AsyncGenerator[AsyncClient, None]:
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_user(*, email: str, password: str, role: str = "employee") -> uuid.UUID:
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


async def _create_audit_log(
    *,
    actor_id: uuid.UUID,
    action: str = "file.approve",
    target_type: str = "file",
    target_id: uuid.UUID | None = None,
    ip_address: str = "127.0.0.1",
    user_agent: str = "test-agent",
    metadata_json: dict[str, object] | None = None,
    reason: str | None = None,
    created_at: datetime | None = None,
) -> uuid.UUID:
    from sqlalchemy import update

    from app.core.database import AsyncSessionFactory
    from app.modules.audit.models import AuditLog

    log = AuditLog(
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=target_id or uuid.uuid4(),
        ip_address=ip_address,
        user_agent=user_agent,
        metadata_json=metadata_json or {},
        reason=reason,
    )
    async with AsyncSessionFactory() as session:
        session.add(log)
        await session.commit()
        await session.refresh(log)
        log_id = log.id

    if created_at is not None:
        # Use a direct UPDATE to override the server-side created_at value.
        async with AsyncSessionFactory() as session:
            await session.execute(
                update(AuditLog).where(AuditLog.id == log_id).values(created_at=created_at)
            )
            await session.commit()

    return log_id


# ---------------------------------------------------------------------------
# Test: employee cannot access audit logs (403)
# ---------------------------------------------------------------------------


async def test_employee_cannot_read_audit_logs(audit_client: AsyncClient) -> None:
    await _create_user(email="emp@company.com", password="pass1234")
    token = await _login(audit_client, email="emp@company.com", password="pass1234")

    response = await audit_client.get(
        "/api/admin/audit-logs",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert response.json()["error_code"] == "PERMISSION_DENIED"


# ---------------------------------------------------------------------------
# Test: knowledge_admin can read audit logs (200)
# ---------------------------------------------------------------------------


async def test_knowledge_admin_can_read_audit_logs(audit_client: AsyncClient) -> None:
    actor_id = await _create_user(
        email="kadmin@company.com", password="pass1234", role="knowledge_admin"
    )
    await _create_audit_log(actor_id=actor_id, action="file.approve")
    token = await _login(audit_client, email="kadmin@company.com", password="pass1234")

    response = await audit_client.get(
        "/api/admin/audit-logs",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["total"] >= 1
    assert data["page"] == 1
    assert "items" in data


# ---------------------------------------------------------------------------
# Test: system_admin can read audit logs (200)
# ---------------------------------------------------------------------------


async def test_system_admin_can_read_audit_logs(audit_client: AsyncClient) -> None:
    actor_id = await _create_user(
        email="sysadmin@company.com", password="pass1234", role="system_admin"
    )
    await _create_audit_log(actor_id=actor_id, action="config.update")
    token = await _login(audit_client, email="sysadmin@company.com", password="pass1234")

    response = await audit_client.get(
        "/api/admin/audit-logs",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["total"] >= 1


# ---------------------------------------------------------------------------
# Test: pagination — page / page_size / total correct
# ---------------------------------------------------------------------------


async def test_pagination_total_and_items_are_correct(audit_client: AsyncClient) -> None:
    actor_id = await _create_user(
        email="pager@company.com", password="pass1234", role="system_admin"
    )
    token = await _login(audit_client, email="pager@company.com", password="pass1234")

    for i in range(5):
        await _create_audit_log(
            actor_id=actor_id,
            action=f"pager.specific.{i}",
            target_type="pager_isolation_test",
        )

    # Use a unique target_type to strictly isolate the 5 logs created above
    # (login and other operations also write audit logs with shared target types).
    response = await audit_client.get(
        "/api/admin/audit-logs?page=1&page_size=2&target_type=pager_isolation_test",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 5
    assert data["page"] == 1
    assert data["page_size"] == 2
    assert len(data["items"]) == 2


async def test_pagination_page_2_returns_correct_items(audit_client: AsyncClient) -> None:
    actor_id = await _create_user(
        email="pager2@company.com", password="pass1234", role="system_admin"
    )
    token = await _login(audit_client, email="pager2@company.com", password="pass1234")

    for i in range(5):
        await _create_audit_log(
            actor_id=actor_id,
            action=f"paged.{i}",
            target_type="pager2_isolation_test",
        )

    # Use a unique target_type to strictly isolate the 5 logs created above.
    response = await audit_client.get(
        "/api/admin/audit-logs?page=2&page_size=3&target_type=pager2_isolation_test",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 5
    assert data["page"] == 2
    assert len(data["items"]) == 2  # 5 total, page_size=3 → page2 has 2


# ---------------------------------------------------------------------------
# Test: results are sorted by created_at descending
# ---------------------------------------------------------------------------


async def test_results_sorted_by_created_at_descending(audit_client: AsyncClient) -> None:
    actor_id = await _create_user(
        email="sortuser@company.com", password="pass1234", role="system_admin"
    )
    token = await _login(audit_client, email="sortuser@company.com", password="pass1234")

    now = datetime.now(UTC)
    await _create_audit_log(
        actor_id=actor_id,
        action="oldest.action",
        target_type="sort_test",
        created_at=now - timedelta(hours=2),
    )
    await _create_audit_log(
        actor_id=actor_id,
        action="middle.action",
        target_type="sort_test",
        created_at=now - timedelta(hours=1),
    )
    await _create_audit_log(
        actor_id=actor_id,
        action="newest.action",
        target_type="sort_test",
        created_at=now,
    )

    # Filter by target_type=sort_test to isolate only our 3 logs.
    response = await audit_client.get(
        "/api/admin/audit-logs?target_type=sort_test",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert len(items) == 3
    # newest first
    assert items[0]["action"] == "newest.action"
    assert items[2]["action"] == "oldest.action"


# ---------------------------------------------------------------------------
# Test: filter by actor_id
# ---------------------------------------------------------------------------


async def test_filter_by_actor_id(audit_client: AsyncClient) -> None:
    actor_a = await _create_user(
        email="actor-a@company.com", password="pass1234", role="system_admin"
    )
    actor_b = await _create_user(
        email="actor-b@company.com", password="pass1234", role="knowledge_admin"
    )
    token = await _login(audit_client, email="actor-a@company.com", password="pass1234")

    # Use a distinct target_type so we can isolate from any login audit logs.
    await _create_audit_log(actor_id=actor_a, action="a.action", target_type="actor_filter_test")
    await _create_audit_log(actor_id=actor_b, action="b.action", target_type="actor_filter_test")

    response = await audit_client.get(
        f"/api/admin/audit-logs?actor_id={actor_a}&target_type=actor_filter_test",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 1
    assert data["items"][0]["actor_id"] == str(actor_a)
    assert data["items"][0]["action"] == "a.action"


# ---------------------------------------------------------------------------
# Test: filter by action
# ---------------------------------------------------------------------------


async def test_filter_by_action(audit_client: AsyncClient) -> None:
    actor_id = await _create_user(
        email="action-filter@company.com", password="pass1234", role="system_admin"
    )
    token = await _login(audit_client, email="action-filter@company.com", password="pass1234")

    await _create_audit_log(actor_id=actor_id, action="file.approve")
    await _create_audit_log(actor_id=actor_id, action="file.reject")
    await _create_audit_log(actor_id=actor_id, action="file.approve")

    response = await audit_client.get(
        "/api/admin/audit-logs?action=file.approve",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 2
    assert all(item["action"] == "file.approve" for item in data["items"])


# ---------------------------------------------------------------------------
# Test: filter by target_type
# ---------------------------------------------------------------------------


async def test_filter_by_target_type(audit_client: AsyncClient) -> None:
    actor_id = await _create_user(
        email="target-filter@company.com", password="pass1234", role="system_admin"
    )
    token = await _login(audit_client, email="target-filter@company.com", password="pass1234")

    await _create_audit_log(actor_id=actor_id, action="file.approve", target_type="file")
    await _create_audit_log(actor_id=actor_id, action="category.create", target_type="category")

    response = await audit_client.get(
        "/api/admin/audit-logs?target_type=file",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 1
    assert data["items"][0]["target_type"] == "file"


# ---------------------------------------------------------------------------
# Test: filter by time range (created_from / created_to)
# ---------------------------------------------------------------------------


async def test_filter_by_time_range(audit_client: AsyncClient) -> None:
    from urllib.parse import urlencode

    actor_id = await _create_user(
        email="time-filter@company.com", password="pass1234", role="system_admin"
    )
    token = await _login(audit_client, email="time-filter@company.com", password="pass1234")

    now = datetime.now(UTC)
    await _create_audit_log(
        actor_id=actor_id,
        action="very.old",
        target_type="time_range_test",
        created_at=now - timedelta(days=10),
    )
    await _create_audit_log(
        actor_id=actor_id,
        action="in.range",
        target_type="time_range_test",
        created_at=now - timedelta(days=3),
    )
    await _create_audit_log(
        actor_id=actor_id,
        action="also.in.range",
        target_type="time_range_test",
        created_at=now - timedelta(days=1),
    )

    # Use urlencode to correctly percent-encode the '+' in ISO 8601 timezone offset.
    params = urlencode(
        {
            "target_type": "time_range_test",
            "created_from": (now - timedelta(days=5)).isoformat(),
            "created_to": (now - timedelta(hours=6)).isoformat(),
        }
    )
    response = await audit_client.get(
        f"/api/admin/audit-logs?{params}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 2
    actions = {item["action"] for item in data["items"]}
    assert actions == {"in.range", "also.in.range"}


# ---------------------------------------------------------------------------
# Test: actor_name and actor_email are resolved from users table
# ---------------------------------------------------------------------------


async def test_actor_name_and_email_are_resolved(audit_client: AsyncClient) -> None:
    actor_id = await _create_user(
        email="named-actor@company.com", password="pass1234", role="system_admin"
    )
    token = await _login(audit_client, email="named-actor@company.com", password="pass1234")
    await _create_audit_log(actor_id=actor_id, action="config.update")

    response = await audit_client.get(
        f"/api/admin/audit-logs?actor_id={actor_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    item = response.json()["data"]["items"][0]
    assert item["actor_email"] == "named-actor@company.com"
    assert item["actor_name"] is not None  # name was set to username part


# ---------------------------------------------------------------------------
# Test: actor_name/email are null for deleted users (LEFT JOIN tolerance)
# ---------------------------------------------------------------------------


async def test_deleted_actor_returns_null_name_email(audit_client: AsyncClient) -> None:
    ghost_actor_id = uuid.uuid4()  # non-existent user
    await _create_user(
        email="real-admin@company.com", password="pass1234", role="system_admin"
    )
    token = await _login(audit_client, email="real-admin@company.com", password="pass1234")
    await _create_audit_log(actor_id=ghost_actor_id, action="ghost.action")

    response = await audit_client.get(
        f"/api/admin/audit-logs?actor_id={ghost_actor_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 1
    item = data["items"][0]
    assert item["actor_name"] is None
    assert item["actor_email"] is None


# ---------------------------------------------------------------------------
# Test: metadata sensitive key values are redacted
# ---------------------------------------------------------------------------


async def test_metadata_sensitive_keys_are_redacted(audit_client: AsyncClient) -> None:
    actor_id = await _create_user(
        email="sensitive@company.com", password="pass1234", role="system_admin"
    )
    token = await _login(audit_client, email="sensitive@company.com", password="pass1234")

    await _create_audit_log(
        actor_id=actor_id,
        action="config.update",
        metadata_json={
            "api_key": "sk-realvalue",
            "secret": "topsecret",
            "password": "hunter2",
            "token": "abc123",
            "safe_field": "visible",
        },
    )

    response = await audit_client.get(
        "/api/admin/audit-logs",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    item = response.json()["data"]["items"][0]
    meta = item["metadata"]
    assert meta is not None
    assert meta["api_key"] == "***"
    assert meta["secret"] == "***"
    assert meta["password"] == "***"
    assert meta["token"] == "***"
    assert meta["safe_field"] == "visible"


# ---------------------------------------------------------------------------
# Test: page_size > 100 is clamped to 100 (or returns 422)
# ---------------------------------------------------------------------------


async def test_page_size_over_100_is_rejected_or_clamped(audit_client: AsyncClient) -> None:
    await _create_user(
        email="clamp@company.com", password="pass1234", role="system_admin"
    )
    token = await _login(audit_client, email="clamp@company.com", password="pass1234")

    response = await audit_client.get(
        "/api/admin/audit-logs?page_size=200",
        headers={"Authorization": f"Bearer {token}"},
    )

    # either 422 (validation rejects it) or 200 with clamped page_size=100
    assert response.status_code in (200, 422)
    if response.status_code == 200:
        assert response.json()["data"]["page_size"] <= 100


# ---------------------------------------------------------------------------
# Test: response schema includes all required fields
# ---------------------------------------------------------------------------


async def test_response_schema_fields(audit_client: AsyncClient) -> None:
    actor_id = await _create_user(
        email="schema-check@company.com", password="pass1234", role="system_admin"
    )
    token = await _login(audit_client, email="schema-check@company.com", password="pass1234")
    target_id = uuid.uuid4()
    await _create_audit_log(
        actor_id=actor_id,
        action="file.approve",
        target_type="file",
        target_id=target_id,
        ip_address="10.0.0.1",
        user_agent="Mozilla/5.0",
        reason="looks good",
        metadata_json={"note": "ok"},
    )

    response = await audit_client.get(
        "/api/admin/audit-logs",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    item = response.json()["data"]["items"][0]
    required_fields = {
        "id",
        "actor_id",
        "actor_name",
        "actor_email",
        "action",
        "target_type",
        "target_id",
        "ip_address",
        "user_agent",
        "reason",
        "metadata",
        "created_at",
    }
    assert required_fields.issubset(set(item.keys()))
    assert item["action"] == "file.approve"
    assert item["target_type"] == "file"
    assert item["target_id"] == str(target_id)
    assert item["ip_address"] == "10.0.0.1"
    assert item["user_agent"] == "Mozilla/5.0"
    assert item["reason"] == "looks good"
    assert item["metadata"] == {"note": "ok"}


# ---------------------------------------------------------------------------
# Test: reading audit logs does NOT create a new audit log entry
#       (prevent read-generates-audit cascade)
# ---------------------------------------------------------------------------


async def test_reading_audit_logs_does_not_create_new_audit_entry(
    audit_client: AsyncClient,
) -> None:
    from sqlalchemy import func, select

    from app.core.database import AsyncSessionFactory
    from app.modules.audit.models import AuditLog

    actor_id = await _create_user(
        email="noaudit@company.com", password="pass1234", role="system_admin"
    )
    token = await _login(audit_client, email="noaudit@company.com", password="pass1234")
    await _create_audit_log(actor_id=actor_id, action="file.approve")

    async with AsyncSessionFactory() as session:
        count_before = (
            await session.execute(select(func.count()).select_from(AuditLog))
        ).scalar_one()

    await audit_client.get(
        "/api/admin/audit-logs",
        headers={"Authorization": f"Bearer {token}"},
    )

    async with AsyncSessionFactory() as session:
        count_after = (
            await session.execute(select(func.count()).select_from(AuditLog))
        ).scalar_one()

    assert count_after == count_before
