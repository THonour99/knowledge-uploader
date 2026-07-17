from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from importlib import import_module
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


async def _reset_database() -> None:
    import_module("app.db.models")
    import_module("app.modules.saved_view.models")

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


async def _create_department(*, name: str, code: str, status: str = "active") -> uuid.UUID:
    from app.core.database import AsyncSessionFactory
    from app.modules.department.models import Department

    department = Department(name=name, code=code, status=status)
    async with AsyncSessionFactory() as session:
        session.add(department)
        await session.commit()
        await session.refresh(department)
    return department.id


async def _create_user(
    *,
    email: str,
    role: str,
    department_id: uuid.UUID,
) -> Any:
    from app.core.database import AsyncSessionFactory
    from app.modules.user.models import User
    from app.modules.user.schemas import AuthUserRecord

    user = User(
        name=email.split("@", 1)[0],
        email=email,
        email_domain=email.rsplit("@", 1)[1],
        password_hash="not-used-by-overridden-auth",
        department_id=department_id,
        department="test",
        role=role,
        status="active",
        email_verified=True,
    )
    async with AsyncSessionFactory() as session:
        session.add(user)
        await session.commit()
        await session.refresh(user)
    return AuthUserRecord(
        id=user.id,
        name=user.name,
        email=user.email,
        email_domain=user.email_domain,
        password_hash=user.password_hash,
        role=user.role,
        status=user.status,
        email_verified=user.email_verified,
        department_id=user.department_id,
        department_name="test",
        department_code="test",
        department="test",
        phone=None,
        failed_login_count=0,
        locked_until=None,
        session_version=0,
    )


async def _manage(*, user_id: uuid.UUID, department_id: uuid.UUID) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.department.models import UserManagedDepartment

    async with AsyncSessionFactory() as session:
        session.add(UserManagedDepartment(user_id=user_id, department_id=department_id))
        await session.commit()


async def _revoke_all(*, user_id: uuid.UUID) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.department.models import UserManagedDepartment

    async with AsyncSessionFactory() as session:
        await session.execute(
            delete(UserManagedDepartment).where(UserManagedDepartment.user_id == user_id)
        )
        await session.commit()


def _app_for(current_user: Any) -> FastAPI:
    from app.core.database import AsyncSessionFactory, get_session
    from app.core.deps import get_current_user
    from app.modules.saved_view.api import router

    app = FastAPI()
    app.include_router(router)

    async def override_current_user() -> Any:
        return current_user

    async def override_session() -> AsyncGenerator[AsyncSession, None]:
        async with AsyncSessionFactory() as session:
            yield session

    app.dependency_overrides[get_current_user] = override_current_user
    app.dependency_overrides[get_session] = override_session
    return app


async def _request(
    current_user: Any,
    method: str,
    path: str,
    *,
    json: dict[str, object] | None = None,
) -> Response:
    transport = ASGITransport(app=_app_for(current_user))  # type: ignore[arg-type]
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.request(method, path, json=json)


def _create_payload(
    *,
    name: str,
    page_key: str = "my_files",
    scope: str = "private",
    department_id: uuid.UUID | None = None,
    query_definition: dict[str, object] | None = None,
    column_preferences: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "page_key": page_key,
        "name": name,
        "scope": scope,
        "department_id": str(department_id) if department_id else None,
        "definition_schema_version": 2,
        "query_definition": query_definition or {},
        "column_preferences": column_preferences or {},
    }


async def test_private_views_are_owner_only_and_page_permissions_are_enforced() -> None:
    department_id = await _create_department(name="研发", code="rd")
    owner = await _create_user(
        email="owner@company.com",
        role="employee",
        department_id=department_id,
    )
    stranger = await _create_user(
        email="stranger@company.com",
        role="employee",
        department_id=department_id,
    )

    created = await _request(
        owner,
        "POST",
        "/api/saved-views",
        json=_create_payload(
            name="我的待处理",
            query_definition={"status": "uploaded", "relationship": "responsible"},
        ),
    )
    assert created.status_code == 201, created.text
    saved_view_id = created.json()["data"]["id"]

    visible = await _request(owner, "GET", f"/api/saved-views/{saved_view_id}")
    assert visible.status_code == 200
    assert (
        visible.json()["data"]["effective_definition"]["query_definition"]["relationship"]
        == "responsible"
    )
    hidden = await _request(stranger, "GET", f"/api/saved-views/{saved_view_id}")
    assert hidden.status_code == 404
    assert hidden.json()["detail"]["error_code"] == "SAVED_VIEW_NOT_FOUND"

    department_scope = await _request(
        owner,
        "POST",
        "/api/saved-views",
        json=_create_payload(
            name="越权共享",
            scope="department",
            department_id=department_id,
        ),
    )
    assert department_scope.status_code == 422
    statistics = await _request(owner, "GET", "/api/saved-views?page_key=statistics")
    assert statistics.status_code == 422


