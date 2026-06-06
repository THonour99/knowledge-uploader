from __future__ import annotations

import os
from collections.abc import AsyncGenerator, Callable
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from importlib import import_module
from typing import Protocol, cast
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from redis.asyncio import from_url
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


class AuthServiceModule(Protocol):
    DUMMY_PASSWORD_HASH: str
    verify_password: Callable[[str, str], bool]


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
        auth_login_rate_limit_per_hour=3,
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


@pytest.fixture
async def verification_client() -> AsyncGenerator[AsyncClient, None]:
    from app.core.config import Settings
    from app.core.database import AsyncSessionFactory, get_session
    from app.core.deps import get_app_settings
    from app.main import app

    settings = Settings(
        allowed_email_domains="company.com",
        jwt_secret="test-jwt-secret-with-more-than-32-bytes",
        cache_redis_url=os.environ["CACHE_REDIS_URL"],
        require_email_verification=True,
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
    assert body["data"] == {"accepted": True}

    rejected = await client.post(
        "/api/auth/register",
        json={"name": "Bob", "email": "bob@outside.com", "password": "password123"},
    )

    assert rejected.status_code == 400
    assert rejected.json()["error_code"] == "EMAIL_DOMAIN_NOT_ALLOWED"


async def test_register_existing_email_returns_generic_success(client: AsyncClient) -> None:
    from sqlalchemy import func, select

    from app.core.database import AsyncSessionFactory
    from app.modules.user.models import User

    payload = {"name": "Alice", "email": "alice@company.com", "password": "password123"}
    first = await client.post("/api/auth/register", json=payload)
    duplicate = await client.post("/api/auth/register", json=payload)

    assert first.status_code == 201
    assert duplicate.status_code == 201
    assert duplicate.json()["data"] == {"accepted": True}

    async with AsyncSessionFactory() as session:
        result = await session.execute(select(func.count()).select_from(User))
        assert result.scalar_one() == 1


async def test_register_writes_verification_outbox_with_encrypted_token(
    verification_client: AsyncClient,
) -> None:
    from sqlalchemy import select

    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.core.security import decrypt_secret
    from app.modules.auth import events
    from app.modules.auth.models import EmailVerificationToken

    response = await verification_client.post(
        "/api/auth/register",
        json={"name": "Verify", "email": "verify@company.com", "password": "password123"},
    )

    assert response.status_code == 201
    assert response.json()["data"] == {"accepted": True}

    async with AsyncSessionFactory() as session:
        token = (
            await session.execute(select(EmailVerificationToken))
        ).scalar_one()
        outbox = (
            await session.execute(
                select(EventOutbox).where(EventOutbox.event_type == events.AUTH_USER_REGISTERED)
            )
        ).scalar_one()

    encrypted_token = outbox.payload["verification_token_encrypted"]
    assert isinstance(encrypted_token, str)
    assert "verification_token" not in outbox.payload
    raw_token = decrypt_secret(encrypted_token, "RZ1Sw_27VrN9c5Cfsq01qiwViwT6y7jDCuXYn7tgGJY=")
    assert token.token_hash == sha256(raw_token.encode("utf-8")).hexdigest()
    assert raw_token not in response.text
    assert outbox.trace_id is not None


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


async def test_login_attempts_write_audit_logs(client: AsyncClient) -> None:
    from sqlalchemy import select

    from app.core.database import AsyncSessionFactory
    from app.modules.audit.models import AuditLog

    user_id = await _create_user(email="audit-login@company.com", password="password123")

    failed = await client.post(
        "/api/auth/login",
        headers={"User-Agent": "auth-audit-test"},
        json={"email": "audit-login@company.com", "password": "wrong-password"},
    )
    success = await client.post(
        "/api/auth/login",
        headers={"User-Agent": "auth-audit-test"},
        json={"email": "audit-login@company.com", "password": "password123"},
    )
    unknown = await client.post(
        "/api/auth/login",
        headers={"User-Agent": "auth-audit-test"},
        json={"email": "missing-login@company.com", "password": "password123"},
    )

    assert failed.status_code == 401
    assert success.status_code == 200
    assert unknown.status_code == 401

    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(AuditLog)
            .where(AuditLog.action.like("auth.login.%"))
            .order_by(AuditLog.created_at)
        )
        logs = list(result.scalars())

    assert len(logs) == 3
    known_failure = next(
        log for log in logs if log.metadata_json["failure_reason"] == "invalid_password"
    )
    known_success = next(log for log in logs if log.metadata_json["success"] is True)
    unknown_failure = next(
        log for log in logs if log.metadata_json["failure_reason"] == "unknown_user"
    )

    assert known_failure.actor_id == user_id
    assert known_failure.target_type == "auth_login"
    assert known_failure.user_agent == "auth-audit-test"
    assert known_success.actor_id == user_id
    assert known_success.action == "auth.login.success"
    assert unknown_failure.actor_id == UUID(int=0)
    assert unknown_failure.metadata_json["user_id"] is None
    assert "password123" not in str([log.metadata_json for log in logs])
    assert "wrong-password" not in str([log.metadata_json for log in logs])


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


