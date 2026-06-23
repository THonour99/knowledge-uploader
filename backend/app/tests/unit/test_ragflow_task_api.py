from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncGenerator, Awaitable, Callable
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from redis.asyncio import from_url
from sqlalchemy import select

pytestmark = pytest.mark.asyncio
UNASSIGNED_DEPARTMENT_ID = UUID("00000000-0000-0000-0000-000000000001")


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


async def _create_department(*, name: str, code: str) -> UUID:
    from app.core.database import AsyncSessionFactory
    from app.modules.department.models import Department

    department = Department(name=name, code=code, status="active")
    async with AsyncSessionFactory() as session:
        session.add(department)
        await session.commit()
        await session.refresh(department)
        return department.id


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
    department_id: UUID = UNASSIGNED_DEPARTMENT_ID,
    department: str | None = "QA",
    dataset_mapping_id: UUID | None = None,
    ragflow_dataset_id: str | None = None,
    ragflow_document_id: str | None = None,
    ragflow_parse_status: str | None = None,
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
        department=department,
        dataset_mapping_id=dataset_mapping_id,
        visibility="private",
        description="phase4 task target",
        tags=[],
        status=status_value,
        review_status=review_status,
        ragflow_dataset_id=ragflow_dataset_id,
        ragflow_document_id=ragflow_document_id,
        ragflow_parse_status=ragflow_parse_status,
        ai_analysis_enabled_at_upload=False,
    )
    if department_id is not None:
        file.department_id = department_id
    async with AsyncSessionFactory() as session:
        session.add(file)
        await session.commit()
        await session.refresh(file)
        return file.id


async def _create_category_and_mapping(
    client: AsyncClient,
    token: str,
    *,
    ragflow_dataset_id: str = "ragflow-phase4",
    ragflow_dataset_name: str = "阶段四知识库",
) -> tuple[str, str]:
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
    assert category_response.status_code == 201, category_response.text
    category_id = str(category_response.json()["data"]["id"])

    mapping_response = await client.post(
        "/api/datasets",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "阶段四 Dataset",
            "category_id": category_id,
            "ragflow_dataset_id": ragflow_dataset_id,
            "ragflow_dataset_name": ragflow_dataset_name,
            "enabled": True,
        },
    )
    assert mapping_response.status_code == 201, mapping_response.text
    return category_id, str(mapping_response.json()["data"]["id"])


async def _create_admin_token(client: AsyncClient) -> str:
    await _create_user(
        email="phase4-system@company.com",
        password="password123",
        role="system_admin",
    )
    return await _login(client, email="phase4-system@company.com", password="password123")


