from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from importlib import import_module

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

PDF_BYTES = (
    b"%PDF-1.4\n"
    b"1 0 obj\n<< /Type /Catalog >>\nendobj\n"
    b"xref\n0 1\n0000000000 65535 f \n"
    b"trailer\n<< /Root 1 0 R >>\n"
    b"startxref\n9\n%%EOF\n"
)


async def _reset_database() -> None:
    import_module("app.db.models")

    from app.core.database import engine
    from app.db.base import Base

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()


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
async def policy_client() -> AsyncGenerator[AsyncClient, None]:
    from app.core.config import Settings
    from app.core.database import AsyncSessionFactory, get_session
    from app.core.deps import get_app_settings
    from app.main import app

    settings = Settings(
        allowed_email_domains="company.com",
        jwt_secret="test-jwt-secret-with-more-than-32-bytes",
        cache_redis_url=os.environ["CACHE_REDIS_URL"],
        require_email_verification=False,
        minio_bucket="test-knowledge-files",
        upload_max_file_size_bytes=10 * 1024 * 1024,
        upload_rate_limit_per_minute=20,
        upload_allowed_extensions="pdf,docx,txt",
        upload_allowed_mime_types="application/pdf,text/plain",
    )

    async def override_session() -> AsyncGenerator[AsyncSession, None]:
        async with AsyncSessionFactory() as session:
            yield session

    app.dependency_overrides[get_app_settings] = lambda: settings
    app.dependency_overrides[get_session] = override_session
    transport = ASGITransport(app=app)  # type: ignore[arg-type]
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
    app.dependency_overrides.clear()


async def _create_user(*, email: str, password: str, role: str = "employee") -> None:
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


async def _login(client: AsyncClient, *, email: str, password: str) -> str:
    response = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    return str(response.json()["data"]["access_token"])


async def test_employee_can_access_upload_policy(policy_client: AsyncClient) -> None:
    await _create_user(email="employee@company.com", password="password123")
    token = await _login(policy_client, email="employee@company.com", password="password123")

    response = await policy_client.get(
        "/api/upload-policy",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert isinstance(data["allowed_extensions"], list)
    assert len(data["allowed_extensions"]) > 0
    assert isinstance(data["allow_multi_file"], bool)
    assert isinstance(data["upload_enabled"], bool)
    assert isinstance(data["max_file_size_mb"], int)
    assert isinstance(data["allow_user_delete"], bool)


async def test_upload_policy_returns_env_fallback_extensions(policy_client: AsyncClient) -> None:
    await _create_user(email="employee2@company.com", password="password123")
    token = await _login(policy_client, email="employee2@company.com", password="password123")

    response = await policy_client.get(
        "/api/upload-policy",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert "pdf" in data["allowed_extensions"]
    assert "docx" in data["allowed_extensions"]
    assert "txt" in data["allowed_extensions"]


async def test_upload_policy_requires_auth(policy_client: AsyncClient) -> None:
    response = await policy_client.get("/api/upload-policy")
    assert response.status_code == 401