async def test_forgot_password_writes_reset_outbox_with_encrypted_token(
    client: AsyncClient,
) -> None:
    from sqlalchemy import select

    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.core.security import decrypt_secret
    from app.modules.auth import events
    from app.modules.auth.models import PasswordResetToken

    await _create_user(email="forgot@company.com", password="password123")

    response = await client.post("/api/auth/forgot-password", json={"email": "forgot@company.com"})

    assert response.status_code == 200

    async with AsyncSessionFactory() as session:
        token = (await session.execute(select(PasswordResetToken))).scalar_one()
        outbox = (
            await session.execute(
                select(EventOutbox).where(
                    EventOutbox.event_type == events.AUTH_PASSWORD_RESET_REQUESTED
                )
            )
        ).scalar_one()

    encrypted_token = outbox.payload["password_reset_token_encrypted"]
    assert isinstance(encrypted_token, str)
    assert "password_reset_token" not in outbox.payload
    raw_token = decrypt_secret(encrypted_token, "RZ1Sw_27VrN9c5Cfsq01qiwViwT6y7jDCuXYn7tgGJY=")
    assert token.token_hash == sha256(raw_token.encode("utf-8")).hexdigest()
    assert raw_token not in response.text


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


async def test_locked_user_existing_jwt_is_rejected(client: AsyncClient) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.user.models import User

    user_id = await _create_user(email="locked-token@company.com", password="password123")
    login = await client.post(
        "/api/auth/login",
        json={"email": "locked-token@company.com", "password": "password123"},
    )
    token = login.json()["data"]["access_token"]

    async with AsyncSessionFactory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        user.status = "locked"
        user.locked_until = datetime.now(UTC) + timedelta(minutes=15)
        user.session_version += 1
        await session.commit()

    me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert me.status_code == 403
    assert me.json()["error_code"] == "USER_LOCKED"


async def test_expired_lock_does_not_reactivate_old_jwt(client: AsyncClient) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.user.models import User

    user_id = await _create_user(email="expired-lock@company.com", password="password123")
    login = await client.post(
        "/api/auth/login",
        json={"email": "expired-lock@company.com", "password": "password123"},
    )
    old_token = login.json()["data"]["access_token"]

    async with AsyncSessionFactory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        user.status = "locked"
        user.locked_until = datetime.now(UTC) - timedelta(minutes=1)
        user.session_version += 1
        await session.commit()

    wrong_login = await client.post(
        "/api/auth/login",
        json={"email": "expired-lock@company.com", "password": "wrong-password"},
    )
    assert wrong_login.status_code == 401

    old_me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {old_token}"})
    assert old_me.status_code == 403

    new_login = await client.post(
        "/api/auth/login",
        json={"email": "expired-lock@company.com", "password": "password123"},
    )
    assert new_login.status_code == 200
    new_token = new_login.json()["data"]["access_token"]

    old_me_after_relogin = await client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {old_token}"},
    )
    new_me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {new_token}"})

    assert old_me_after_relogin.status_code == 401
    assert new_me.status_code == 200


async def test_expired_lock_wrong_password_counts_failed_login(client: AsyncClient) -> None:
    from sqlalchemy import select

    from app.core.database import AsyncSessionFactory
    from app.modules.audit.models import AuditLog
    from app.modules.user.models import User

    user_id = await _create_user(email="expired-lock-wrong@company.com", password="password123")
    async with AsyncSessionFactory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        user.status = "locked"
        user.locked_until = datetime.now(UTC) - timedelta(minutes=1)
        user.failed_login_count = 0
        user.session_version += 1
        await session.commit()

    wrong_login = await client.post(
        "/api/auth/login",
        json={"email": "expired-lock-wrong@company.com", "password": "wrong-password"},
    )

    assert wrong_login.status_code == 401
    async with AsyncSessionFactory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        assert user.failed_login_count == 1
        result = await session.execute(
            select(AuditLog).where(
                AuditLog.actor_id == user_id,
                AuditLog.action == "auth.login.failed",
            )
        )
        audit_log = result.scalar_one()
        assert audit_log.metadata_json["failure_reason"] == "invalid_password"


async def test_unknown_email_login_runs_dummy_password_verification(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth_service = cast(AuthServiceModule, import_module("app.modules.auth.service"))

    calls: list[str] = []
    original_verify_password = auth_service.verify_password

    def tracked_verify_password(password: str, password_hash: str) -> bool:
        calls.append(password_hash)
        return original_verify_password(password, password_hash)

    monkeypatch.setattr(auth_service, "verify_password", tracked_verify_password)

    response = await client.post(
        "/api/auth/login",
        json={"email": "missing@company.com", "password": "password123"},
    )

    assert response.status_code == 401
    assert calls == [auth_service.DUMMY_PASSWORD_HASH]


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

    wrong_password = await client.post(
        "/api/auth/login",
        json={"email": "disabled@company.com", "password": "wrong-password"},
    )
    assert wrong_password.status_code == 401
    assert wrong_password.json()["error_code"] == "AUTHENTICATION_FAILED"

    response = await client.post(
        "/api/auth/login",
        json={"email": "disabled@company.com", "password": "password123"},
    )

    assert response.status_code == 403
    assert response.json()["error_code"] == "USER_DISABLED"


async def test_login_is_rate_limited_for_unknown_email(client: AsyncClient) -> None:
    for _ in range(3):
        response = await client.post(
            "/api/auth/login",
            json={"email": "unknown@company.com", "password": "password123"},
        )
        assert response.status_code == 401

    limited = await client.post(
        "/api/auth/login",
        json={"email": "unknown@company.com", "password": "password123"},
    )

    assert limited.status_code == 429
    assert limited.json()["error_code"] == "RATE_LIMITED"


async def test_forgot_password_rate_limit_returns_429(client: AsyncClient) -> None:
    for _ in range(2):
        response = await client.post(
            "/api/auth/forgot-password",
            json={"email": "rate-forgot@company.com"},
        )
        assert response.status_code == 200

    limited = await client.post(
        "/api/auth/forgot-password",
        json={"email": "rate-forgot@company.com"},
    )

    assert limited.status_code == 429
    assert limited.json()["error_code"] == "RATE_LIMITED"