async def test_department_access_is_live_scoped_and_system_admin_is_global() -> None:
    department_a = await _create_department(name="一部", code="d1")
    department_b = await _create_department(name="二部", code="d2")
    owner = await _create_user(
        email="owner-admin@company.com",
        role="dept_admin",
        department_id=department_a,
    )
    peer = await _create_user(
        email="peer-admin@company.com",
        role="dept_admin",
        department_id=department_a,
    )
    system_admin = await _create_user(
        email="system@company.com",
        role="system_admin",
        department_id=department_b,
    )
    await _manage(user_id=owner.id, department_id=department_a)
    await _manage(user_id=peer.id, department_id=department_a)

    fake_department = await _request(
        owner,
        "POST",
        "/api/saved-views",
        json=_create_payload(
            name="伪造部门",
            page_key="review_files",
            scope="department",
            department_id=department_b,
        ),
    )
    assert fake_department.status_code == 422

    mismatch = await _request(
        owner,
        "POST",
        "/api/saved-views",
        json=_create_payload(
            name="过滤越界",
            page_key="review_files",
            scope="department",
            department_id=department_a,
            query_definition={"department_id": str(department_b)},
        ),
    )
    assert mismatch.status_code == 422

    created = await _request(
        owner,
        "POST",
        "/api/saved-views",
        json=_create_payload(
            name="部门待审核",
            page_key="review_files",
            scope="department",
            department_id=department_a,
            query_definition={"queue": "overdue"},
        ),
    )
    assert created.status_code == 201, created.text
    data = created.json()["data"]
    saved_view_id = data["id"]
    assert data["effective_definition"]["query_definition"]["department_id"] == str(department_a)

    assert (await _request(peer, "GET", f"/api/saved-views/{saved_view_id}")).status_code == 200
    peer_patch = await _request(
        peer,
        "PATCH",
        f"/api/saved-views/{saved_view_id}",
        json={"row_version": 1, "name": "不允许改"},
    )
    assert peer_patch.status_code == 404

    await _revoke_all(user_id=peer.id)
    assert (await _request(peer, "GET", f"/api/saved-views/{saved_view_id}")).status_code == 404
    assert (
        await _request(system_admin, "GET", f"/api/saved-views/{saved_view_id}")
    ).status_code == 200

    await _revoke_all(user_id=owner.id)
    assert (await _request(owner, "GET", f"/api/saved-views/{saved_view_id}")).status_code == 404
    deleted = await _request(system_admin, "DELETE", f"/api/saved-views/{saved_view_id}")
    assert deleted.status_code == 204


@pytest.mark.parametrize(
    "query_definition",
    [
        {"items": []},
        {"results": []},
        {"rows": []},
        {"file_ids": []},
        {"total": 1},
        {"url": "https://internal.example"},
        {"token": "secret"},
        {"permissions": ["admin"]},
        {"managed_department_ids": []},
        {"page": 2},
        {"deepLink": "/admin"},
        {"unknown_filter": "value"},
    ],
)
async def test_query_definition_rejects_results_permissions_and_unknown_fields(
    query_definition: dict[str, object],
) -> None:
    department_id = await _create_department(name="安全", code="sec")
    employee = await _create_user(
        email="security@company.com",
        role="employee",
        department_id=department_id,
    )
    response = await _request(
        employee,
        "POST",
        "/api/saved-views",
        json=_create_payload(name="恶意视图", query_definition=query_definition),
    )
    assert response.status_code == 422


async def test_definition_size_depth_urls_and_columns_are_bounded() -> None:
    department_id = await _create_department(name="边界", code="bound")
    employee = await _create_user(
        email="bounds@company.com",
        role="employee",
        department_id=department_id,
    )
    invalid_definitions: list[tuple[dict[str, object], dict[str, object]]] = [
        ({"q": "x" * 9000}, {}),
        ({"q": {"a": {"b": {"c": {"d": "x"}}}}}, {}),
        ({"q": "https://example.com/private"}, {}),
        ({"q": chr(0xD800)}, {}),
        ({}, {"visible": ["ragflow_api_key"]}),
        ({}, {"visible": ["original_name"] * 2}),
    ]
    for index, (query_definition, column_preferences) in enumerate(invalid_definitions):
        response = await _request(
            employee,
            "POST",
            "/api/saved-views",
            json=_create_payload(
                name=f"边界-{index}",
                query_definition=query_definition,
                column_preferences=column_preferences,
            ),
        )
        assert response.status_code == 422, response.text


