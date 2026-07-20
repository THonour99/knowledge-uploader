from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from importlib import import_module

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.user.schemas import AuthUserRecord

pytestmark = pytest.mark.asyncio


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


async def _department(name: str) -> uuid.UUID:
    from app.core.database import AsyncSessionFactory
    from app.modules.department.models import Department

    item = Department(name=name, code=f"api-{uuid.uuid4().hex[:8]}")
    async with AsyncSessionFactory() as session:
        session.add(item)
        await session.commit()
        return item.id


async def _user(email: str, *, department_id: uuid.UUID, role: str) -> AuthUserRecord:
    from app.core.database import AsyncSessionFactory
    from app.core.security import hash_password
    from app.modules.user.models import User

    item = User(
        name=email.split("@", maxsplit=1)[0],
        email=email,
        email_domain=email.rsplit("@", maxsplit=1)[1],
        password_hash=hash_password("password123"),
        department_id=department_id,
        role=role,
        status="active",
        email_verified=True,
    )
    async with AsyncSessionFactory() as session:
        session.add(item)
        await session.commit()
        await session.refresh(item)
    return AuthUserRecord(
        id=item.id,
        name=item.name,
        email=item.email,
        email_domain=item.email_domain,
        password_hash=item.password_hash,
        role=item.role,
        status=item.status,
        email_verified=item.email_verified,
        department_id=item.department_id,
        department_name=None,
        department_code=None,
        department=None,
        phone=None,
        failed_login_count=0,
        locked_until=None,
        session_version=0,
        managed_department_ids=[],
    )


@asynccontextmanager
async def _api_client(identity: AuthUserRecord) -> AsyncIterator[AsyncClient]:
    from app.core.database import AsyncSessionFactory, get_session
    from app.core.deps import get_current_user
    from app.main import app

    async def override_session() -> AsyncGenerator[AsyncSession, None]:
        async with AsyncSessionFactory() as session:
            yield session

    app.dependency_overrides[get_current_user] = lambda: identity
    app.dependency_overrides[get_session] = override_session
    transport = ASGITransport(app=app)  # type: ignore[arg-type]
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client
    finally:
        app.dependency_overrides.clear()


async def test_public_api_minimizes_fields_and_blocks_admin_access() -> None:
    department_id = await _department("公告接口部门")
    admin = await _user(
        "announcement-admin@company.com", department_id=department_id, role="system_admin"
    )
    employee = await _user(
        "announcement-user@company.com", department_id=department_id, role="employee"
    )
    payload = {
        "title": "接口公告",
        "body_markdown": "# 仅详情可见",
        "audience_type": "all",
        "department_ids": [],
        "roles": [],
        "visible_from": None,
        "expires_at": None,
        "is_pinned": True,
    }

    async with _api_client(admin) as client:
        created = await client.post("/api/admin/announcements", json=payload)
        assert created.status_code == 200
        announcement_id = created.json()["data"]["id"]
        published = await client.post(
            f"/api/admin/announcements/{announcement_id}/publish",
            json={"row_version": created.json()["data"]["row_version"]},
        )
        assert published.status_code == 200

    async with _api_client(employee) as client:
        forbidden = await client.get("/api/admin/announcements")
        assert forbidden.status_code == 403

        listed = await client.get("/api/announcements")
        assert listed.status_code == 200
        summary = listed.json()["data"]["items"][0]
        assert set(summary) == {
            "id",
            "title",
            "state",
            "visible_from",
            "expires_at",
            "is_pinned",
            "is_read",
        }

        detail_response = await client.get(f"/api/announcements/{announcement_id}")
        assert detail_response.status_code == 200
        detail = detail_response.json()["data"]
        assert set(detail) == {*summary, "body_markdown"}
        assert detail["body_markdown"] == "# 仅详情可见"

    from app.core.database import AsyncSessionFactory
    from app.modules.audit.models import AuditLog

    async with AsyncSessionFactory() as session:
        logs = list(
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.target_type == "announcement")
                )
            ).scalars()
        )
    assert logs
    assert all("# 仅详情可见" not in str(log.metadata_json) for log in logs)


async def test_clone_rejects_reused_row_version() -> None:
    department_id = await _department("公告复制部门")
    admin = await _user(
        "clone-api-admin@company.com", department_id=department_id, role="system_admin"
    )
    payload = {
        "title": "复制接口公告",
        "body_markdown": "正文",
        "audience_type": "all",
        "department_ids": [],
        "roles": [],
        "visible_from": None,
        "expires_at": None,
        "is_pinned": False,
    }

    async with _api_client(admin) as client:
        created = await client.post("/api/admin/announcements", json=payload)
        announcement_id = created.json()["data"]["id"]
        row_version = created.json()["data"]["row_version"]

        first = await client.post(
            f"/api/admin/announcements/{announcement_id}/clone",
            json={"row_version": row_version},
        )
        duplicate = await client.post(
            f"/api/admin/announcements/{announcement_id}/clone",
            json={"row_version": row_version},
        )

    assert first.status_code == 200
    assert duplicate.status_code == 409
