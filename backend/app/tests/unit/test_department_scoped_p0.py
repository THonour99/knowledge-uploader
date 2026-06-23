from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient, Response
from redis.asyncio import from_url
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.tests.conftest import TEST_ALEMBIC_DATABASE_URL

UNASSIGNED_DEPARTMENT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
PASSWORD = "password123"
PDF_BYTES = (
    b"%PDF-1.4\n"
    b"1 0 obj\n<< /Type /Catalog >>\nendobj\n"
    b"xref\n0 1\n0000000000 65535 f \n"
    b"trailer\n<< /Root 1 0 R >>\n"
    b"startxref\n9\n%%EOF\n"
)


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _alembic_config() -> Any:
    from alembic.config import Config

    backend_root = _backend_root()
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "app/db/migrations"))
    config.set_main_option("sqlalchemy.url", TEST_ALEMBIC_DATABASE_URL)
    return config


def _reset_public_schema_sync() -> None:
    engine = create_engine(TEST_ALEMBIC_DATABASE_URL)
    try:
        with engine.begin() as connection:
            connection.execute(text("drop schema if exists public cascade"))
            connection.execute(text("create schema public"))
    finally:
        engine.dispose()


def test_department_migrations_seed_backfill_and_upgrade_legacy_role() -> None:
    from alembic import command

    config = _alembic_config()
    legacy_admin_id = uuid.uuid4()
    uploader_id = uuid.uuid4()
    file_id = uuid.uuid4()

    _reset_public_schema_sync()
    try:
        command.upgrade(config, "fa4c9d8e2b71")
        engine = create_engine(TEST_ALEMBIC_DATABASE_URL)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        insert into users (
                            id, name, email, email_domain, password_hash, role,
                            status, email_verified
                        )
                        values
                            (
                                :legacy_admin_id, 'legacy-admin',
                                'legacy-admin@company.com', 'company.com', 'x',
                                'knowledge_admin', 'active', true
                            ),
                            (
                                :uploader_id, 'uploader',
                                'uploader@company.com', 'company.com', 'x',
                                'employee', 'active', true
                            )
                        """
                    ),
                    {
                        "legacy_admin_id": legacy_admin_id,
                        "uploader_id": uploader_id,
                    },
                )
                connection.execute(
                    text(
                        """
                        insert into files (
                            id, original_name, stored_name, extension, mime_type,
                            size, hash, storage_type, bucket, object_key, uploader_id,
                            department, visibility, status, review_status,
                            ai_analysis_enabled_at_upload
                        )
                        values (
                            :file_id, 'legacy.pdf', 'legacy.pdf', 'pdf',
                            'application/pdf', 128, :hash_value, 'minio',
                            'knowledge-files', 'uploads/legacy.pdf', :uploader_id,
                            'Unknown Legacy Department', 'private', 'uploaded',
                            'pending', false
                        )
                        """
                    ),
                    {
                        "file_id": file_id,
                        "hash_value": "a" * 64,
                        "uploader_id": uploader_id,
                    },
                )
        finally:
            engine.dispose()

        command.upgrade(config, "20260623d003")

        engine = create_engine(TEST_ALEMBIC_DATABASE_URL)
        try:
            with engine.connect() as connection:
                unassigned = (
                    connection.execute(
                        text(
                            """
                        select id, code, status
                        from departments
                        where id = :department_id
                        """
                        ),
                        {"department_id": UNASSIGNED_DEPARTMENT_ID},
                    )
                    .mappings()
                    .one()
                )
                migrated_user = (
                    connection.execute(
                        text(
                            """
                        select role, department_id
                        from users
                        where id = :user_id
                        """
                        ),
                        {"user_id": legacy_admin_id},
                    )
                    .mappings()
                    .one()
                )
                migrated_file = (
                    connection.execute(
                        text(
                            """
                        select department_id, department
                        from files
                        where id = :file_id
                        """
                        ),
                        {"file_id": file_id},
                    )
                    .mappings()
                    .one()
                )

            assert unassigned["id"] == UNASSIGNED_DEPARTMENT_ID
            assert unassigned["code"] == "unassigned"
            assert unassigned["status"] == "active"
            assert migrated_user["role"] == "system_admin"
            assert migrated_user["department_id"] == UNASSIGNED_DEPARTMENT_ID
            assert migrated_file["department_id"] == UNASSIGNED_DEPARTMENT_ID
            assert migrated_file["department"] == "Unknown Legacy Department"
        finally:
            engine.dispose()
    finally:
        _reset_public_schema_sync()


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


@pytest.fixture
async def p0_client() -> AsyncGenerator[AsyncClient, None]:
    from app.core.config import Settings
    from app.core.database import AsyncSessionFactory, engine, get_session
    from app.core.deps import get_app_settings
    from app.main import app

    await _reset_database()
    settings = Settings(
        allowed_email_domains="company.com",
        jwt_secret="test-jwt-secret-with-more-than-32-bytes",
        cache_redis_url=os.environ["CACHE_REDIS_URL"],
        require_email_verification=False,
        minio_bucket="test-knowledge-files",
        upload_allowed_extensions="pdf,txt",
        upload_allowed_mime_types="application/pdf,text/plain",
        upload_rate_limit_per_minute=20,
    )

    async def override_session() -> AsyncGenerator[AsyncSession, None]:
        async with AsyncSessionFactory() as session:
            yield session

    app.dependency_overrides[get_app_settings] = lambda: settings
    app.dependency_overrides[get_session] = override_session

    try:
        from app.modules.document.api import get_document_storage
    except ImportError:
        pass
    else:
        app.dependency_overrides[get_document_storage] = lambda: _FakeDocumentStorage()

    transport = ASGITransport(app=app)  # type: ignore[arg-type]
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client

    app.dependency_overrides.clear()
    from app.db.base import Base

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()


class _FakeDocumentStorage:
    async def put_object(
        self,
        *,
        bucket: str,
        object_key: str,
        data: bytes,
        content_type: str,
    ) -> None:
        return None

    async def delete_object(self, *, bucket: str, object_key: str) -> None:
        return None


async def _create_department(
    *,
    name: str,
    code: str,
    status: str = "active",
) -> uuid.UUID:
    from app.core.database import AsyncSessionFactory
    from app.modules.department.models import Department

    department = Department(name=name, code=code, status=status)
    async with AsyncSessionFactory() as session:
        session.add(department)
        await session.commit()
        await session.refresh(department)
        return department.id


async def _disable_department(*, department_id: uuid.UUID) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.department.models import Department

    async with AsyncSessionFactory() as session:
        department = await session.get(Department, department_id)
        assert department is not None
        department.status = "disabled"
        await session.commit()


async def _create_user(
    *,
    email: str,
    role: str = "employee",
    department_id: uuid.UUID = UNASSIGNED_DEPARTMENT_ID,
    department: str | None = "Unassigned",
    status: str = "active",
) -> uuid.UUID:
    from app.core.database import AsyncSessionFactory
    from app.core.security import hash_password
    from app.modules.user.models import User

    normalized_email = email.lower()
    user = User(
        name=email.split("@", 1)[0],
        email=normalized_email,
        email_domain=normalized_email.rsplit("@", 1)[1],
        password_hash=hash_password(PASSWORD),
        department_id=department_id,
        department=department,
        role=role,
        status=status,
        email_verified=True,
    )
    async with AsyncSessionFactory() as session:
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user.id


async def _login(client: AsyncClient, *, email: str) -> str:
    response = await client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return str(response.json()["data"]["access_token"])


async def _assign_managed_departments(
    *,
    user_id: uuid.UUID,
    department_ids: list[uuid.UUID],
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.department.models import UserManagedDepartment

    async with AsyncSessionFactory() as session:
        for department_id in department_ids:
            session.add(UserManagedDepartment(user_id=user_id, department_id=department_id))
        await session.commit()


async def _clear_managed_departments(*, user_id: uuid.UUID) -> None:
    from sqlalchemy import delete

    from app.core.database import AsyncSessionFactory
    from app.modules.department.models import UserManagedDepartment

    async with AsyncSessionFactory() as session:
        await session.execute(
            delete(UserManagedDepartment).where(UserManagedDepartment.user_id == user_id)
        )
        await session.commit()


async def _managed_department_ids(*, user_id: uuid.UUID) -> set[uuid.UUID]:
    from app.core.database import AsyncSessionFactory
    from app.modules.department.models import UserManagedDepartment

    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(UserManagedDepartment.department_id).where(
                UserManagedDepartment.user_id == user_id
            )
        )
        return set(result.scalars())


async def _create_category_and_dataset() -> tuple[uuid.UUID, uuid.UUID]:
    from app.core.database import AsyncSessionFactory
    from app.modules.review.models import Category, DatasetMapping

    category_id = uuid.uuid4()
    dataset_id = uuid.uuid4()
    category = Category(
        id=category_id,
        name=f"Category {uuid.uuid4().hex[:8]}",
        code=f"cat-{uuid.uuid4().hex[:8]}",
        require_review=True,
        auto_sync_enabled=True,
    )
    dataset = DatasetMapping(
        id=dataset_id,
        name=f"Dataset {uuid.uuid4().hex[:8]}",
        category_id=category_id,
        ragflow_dataset_id=f"ragflow-{uuid.uuid4().hex[:8]}",
        ragflow_dataset_name="RAGFlow Dataset",
        enabled=True,
    )
    async with AsyncSessionFactory() as session:
        session.add(category)
        session.add(dataset)
        await session.commit()
        await session.refresh(category)
        await session.refresh(dataset)
        return category.id, dataset.id


async def _create_file(
    *,
    uploader_id: uuid.UUID,
    department_id: uuid.UUID,
    department: str,
    status: str = "pending_review",
    review_status: str = "pending",
    dataset_mapping_id: uuid.UUID | None = None,
    ragflow_dataset_id: str | None = None,
) -> uuid.UUID:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File

    file_id = uuid.uuid4()
    file = File(
        id=file_id,
        original_name=f"{file_id}.pdf",
        stored_name=f"{file_id}.pdf",
        extension="pdf",
        mime_type="application/pdf",
        size=128,
        hash=uuid.uuid4().hex + uuid.uuid4().hex,
        storage_type="minio",
        bucket="knowledge-files",
        object_key=f"uploads/{uploader_id}/{file_id}.pdf",
        uploader_id=uploader_id,
        department_id=department_id,
        department=department,
        dataset_mapping_id=dataset_mapping_id,
        visibility="private",
        description="department scoped p0 target",
        tags=[],
        status=status,
        review_status=review_status,
        ragflow_dataset_id=ragflow_dataset_id,
        ai_analysis_enabled_at_upload=False,
        uploaded_at=datetime.now(UTC),
    )
    async with AsyncSessionFactory() as session:
        session.add(file)
        await session.commit()
        return file_id


async def _create_sync_task(*, file_id: uuid.UUID, status: str = "failed") -> uuid.UUID:
    from app.core.database import AsyncSessionFactory
    from app.modules.ragflow.models import SyncTask

    task = SyncTask(
        file_id=file_id,
        task_type="ragflow_upload",
        status=status,
        retry_count=0,
        max_retry_count=3,
        error_message="network timeout" if status == "failed" else None,
    )
    async with AsyncSessionFactory() as session:
        session.add(task)
        await session.commit()
        await session.refresh(task)
        return task.id


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _assert_hidden_or_forbidden(response: Response) -> None:
    assert response.status_code in (403, 404), response.text
    if response.status_code == 403:
        assert response.json()["error_code"] == "PERMISSION_DENIED"


def _managed_ids_from_payload(payload: dict[str, object]) -> set[str]:
    data = payload["data"]
    assert isinstance(data, dict)
    if "department_ids" in data:
        department_ids = data["department_ids"]
        assert isinstance(department_ids, list)
        return {str(item) for item in department_ids}
    departments = data.get("departments")
    assert isinstance(departments, list)
    return {str(item["id"]) for item in departments if isinstance(item, dict)}


@pytest.mark.asyncio
async def test_department_api_is_system_admin_only_and_disables_departments(
    p0_client: AsyncClient,
) -> None:
    system_admin_id = await _create_user(
        email="dept-root@company.com",
        role="system_admin",
    )
    dept_admin_id = await _create_user(
        email="dept-local-admin@company.com",
        role="dept_admin",
    )
    system_token = await _login(p0_client, email="dept-root@company.com")
    dept_token = await _login(p0_client, email="dept-local-admin@company.com")

    create_response = await p0_client.post(
        "/api/admin/departments",
        headers=_auth(system_token),
        json={"name": "Finance", "code": "finance"},
    )
    forbidden_response = await p0_client.get(
        "/api/admin/departments",
        headers=_auth(dept_token),
    )

    assert create_response.status_code == 201, create_response.text
    finance = create_response.json()["data"]
    assert finance["name"] == "Finance"
    assert finance["code"] == "finance"
    assert forbidden_response.status_code == 403

    list_response = await p0_client.get(
        "/api/admin/departments",
        headers=_auth(system_token),
    )
    assert list_response.status_code == 200
    codes = {item["code"] for item in list_response.json()["data"]["items"]}
    assert {"unassigned", "finance"}.issubset(codes)

    unassigned_delete = await p0_client.delete(
        f"/api/admin/departments/{UNASSIGNED_DEPARTMENT_ID}",
        headers=_auth(system_token),
    )
    disable_response = await p0_client.delete(
        f"/api/admin/departments/{finance['id']}",
        headers=_auth(system_token),
    )

    assert unassigned_delete.status_code in (400, 409)
    assert unassigned_delete.json()["error_code"] == "UNASSIGNED_DEPARTMENT_IMMUTABLE"
    assert disable_response.status_code in (200, 204)
    if disable_response.status_code == 200:
        assert disable_response.json()["data"] == {}
    assert system_admin_id != dept_admin_id


@pytest.mark.asyncio
async def test_managed_department_authorization_requires_dept_admin_and_clears_on_demotion(
    p0_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.audit.models import AuditLog

    sales_id = await _create_department(name="Sales", code="sales")
    support_id = await _create_department(name="Support", code="support")
    system_admin_id = await _create_user(email="scope-root@company.com", role="system_admin")
    target_id = await _create_user(email="scope-target@company.com", role="employee")
    token = await _login(p0_client, email="scope-root@company.com")

    promote_response = await p0_client.patch(
        f"/api/users/{target_id}/role",
        headers=_auth(token),
        json={"role": "dept_admin"},
    )
    initial_scope = await p0_client.get(
        f"/api/admin/users/{target_id}/managed-departments",
        headers=_auth(token),
    )
    assign_response = await p0_client.put(
        f"/api/admin/users/{target_id}/managed-departments",
        headers=_auth(token),
        json={"department_ids": [str(sales_id), str(support_id)]},
    )
    demote_response = await p0_client.patch(
        f"/api/users/{target_id}/role",
        headers=_auth(token),
        json={"role": "employee"},
    )
    rejected_assign = await p0_client.put(
        f"/api/admin/users/{target_id}/managed-departments",
        headers=_auth(token),
        json={"department_ids": [str(sales_id)]},
    )

    assert promote_response.status_code == 200, promote_response.text
    assert promote_response.json()["data"]["role"] == "dept_admin"
    assert initial_scope.status_code == 200
    assert _managed_ids_from_payload(initial_scope.json()) == set()
    assert assign_response.status_code == 200, assign_response.text
    assert _managed_ids_from_payload(assign_response.json()) == {str(sales_id), str(support_id)}
    assert demote_response.status_code == 200
    assert demote_response.json()["data"]["role"] == "employee"
    assert await _managed_department_ids(user_id=target_id) == set()
    assert rejected_assign.status_code in (400, 409)
    assert rejected_assign.json()["error_code"] == "MANAGED_DEPARTMENTS_REQUIRE_DEPT_ADMIN"

    async with AsyncSessionFactory() as session:
        result = await session.execute(select(AuditLog).where(AuditLog.actor_id == system_admin_id))
        actions = {log.action for log in result.scalars()}

    assert "user.managed_departments.replace" in actions
    assert "user.role.change" in actions


@pytest.mark.asyncio
async def test_upload_ignores_forged_department_id_and_uses_uploader_department(
    p0_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File

    finance_id = await _create_department(name="Upload Finance", code="upload-finance")
    legal_id = await _create_department(name="Upload Legal", code="upload-legal")
    uploader_id = await _create_user(
        email="upload-forger@company.com",
        department_id=finance_id,
        department="Upload Finance",
    )
    token = await _login(p0_client, email="upload-forger@company.com")

    response = await p0_client.post(
        "/api/files/upload",
        headers=_auth(token),
        files={"file": ("forged.pdf", PDF_BYTES, "application/pdf")},
        data={"department_id": str(legal_id), "visibility": "company"},
    )

    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["uploader_id"] == str(uploader_id)
    assert data["department_id"] == str(finance_id)
    assert data["department_code"] == "upload-finance"

    async with AsyncSessionFactory() as session:
        saved_file = await session.get(File, uuid.UUID(data["id"]))

    assert saved_file is not None
    assert saved_file.department_id == finance_id
    assert saved_file.department == "Upload Finance"


@pytest.mark.asyncio
async def test_dept_admin_empty_scope_lists_no_review_files_or_tasks(
    p0_client: AsyncClient,
) -> None:
    finance_id = await _create_department(name="Empty Finance", code="empty-finance")
    legal_id = await _create_department(name="Empty Legal", code="empty-legal")
    finance_uploader_id = await _create_user(
        email="empty-finance-owner@company.com",
        department_id=finance_id,
        department="Empty Finance",
    )
    legal_uploader_id = await _create_user(
        email="empty-legal-owner@company.com",
        department_id=legal_id,
        department="Empty Legal",
    )
    await _create_user(email="empty-scope-admin@company.com", role="dept_admin")
    token = await _login(p0_client, email="empty-scope-admin@company.com")
    finance_file_id = await _create_file(
        uploader_id=finance_uploader_id,
        department_id=finance_id,
        department="Empty Finance",
    )
    legal_file_id = await _create_file(
        uploader_id=legal_uploader_id,
        department_id=legal_id,
        department="Empty Legal",
    )
    await _create_sync_task(file_id=finance_file_id)
    await _create_sync_task(file_id=legal_file_id)

    review_response = await p0_client.get("/api/review/files", headers=_auth(token))
    tasks_response = await p0_client.get("/api/tasks", headers=_auth(token))

    assert review_response.status_code == 200, review_response.text
    assert review_response.json()["data"]["total"] == 0
    assert review_response.json()["data"]["items"] == []
    assert tasks_response.status_code == 200, tasks_response.text
    assert tasks_response.json()["data"]["total"] == 0
    assert tasks_response.json()["data"]["items"] == []


@pytest.mark.asyncio
async def test_dept_admin_scope_isolates_files_review_actions_and_ragflow_tasks(
    p0_client: AsyncClient,
) -> None:
    finance_id = await _create_department(name="Scoped Finance", code="scoped-finance")
    legal_id = await _create_department(name="Scoped Legal", code="scoped-legal")
    finance_uploader_id = await _create_user(
        email="finance-owner@company.com",
        department_id=finance_id,
        department="Scoped Finance",
    )
    legal_uploader_id = await _create_user(
        email="legal-owner@company.com",
        department_id=legal_id,
        department="Scoped Legal",
    )
    admin_id = await _create_user(email="finance-admin@company.com", role="dept_admin")
    await _assign_managed_departments(user_id=admin_id, department_ids=[finance_id])
    token = await _login(p0_client, email="finance-admin@company.com")
    category_id, dataset_id = await _create_category_and_dataset()
    finance_file_id = await _create_file(
        uploader_id=finance_uploader_id,
        department_id=finance_id,
        department="Scoped Finance",
        dataset_mapping_id=dataset_id,
        ragflow_dataset_id="ragflow-finance",
    )
    legal_file_id = await _create_file(
        uploader_id=legal_uploader_id,
        department_id=legal_id,
        department="Scoped Legal",
        dataset_mapping_id=dataset_id,
        ragflow_dataset_id="ragflow-legal",
    )
    finance_task_id = await _create_sync_task(file_id=finance_file_id)
    legal_task_id = await _create_sync_task(file_id=legal_file_id)

    review_list = await p0_client.get("/api/review/files", headers=_auth(token))
    file_detail = await p0_client.get(f"/api/files/{finance_file_id}", headers=_auth(token))
    hidden_detail = await p0_client.get(f"/api/files/{legal_file_id}", headers=_auth(token))
    hidden_approve = await p0_client.post(
        f"/api/files/{legal_file_id}/approve",
        headers=_auth(token),
        json={"category_id": str(category_id), "dataset_mapping_id": str(dataset_id)},
    )
    task_list = await p0_client.get("/api/tasks", headers=_auth(token))
    task_detail = await p0_client.get(f"/api/tasks/{finance_task_id}", headers=_auth(token))
    hidden_task_detail = await p0_client.get(
        f"/api/tasks/{legal_task_id}",
        headers=_auth(token),
    )
    hidden_task_retry = await p0_client.post(
        f"/api/tasks/{legal_task_id}/retry",
        headers=_auth(token),
    )

    assert review_list.status_code == 200, review_list.text
    listed_file_ids = {item["id"] for item in review_list.json()["data"]["items"]}
    assert str(finance_file_id) in listed_file_ids
    assert str(legal_file_id) not in listed_file_ids
    assert file_detail.status_code == 200, file_detail.text
    assert file_detail.json()["data"]["department_id"] == str(finance_id)
    _assert_hidden_or_forbidden(hidden_detail)
    _assert_hidden_or_forbidden(hidden_approve)
    assert task_list.status_code == 200, task_list.text
    listed_task_ids = {item["id"] for item in task_list.json()["data"]["items"]}
    assert str(finance_task_id) in listed_task_ids
    assert str(legal_task_id) not in listed_task_ids
    assert task_detail.status_code == 200, task_detail.text
    _assert_hidden_or_forbidden(hidden_task_detail)
    _assert_hidden_or_forbidden(hidden_task_retry)


@pytest.mark.asyncio
async def test_self_review_is_denied_by_default_for_dept_and_system_admins(
    p0_client: AsyncClient,
) -> None:
    department_id = await _create_department(name="Self Review", code="self-review")
    category_id, dataset_id = await _create_category_and_dataset()
    dept_admin_id = await _create_user(
        email="self-dept-admin@company.com",
        role="dept_admin",
        department_id=department_id,
        department="Self Review",
    )
    await _assign_managed_departments(user_id=dept_admin_id, department_ids=[department_id])
    dept_token = await _login(p0_client, email="self-dept-admin@company.com")
    dept_file_id = await _create_file(
        uploader_id=dept_admin_id,
        department_id=department_id,
        department="Self Review",
        dataset_mapping_id=dataset_id,
        ragflow_dataset_id="ragflow-self-dept",
    )

    await _create_user(
        email="non-self-reviewer@company.com",
        role="dept_admin",
        department_id=department_id,
        department="Self Review",
    )
    system_admin_id = await _create_user(
        email="self-system-admin@company.com",
        role="system_admin",
        department_id=department_id,
        department="Self Review",
    )
    system_token = await _login(p0_client, email="self-system-admin@company.com")
    await _assign_managed_departments(
        user_id=await _create_user(email="other-dept-admin@company.com", role="dept_admin"),
        department_ids=[department_id],
    )
    system_file_id = await _create_file(
        uploader_id=system_admin_id,
        department_id=department_id,
        department="Self Review",
        dataset_mapping_id=dataset_id,
        ragflow_dataset_id="ragflow-self-system",
    )

    dept_approve = await p0_client.post(
        f"/api/files/{dept_file_id}/approve",
        headers=_auth(dept_token),
        json={"category_id": str(category_id), "dataset_mapping_id": str(dataset_id)},
    )
    dept_reject = await p0_client.post(
        f"/api/files/{dept_file_id}/reject",
        headers=_auth(dept_token),
        json={"reason": "self review"},
    )
    system_approve = await p0_client.post(
        f"/api/files/{system_file_id}/approve",
        headers=_auth(system_token),
        json={"category_id": str(category_id), "dataset_mapping_id": str(dataset_id)},
    )

    assert dept_approve.status_code == 403
    assert dept_reject.status_code == 403
    assert system_approve.status_code == 403
    assert dept_approve.json()["error_code"] == "PERMISSION_DENIED"
    assert system_approve.json()["error_code"] == "PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_revoked_managed_department_blocks_old_file_and_task_ids(
    p0_client: AsyncClient,
) -> None:
    finance_id = await _create_department(name="Revoked Finance", code="revoked-finance")
    uploader_id = await _create_user(
        email="revoked-owner@company.com",
        department_id=finance_id,
        department="Revoked Finance",
    )
    admin_id = await _create_user(email="revoked-admin@company.com", role="dept_admin")
    await _assign_managed_departments(user_id=admin_id, department_ids=[finance_id])
    token = await _login(p0_client, email="revoked-admin@company.com")
    category_id, dataset_id = await _create_category_and_dataset()
    file_id = await _create_file(
        uploader_id=uploader_id,
        department_id=finance_id,
        department="Revoked Finance",
        dataset_mapping_id=dataset_id,
        ragflow_dataset_id="ragflow-revoked",
    )
    task_id = await _create_sync_task(file_id=file_id)

    before_file = await p0_client.get(f"/api/files/{file_id}", headers=_auth(token))
    before_task = await p0_client.get(f"/api/tasks/{task_id}", headers=_auth(token))
    await _clear_managed_departments(user_id=admin_id)
    after_review_list = await p0_client.get("/api/review/files", headers=_auth(token))
    after_file = await p0_client.get(f"/api/files/{file_id}", headers=_auth(token))
    after_approve = await p0_client.post(
        f"/api/files/{file_id}/approve",
        headers=_auth(token),
        json={"category_id": str(category_id), "dataset_mapping_id": str(dataset_id)},
    )
    after_task = await p0_client.get(f"/api/tasks/{task_id}", headers=_auth(token))
    after_retry = await p0_client.post(f"/api/tasks/{task_id}/retry", headers=_auth(token))

    assert before_file.status_code == 200, before_file.text
    assert before_task.status_code == 200, before_task.text
    assert after_review_list.status_code == 200, after_review_list.text
    assert after_review_list.json()["data"]["total"] == 0
    _assert_hidden_or_forbidden(after_file)
    _assert_hidden_or_forbidden(after_approve)
    _assert_hidden_or_forbidden(after_task)
    _assert_hidden_or_forbidden(after_retry)


@pytest.mark.asyncio
async def test_disabled_managed_department_blocks_old_file_delete_and_submit_ids(
    p0_client: AsyncClient,
) -> None:
    finance_id = await _create_department(name="Disabled Finance", code="disabled-finance")
    uploader_id = await _create_user(
        email="disabled-owner@company.com",
        department_id=finance_id,
        department="Disabled Finance",
    )
    admin_id = await _create_user(email="disabled-admin@company.com", role="dept_admin")
    await _assign_managed_departments(user_id=admin_id, department_ids=[finance_id])
    token = await _login(p0_client, email="disabled-admin@company.com")
    detail_file_id = await _create_file(
        uploader_id=uploader_id,
        department_id=finance_id,
        department="Disabled Finance",
    )
    delete_file_id = await _create_file(
        uploader_id=uploader_id,
        department_id=finance_id,
        department="Disabled Finance",
    )
    submit_file_id = await _create_file(
        uploader_id=uploader_id,
        department_id=finance_id,
        department="Disabled Finance",
        status="uploaded",
    )

    before_detail = await p0_client.get(f"/api/files/{detail_file_id}", headers=_auth(token))
    before_profile = await p0_client.get("/api/auth/me", headers=_auth(token))
    await _disable_department(department_id=finance_id)
    after_profile = await p0_client.get("/api/auth/me", headers=_auth(token))
    after_review_list = await p0_client.get("/api/review/files", headers=_auth(token))
    after_detail = await p0_client.get(f"/api/files/{detail_file_id}", headers=_auth(token))
    after_delete = await p0_client.delete(f"/api/files/{delete_file_id}", headers=_auth(token))
    after_submit = await p0_client.post(
        f"/api/files/{submit_file_id}/submit-review",
        headers=_auth(token),
    )

    assert before_detail.status_code == 200, before_detail.text
    assert before_profile.status_code == 200, before_profile.text
    assert before_profile.json()["data"]["managed_department_ids"] == [str(finance_id)]
    assert await _managed_department_ids(user_id=admin_id) == {finance_id}
    assert after_profile.status_code == 200, after_profile.text
    assert after_profile.json()["data"]["managed_department_ids"] == []
    assert after_review_list.status_code == 200, after_review_list.text
    assert after_review_list.json()["data"]["total"] == 0
    assert after_review_list.json()["data"]["items"] == []
    _assert_hidden_or_forbidden(after_detail)
    _assert_hidden_or_forbidden(after_delete)
    _assert_hidden_or_forbidden(after_submit)