async def test_optimistic_lock_unique_name_pagination_and_admin_audit() -> None:
    department_id = await _create_department(name="审计", code="audit")
    admin = await _create_user(
        email="audit-admin@company.com",
        role="dept_admin",
        department_id=department_id,
    )
    await _manage(user_id=admin.id, department_id=department_id)

    first = await _request(
        admin,
        "POST",
        "/api/saved-views",
        json=_create_payload(
            name="Ops",
            page_key="review_files",
            query_definition={"queue": "mine"},
        ),
    )
    assert first.status_code == 201, first.text
    saved_view_id = first.json()["data"]["id"]

    surrogate_update = await _request(
        admin,
        "PATCH",
        f"/api/saved-views/{saved_view_id}",
        json={"row_version": 1, "query_definition": {"q": chr(0xD800)}},
    )
    assert surrogate_update.status_code == 422

    duplicate = await _request(
        admin,
        "POST",
        "/api/saved-views",
        json=_create_payload(name=" ops ", page_key="review_files"),
    )
    assert duplicate.status_code == 409

    updated = await _request(
        admin,
        "PATCH",
        f"/api/saved-views/{saved_view_id}",
        json={"row_version": 1, "name": "Ops-2"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["data"]["row_version"] == 2
    stale = await _request(
        admin,
        "PATCH",
        f"/api/saved-views/{saved_view_id}",
        json={"row_version": 1, "name": "stale"},
    )
    assert stale.status_code == 409

    for name in ("Ops-3", "Ops-4"):
        response = await _request(
            admin,
            "POST",
            "/api/saved-views",
            json=_create_payload(name=name, page_key="review_files"),
        )
        assert response.status_code == 201
    page = await _request(
        admin,
        "GET",
        "/api/saved-views?page_key=review_files&page=2&page_size=2",
    )
    assert page.status_code == 200
    assert page.json()["data"]["total"] == 3
    assert page.json()["data"]["total_pages"] == 2
    assert len(page.json()["data"]["items"]) == 1

    from app.core.database import AsyncSessionFactory
    from app.modules.audit.models import AuditLog

    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(AuditLog).where(
                AuditLog.actor_id == admin.id,
                AuditLog.target_type == "saved_view",
            )
        )
        audits = list(result.scalars())
    assert {audit.action for audit in audits} == {"saved_view.created", "saved_view.updated"}
    assert len(audits) == 4
    for audit in audits:
        serialized = str(audit.metadata_json)
        assert "queue" not in serialized
        assert "Ops" not in serialized
        assert len(str(audit.metadata_json["definition_sha256"])) == 64


async def test_sequential_schema_migration_and_newer_schema_read_only() -> None:
    department_id = await _create_department(name="兼容", code="compat")
    admin = await _create_user(
        email="compat-admin@company.com",
        role="system_admin",
        department_id=department_id,
    )

    from app.core.database import AsyncSessionFactory
    from app.modules.saved_view.models import SavedView

    legacy = SavedView(
        owner_id=admin.id,
        scope="private",
        page_key="my_files",
        name="legacy",
        definition_schema_version=1,
        query_definition={"search": "quarterly"},
        column_preferences={"columns": ["original_name"]},
    )
    newer = SavedView(
        owner_id=admin.id,
        scope="private",
        page_key="my_files",
        name="future",
        definition_schema_version=99,
        query_definition={"future_filter": True},
        column_preferences={},
    )
    async with AsyncSessionFactory() as session:
        session.add_all([legacy, newer])
        await session.commit()
        await session.refresh(legacy)
        await session.refresh(newer)

    legacy_response = await _request(admin, "GET", f"/api/saved-views/{legacy.id}")
    assert legacy_response.status_code == 200
    legacy_data = legacy_response.json()["data"]
    assert legacy_data["stored_schema_version"] == 1
    assert legacy_data["effective_schema_version"] == 2
    assert legacy_data["compatibility"] == "migrated"
    assert legacy_data["effective_definition"]["query_definition"]["q"] == "quarterly"
    assert legacy_data["effective_definition"]["column_preferences"]["visible"] == ["original_name"]

    newer_response = await _request(admin, "GET", f"/api/saved-views/{newer.id}")
    assert newer_response.status_code == 200
    newer_data = newer_response.json()["data"]
    assert newer_data["compatibility"] == "unsupported"
    assert newer_data["effective_schema_version"] is None
    assert newer_data["effective_definition"] is None

    update = await _request(
        admin,
        "PATCH",
        f"/api/saved-views/{newer.id}",
        json={"row_version": 1, "name": "cannot-update"},
    )
    assert update.status_code == 409
    assert (await _request(admin, "DELETE", f"/api/saved-views/{newer.id}")).status_code == 204
