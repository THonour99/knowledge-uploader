from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from redis.asyncio import from_url
from sqlalchemy import select

pytestmark = pytest.mark.asyncio


async def _reset_database() -> None:
    from importlib import import_module

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
async def task_client() -> AsyncGenerator[AsyncClient, None]:
    from sqlalchemy.ext.asyncio import AsyncSession

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


async def _create_file(
    *,
    uploader_id: UUID,
    status_value: str = "pending_review",
    review_status: str = "in_review",
    hash_value: str = "b" * 64,
) -> UUID:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File

    file = File(
        original_name="phase4-handbook.pdf",
        stored_name="file-phase4-handbook.pdf",
        extension="pdf",
        mime_type="application/pdf",
        size=128,
        hash=hash_value,
        storage_type="minio",
        bucket="knowledge-files",
        object_key=f"uploads/{uploader_id}/file-phase4-handbook.pdf",
        uploader_id=uploader_id,
        department="QA",
        visibility="private",
        description="phase4 task target",
        tags=[],
        status=status_value,
        review_status=review_status,
        ai_analysis_enabled_at_upload=False,
    )
    async with AsyncSessionFactory() as session:
        session.add(file)
        await session.commit()
        await session.refresh(file)
        return file.id


async def _create_category_and_mapping(client: AsyncClient, token: str) -> tuple[str, str]:
    category_response = await client.post(
        "/api/categories",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "阶段四任务分类",
            "code": "phase4-task",
            "require_review": True,
            "default_visibility": "company",
            "auto_sync_enabled": True,
        },
    )
    assert category_response.status_code == 201
    category_id = str(category_response.json()["data"]["id"])

    mapping_response = await client.post(
        "/api/datasets",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "阶段四 Dataset",
            "category_id": category_id,
            "ragflow_dataset_id": "ragflow-phase4",
            "ragflow_dataset_name": "阶段四知识库",
            "enabled": True,
        },
    )
    assert mapping_response.status_code == 201
    return category_id, str(mapping_response.json()["data"]["id"])


async def _create_admin_token(client: AsyncClient) -> str:
    await _create_user(
        email="phase4-system@company.com",
        password="password123",
        role="system_admin",
    )
    return await _login(client, email="phase4-system@company.com", password="password123")


async def test_approving_file_creates_one_ragflow_upload_task(
    task_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ragflow.models import SyncTask

    token = await _create_admin_token(task_client)
    uploader_id = await _create_user(email="phase4-uploader@company.com", password="password123")
    file_id = await _create_file(uploader_id=uploader_id)
    category_id, mapping_id = await _create_category_and_mapping(task_client, token)

    response = await task_client.post(
        f"/api/files/{file_id}/approve",
        headers={"Authorization": f"Bearer {token}"},
        json={"category_id": category_id, "dataset_mapping_id": mapping_id},
    )

    assert response.status_code == 200
    async with AsyncSessionFactory() as session:
        result = await session.execute(select(SyncTask).where(SyncTask.file_id == file_id))
        tasks = list(result.scalars())

    assert len(tasks) == 1
    assert tasks[0].task_type == "ragflow_upload"
    assert tasks[0].status == "queued"
    assert tasks[0].retry_count == 0


async def test_create_ragflow_upload_task_is_idempotent(
    task_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ragflow.tasks import create_ragflow_upload_sync_task

    await _create_admin_token(task_client)
    uploader_id = await _create_user(email="phase4-idempotent@company.com", password="password123")
    file_id = await _create_file(uploader_id=uploader_id)

    async with AsyncSessionFactory() as session:
        first_task_id = await create_ragflow_upload_sync_task(session=session, file_id=file_id)
        second_task_id = await create_ragflow_upload_sync_task(session=session, file_id=file_id)
        await session.commit()

    assert first_task_id == second_task_id


async def test_admin_can_list_and_get_tasks(task_client: AsyncClient) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ragflow.tasks import create_ragflow_upload_sync_task

    token = await _create_admin_token(task_client)
    uploader_id = await _create_user(email="phase4-list@company.com", password="password123")
    file_id = await _create_file(uploader_id=uploader_id)
    async with AsyncSessionFactory() as session:
        task_id = await create_ragflow_upload_sync_task(session=session, file_id=file_id)
        await session.commit()

    list_response = await task_client.get(
        "/api/tasks",
        headers={"Authorization": f"Bearer {token}"},
    )
    detail_response = await task_client.get(
        f"/api/tasks/{task_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert list_response.status_code == 200
    assert list_response.json()["data"]["total"] == 1
    assert list_response.json()["data"]["items"][0]["id"] == str(task_id)
    assert detail_response.status_code == 200
    assert detail_response.json()["data"]["task_type"] == "ragflow_upload"
    assert detail_response.json()["data"]["logs"][0]["status"] == "queued"


async def test_employee_cannot_list_tasks(task_client: AsyncClient) -> None:
    await _create_user(
        email="phase4-employee@company.com",
        password="password123",
        role="employee",
    )
    token = await _login(task_client, email="phase4-employee@company.com", password="password123")

    response = await task_client.get("/api/tasks", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 403


async def test_failed_task_can_be_retried(task_client: AsyncClient) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ragflow.models import SyncTask

    token = await _create_admin_token(task_client)
    uploader_id = await _create_user(email="phase4-retry@company.com", password="password123")
    file_id = await _create_file(uploader_id=uploader_id)
    async with AsyncSessionFactory() as session:
        task = SyncTask(
            file_id=file_id,
            task_type="ragflow_upload",
            status="failed",
            retry_count=1,
            max_retry_count=3,
            error_message="network timeout",
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        task_id = task.id

    response = await task_client.post(
        f"/api/tasks/{task_id}/retry",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "queued"
    assert data["retry_count"] == 2
    assert data["error_message"] is None
    assert data["logs"][-1]["status"] == "queued"


async def test_cancel_queued_task_marks_canceled(task_client: AsyncClient) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ragflow.tasks import create_ragflow_upload_sync_task

    token = await _create_admin_token(task_client)
    uploader_id = await _create_user(email="phase4-cancel@company.com", password="password123")
    file_id = await _create_file(uploader_id=uploader_id)
    async with AsyncSessionFactory() as session:
        task_id = await create_ragflow_upload_sync_task(session=session, file_id=file_id)
        await session.commit()

    response = await task_client.post(
        f"/api/tasks/{task_id}/cancel",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "canceled"
    assert data["logs"][-1]["status"] == "canceled"


async def test_ragflow_upload_worker_marks_task_succeeded(task_client: AsyncClient) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ragflow.models import SyncTask
    from app.modules.ragflow.tasks import (
        create_ragflow_upload_sync_task,
        run_ragflow_upload_task_async,
    )

    await _create_admin_token(task_client)
    uploader_id = await _create_user(email="phase4-worker@company.com", password="password123")
    file_id = await _create_file(uploader_id=uploader_id)
    async with AsyncSessionFactory() as session:
        task_id = await create_ragflow_upload_sync_task(session=session, file_id=file_id)
        await session.commit()

    await run_ragflow_upload_task_async(str(task_id))

    async with AsyncSessionFactory() as session:
        result = await session.execute(select(SyncTask).where(SyncTask.id == task_id))
        task = result.scalar_one()

    assert task.status == "succeeded"
    assert task.started_at is not None
    assert task.finished_at is not None
