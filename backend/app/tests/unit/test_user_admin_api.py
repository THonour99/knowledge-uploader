from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from importlib import import_module
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from redis.asyncio import from_url
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


async def _reset_database() -> None:
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
async def client() -> AsyncGenerator[AsyncClient, None]:
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


async def test_employee_cannot_disable_users(client: AsyncClient) -> None:
    actor_id = await _create_user(email="employee@company.com", password="password123")
    target_id = await _create_user(email="target@company.com", password="password123")
    token = await _login(client, email="employee@company.com", password="password123")

    response = await client.post(
        f"/api/users/{target_id}/disable",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert actor_id != target_id


async def test_admin_can_disable_and_enable_user_with_audit_log(client: AsyncClient) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.audit.models import AuditLog

    admin_id = await _create_user(
        email="admin@company.com",
        password="password123",
        role="knowledge_admin",
    )
    target_id = await _create_user(email="worker@company.com", password="password123")
    token = await _login(client, email="admin@company.com", password="password123")

    disabled = await client.post(
        f"/api/users/{target_id}/disable",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert disabled.status_code == 200
    assert disabled.json()["data"]["status"] == "disabled"

    denied_login = await client.post(
        "/api/auth/login",
        json={"email": "worker@company.com", "password": "password123"},
    )
    assert denied_login.status_code == 403

    enabled = await client.post(
        f"/api/users/{target_id}/enable",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert enabled.status_code == 200
    assert enabled.json()["data"]["status"] == "active"

    async with AsyncSessionFactory() as session:
        result = await session.execute(select(AuditLog).order_by(AuditLog.created_at))
        logs = list(result.scalars())

    assert [log.action for log in logs] == ["user.disable", "user.enable"]
    assert all(log.actor_id == admin_id for log in logs)
    assert all(log.target_id == target_id for log in logs)
