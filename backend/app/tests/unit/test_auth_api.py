from __future__ import annotations

import os
from collections.abc import AsyncGenerator, Awaitable, Callable
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


async def _create_department(*, name: str, code: str, status: str = "active") -> UUID:
    from app.core.database import AsyncSessionFactory
    from app.modules.department.models import Department

    department = Department(name=name, code=code, status=status)
    async with AsyncSessionFactory() as session:
        session.add(department)
        await session.commit()
        await session.refresh(department)
        return department.id


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


async def test_register_rejects_password_below_configured_min_length(
    client: AsyncClient,
    set_system_config: Callable[[str, object], Awaitable[None]],
) -> None:
    # 密码最小长度由 runtime_config (DB 优先) 控制, 设为 12 后 8 位密码必须被拒
    await set_system_config("security.password_min_length", 12)

    response = await client.post(
        "/api/auth/register",
        json={"name": "Weak", "email": "weak-pass@company.com", "password": "short8ch"},
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "WEAK_PASSWORD"


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


async def test_register_assigns_only_an_active_department_without_elevating_role(
    client: AsyncClient,
) -> None:
    from sqlalchemy import select

    from app.core.database import AsyncSessionFactory
    from app.modules.user.models import User

    department_id = await _create_department(name="研发部", code="engineering")
    response = await client.post(
        "/api/auth/register",
        json={
            "name": "Engineer",
            "email": "engineer@company.com",
            "password": "password123",
            "department_id": str(department_id),
        },
    )
    assert response.status_code == 201

    async with AsyncSessionFactory() as session:
        user = (
            await session.execute(select(User).where(User.email == "engineer@company.com"))
        ).scalar_one()

    assert user.department_id == department_id
    assert user.department == "研发部"
    assert user.role == "employee"


async def test_disabled_department_stops_counting_as_an_active_assignment(
    client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.department.models import Department

    department_id = await _create_department(name="Operations", code="operations")
    await client.post(
        "/api/auth/register",
        json={
            "name": "Operator",
            "email": "operator@company.com",
            "password": "password123",
            "department_id": str(department_id),
        },
    )

    first_login = await client.post(
        "/api/auth/login",
        json={"email": "operator@company.com", "password": "password123"},
    )
    assert first_login.status_code == 200
    assert first_login.json()["data"]["user"]["department_assigned"] is True

    async with AsyncSessionFactory() as session:
        department = await session.get(Department, department_id)
        assert department is not None
        department.status = "disabled"
        await session.commit()

    second_login = await client.post(
        "/api/auth/login",
        json={"email": "operator@company.com", "password": "password123"},
    )
    assert second_login.status_code == 200
    assert second_login.json()["data"]["user"]["department_assigned"] is False
    assert second_login.json()["data"]["user"]["department_code"] is None


async def test_registration_department_selector_is_minimal_stable_and_public(
    client: AsyncClient,
) -> None:
    await _create_department(name="Beta Department", code="beta")
    await _create_department(name="Alpha Department", code="alpha")
    await _create_department(name="Disabled Department", code="disabled", status="disabled")

    response = await client.get("/api/auth/registration-departments")

    assert response.status_code == 200
    items = response.json()["data"]
    assert [item["code"] for item in items] == ["alpha", "beta"]
    assert all(set(item) == {"id", "name", "code"} for item in items)


async def test_register_rejects_disabled_unassigned_and_legacy_text_department(
    client: AsyncClient,
) -> None:
    disabled_id = await _create_department(
        name="Disabled Department",
        code="disabled",
        status="disabled",
    )
    disabled = await client.post(
        "/api/auth/register",
        json={
            "name": "Disabled Dept",
            "email": "disabled-dept@company.com",
            "password": "password123",
            "department_id": str(disabled_id),
        },
    )
    assert disabled.status_code == 400
    assert disabled.json()["error_code"] == "DEPARTMENT_NOT_FOUND"

    unassigned = await client.post(
        "/api/auth/register",
        json={
            "name": "Unassigned Dept",
            "email": "unassigned-dept@company.com",
            "password": "password123",
            "department_id": "00000000-0000-0000-0000-000000000001",
        },
    )
    assert unassigned.status_code == 400
    assert unassigned.json()["error_code"] == "DEPARTMENT_NOT_FOUND"

    legacy = await client.post(
        "/api/auth/register",
        json={
            "name": "Legacy",
            "email": "legacy-dept@company.com",
            "password": "password123",
            "department": "研发部",
        },
    )
    assert legacy.status_code == 422


async def test_register_creates_active_user_without_email_verification_by_default(
    client: AsyncClient,
) -> None:
    from sqlalchemy import select

    from app.core.database import AsyncSessionFactory
    from app.modules.user.models import User

    response = await client.post(
        "/api/auth/register",
        json={"name": "No Verify", "email": "no-verify@company.com", "password": "password123"},
    )

    assert response.status_code == 201

    async with AsyncSessionFactory() as session:
        user = (
            await session.execute(select(User).where(User.email == "no-verify@company.com"))
        ).scalar_one()

    assert user.status == "active"
    assert user.email_verified is True


async def test_register_writes_verification_outbox_without_replayable_token(
    verification_client: AsyncClient,
) -> None:
    from sqlalchemy import select

    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.auth import events
    from app.modules.auth.models import EmailVerificationToken

    response = await verification_client.post(
        "/api/auth/register",
        json={"name": "Verify", "email": "verify@company.com", "password": "password123"},
    )

    assert response.status_code == 201
    assert response.json()["data"] == {"accepted": True}

    async with AsyncSessionFactory() as session:
        token = (await session.execute(select(EmailVerificationToken))).scalar_one()
        outbox = (
            await session.execute(
                select(EventOutbox).where(EventOutbox.event_type == events.AUTH_USER_REGISTERED)
            )
        ).scalar_one()

    assert len(token.token_hash) == 64
    assert token.token_hash not in outbox.payload.values()
    assert "verification_token_encrypted" not in outbox.payload
    assert "verification_token" not in outbox.payload
    assert "token_expires_at" in outbox.payload
    assert response.text.find(token.token_hash) == -1
    assert outbox.trace_id is not None


async def test_login_issues_jwt_and_me_returns_current_user(
    client: AsyncClient,
    set_system_config: Callable[[str, object], Awaitable[None]],
) -> None:
    # 注册后免邮箱验证改由 runtime_config (DB 优先) 控制, 注册即激活方可直接登录
    await set_system_config("security.require_email_verification", False)
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
    replayed = await client.post("/api/auth/verify-email", json={"token": "verify-token"})
    assert replayed.status_code == 400
    assert replayed.json()["error_code"] == "INVALID_TOKEN"

    login = await client.post(
        "/api/auth/login",
        json={"email": "pending@company.com", "password": "password123"},
    )
    assert login.status_code == 200


async def test_pending_unverified_user_cannot_login(
    client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.user.models import User

    user_id = await _create_user(
        email="pending-login@company.com",
        password="password123",
        status="pending_email_verification",
        email_verified=False,
    )

    login = await client.post(
        "/api/auth/login",
        json={"email": "pending-login@company.com", "password": "password123"},
    )

    assert login.status_code == 403
    assert login.json()["error_code"] == "EMAIL_NOT_VERIFIED"

    async with AsyncSessionFactory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        assert user.status == "pending_email_verification"
        assert user.email_verified is False


@pytest.mark.parametrize(
    ("user_status", "email_verified"),
    [
        ("pending_email_verification", False),
        ("active", False),
    ],
)
async def test_unverified_user_existing_jwt_is_rejected(
    client: AsyncClient,
    user_status: str,
    email_verified: bool,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.security import create_jwt, password_fingerprint
    from app.modules.user.models import User

    jwt_secret = "test-jwt-secret-with-more-than-32-bytes"
    user_id = await _create_user(
        email=f"unverified-token-{user_status}@company.com",
        password="password123",
        status=user_status,
        email_verified=email_verified,
    )
    async with AsyncSessionFactory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        token = create_jwt(
            {
                "sub": str(user.id),
                "sv": user.session_version,
                "pwd": password_fingerprint(user.password_hash, jwt_secret),
            },
            jwt_secret,
            60,
        )

    response = await client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert response.json()["error_code"] == "EMAIL_NOT_VERIFIED"


async def test_password_reset_does_not_verify_or_activate_pending_user(
    client: AsyncClient,
) -> None:
    user_id = await _create_user(
        email="pending-reset@company.com",
        password="oldpassword123",
        status="pending_email_verification",
        email_verified=False,
    )
    await _create_password_reset_token(user_id, "pending-reset-token")

    reset = await client.post(
        "/api/auth/reset-password",
        json={"token": "pending-reset-token", "new_password": "newpassword123"},
    )
    assert reset.status_code == 200
    assert reset.json()["data"]["status"] == "pending_email_verification"

    login = await client.post(
        "/api/auth/login",
        json={"email": "pending-reset@company.com", "password": "newpassword123"},
    )
    assert login.status_code == 403
    assert login.json()["error_code"] == "EMAIL_NOT_VERIFIED"


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


async def test_forgot_password_writes_reset_outbox_without_replayable_token(
    client: AsyncClient,
) -> None:
    from sqlalchemy import select

    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
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

    assert len(token.token_hash) == 64
    assert token.token_hash not in outbox.payload.values()
    assert "password_reset_token_encrypted" not in outbox.payload
    assert "password_reset_token" not in outbox.payload
    assert "token_expires_at" in outbox.payload
    assert response.text.find(token.token_hash) == -1


async def test_register_verification_token_not_stored_in_db_outbox_or_logs(
    verification_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from sqlalchemy import select

    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.auth import events
    from app.modules.auth.models import EmailVerificationToken

    raw_token = "raw-verification-token"
    sent: list[dict[str, str]] = []

    def fake_enqueue_email(*, recipient: str, subject: str, body: str) -> None:
        sent.append({"recipient": recipient, "subject": subject, "body": body})

    monkeypatch.setattr("app.modules.auth.service.secrets.token_urlsafe", lambda _size: raw_token)
    monkeypatch.setattr("app.modules.auth.service.enqueue_email", fake_enqueue_email)

    response = await verification_client.post(
        "/api/auth/register",
        json={
            "name": "Pending User",
            "email": "token-check@company.com",
            "password": "password123",
        },
    )

    assert response.status_code == 201

    async with AsyncSessionFactory() as session:
        token = (await session.execute(select(EmailVerificationToken))).scalar_one()
        outbox = (
            await session.execute(
                select(EventOutbox).where(EventOutbox.event_type == events.AUTH_USER_REGISTERED)
            )
        ).scalar_one()

    assert token.token_hash == sha256(raw_token.encode("utf-8")).hexdigest()
    assert raw_token not in token.token_hash
    assert raw_token not in str(outbox.payload)
    assert "token" not in outbox.payload
    assert sent[0]["recipient"] == "token-check@company.com"
    assert f"http://localhost/verify-email?token={raw_token}" in sent[0]["body"]
    assert raw_token in sent[0]["body"]
    assert raw_token not in caplog.text


async def test_forgot_password_token_not_stored_in_db_outbox_or_logs(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from sqlalchemy import select

    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.auth import events
    from app.modules.auth.models import PasswordResetToken

    raw_token = "raw-reset-token"
    sent: list[dict[str, str]] = []

    def fake_enqueue_email(*, recipient: str, subject: str, body: str) -> None:
        sent.append({"recipient": recipient, "subject": subject, "body": body})

    await _create_user(email="reset-token-check@company.com", password="password123")
    monkeypatch.setattr("app.modules.auth.service.secrets.token_urlsafe", lambda _size: raw_token)
    monkeypatch.setattr("app.modules.auth.service.enqueue_email", fake_enqueue_email)

    response = await client.post(
        "/api/auth/forgot-password",
        json={"email": "reset-token-check@company.com"},
    )

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

    assert token.token_hash == sha256(raw_token.encode("utf-8")).hexdigest()
    assert raw_token not in token.token_hash
    assert raw_token not in str(outbox.payload)
    assert "token" not in outbox.payload
    assert "password_reset_token" not in outbox.payload
    assert sent[0]["recipient"] == "reset-token-check@company.com"
    assert f"http://localhost/reset-password/{raw_token}" in sent[0]["body"]
    assert raw_token in sent[0]["body"]
    assert raw_token not in caplog.text


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
