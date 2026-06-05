from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from importlib import import_module
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from redis.asyncio import from_url
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
        auth_register_rate_limit_per_hour=2,
        auth_password_reset_rate_limit_per_hour=2,
        auth_resend_verification_rate_limit_per_hour=2,
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


async def _create_user(
    *,
    email: str,
    password: str,
    status: str = "active",
    email_verified: bool = True,
    role: str = "employee",
) -> UUID:
    from app.core.database import AsyncSessionFactory
    from app.core.security import hash_password
    from app.modules.user.models import User

    normalized_email = email.lower()
    user = User(
        name="Test User",
        email=normalized_email,
        email_domain=normalized_email.rsplit("@", 1)[1],
        password_hash=hash_password(password),
        role=role,
        status=status,
        email_verified=email_verified,
    )
    async with AsyncSessionFactory() as session:
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user.id


async def _create_verification_token(user_id: UUID, raw_token: str) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.auth.models import EmailVerificationToken

    token = EmailVerificationToken(
        user_id=user_id,
        token_hash=sha256(raw_token.encode("utf-8")).hexdigest(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    async with AsyncSessionFactory() as session:
        session.add(token)
        await session.commit()


async def _create_password_reset_token(user_id: UUID, raw_token: str) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.auth.models import PasswordResetToken

    token = PasswordResetToken(
        user_id=user_id,
        token_hash=sha256(raw_token.encode("utf-8")).hexdigest(),
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
    )
    async with AsyncSessionFactory() as session:
        session.add(token)
        await session.commit()


async def test_register_accepts_allowed_domain_and_rejects_other_domain(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/api/auth/register",
        json={"name": "Alice", "email": "Alice@company.com", "password": "password123"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    assert body["data"]["email"] == "alice@company.com"
    assert body["data"]["status"] == "active"

    rejected = await client.post(
        "/api/auth/register",
        json={"name": "Bob", "email": "bob@outside.com", "password": "password123"},
    )

    assert rejected.status_code == 400
    assert rejected.json()["error_code"] == "EMAIL_DOMAIN_NOT_ALLOWED"


async def test_login_issues_jwt_and_me_returns_current_user(client: AsyncClient) -> None:
    await client.post(
        "/api/auth/register",
        json={"name": "Charlie", "email": "charlie@company.com", "password": "password123"},
    )

    login = await client.post(
        "/api/auth/login",
        json={"email": "CHARLIE@company.com", "password": "password123"},
    )

    assert login.status_code == 200
    token = login.json()["data"]["access_token"]

    me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert me.status_code == 200
    assert me.json()["data"]["email"] == "charlie@company.com"


async def test_logout_revokes_current_jwt(client: AsyncClient) -> None:
    await _create_user(email="logout@company.com", password="password123")
    login = await client.post(
        "/api/auth/login",
        json={"email": "logout@company.com", "password": "password123"},
    )
    token = login.json()["data"]["access_token"]

    logout = await client.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert logout.status_code == 200

    me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 401


async def test_verify_email_activates_pending_user(client: AsyncClient) -> None:
    user_id = await _create_user(
        email="pending@company.com",
        password="password123",
        status="pending_email_verification",
        email_verified=False,
    )
    await _create_verification_token(user_id, "verify-token")

    verified = await client.post("/api/auth/verify-email", json={"token": "verify-token"})

    assert verified.status_code == 200
    assert verified.json()["data"]["status"] == "active"

    login = await client.post(
        "/api/auth/login",
        json={"email": "pending@company.com", "password": "password123"},
    )
    assert login.status_code == 200


async def test_reset_password_allows_login_with_new_password(client: AsyncClient) -> None:
    user_id = await _create_user(email="reset@company.com", password="oldpassword123")
    await _create_password_reset_token(user_id, "reset-token")

    reset = await client.post(
        "/api/auth/reset-password",
        json={"token": "reset-token", "new_password": "newpassword123"},
    )

    assert reset.status_code == 200

    old_login = await client.post(
        "/api/auth/login",
        json={"email": "reset@company.com", "password": "oldpassword123"},
    )
    assert old_login.status_code == 401

    new_login = await client.post(
        "/api/auth/login",
        json={"email": "reset@company.com", "password": "newpassword123"},
    )
    assert new_login.status_code == 200


async def test_reset_password_invalidates_existing_jwt(client: AsyncClient) -> None:
    user_id = await _create_user(email="token-reset@company.com", password="oldpassword123")
    login = await client.post(
        "/api/auth/login",
        json={"email": "token-reset@company.com", "password": "oldpassword123"},
    )
    token = login.json()["data"]["access_token"]
    await _create_password_reset_token(user_id, "reset-token")

    reset = await client.post(
        "/api/auth/reset-password",
        json={"token": "reset-token", "new_password": "newpassword123"},
    )
    assert reset.status_code == 200

    me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 401


async def test_register_is_rate_limited_by_client_ip(client: AsyncClient) -> None:
    for index in range(2):
        response = await client.post(
            "/api/auth/register",
            json={
                "name": f"Rate {index}",
                "email": f"rate-{index}@company.com",
                "password": "password123",
            },
        )
        assert response.status_code == 201

    limited = await client.post(
        "/api/auth/register",
        json={"name": "Rate 3", "email": "rate-3@company.com", "password": "password123"},
    )

    assert limited.status_code == 429
    assert limited.json()["error_code"] == "RATE_LIMITED"


async def test_validation_error_does_not_echo_password_or_token(client: AsyncClient) -> None:
    sensitive_token = "secret-reset-token"
    sensitive_password = "x" * 200

    response = await client.post(
        "/api/auth/reset-password",
        json={"token": sensitive_token, "new_password": sensitive_password},
    )

    body_text = response.text
    assert response.status_code == 422
    assert response.json()["message"] == "request validation failed"
    assert sensitive_token not in body_text
    assert sensitive_password not in body_text


async def test_disabled_user_cannot_login(client: AsyncClient) -> None:
    await _create_user(email="disabled@company.com", password="password123", status="disabled")

    response = await client.post(
        "/api/auth/login",
        json={"email": "disabled@company.com", "password": "password123"},
    )

    assert response.status_code == 403
    assert response.json()["error_code"] == "USER_DISABLED"
