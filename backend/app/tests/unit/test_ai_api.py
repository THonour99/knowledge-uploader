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
async def ai_client() -> AsyncGenerator[AsyncClient, None]:
    from app.core.config import Settings
    from app.core.database import AsyncSessionFactory, get_session
    from app.core.deps import get_app_settings
    from app.main import app

    settings = Settings(
        allowed_email_domains="company.com",
        jwt_secret="test-jwt-secret-with-more-than-32-bytes",
        cache_redis_url=os.environ["CACHE_REDIS_URL"],
        require_email_verification=False,
        llm_provider="mock",
        llm_api_key="sk-test-secret",
        llm_model="test-model",
        allow_external_llm=True,
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


async def test_system_admin_reads_ai_config_without_secret_echo(ai_client: AsyncClient) -> None:
    await _create_user(email="root@company.com", password="password123", role="system_admin")
    token = await _login(ai_client, email="root@company.com", password="password123")

    response = await ai_client.get(
        "/api/admin/ai/config",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["global"]["ai_analysis_enabled"] is True
    assert {feature["key"] for feature in data["features"]} >= {
        "summary",
        "auto_category",
        "tag_generation",
        "sensitive_detection",
    }
    assert data["providers"][0]["has_api_key"] is True
    assert data["providers"][0]["api_key_masked"] == "sk-****cret"
    assert "sk-test-secret" not in response.text


async def test_employee_cannot_read_ai_config(ai_client: AsyncClient) -> None:
    await _create_user(email="employee@company.com", password="password123", role="employee")
    token = await _login(ai_client, email="employee@company.com", password="password123")

    response = await ai_client.get(
        "/api/admin/ai/config",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403


async def test_provider_key_is_encrypted_and_masked(ai_client: AsyncClient) -> None:
    from sqlalchemy import select

    from app.core.database import AsyncSessionFactory
    from app.core.security import decrypt_api_key
    from app.modules.ai.models import AiProvider
    from app.modules.audit.models import AuditLog

    await _create_user(email="provider@company.com", password="password123", role="system_admin")
    token = await _login(ai_client, email="provider@company.com", password="password123")

    response = await ai_client.post(
        "/api/admin/ai/providers",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "测试供应商",
            "provider_type": "mock",
            "api_key": "sk-live-secret",
            "chat_model": "mock-chat",
        },
    )

    assert response.status_code == 201
    provider_data = response.json()["data"]
    assert provider_data["has_api_key"] is True
    assert provider_data["api_key_masked"] == "sk-****cret"
    assert "sk-live-secret" not in response.text

    async with AsyncSessionFactory() as session:
        provider = await session.get(AiProvider, UUID(provider_data["id"]))
        assert provider is not None
        assert provider.api_key_encrypted != "sk-live-secret"
        assert (
            decrypt_api_key(
                provider.api_key_encrypted or "", "RZ1Sw_27VrN9c5Cfsq01qiwViwT6y7jDCuXYn7tgGJY="
            )
            == "sk-live-secret"
        )
        result = await session.execute(
            select(AuditLog).where(AuditLog.action == "ai.provider.create")
        )
        audit_log = result.scalar_one()
        audit_metadata = str(audit_log.metadata_json)
        assert "sk-live-secret" not in audit_metadata
        assert "api_key" not in audit_metadata


async def test_update_feature_writes_audit_log(ai_client: AsyncClient) -> None:
    from app.core.audit import AUDIT_LOGS
    from app.core.database import AsyncSessionFactory

    await _create_user(email="audit@company.com", password="password123", role="system_admin")
    token = await _login(ai_client, email="audit@company.com", password="password123")
    seed = await ai_client.get(
        "/api/admin/ai/config",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert seed.status_code == 200

    response = await ai_client.patch(
        "/api/admin/ai/features/sensitive_detection",
        headers={"Authorization": f"Bearer {token}"},
        json={"enabled": False},
    )

    assert response.status_code == 200
    assert response.json()["data"]["enabled"] is False
    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(AUDIT_LOGS.c.action).where(AUDIT_LOGS.c.action == "ai.feature.update")
        )
        assert result.scalar_one() == "ai.feature.update"


async def test_mock_provider_connection_test(ai_client: AsyncClient) -> None:
    await _create_user(email="test@company.com", password="password123", role="system_admin")
    token = await _login(ai_client, email="test@company.com", password="password123")
    config = await ai_client.get(
        "/api/admin/ai/config",
        headers={"Authorization": f"Bearer {token}"},
    )
    provider_id = config.json()["data"]["providers"][0]["id"]

    response = await ai_client.post(
        f"/api/admin/ai/providers/{provider_id}/test",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "provider_id": provider_id,
        "status": "success",
        "latency_ms": 0,
        "message": "ok",
    }


async def test_provider_connection_blocks_external_url_when_feature_disabled(
    ai_client: AsyncClient,
) -> None:
    await _create_user(email="external@company.com", password="password123", role="system_admin")
    token = await _login(ai_client, email="external@company.com", password="password123")
    seed = await ai_client.get(
        "/api/admin/ai/config",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert seed.status_code == 200
    feature_response = await ai_client.patch(
        "/api/admin/ai/features/allow_external_llm",
        headers={"Authorization": f"Bearer {token}"},
        json={"enabled": False},
    )
    assert feature_response.status_code == 200

    provider_response = await ai_client.post(
        "/api/admin/ai/providers",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "疑似本地域名绕过",
            "provider_type": "openai_compatible",
            "base_url": "http://localhost.evil.example/v1",
            "chat_model": "gpt-test",
            "enabled": True,
        },
    )
    assert provider_response.status_code == 201
    provider_id = provider_response.json()["data"]["id"]

    response = await ai_client.post(
        f"/api/admin/ai/providers/{provider_id}/test",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "failed"
    assert response.json()["data"]["message"] == "external model provider is disabled"
