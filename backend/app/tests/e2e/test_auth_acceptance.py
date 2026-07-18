"""Local AUTH-001..004 probes over the real PostgreSQL and ASGI API stack.

These scenarios intentionally do not claim SMTP delivery.  Test-mode email task
publication is disabled; deterministic raw tokens are injected only so the API's
one-time token and account-state contracts can be exercised repeatably.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from importlib import import_module
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from redis.asyncio import from_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.tests.safety import require_safe_test_database_reset, require_safe_test_redis_reset

pytestmark = pytest.mark.asyncio

PDF_BYTES = (
    b"%PDF-1.4\n"
    b"1 0 obj\n<< /Type /Catalog >>\nendobj\n"
    b"xref\n0 1\n0000000000 65535 f \n"
    b"trailer\n<< /Root 1 0 R >>\n"
    b"startxref\n9\n%%EOF\n"
)
UNASSIGNED_DEPARTMENT_ID = "00000000-0000-0000-0000-000000000001"


@dataclass
class GateOnlyStorage:
    """Storage double that must stay untouched when the department gate rejects upload."""

    put_calls: int = 0

    async def put_object(
        self,
        *,
        bucket: str,
        object_key: str,
        data: bytes,
        content_type: str,
    ) -> None:
        self.put_calls += 1
        pytest.fail(
            "department gate must reject before storage write: "
            f"{bucket}/{object_key} ({content_type}, {len(data)} bytes)"
        )


async def _reset_database() -> None:
    require_safe_test_database_reset()
    require_safe_test_redis_reset()
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


@asynccontextmanager
async def _api_client(
    *,
    require_email_verification: bool,
    storage: GateOnlyStorage | None = None,
) -> AsyncIterator[AsyncClient]:
    from app.core.config import Settings
    from app.core.database import AsyncSessionFactory, get_session
    from app.core.deps import get_app_settings
    from app.main import app
    from app.modules.document.api import get_document_storage

    settings = Settings(
        allowed_email_domains="company.com",
        jwt_secret="test-jwt-secret-with-more-than-32-bytes",
        cache_redis_url=os.environ["CACHE_REDIS_URL"],
        require_email_verification=require_email_verification,
        auth_login_rate_limit_per_hour=100,
        auth_register_rate_limit_per_hour=100,
        auth_password_reset_rate_limit_per_hour=100,
        minio_bucket="auth-acceptance-files",
        upload_max_file_size_bytes=1024,
        upload_rate_limit_per_minute=20,
        upload_allowed_extensions="pdf",
        upload_allowed_mime_types="application/pdf",
    )

    async def override_session() -> AsyncGenerator[AsyncSession, None]:
        async with AsyncSessionFactory() as session:
            yield session

    app.dependency_overrides[get_app_settings] = lambda: settings
    app.dependency_overrides[get_session] = override_session
    if storage is not None:
        app.dependency_overrides[get_document_storage] = lambda: storage

    transport = ASGITransport(app=app)  # type: ignore[arg-type]
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client
    finally:
        app.dependency_overrides.clear()


async def _create_department(*, name: str, code: str) -> UUID:
    from app.core.database import AsyncSessionFactory
    from app.modules.department.models import Department

    async with AsyncSessionFactory() as session:
        department = Department(name=name, code=code, status="active")
        session.add(department)
        await session.commit()
        await session.refresh(department)
        return department.id


async def _create_legacy_draft(*, uploader_id: UUID) -> UUID:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File
    from app.modules.user.models import User

    async with AsyncSessionFactory() as session:
        uploader = await session.get(User, uploader_id)
        assert uploader is not None
        draft = File(
            original_name="legacy-unassigned.pdf",
            stored_name="legacy-unassigned.pdf",
            extension="pdf",
            mime_type="application/pdf",
            size=128,
            hash="a" * 64,
            storage_type="minio",
            bucket="auth-acceptance-files",
            object_key=f"legacy/{uploader_id}/legacy-unassigned.pdf",
            uploader_id=uploader_id,
            department_id=uploader.department_id,
            department=uploader.department,
            visibility="private",
            status="uploaded",
            review_status="pending",
            ai_analysis_enabled_at_upload=False,
        )
        session.add(draft)
        await session.commit()
        await session.refresh(draft)
        return draft.id


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_auth_001_and_002_department_registration_and_legacy_gate() -> None:
    storage = GateOnlyStorage()
    async with _api_client(require_email_verification=False, storage=storage) as client:
        department_id = await _create_department(name="验收研发部", code="acceptance-engineering")
        registered = await client.post(
            "/api/auth/register",
            json={
                "name": "部门员工",
                "email": "department-user@company.com",
                "password": "password123",
                "department_id": str(department_id),
            },
        )
        assert registered.status_code == 201, registered.text

        login = await client.post(
            "/api/auth/login",
            json={"email": "department-user@company.com", "password": "password123"},
        )
        assert login.status_code == 200, login.text
        token = str(login.json()["data"]["access_token"])
        me = await client.get("/api/auth/me", headers=_bearer(token))
        assert me.status_code == 200, me.text
        profile = me.json()["data"]
        assert profile["department_id"] == str(department_id)
        assert profile["department_name"] == "验收研发部"
        assert profile["department"] == "验收研发部"
        assert profile["department_code"] == "acceptance-engineering"
        assert profile["department_assigned"] is True
        assert profile["role"] == "employee"

        legacy_registered = await client.post(
            "/api/auth/register",
            json={
                "name": "兼容未分配员工",
                "email": "legacy-unassigned@company.com",
                "password": "password123",
            },
        )
        assert legacy_registered.status_code == 201, legacy_registered.text
        legacy_login = await client.post(
            "/api/auth/login",
            json={"email": "legacy-unassigned@company.com", "password": "password123"},
        )
        assert legacy_login.status_code == 200, legacy_login.text
        legacy_token = str(legacy_login.json()["data"]["access_token"])
        legacy_me = await client.get("/api/auth/me", headers=_bearer(legacy_token))
        assert legacy_me.status_code == 200, legacy_me.text
        legacy_profile = legacy_me.json()["data"]
        assert legacy_profile["department_id"] == UNASSIGNED_DEPARTMENT_ID
        assert legacy_profile["department_name"] == "未分配"
        assert legacy_profile["department_code"] == "unassigned"
        assert legacy_profile["department_assigned"] is False
        assert legacy_profile["role"] == "employee"

        blocked_upload = await client.post(
            "/api/files/upload",
            headers=_bearer(legacy_token),
            files={"file": ("blocked.pdf", PDF_BYTES, "application/pdf")},
            data={"submit_after_upload": "false"},
        )
        assert blocked_upload.status_code == 403
        assert blocked_upload.json()["error_code"] == "DEPARTMENT_ASSIGNMENT_REQUIRED"
        assert blocked_upload.json()["message"] == "department assignment is required"
        assert storage.put_calls == 0

        legacy_file_id = await _create_legacy_draft(uploader_id=UUID(legacy_profile["id"]))
        blocked_submit = await client.post(
            f"/api/files/{legacy_file_id}/submit-review",
            headers=_bearer(legacy_token),
        )
        assert blocked_submit.status_code == 403
        assert blocked_submit.json()["error_code"] == "DEPARTMENT_ASSIGNMENT_REQUIRED"
        assert blocked_submit.json()["message"] == "department assignment is required"

        from app.core.database import AsyncSessionFactory
        from app.modules.document.models import File

        async with AsyncSessionFactory() as session:
            unchanged = await session.get(File, legacy_file_id)
            assert unchanged is not None
            assert unchanged.status == "uploaded"
            assert unchanged.review_version == 0


async def test_auth_003_email_verification_gate_and_single_use_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_token = "auth-003-email-verification-single-use-token"
    monkeypatch.setattr(
        "app.modules.auth.service.secrets.token_urlsafe",
        lambda _size: raw_token,
    )

    async with _api_client(require_email_verification=True) as client:
        department_id = await _create_department(name="验证门禁部", code="verification-gate")
        registered = await client.post(
            "/api/auth/register",
            json={
                "name": "待验证员工",
                "email": "verify-gate@company.com",
                "password": "password123",
                "department_id": str(department_id),
            },
        )
        assert registered.status_code == 201, registered.text

        pending_login = await client.post(
            "/api/auth/login",
            json={"email": "verify-gate@company.com", "password": "password123"},
        )
        assert pending_login.status_code == 403
        assert pending_login.json()["error_code"] == "EMAIL_NOT_VERIFIED"
        assert "access_token" not in pending_login.text

        verified = await client.post("/api/auth/verify-email", json={"token": raw_token})
        assert verified.status_code == 200, verified.text
        assert verified.json()["data"]["status"] == "active"
        assert verified.json()["data"]["email_verified"] is True

        replayed = await client.post("/api/auth/verify-email", json={"token": raw_token})
        assert replayed.status_code == 400
        assert replayed.json()["error_code"] == "INVALID_TOKEN"

        active_login = await client.post(
            "/api/auth/login",
            json={"email": "verify-gate@company.com", "password": "password123"},
        )
        assert active_login.status_code == 200, active_login.text
        assert active_login.json()["data"]["user"]["email_verified"] is True
        assert active_login.json()["data"]["user"]["department_id"] == str(department_id)


async def test_auth_004_password_reset_does_not_activate_unverified_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issued_tokens = iter(
        (
            "auth-004-verification-token-remains-pending",
            "auth-004-password-reset-single-use-token",
        )
    )
    monkeypatch.setattr(
        "app.modules.auth.service.secrets.token_urlsafe",
        lambda _size: next(issued_tokens),
    )

    async with _api_client(require_email_verification=True) as client:
        department_id = await _create_department(name="重置门禁部", code="reset-gate")
        registered = await client.post(
            "/api/auth/register",
            json={
                "name": "待验证重置员工",
                "email": "reset-pending@company.com",
                "password": "oldpassword123",
                "department_id": str(department_id),
            },
        )
        assert registered.status_code == 201, registered.text

        requested = await client.post(
            "/api/auth/forgot-password",
            json={"email": "reset-pending@company.com"},
        )
        assert requested.status_code == 200, requested.text

        reset = await client.post(
            "/api/auth/reset-password",
            json={
                "token": "auth-004-password-reset-single-use-token",
                "new_password": "newpassword123",
            },
        )
        assert reset.status_code == 200, reset.text
        assert reset.json()["data"]["status"] == "pending_email_verification"
        assert reset.json()["data"]["email_verified"] is False

        old_password = await client.post(
            "/api/auth/login",
            json={"email": "reset-pending@company.com", "password": "oldpassword123"},
        )
        assert old_password.status_code == 401
        assert old_password.json()["error_code"] == "AUTHENTICATION_FAILED"

        new_password = await client.post(
            "/api/auth/login",
            json={"email": "reset-pending@company.com", "password": "newpassword123"},
        )
        assert new_password.status_code == 403
        assert new_password.json()["error_code"] == "EMAIL_NOT_VERIFIED"
        assert "access_token" not in new_password.text

        from sqlalchemy import select

        from app.core.database import AsyncSessionFactory
        from app.modules.user.models import User

        async with AsyncSessionFactory() as session:
            user = (
                await session.execute(select(User).where(User.email == "reset-pending@company.com"))
            ).scalar_one()
            assert user.status == "pending_email_verification"
            assert user.email_verified is False