async def test_approving_file_queues_ragflow_creation_event(
    task_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox

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
    assert response.json()["data"]["status"] == "queued"
    async with AsyncSessionFactory() as session:
        event_result = await session.execute(
            select(EventOutbox).where(EventOutbox.event_type == "review.file.approved")
        )
        outbox_event = event_result.scalar_one()

    assert outbox_event.payload["file_id"] == str(file_id)
    assert outbox_event.payload["status"] == "queued"
    assert outbox_event.payload["ragflow_dataset_id"] == "ragflow-phase4"


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


async def test_ragflow_create_upload_worker_creates_task_and_queue_event(
    task_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.ragflow.models import SyncTask
    from app.modules.ragflow.tasks import run_create_ragflow_upload_task_async

    await _create_admin_token(task_client)
    uploader_id = await _create_user(
        email="phase4-create-worker@company.com",
        password="password123",
    )
    file_id = await _create_file(uploader_id=uploader_id)

    task_id = await run_create_ragflow_upload_task_async(str(file_id))

    async with AsyncSessionFactory() as session:
        task = await session.get(SyncTask, UUID(task_id))
        event_result = await session.execute(
            select(EventOutbox).where(
                EventOutbox.event_type == "ragflow.sync_task.queued",
                EventOutbox.aggregate_id == task_id,
            )
        )
        outbox_event = event_result.scalar_one()

    assert task is not None
    assert task.file_id == file_id
    assert task.task_type == "ragflow_upload"
    assert task.status == "queued"
    assert outbox_event.payload["sync_task_id"] == task_id


async def test_create_ragflow_upload_task_uses_redis_sync_lock(
    task_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ragflow import sync_locks
    from app.modules.ragflow.models import SyncTask
    from app.modules.ragflow.sync_locks import RagflowSyncLockBusy
    from app.modules.ragflow.tasks import create_ragflow_upload_sync_task

    await _create_admin_token(task_client)
    uploader_id = await _create_user(email="phase4-lock@company.com", password="password123")
    file_id = await _create_file(uploader_id=uploader_id)
    lock_key = f"lock:sync:{file_id}"
    redis_client = from_url(  # type: ignore[no-untyped-call]
        os.environ["CACHE_REDIS_URL"],
        encoding="utf-8",
        decode_responses=True,
    )
    await redis_client.set(lock_key, "busy", ex=30)
    monkeypatch.setattr(sync_locks, "SYNC_LOCK_WAIT_SECONDS", 0.0)

    try:
        async with AsyncSessionFactory() as session:
            with pytest.raises(RagflowSyncLockBusy, match="ragflow sync lock is busy"):
                await create_ragflow_upload_sync_task(session=session, file_id=file_id)
            result = await session.execute(select(SyncTask).where(SyncTask.file_id == file_id))
            assert result.scalar_one_or_none() is None
    finally:
        await redis_client.delete(lock_key)
        await redis_client.aclose()


async def test_create_ragflow_upload_task_releases_redis_sync_lock_after_commit(
    task_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ragflow.tasks import create_ragflow_upload_sync_task

    await _create_admin_token(task_client)
    uploader_id = await _create_user(
        email="phase4-lock-release@company.com",
        password="password123",
    )
    file_id = await _create_file(uploader_id=uploader_id)
    lock_key = f"lock:sync:{file_id}"
    redis_client = from_url(  # type: ignore[no-untyped-call]
        os.environ["CACHE_REDIS_URL"],
        encoding="utf-8",
        decode_responses=True,
    )

    try:
        async with AsyncSessionFactory() as session:
            await create_ragflow_upload_sync_task(session=session, file_id=file_id)
            assert await redis_client.get(lock_key) is not None
            await session.commit()

        for _ in range(20):
            if not await redis_client.exists(lock_key):
                break
            await asyncio.sleep(0.05)
        assert not await redis_client.exists(lock_key)
    finally:
        await redis_client.delete(lock_key)
        await redis_client.aclose()


async def test_create_ragflow_upload_task_uses_configured_retry_count(
    task_client: AsyncClient,
    set_system_config: Callable[[str, object], Awaitable[None]],
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ragflow.models import SyncTask
    from app.modules.ragflow.tasks import create_ragflow_upload_sync_task

    await _create_admin_token(task_client)
    uploader_id = await _create_user(
        email="phase5-configured-retry@company.com",
        password="password123",
    )
    file_id = await _create_file(uploader_id=uploader_id)
    # 最大重试次数由 runtime_config (DB 优先) 控制
    await set_system_config("ragflow.sync_max_retries", 7)

    async with AsyncSessionFactory() as session:
        task_id = await create_ragflow_upload_sync_task(session=session, file_id=file_id)
        await session.commit()

    async with AsyncSessionFactory() as session:
        task = await session.get(SyncTask, task_id)
        assert task is not None

    assert task.max_retry_count == 7


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


async def test_employee_cannot_get_retry_or_cancel_tasks(task_client: AsyncClient) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ragflow.models import SyncTask
    from app.modules.ragflow.tasks import create_ragflow_upload_sync_task

    await _create_user(
        email="phase4-employee-denied@company.com",
        password="password123",
        role="employee",
    )
    token = await _login(
        task_client,
        email="phase4-employee-denied@company.com",
        password="password123",
    )
    uploader_id = await _create_user(
        email="phase4-denied-owner@company.com",
        password="password123",
    )
    file_id = await _create_file(uploader_id=uploader_id)
    async with AsyncSessionFactory() as session:
        task_id = await create_ragflow_upload_sync_task(session=session, file_id=file_id)
        task = await session.get(SyncTask, task_id)
        assert task is not None
        task.status = "failed"
        await session.commit()

    responses = [
        await task_client.get(
            f"/api/tasks/{task_id}",
            headers={"Authorization": f"Bearer {token}"},
        ),
        await task_client.post(
            f"/api/tasks/{task_id}/retry",
            headers={"Authorization": f"Bearer {token}"},
        ),
        await task_client.post(
            f"/api/tasks/{task_id}/cancel",
            headers={"Authorization": f"Bearer {token}"},
        ),
    ]

    assert [response.status_code for response in responses] == [403, 403, 403]


async def test_failed_task_can_be_retried(task_client: AsyncClient) -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
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

    async with AsyncSessionFactory() as session:
        event_result = await session.execute(
            select(EventOutbox).where(
                EventOutbox.event_type == "ragflow.sync_task.queued",
                EventOutbox.aggregate_id == str(task_id),
            )
        )
        assert event_result.scalar_one().payload["sync_task_id"] == str(task_id)


async def test_retry_returns_conflict_when_sync_lock_is_busy(task_client: AsyncClient) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ragflow.models import SyncTask

    token = await _create_admin_token(task_client)
    uploader_id = await _create_user(
        email="phase4-retry-lock@company.com",
        password="password123",
    )
    file_id = await _create_file(uploader_id=uploader_id)
    async with AsyncSessionFactory() as session:
        task = SyncTask(
            file_id=file_id,
            task_type="ragflow_upload",
            status="failed",
            retry_count=0,
            max_retry_count=3,
            error_message="network timeout",
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        task_id = task.id

    redis_client = from_url(  # type: ignore[no-untyped-call]
        os.environ["CACHE_REDIS_URL"],
        encoding="utf-8",
        decode_responses=True,
    )
    await redis_client.set(f"lock:sync:{file_id}", "busy", ex=30)
    try:
        response = await task_client.post(
            f"/api/tasks/{task_id}/retry",
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        await redis_client.delete(f"lock:sync:{file_id}")
        await redis_client.aclose()

    assert response.status_code == 409
    assert response.json()["error_code"] == "VALIDATION_ERROR"


async def test_retry_returns_conflict_when_file_has_active_upload_task(
    task_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ragflow.models import SyncTask

    token = await _create_admin_token(task_client)
    uploader_id = await _create_user(
        email="phase4-retry-active@company.com",
        password="password123",
    )
    file_id = await _create_file(uploader_id=uploader_id)
    async with AsyncSessionFactory() as session:
        failed_task = SyncTask(
            file_id=file_id,
            task_type="ragflow_upload",
            status="failed",
            retry_count=0,
            max_retry_count=3,
            error_message="network timeout",
        )
        active_task = SyncTask(
            file_id=file_id,
            task_type="ragflow_upload",
            status="queued",
            retry_count=0,
            max_retry_count=3,
        )
        session.add_all([failed_task, active_task])
        await session.commit()
        await session.refresh(failed_task)
        failed_task_id = failed_task.id

    response = await task_client.post(
        f"/api/tasks/{failed_task_id}/retry",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 409
    assert response.json()["error_code"] == "VALIDATION_ERROR"


async def test_task_admin_operations_write_audit_logs(task_client: AsyncClient) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.audit.models import AuditLog
    from app.modules.ragflow.models import SyncTask

    token = await _create_admin_token(task_client)
    uploader_id = await _create_user(email="phase4-audit@company.com", password="password123")
    file_id = await _create_file(uploader_id=uploader_id)
    async with AsyncSessionFactory() as session:
        task = SyncTask(
            file_id=file_id,
            task_type="ragflow_upload",
            status="failed",
            retry_count=0,
            max_retry_count=3,
            error_message="timeout",
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        task_id = task.id

    assert (
        await task_client.get("/api/tasks", headers={"Authorization": f"Bearer {token}"})
    ).status_code == 200
    assert (
        await task_client.get(f"/api/tasks/{task_id}", headers={"Authorization": f"Bearer {token}"})
    ).status_code == 200
    assert (
        await task_client.post(
            f"/api/tasks/{task_id}/retry",
            headers={"Authorization": f"Bearer {token}"},
        )
    ).status_code == 200
    assert (
        await task_client.post(
            f"/api/tasks/{task_id}/cancel",
            headers={"Authorization": f"Bearer {token}"},
        )
    ).status_code == 200

    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(AuditLog).where(AuditLog.action.like("task.%")).order_by(AuditLog.created_at)
        )
        audit_logs = list(result.scalars())

    assert [log.action for log in audit_logs] == [
        "task.list",
        "task.get",
        "task.retry",
        "task.cancel",
    ]
    assert audit_logs[0].target_type == "task_collection"
    assert audit_logs[1].target_type == "task"


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


class _FakeReadableStorage:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.reads: list[tuple[str, str]] = []

    async def get_object(self, *, bucket: str, object_key: str) -> bytes:
        self.reads.append((bucket, object_key))
        return self.payload


class _FakeRagflowClient:
    def __init__(
        self,
        *,
        document_id: str = "ragflow-doc-phase5",
        run_statuses: list[str] | None = None,
    ) -> None:
        self.document_id = document_id
        self.run_statuses = run_statuses or ["DONE"]
        self.uploads: list[dict[str, object]] = []
        self.metadata_updates: list[dict[str, object]] = []
        self.parse_requests: list[tuple[str, str]] = []
        self.status_requests: list[tuple[str, str]] = []

    async def ping(self) -> bool:
        return True

    async def upload_document(
        self,
        *,
        dataset_id: str,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> object:
        from app.adapters.ragflow.base import RagflowUploadResult

        self.uploads.append(
            {
                "dataset_id": dataset_id,
                "filename": filename,
                "content": content,
                "content_type": content_type,
            }
        )
        return RagflowUploadResult(document_id=self.document_id, raw={"id": self.document_id})

    async def update_document_metadata(
        self,
        *,
        dataset_id: str,
        document_id: str,
        name: str,
        metadata: dict[str, object],
    ) -> None:
        self.metadata_updates.append(
            {
                "dataset_id": dataset_id,
                "document_id": document_id,
                "name": name,
                "metadata": metadata,
            }
        )

    async def start_parse(self, *, dataset_id: str, document_id: str) -> None:
        self.parse_requests.append((dataset_id, document_id))

    async def get_document_status(self, *, dataset_id: str, document_id: str) -> object:
        from app.adapters.ragflow.base import RagflowDocumentStatus

        self.status_requests.append((dataset_id, document_id))
        run_status = self.run_statuses.pop(0) if self.run_statuses else "DONE"
        return RagflowDocumentStatus(
            document_id=document_id,
            run=run_status,
            progress=1.0 if run_status == "DONE" else 0.0,
            raw={"id": document_id, "run": run_status},
        )

    async def delete_document(self, *, dataset_id: str, document_id: str) -> None:
        self.metadata_updates.append(
            {
                "dataset_id": dataset_id,
                "document_id": document_id,
                "deleted": True,
            }
        )


async def test_ragflow_upload_worker_uploads_minio_object_and_parses_document(
    task_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File
    from app.modules.ragflow import tasks
    from app.modules.ragflow.models import SyncTask, SyncTaskLog
    from app.modules.ragflow.tasks import (
        create_ragflow_upload_sync_task,
        run_ragflow_upload_task_async,
    )

    token = await _create_admin_token(task_client)
    uploader_id = await _create_user(email="phase4-worker@company.com", password="password123")
    department_id = await _create_department(name="研发知识部", code="research-ops")
    _, mapping_id = await _create_category_and_mapping(
        task_client,
        token,
        ragflow_dataset_id="ragflow-phase5",
        ragflow_dataset_name="阶段五知识库",
    )
    file_id = await _create_file(
        uploader_id=uploader_id,
        status_value="queued",
        review_status="approved",
        department_id=department_id,
        department="Legacy QA",
        dataset_mapping_id=UUID(mapping_id),
        ragflow_dataset_id="ragflow-phase5",
    )
    async with AsyncSessionFactory() as session:
        task_id = await create_ragflow_upload_sync_task(session=session, file_id=file_id)
        await session.commit()

    storage = _FakeReadableStorage(b"phase 5 document body")
    client = _FakeRagflowClient()
    monkeypatch.setattr(tasks, "build_document_storage", lambda _settings: storage)

    async def _fake_build_ragflow_client() -> object:
        return client

    monkeypatch.setattr(
        tasks, "build_ragflow_client_from_runtime_config", _fake_build_ragflow_client
    )

    await run_ragflow_upload_task_async(str(task_id))

    async with AsyncSessionFactory() as session:
        result = await session.execute(select(SyncTask).where(SyncTask.id == task_id))
        task = result.scalar_one()
        log_result = await session.execute(
            select(SyncTaskLog).where(SyncTaskLog.task_id == task_id).order_by(SyncTaskLog.id.asc())
        )
        logs = list(log_result.scalars())
        file = await session.get(File, file_id)
        assert file is not None

    assert task.status == "succeeded"
    assert task.started_at is not None
    assert task.finished_at is not None
    assert file.status == "parsed"
    assert file.ragflow_document_id == "ragflow-doc-phase5"
    assert file.ragflow_parse_status == "DONE"
    assert file.ragflow_error_message is None
    assert file.last_sync_at is not None
    assert storage.reads == [("knowledge-files", f"uploads/{uploader_id}/file-phase4-handbook.pdf")]
    assert client.uploads == [
        {
            "dataset_id": "ragflow-phase5",
            "filename": "file-phase4-handbook.pdf",
            "content": b"phase 5 document body",
            "content_type": "application/pdf",
        }
    ]
    assert client.parse_requests == [("ragflow-phase5", "ragflow-doc-phase5")]
    assert client.status_requests == [("ragflow-phase5", "ragflow-doc-phase5")]
    assert [log.message for log in logs] == [
        "ragflow upload task queued",
        "ragflow upload task started",
        "ragflow document upload started",
        "ragflow document uploaded",
        "ragflow document metadata updated",
        "ragflow document parse started",
        "ragflow parse status DONE",
        "ragflow upload task completed",
    ]
    assert client.metadata_updates[0]["metadata"] == {
        "source": "knowledge_uploader",
        "file_id": str(file_id),
        "uploader": str(uploader_id),
        "department": "Legacy QA",
        "department_id": str(department_id),
        "department_name": "研发知识部",
        "department_code": "research-ops",
        "category": None,
        "tags": [],
        "visibility": "private",
        "summary": None,
        "version": "1",
        "uploaded_at": file.uploaded_at.isoformat(),
    }


async def test_ragflow_upload_worker_reuses_existing_document_on_retry(
    task_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File
    from app.modules.ragflow import tasks
    from app.modules.ragflow.models import SyncTask
    from app.modules.ragflow.tasks import create_ragflow_upload_sync_task

    token = await _create_admin_token(task_client)
    uploader_id = await _create_user(
        email="phase5-retry-existing-doc@company.com",
        password="password123",
    )
    _, mapping_id = await _create_category_and_mapping(
        task_client,
        token,
        ragflow_dataset_id="ragflow-phase5",
        ragflow_dataset_name="阶段五知识库",
    )
    file_id = await _create_file(
        uploader_id=uploader_id,
        status_value="failed",
        review_status="approved",
        dataset_mapping_id=UUID(mapping_id),
        ragflow_dataset_id="ragflow-phase5",
        ragflow_document_id="existing-ragflow-doc",
        ragflow_parse_status="FAIL",
    )
    async with AsyncSessionFactory() as session:
        task_id = await create_ragflow_upload_sync_task(session=session, file_id=file_id)
        await session.commit()

    storage = _FakeReadableStorage(b"should not be read")
    client = _FakeRagflowClient(document_id="existing-ragflow-doc", run_statuses=["DONE"])
    monkeypatch.setattr(tasks, "build_document_storage", lambda _settings: storage)

    async def _fake_build_ragflow_client() -> object:
        return client

    monkeypatch.setattr(
        tasks, "build_ragflow_client_from_runtime_config", _fake_build_ragflow_client
    )

    await tasks.run_ragflow_upload_task_async(str(task_id))

    async with AsyncSessionFactory() as session:
        result = await session.execute(select(SyncTask).where(SyncTask.id == task_id))
        task = result.scalar_one()
        file = await session.get(File, file_id)
        assert file is not None

    assert task.status == "succeeded"
    assert file.status == "parsed"
    assert file.ragflow_document_id == "existing-ragflow-doc"
    assert file.ragflow_parse_status == "DONE"
    assert storage.reads == []
    assert client.uploads == []
    assert client.status_requests == [("ragflow-phase5", "existing-ragflow-doc")]


async def test_ragflow_upload_worker_queues_status_check_for_nonterminal_status(
    task_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File
    from app.modules.ragflow import tasks
    from app.modules.ragflow.models import SyncTask
    from app.modules.ragflow.tasks import create_ragflow_upload_sync_task

    token = await _create_admin_token(task_client)
    uploader_id = await _create_user(
        email="phase5-pending-parse@company.com",
        password="password123",
    )
    _, mapping_id = await _create_category_and_mapping(
        task_client,
        token,
        ragflow_dataset_id="ragflow-phase5",
        ragflow_dataset_name="阶段五知识库",
    )
    file_id = await _create_file(
        uploader_id=uploader_id,
        status_value="queued",
        review_status="approved",
        dataset_mapping_id=UUID(mapping_id),
        ragflow_dataset_id="ragflow-phase5",
    )
    async with AsyncSessionFactory() as session:
        task_id = await create_ragflow_upload_sync_task(session=session, file_id=file_id)
        await session.commit()

    storage = _FakeReadableStorage(b"phase 5 document body")
    client = _FakeRagflowClient(run_statuses=["RUNNING"])
    monkeypatch.setattr(tasks, "build_document_storage", lambda _settings: storage)

    async def _fake_build_ragflow_client() -> object:
        return client

    monkeypatch.setattr(
        tasks, "build_ragflow_client_from_runtime_config", _fake_build_ragflow_client
    )

    await tasks.run_ragflow_upload_task_async(str(task_id))

    async with AsyncSessionFactory() as session:
        task = await session.get(SyncTask, task_id)
        status_check_result = await session.execute(
            select(SyncTask).where(
                SyncTask.file_id == file_id,
                SyncTask.task_type == "ragflow_status_check",
            )
        )
        status_check_task = status_check_result.scalar_one()
        file = await session.get(File, file_id)
        assert task is not None
        assert file is not None

    assert task.status == "succeeded"
    assert task.error_message is None
    assert status_check_task.status == "queued"
    assert status_check_task.retry_count == 0
    assert file.status == "parsing"
    assert file.ragflow_document_id == "ragflow-doc-phase5"
    assert file.ragflow_parse_status == "RUNNING"
    assert file.ragflow_error_message is None
    assert client.uploads != []
    assert client.parse_requests == [("ragflow-phase5", "ragflow-doc-phase5")]
    assert client.status_requests == [("ragflow-phase5", "ragflow-doc-phase5")]


async def test_ragflow_status_check_worker_marks_done_as_parsed(
    task_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File
    from app.modules.ragflow import tasks
    from app.modules.ragflow.models import SyncTask

    token = await _create_admin_token(task_client)
    uploader_id = await _create_user(
        email="phase5-status-check-done@company.com",
        password="password123",
    )
    _, mapping_id = await _create_category_and_mapping(
        task_client,
        token,
        ragflow_dataset_id="ragflow-phase5",
        ragflow_dataset_name="阶段五知识库",
    )
    file_id = await _create_file(
        uploader_id=uploader_id,
        status_value="parsing",
        review_status="approved",
        dataset_mapping_id=UUID(mapping_id),
        ragflow_dataset_id="ragflow-phase5",
        ragflow_document_id="existing-ragflow-doc",
        ragflow_parse_status="RUNNING",
    )
    async with AsyncSessionFactory() as session:
        task = SyncTask(
            file_id=file_id,
            task_type="ragflow_status_check",
            status="queued",
            retry_count=0,
            max_retry_count=3,
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        task_id = task.id

    storage = _FakeReadableStorage(b"should not be read")
    client = _FakeRagflowClient(document_id="existing-ragflow-doc", run_statuses=["DONE"])
    monkeypatch.setattr(tasks, "build_document_storage", lambda _settings: storage)

    async def _fake_build_ragflow_client() -> object:
        return client

    monkeypatch.setattr(
        tasks, "build_ragflow_client_from_runtime_config", _fake_build_ragflow_client
    )

    await tasks.run_ragflow_upload_task_async(str(task_id))

    async with AsyncSessionFactory() as session:
        loaded_task = await session.get(SyncTask, task_id)
        loaded_file = await session.get(File, file_id)
        assert loaded_task is not None
        assert loaded_file is not None

    assert loaded_task.status == "succeeded"
    assert loaded_file.status == "parsed"
    assert loaded_file.ragflow_parse_status == "DONE"
    assert loaded_file.ragflow_error_message is None
    assert storage.reads == []
    assert client.uploads == []
    assert client.parse_requests == []
    assert client.status_requests == [("ragflow-phase5", "existing-ragflow-doc")]


async def test_ragflow_status_check_worker_marks_fail_as_failed(
    task_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File
    from app.modules.ragflow import tasks
    from app.modules.ragflow.models import SyncTask

    token = await _create_admin_token(task_client)
    uploader_id = await _create_user(
        email="phase5-status-check-fail@company.com",
        password="password123",
    )
    _, mapping_id = await _create_category_and_mapping(
        task_client,
        token,
        ragflow_dataset_id="ragflow-phase5",
        ragflow_dataset_name="阶段五知识库",
    )
    file_id = await _create_file(
        uploader_id=uploader_id,
        status_value="parsing",
        review_status="approved",
        dataset_mapping_id=UUID(mapping_id),
        ragflow_dataset_id="ragflow-phase5",
        ragflow_document_id="existing-ragflow-doc",
        ragflow_parse_status="RUNNING",
    )
    async with AsyncSessionFactory() as session:
        task = SyncTask(
            file_id=file_id,
            task_type="ragflow_status_check",
            status="queued",
            retry_count=0,
            max_retry_count=3,
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        task_id = task.id

    storage = _FakeReadableStorage(b"should not be read")
    client = _FakeRagflowClient(document_id="existing-ragflow-doc", run_statuses=["FAIL"])
    monkeypatch.setattr(tasks, "build_document_storage", lambda _settings: storage)

    async def _fake_build_ragflow_client() -> object:
        return client

    monkeypatch.setattr(
        tasks, "build_ragflow_client_from_runtime_config", _fake_build_ragflow_client
    )

    with pytest.raises(RuntimeError, match="RagflowParseFailedError"):
        await tasks.run_ragflow_upload_task_async(str(task_id))

    async with AsyncSessionFactory() as session:
        loaded_task = await session.get(SyncTask, task_id)
        loaded_file = await session.get(File, file_id)
        assert loaded_task is not None
        assert loaded_file is not None

    assert loaded_task.status == "failed"
    assert loaded_task.error_message == "RagflowParseFailedError"
    assert loaded_file.status == "failed"
    assert loaded_file.ragflow_parse_status == "FAIL"
    assert loaded_file.ragflow_error_message == "RagflowParseFailedError"
    assert storage.reads == []
    assert client.uploads == []
    assert client.parse_requests == []
    assert client.status_requests == [("ragflow-phase5", "existing-ragflow-doc")]


async def test_duplicate_status_check_worker_message_does_not_reclaim_running_task(
    task_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ragflow.models import SyncTask
    from app.modules.ragflow.tasks import run_ragflow_upload_task_async

    await _create_admin_token(task_client)
    uploader_id = await _create_user(
        email="phase5-status-check-duplicate@company.com",
        password="password123",
    )
    file_id = await _create_file(
        uploader_id=uploader_id,
        status_value="parsing",
        review_status="approved",
        ragflow_document_id="existing-ragflow-doc",
        ragflow_parse_status="RUNNING",
    )
    async with AsyncSessionFactory() as session:
        task = SyncTask(
            file_id=file_id,
            task_type="ragflow_status_check",
            status="running",
            retry_count=0,
            max_retry_count=3,
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        task_id = task.id

    await run_ragflow_upload_task_async(str(task_id))

    async with AsyncSessionFactory() as session:
        loaded_task = await session.get(SyncTask, task_id)
        assert loaded_task is not None

    assert loaded_task.status == "running"
    assert loaded_task.finished_at is None


async def test_ragflow_upload_worker_starts_parse_for_existing_unstarted_document(
    task_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File
    from app.modules.ragflow import tasks
    from app.modules.ragflow.models import SyncTask
    from app.modules.ragflow.tasks import create_ragflow_upload_sync_task

    token = await _create_admin_token(task_client)
    uploader_id = await _create_user(
        email="phase5-existing-unstart@company.com",
        password="password123",
    )
    _, mapping_id = await _create_category_and_mapping(
        task_client,
        token,
        ragflow_dataset_id="ragflow-phase5",
        ragflow_dataset_name="阶段五知识库",
    )
    file_id = await _create_file(
        uploader_id=uploader_id,
        status_value="uploaded_to_ragflow",
        review_status="approved",
        dataset_mapping_id=UUID(mapping_id),
        ragflow_dataset_id="ragflow-phase5",
        ragflow_document_id="existing-ragflow-doc",
        ragflow_parse_status="UNSTART",
    )
    async with AsyncSessionFactory() as session:
        task_id = await create_ragflow_upload_sync_task(session=session, file_id=file_id)
        await session.commit()

    storage = _FakeReadableStorage(b"should not be read")
    client = _FakeRagflowClient(
        document_id="existing-ragflow-doc",
        run_statuses=["UNSTART", "DONE"],
    )
    monkeypatch.setattr(tasks, "build_document_storage", lambda _settings: storage)

    async def _fake_build_ragflow_client() -> object:
        return client

    monkeypatch.setattr(
        tasks, "build_ragflow_client_from_runtime_config", _fake_build_ragflow_client
    )

    await tasks.run_ragflow_upload_task_async(str(task_id))

    async with AsyncSessionFactory() as session:
        task = await session.get(SyncTask, task_id)
        file = await session.get(File, file_id)
        assert task is not None
        assert file is not None

    assert task.status == "succeeded"
    assert file.status == "parsed"
    assert file.ragflow_document_id == "existing-ragflow-doc"
    assert file.ragflow_parse_status == "DONE"
    assert storage.reads == []
    assert client.uploads == []
    assert client.metadata_updates[0]["document_id"] == "existing-ragflow-doc"
    assert client.parse_requests == [("ragflow-phase5", "existing-ragflow-doc")]
    assert client.status_requests == [
        ("ragflow-phase5", "existing-ragflow-doc"),
        ("ragflow-phase5", "existing-ragflow-doc"),
    ]


async def test_ragflow_upload_worker_requires_approved_file_before_external_calls(
    task_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ragflow import tasks
    from app.modules.ragflow.models import SyncTask
    from app.modules.ragflow.tasks import create_ragflow_upload_sync_task

    token = await _create_admin_token(task_client)
    uploader_id = await _create_user(
        email="phase5-unapproved-worker@company.com",
        password="password123",
    )
    _, mapping_id = await _create_category_and_mapping(
        task_client,
        token,
        ragflow_dataset_id="ragflow-phase5",
        ragflow_dataset_name="阶段五知识库",
    )
    file_id = await _create_file(
        uploader_id=uploader_id,
        status_value="queued",
        review_status="in_review",
        dataset_mapping_id=UUID(mapping_id),
        ragflow_dataset_id="ragflow-phase5",
    )
    async with AsyncSessionFactory() as session:
        task_id = await create_ragflow_upload_sync_task(session=session, file_id=file_id)
        await session.commit()

    storage = _FakeReadableStorage(b"must not be read")
    client = _FakeRagflowClient()
    monkeypatch.setattr(tasks, "build_document_storage", lambda _settings: storage)

    async def _fake_build_ragflow_client() -> object:
        return client

    monkeypatch.setattr(
        tasks, "build_ragflow_client_from_runtime_config", _fake_build_ragflow_client
    )

    with pytest.raises(RuntimeError, match="RagflowSyncPreconditionError"):
        await tasks.run_ragflow_upload_task_async(str(task_id))

    async with AsyncSessionFactory() as session:
        result = await session.execute(select(SyncTask).where(SyncTask.id == task_id))
        task = result.scalar_one()

    assert task.status == "failed"
    assert task.error_message == "RagflowSyncPreconditionError"
    assert storage.reads == []
    assert client.uploads == []
    assert client.metadata_updates == []
    assert client.parse_requests == []


async def test_ragflow_upload_worker_blocks_critical_sensitive_file_before_external_calls(
    task_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ai.models import DocumentAnalysis
    from app.modules.ragflow import tasks
    from app.modules.ragflow.models import SyncTask
    from app.modules.ragflow.tasks import create_ragflow_upload_sync_task

    token = await _create_admin_token(task_client)
    uploader_id = await _create_user(
        email="phase6-critical-worker@company.com",
        password="password123",
    )
    _, mapping_id = await _create_category_and_mapping(
        task_client,
        token,
        ragflow_dataset_id="ragflow-phase6-critical",
        ragflow_dataset_name="阶段六敏感知识库",
    )
    file_id = await _create_file(
        uploader_id=uploader_id,
        status_value="queued",
        review_status="approved",
        dataset_mapping_id=UUID(mapping_id),
        ragflow_dataset_id="ragflow-phase6-critical",
    )
    async with AsyncSessionFactory() as session:
        session.add(
            DocumentAnalysis(
                file_id=file_id,
                status="succeeded",
                sensitive_risk_level="critical",
                sensitive_hits=[
                    {
                        "rule_name": "生产环境凭据",
                        "risk_level": "critical",
                        "action": "block_sync",
                    }
                ],
            )
        )
        task_id = await create_ragflow_upload_sync_task(session=session, file_id=file_id)
        await session.commit()

    storage = _FakeReadableStorage(b"must not be read")
    client = _FakeRagflowClient()
    monkeypatch.setattr(tasks, "build_document_storage", lambda _settings: storage)

    async def _fake_build_ragflow_client() -> object:
        return client

    monkeypatch.setattr(
        tasks, "build_ragflow_client_from_runtime_config", _fake_build_ragflow_client
    )

    with pytest.raises(RuntimeError, match="RagflowSyncPreconditionError"):
        await tasks.run_ragflow_upload_task_async(str(task_id))

    async with AsyncSessionFactory() as session:
        result = await session.execute(select(SyncTask).where(SyncTask.id == task_id))
        task = result.scalar_one()

    assert task.status == "failed"
    assert task.error_message == "RagflowSyncPreconditionError"
    assert storage.reads == []
    assert client.uploads == []
    assert client.metadata_updates == []
    assert client.parse_requests == []


async def test_ragflow_upload_worker_allows_critical_file_when_block_config_disabled(
    task_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    set_system_config: Callable[[str, object], Awaitable[None]],
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ai.models import DocumentAnalysis
    from app.modules.ragflow import tasks
    from app.modules.ragflow.models import SyncTask
    from app.modules.ragflow.tasks import create_ragflow_upload_sync_task

    token = await _create_admin_token(task_client)
    uploader_id = await _create_user(
        email="phase6-critical-allowed@company.com",
        password="password123",
    )
    _, mapping_id = await _create_category_and_mapping(
        task_client,
        token,
        ragflow_dataset_id="ragflow-phase6-critical-allowed",
        ragflow_dataset_name="阶段六敏感放行知识库",
    )
    file_id = await _create_file(
        uploader_id=uploader_id,
        status_value="queued",
        review_status="approved",
        dataset_mapping_id=UUID(mapping_id),
        ragflow_dataset_id="ragflow-phase6-critical-allowed",
    )
    async with AsyncSessionFactory() as session:
        session.add(
            DocumentAnalysis(
                file_id=file_id,
                status="succeeded",
                sensitive_risk_level="critical",
                sensitive_hits=[
                    {
                        "rule_name": "生产环境凭据",
                        "risk_level": "critical",
                        "action": "block_sync",
                    }
                ],
            )
        )
        task_id = await create_ragflow_upload_sync_task(session=session, file_id=file_id)
        await session.commit()

    # 管理员显式关闭 critical 阻断后, critical 文件允许同步
    await set_system_config("security.block_critical_sensitive_sync", False)

    storage = _FakeReadableStorage(b"critical but allowed body")
    client = _FakeRagflowClient()
    monkeypatch.setattr(tasks, "build_document_storage", lambda _settings: storage)

    async def _fake_build_ragflow_client() -> object:
        return client

    monkeypatch.setattr(
        tasks, "build_ragflow_client_from_runtime_config", _fake_build_ragflow_client
    )

    await tasks.run_ragflow_upload_task_async(str(task_id))

    async with AsyncSessionFactory() as session:
        result = await session.execute(select(SyncTask).where(SyncTask.id == task_id))
        task = result.scalar_one()

    assert task.status == "succeeded"
    assert len(client.uploads) == 1
    assert client.uploads[0]["dataset_id"] == "ragflow-phase6-critical-allowed"


async def test_ragflow_upload_worker_enforces_dataset_allowlist(
    task_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import get_settings
    from app.core.database import AsyncSessionFactory
    from app.modules.ragflow import tasks
    from app.modules.ragflow.models import SyncTask
    from app.modules.ragflow.tasks import create_ragflow_upload_sync_task

    token = await _create_admin_token(task_client)
    uploader_id = await _create_user(
        email="phase5-allowlist-worker@company.com",
        password="password123",
    )
    _, mapping_id = await _create_category_and_mapping(
        task_client,
        token,
        ragflow_dataset_id="ragflow-phase5-denied",
        ragflow_dataset_name="阶段五拒绝知识库",
    )
    file_id = await _create_file(
        uploader_id=uploader_id,
        status_value="queued",
        review_status="approved",
        dataset_mapping_id=UUID(mapping_id),
        ragflow_dataset_id="ragflow-phase5-denied",
    )
    async with AsyncSessionFactory() as session:
        task_id = await create_ragflow_upload_sync_task(session=session, file_id=file_id)
        await session.commit()

    storage = _FakeReadableStorage(b"must not be read")
    client = _FakeRagflowClient()
    monkeypatch.setattr(tasks, "build_document_storage", lambda _settings: storage)

    async def _fake_build_ragflow_client() -> object:
        return client

    monkeypatch.setattr(
        tasks, "build_ragflow_client_from_runtime_config", _fake_build_ragflow_client
    )
    monkeypatch.setenv("RAGFLOW_ALLOWED_DATASET_IDS", "ragflow-phase5-allowed")
    get_settings.cache_clear()

    with pytest.raises(RuntimeError, match="RagflowSyncPreconditionError"):
        await tasks.run_ragflow_upload_task_async(str(task_id))
    get_settings.cache_clear()

    async with AsyncSessionFactory() as session:
        result = await session.execute(select(SyncTask).where(SyncTask.id == task_id))
        task = result.scalar_one()

    assert task.status == "failed"
    assert task.error_message == "RagflowSyncPreconditionError"
    assert storage.reads == []
    assert client.uploads == []


async def test_duplicate_running_worker_message_does_not_complete_task(
    task_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ragflow.models import SyncTask
    from app.modules.ragflow.tasks import run_ragflow_upload_task_async

    await _create_admin_token(task_client)
    uploader_id = await _create_user(
        email="phase4-running-worker@company.com",
        password="password123",
    )
    file_id = await _create_file(uploader_id=uploader_id)
    async with AsyncSessionFactory() as session:
        task = SyncTask(
            file_id=file_id,
            task_type="ragflow_upload",
            status="running",
            retry_count=0,
            max_retry_count=3,
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        task_id = task.id

    await run_ragflow_upload_task_async(str(task_id))

    async with AsyncSessionFactory() as session:
        result = await session.execute(select(SyncTask).where(SyncTask.id == task_id))
        task = result.scalar_one()

    assert task.status == "running"
    assert task.finished_at is None


async def test_ragflow_upload_worker_marks_task_failed_on_error(
    task_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ragflow import tasks
    from app.modules.ragflow.models import SyncTask
    from app.modules.ragflow.tasks import create_ragflow_upload_sync_task

    await _create_admin_token(task_client)
    uploader_id = await _create_user(email="phase4-fail-worker@company.com", password="password123")
    file_id = await _create_file(uploader_id=uploader_id)
    async with AsyncSessionFactory() as session:
        task_id = await create_ragflow_upload_sync_task(session=session, file_id=file_id)
        await session.commit()

    async def fail_upload(_sync_task_id: UUID) -> None:
        raise RuntimeError("credential-value-should-not-be-stored")

    monkeypatch.setattr(tasks, "_run_ragflow_upload_task", fail_upload)

    with pytest.raises(RuntimeError, match="RuntimeError") as error:
        await tasks.run_ragflow_upload_task_async(str(task_id))

    async with AsyncSessionFactory() as session:
        result = await session.execute(select(SyncTask).where(SyncTask.id == task_id))
        task = result.scalar_one()

    assert "credential-value" not in str(error.value)
    assert task.status == "failed"
    assert task.error_message == "RuntimeError"
    assert "credential-value" not in (task.error_message or "")


async def test_stale_worker_message_does_not_revive_failed_task(
    task_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ragflow.models import SyncTask
    from app.modules.ragflow.tasks import run_ragflow_upload_task_async

    await _create_admin_token(task_client)
    uploader_id = await _create_user(
        email="phase4-stale-worker@company.com",
        password="password123",
    )
    file_id = await _create_file(uploader_id=uploader_id)
    async with AsyncSessionFactory() as session:
        task = SyncTask(
            file_id=file_id,
            task_type="ragflow_upload",
            status="failed",
            retry_count=1,
            max_retry_count=3,
            error_message="previous failure",
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        task_id = task.id

    await run_ragflow_upload_task_async(str(task_id))

    async with AsyncSessionFactory() as session:
        result = await session.execute(select(SyncTask).where(SyncTask.id == task_id))
        task = result.scalar_one()

    assert task.status == "failed"
    assert task.error_message == "previous failure"
