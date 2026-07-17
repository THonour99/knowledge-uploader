from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
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
    review_status: str = "pending",
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

    submitted_at = datetime.now(UTC) if status_value == "pending_review" else None
    review_due_at = submitted_at + timedelta(hours=24) if submitted_at is not None else None
    file = File(
        original_name="phase4-handbook.pdf",
        title="phase4-handbook.pdf",
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
        submitted_at=submitted_at,
        review_due_at=review_due_at,
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


async def _create_incomplete_version_switch_task(
    *,
    uploader_id: UUID,
    department_id: UUID = UNASSIGNED_DEPARTMENT_ID,
    task_status: str = "failed",
    retry_count: int = 3,
    max_retry_count: int = 3,
) -> tuple[UUID, UUID, UUID]:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File
    from app.modules.ragflow.models import SyncTask

    predecessor_id = await _create_file(
        uploader_id=uploader_id,
        department_id=department_id,
        status_value="parsed",
        review_status="approved",
        hash_value="3" * 64,
        ragflow_dataset_id="reconcile-dataset",
        ragflow_document_id="reconcile-v1",
        ragflow_parse_status="DONE",
    )
    candidate_id = await _create_file(
        uploader_id=uploader_id,
        department_id=department_id,
        status_value="parsed",
        review_status="approved",
        hash_value="4" * 64,
        ragflow_dataset_id="reconcile-dataset",
        ragflow_document_id="reconcile-v2",
        ragflow_parse_status="DONE",
    )
    async with AsyncSessionFactory() as session:
        predecessor = await session.get(File, predecessor_id)
        candidate = await session.get(File, candidate_id)
        assert predecessor is not None and candidate is not None
        candidate.is_current_version = False
        await session.flush()
        candidate.series_id = predecessor.series_id
        candidate.version_number = 2
        candidate.replaces_file_id = predecessor.id
        candidate.replacement_remote_action = "archive"
        candidate.version_switch_status = "pending"
        task = SyncTask(
            file_id=candidate_id,
            task_type="ragflow_upload",
            status=task_status,
            retry_count=retry_count,
            max_retry_count=max_retry_count,
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        return predecessor_id, candidate_id, task.id


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

    claim_response = await task_client.post(
        f"/api/review/files/{file_id}/claim",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert claim_response.status_code == 200

    response = await task_client.post(
        f"/api/files/{file_id}/approve",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "sync_decision": "sync",
            "category_id": category_id,
            "dataset_mapping_id": mapping_id,
        },
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
    assert outbox_event.payload["sync_decision"] == "sync"
    assert outbox_event.payload["dataset_mapping_id"] == mapping_id
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
            f"/api/tasks/{task_id}/reconcile-version-switch",
            headers={"Authorization": f"Bearer {token}"},
            json={"reason": "unauthorized"},
        ),
        await task_client.post(
            f"/api/tasks/{task_id}/cancel",
            headers={"Authorization": f"Bearer {token}"},
        ),
    ]

    assert [response.status_code for response in responses] == [403, 403, 403, 403]


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


async def test_incomplete_version_switch_cancel_and_exhausted_reconcile_are_safe(
    task_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.audit.models import AuditLog
    from app.modules.document.models import File
    from app.modules.ragflow import events
    from app.modules.ragflow.models import SyncTask

    token = await _create_admin_token(task_client)
    uploader_id = await _create_user(
        email="version-reconcile-owner@company.com",
        password="password123",
    )
    predecessor_id = await _create_file(
        uploader_id=uploader_id,
        status_value="parsed",
        review_status="approved",
        hash_value="1" * 64,
        ragflow_dataset_id="version-reconcile-dataset",
        ragflow_document_id="version-reconcile-v1",
        ragflow_parse_status="DONE",
    )
    candidate_id = await _create_file(
        uploader_id=uploader_id,
        status_value="parsed",
        review_status="approved",
        hash_value="2" * 64,
        ragflow_dataset_id="version-reconcile-dataset",
        ragflow_document_id="version-reconcile-v2",
        ragflow_parse_status="DONE",
    )
    async with AsyncSessionFactory() as session:
        predecessor = await session.get(File, predecessor_id)
        candidate = await session.get(File, candidate_id)
        assert predecessor is not None and candidate is not None
        candidate.is_current_version = False
        await session.flush()
        candidate.series_id = predecessor.series_id
        candidate.version_number = 2
        candidate.replaces_file_id = predecessor.id
        candidate.replacement_remote_action = "archive"
        candidate.version_switch_status = "pending"
        task = SyncTask(
            file_id=candidate_id,
            task_type="ragflow_upload",
            status="queued",
            retry_count=3,
            max_retry_count=3,
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        task_id = task.id

    cancel = await task_client.post(
        f"/api/tasks/{task_id}/cancel",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert cancel.status_code == 409
    assert cancel.json()["error_code"] == "VALIDATION_ERROR"
    assert cancel.json()["message"] == "an incomplete version switch task cannot be canceled"

    async with AsyncSessionFactory() as session:
        task = await session.get(SyncTask, task_id)
        assert task is not None
        task.status = "failed"
        task.error_message = "version activation interrupted"
        task.reconcile_attempt_count = 3
        await session.commit()

    ordinary_retry = await task_client.post(
        f"/api/tasks/{task_id}/retry",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert ordinary_retry.status_code == 400
    assert ordinary_retry.json()["message"] == "task cannot be retried"

    reason = "远端激活中断, 已人工核对旧版本不可删除"
    reconciled = await task_client.post(
        f"/api/tasks/{task_id}/reconcile-version-switch",
        headers={"Authorization": f"Bearer {token}"},
        json={"reason": reason},
    )
    assert reconciled.status_code == 200, reconciled.text
    data = reconciled.json()["data"]
    assert data["status"] == "queued"
    assert data["retry_count"] == 3
    assert data["max_retry_count"] == 3

    async with AsyncSessionFactory() as session:
        task = await session.get(SyncTask, task_id)
        assert task is not None
        assert task.reconcile_attempt_count == 0
        queued_events_before = list(
            (
                await session.execute(
                    select(EventOutbox).where(
                        EventOutbox.event_type == events.RAGFLOW_SYNC_TASK_QUEUED,
                        EventOutbox.aggregate_id == str(task_id),
                    )
                )
            ).scalars()
        )
    assert len(queued_events_before) == 1

    repeated = await task_client.post(
        f"/api/tasks/{task_id}/reconcile-version-switch",
        headers={"Authorization": f"Bearer {token}"},
        json={"reason": reason},
    )
    assert repeated.status_code == 200
    assert repeated.json()["data"]["status"] == "queued"

    async with AsyncSessionFactory() as session:
        queued_events_after = list(
            (
                await session.execute(
                    select(EventOutbox).where(
                        EventOutbox.event_type == events.RAGFLOW_SYNC_TASK_QUEUED,
                        EventOutbox.aggregate_id == str(task_id),
                    )
                )
            ).scalars()
        )
        audits = list(
            (
                await session.execute(
                    select(AuditLog)
                    .where(
                        AuditLog.action == "task.version_switch_reconcile",
                        AuditLog.target_id == task_id,
                    )
                    .order_by(AuditLog.created_at, AuditLog.id)
                )
            ).scalars()
        )
    assert len(queued_events_after) == 1
    assert [audit.reason for audit in audits] == [reason, reason]
    assert [audit.metadata_json["idempotent"] for audit in audits] == [False, True]


async def test_version_switch_reconcile_rejects_ineligible_files_without_side_effects(
    task_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.audit.models import AuditLog
    from app.modules.document.models import File
    from app.modules.ragflow.models import SyncTask

    token = await _create_admin_token(task_client)
    uploader_id = await _create_user(
        email="reconcile-ineligible@company.com",
        password="password123",
    )
    nonreplacement_file_id = await _create_file(uploader_id=uploader_id, hash_value="5" * 64)
    async with AsyncSessionFactory() as session:
        nonreplacement_task = SyncTask(
            file_id=nonreplacement_file_id,
            task_type="ragflow_upload",
            status="failed",
            retry_count=3,
            max_retry_count=3,
        )
        session.add(nonreplacement_task)
        await session.commit()
        await session.refresh(nonreplacement_task)
        nonreplacement_task_id = nonreplacement_task.id

    predecessor_id, candidate_id, completed_task_id = await _create_incomplete_version_switch_task(
        uploader_id=uploader_id
    )
    async with AsyncSessionFactory() as session:
        predecessor = await session.get(File, predecessor_id)
        candidate = await session.get(File, candidate_id)
        assert predecessor is not None and candidate is not None
        predecessor.is_current_version = False
        await session.flush()
        candidate.is_current_version = True
        candidate.remote_visibility = "current"
        candidate.version_switch_status = "completed"
        await session.commit()

    responses = [
        await task_client.post(
            f"/api/tasks/{task_id}/reconcile-version-switch",
            headers={"Authorization": f"Bearer {token}"},
            json={"reason": "must be rejected"},
        )
        for task_id in (nonreplacement_task_id, completed_task_id)
    ]
    assert [response.status_code for response in responses] == [409, 409]
    assert all(
        response.json()["message"] == "task is not eligible for version switch reconciliation"
        for response in responses
    )

    task_ids = {nonreplacement_task_id, completed_task_id}
    async with AsyncSessionFactory() as session:
        audit_count = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.action == "task.version_switch_reconcile",
                        AuditLog.target_id.in_(task_ids),
                    )
                )
            )
            .scalars()
            .all()
        )
        event_count = (
            (
                await session.execute(
                    select(EventOutbox).where(
                        EventOutbox.aggregate_id.in_({str(task_id) for task_id in task_ids})
                    )
                )
            )
            .scalars()
            .all()
        )
    assert audit_count == []
    assert event_count == []


async def test_version_switch_reconcile_is_department_scoped(task_client: AsyncClient) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.user.models import User

    admin_department_id = await _create_department(
        name="恢复管理员部门",
        code="reconcile-admin-dept",
    )
    file_department_id = await _create_department(
        name="恢复文件部门",
        code="reconcile-file-dept",
    )
    admin_id = await _create_user(
        email="reconcile-dept-admin@company.com",
        password="password123",
        role="dept_admin",
    )
    uploader_id = await _create_user(
        email="reconcile-cross-dept-owner@company.com",
        password="password123",
    )
    async with AsyncSessionFactory() as session:
        admin = await session.get(User, admin_id)
        assert admin is not None
        admin.department_id = admin_department_id
        admin.department = "恢复管理员部门"
        await session.commit()
    token = await _login(
        task_client,
        email="reconcile-dept-admin@company.com",
        password="password123",
    )
    _predecessor_id, _candidate_id, task_id = await _create_incomplete_version_switch_task(
        uploader_id=uploader_id,
        department_id=file_department_id,
    )

    response = await task_client.post(
        f"/api/tasks/{task_id}/reconcile-version-switch",
        headers={"Authorization": f"Bearer {token}"},
        json={"reason": "cross department"},
    )

    assert response.status_code == 404
    assert response.json()["message"] == "task not found"


async def test_version_switch_reconcile_lock_busy_has_no_event_or_audit(
    task_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.audit.models import AuditLog
    from app.modules.ragflow import service as ragflow_service  # noqa: TID251

    token = await _create_admin_token(task_client)
    uploader_id = await _create_user(
        email="reconcile-lock-owner@company.com",
        password="password123",
    )
    _predecessor_id, _candidate_id, task_id = await _create_incomplete_version_switch_task(
        uploader_id=uploader_id
    )

    async def lock_unavailable(**_kwargs: object) -> bool:
        return False

    monkeypatch.setattr(ragflow_service, "acquire_sync_lock", lock_unavailable)
    response = await task_client.post(
        f"/api/tasks/{task_id}/reconcile-version-switch",
        headers={"Authorization": f"Bearer {token}"},
        json={"reason": "operator verified remote state"},
    )

    assert response.status_code == 409
    assert response.json()["message"] == "ragflow sync task is busy"
    async with AsyncSessionFactory() as session:
        audits = list(
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.action == "task.version_switch_reconcile",
                        AuditLog.target_id == task_id,
                    )
                )
            ).scalars()
        )
        events = list(
            (
                await session.execute(
                    select(EventOutbox).where(EventOutbox.aggregate_id == str(task_id))
                )
            ).scalars()
        )
    assert audits == []
    assert events == []


async def test_version_switch_reconcile_rejects_active_status_check_sibling(
    task_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ragflow.models import SyncTask

    token = await _create_admin_token(task_client)
    uploader_id = await _create_user(
        email="reconcile-status-check-owner@company.com",
        password="password123",
    )
    _predecessor_id, candidate_id, task_id = await _create_incomplete_version_switch_task(
        uploader_id=uploader_id
    )
    async with AsyncSessionFactory() as session:
        task = await session.get(SyncTask, task_id)
        assert task is not None
        task.task_type = "ragflow_status_check"
        sibling = SyncTask(
            file_id=candidate_id,
            task_type="ragflow_status_check",
            status="queued",
        )
        session.add(sibling)
        await session.commit()

    response = await task_client.post(
        f"/api/tasks/{task_id}/reconcile-version-switch",
        headers={"Authorization": f"Bearer {token}"},
        json={"reason": "resume status polling"},
    )

    assert response.status_code == 409
    assert response.json()["message"] == "another active ragflow synchronization task exists"


@pytest.mark.parametrize(
    ("target_type", "sibling_type", "sibling_status"),
    (
        ("ragflow_upload", "ragflow_status_check", "queued"),
        ("ragflow_upload", "ragflow_status_check", "running"),
        ("ragflow_status_check", "ragflow_upload", "queued"),
        ("ragflow_status_check", "ragflow_upload", "running"),
    ),
)
async def test_version_switch_reconcile_rejects_cross_type_active_sibling_without_side_effects(
    task_client: AsyncClient,
    target_type: str,
    sibling_type: str,
    sibling_status: str,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.audit.models import AuditLog
    from app.modules.ragflow.models import SyncTask

    token = await _create_admin_token(task_client)
    uploader_id = await _create_user(
        email=(f"reconcile-cross-{target_type}-{sibling_type}-{sibling_status}" "@company.com"),
        password="password123",
    )
    _predecessor_id, candidate_id, task_id = await _create_incomplete_version_switch_task(
        uploader_id=uploader_id
    )
    async with AsyncSessionFactory() as session:
        task = await session.get(SyncTask, task_id)
        assert task is not None
        task.task_type = target_type
        sibling = SyncTask(
            file_id=candidate_id,
            task_type=sibling_type,
            status=sibling_status,
            lease_token="active-sibling" if sibling_status == "running" else None,
        )
        session.add(sibling)
        await session.commit()

    response = await task_client.post(
        f"/api/tasks/{task_id}/reconcile-version-switch",
        headers={"Authorization": f"Bearer {token}"},
        json={"reason": "must not race another version task"},
    )

    assert response.status_code == 409
    assert response.json()["message"] == "another active ragflow synchronization task exists"
    async with AsyncSessionFactory() as session:
        task = await session.get(SyncTask, task_id)
        audits = list(
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.action == "task.version_switch_reconcile",
                        AuditLog.target_id == task_id,
                    )
                )
            ).scalars()
        )
        events = list(
            (
                await session.execute(
                    select(EventOutbox).where(EventOutbox.aggregate_id == str(task_id))
                )
            ).scalars()
        )
    assert task is not None
    assert task.status == "failed"
    assert audits == []
    assert events == []


async def test_version_switch_reconcile_rejects_non_exhausted_failed_task_without_side_effects(
    task_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.audit.models import AuditLog
    from app.modules.ragflow.models import SyncTask

    token = await _create_admin_token(task_client)
    uploader_id = await _create_user(
        email="reconcile-non-exhausted@company.com",
        password="password123",
    )
    _predecessor_id, _candidate_id, task_id = await _create_incomplete_version_switch_task(
        uploader_id=uploader_id,
        retry_count=1,
        max_retry_count=3,
    )

    response = await task_client.post(
        f"/api/tasks/{task_id}/reconcile-version-switch",
        headers={"Authorization": f"Bearer {token}"},
        json={"reason": "ordinary retry budget still exists"},
    )

    assert response.status_code == 409
    assert response.json()["message"] == ("task is not eligible for version switch reconciliation")
    async with AsyncSessionFactory() as session:
        task = await session.get(SyncTask, task_id)
        audits = list(
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.action == "task.version_switch_reconcile",
                        AuditLog.target_id == task_id,
                    )
                )
            ).scalars()
        )
        events = list(
            (
                await session.execute(
                    select(EventOutbox).where(EventOutbox.aggregate_id == str(task_id))
                )
            ).scalars()
        )
    assert task is not None
    assert task.status == "failed"
    assert task.retry_count == 1
    assert task.max_retry_count == 3
    assert audits == []
    assert events == []


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
        self.find_requests: list[tuple[str, str]] = []
        self.remote_documents: dict[tuple[str, str], object] = {}

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
        result = RagflowUploadResult(
            document_id=self.document_id,
            raw={"id": self.document_id, "name": filename},
        )
        self.remote_documents[(dataset_id, filename)] = result
        return result

    async def find_document_by_name(
        self,
        *,
        dataset_id: str,
        name: str,
    ) -> object | None:
        self.find_requests.append((dataset_id, name))
        return self.remote_documents.get((dataset_id, name))

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


class _LostFirstUploadResponseClient(_FakeRagflowClient):
    def __init__(self) -> None:
        super().__init__(document_id="ragflow-reconciled-doc")
        self._lose_response = True

    async def upload_document(
        self,
        *,
        dataset_id: str,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> object:
        result = await super().upload_document(
            dataset_id=dataset_id,
            filename=filename,
            content=content,
            content_type=content_type,
        )
        if self._lose_response:
            from app.adapters.ragflow.base import RagflowSubmissionOutcomeUnknownError

            self._lose_response = False
            raise RagflowSubmissionOutcomeUnknownError(
                "connection lost after remote upload committed"
            )
        return result


class _PerNameRagflowClient(_FakeRagflowClient):
    async def upload_document(
        self,
        *,
        dataset_id: str,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> object:
        self.document_id = f"ragflow-doc-{len(self.uploads) + 1}"
        return await super().upload_document(
            dataset_id=dataset_id,
            filename=filename,
            content=content,
            content_type=content_type,
        )


class _EventuallyConsistentLostUploadClient(_LostFirstUploadResponseClient):
    async def find_document_by_name(
        self,
        *,
        dataset_id: str,
        name: str,
    ) -> object | None:
        self.find_requests.append((dataset_id, name))
        if len(self.find_requests) <= 4:
            return None
        return self.remote_documents.get((dataset_id, name))


class _ExplicitlyRejectedUploadClient(_FakeRagflowClient):
    async def upload_document(
        self,
        *,
        dataset_id: str,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> object:
        from app.adapters.ragflow.base import RagflowClientError

        self.uploads.append(
            {
                "dataset_id": dataset_id,
                "filename": filename,
                "content": content,
                "content_type": content_type,
            }
        )
        raise RagflowClientError("HTTP 413 private-body sk-live-secret must-never-reach-database")


class _BlockingReconcileClient(_FakeRagflowClient):
    def __init__(self) -> None:
        super().__init__()
        self.reconcile_entered = asyncio.Event()
        self.reconcile_release = asyncio.Event()

    async def find_document_by_name(
        self,
        *,
        dataset_id: str,
        name: str,
    ) -> object | None:
        self.find_requests.append((dataset_id, name))
        self.reconcile_entered.set()
        await self.reconcile_release.wait()
        return None


async def test_ragflow_upload_worker_uploads_minio_object_and_parses_document(
    task_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.ai.models import DocumentAnalysis
    from app.modules.audit.models import AuditLog
    from app.modules.document.models import File
    from app.modules.ragflow import events as ragflow_events
    from app.modules.ragflow import tasks
    from app.modules.ragflow.models import SyncTask, SyncTaskLog
    from app.modules.ragflow.tasks import (
        create_ragflow_upload_sync_task,
        run_ragflow_upload_task_async,
    )
    from app.modules.user.models import User

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
    reviewed_at = datetime(2026, 7, 16, 8, 30, tzinfo=UTC)
    async with AsyncSessionFactory() as session:
        reviewer_id = (
            await session.execute(select(User.id).where(User.email == "phase4-system@company.com"))
        ).scalar_one()
        session.add(
            AuditLog(
                actor_id=reviewer_id,
                action="file.approve",
                target_type="file",
                target_id=file_id,
                ip_address="127.0.0.1",
                user_agent="metadata-contract-test",
                metadata_json={
                    "object_key": "must-not-leak",
                    "private_note": "must-not-leak",
                },
                reason="private approval note must not leak",
                created_at=reviewed_at,
            )
        )
        session.add(
            DocumentAnalysis(
                file_id=file_id,
                status="succeeded",
                sensitive_risk_level="high",
            )
        )
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
    # Redelivery after the terminal commit is a no-op and must not duplicate
    # the canonical completion event.
    await run_ragflow_upload_task_async(str(task_id))

    async with AsyncSessionFactory() as session:
        result = await session.execute(select(SyncTask).where(SyncTask.id == task_id))
        task = result.scalar_one()
        log_result = await session.execute(
            select(SyncTaskLog).where(SyncTaskLog.task_id == task_id).order_by(SyncTaskLog.id.asc())
        )
        logs = list(log_result.scalars())
        file = await session.get(File, file_id)
        success_event_result = await session.execute(
            select(EventOutbox).where(
                EventOutbox.event_type == ragflow_events.RAGFLOW_SYNC_TASK_SUCCEEDED,
                EventOutbox.aggregate_id == str(task_id),
            )
        )
        success_events = list(success_event_result.scalars())
        assert file is not None

    assert task.status == "succeeded"
    assert task.started_at is not None
    assert task.finished_at is not None
    assert file.status == "parsed"
    assert file.ragflow_document_id == "ragflow-doc-phase5"
    assert file.ragflow_parse_status == "DONE"
    assert file.ragflow_error_message is None
    assert file.last_sync_at is not None
    assert len(success_events) == 1
    assert success_events[0].payload == {
        "sync_task_id": str(task_id),
        "file_id": str(file_id),
        "task_type": "ragflow_upload",
        "status": "succeeded",
    }
    assert storage.reads == [("knowledge-files", f"uploads/{uploader_id}/file-phase4-handbook.pdf")]
    expected_document_name = f"{file_id}-phase4-handbook.pdf"
    assert client.uploads == [
        {
            "dataset_id": "ragflow-phase5",
            "filename": expected_document_name,
            "content": b"phase 5 document body",
            "content_type": "application/pdf",
        }
    ]
    assert client.metadata_updates[0]["name"] == expected_document_name
    assert client.parse_requests == [("ragflow-phase5", "ragflow-doc-phase5")]
    assert client.status_requests == [("ragflow-phase5", "ragflow-doc-phase5")]
    assert [log.message for log in logs] == [
        "ragflow upload task queued",
        "ragflow upload task started",
        "ragflow document reconciliation started",
        "ragflow document remote upload requested",
        "ragflow document uploaded",
        "ragflow document metadata updated",
        "ragflow document parse started",
        "ragflow parse status DONE",
        "initial ragflow document version activated",
        "ragflow upload task completed",
    ]
    assert client.metadata_updates[0]["metadata"] == {
        "source": "knowledge_uploader",
        "file_id": str(file_id),
        "version_id": str(file_id),
        "version_number": 1,
        "series_id": str(file_id),
        "replaces_file_id": None,
        "is_current_version": True,
        "uploader_id": str(uploader_id),
        "department_id": str(department_id),
        "department_name": "研发知识部",
        "department_code": "research-ops",
        "category_id": None,
        "tags": [],
        "visibility": "private",
        "reviewer_id": str(reviewer_id),
        "reviewed_at": reviewed_at.isoformat(),
        "sensitive_risk_level": "high",
        "content_hash": "b" * 64,
        "uploaded_at": file.uploaded_at.isoformat(),
    }
    metadata = client.metadata_updates[0]["metadata"]
    assert isinstance(metadata, dict)
    assert set(metadata).isdisjoint(
        {"email", "object_key", "api_key", "description", "private_note", "reason"}
    )


async def test_ragflow_upload_automatically_reconciles_remote_success_without_duplicate(
    task_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File
    from app.modules.ragflow import tasks
    from app.modules.ragflow.models import SyncTask, SyncTaskLog
    from app.modules.ragflow.tasks import create_ragflow_upload_sync_task

    token = await _create_admin_token(task_client)
    uploader_id = await _create_user(
        email="ragflow-reconcile-owner@company.com",
        password="password123",
    )
    _, mapping_id = await _create_category_and_mapping(
        task_client,
        token,
        ragflow_dataset_id="ragflow-reconcile",
        ragflow_dataset_name="远端对账知识库",
    )
    file_id = await _create_file(
        uploader_id=uploader_id,
        status_value="queued",
        review_status="approved",
        dataset_mapping_id=UUID(mapping_id),
        ragflow_dataset_id="ragflow-reconcile",
    )
    async with AsyncSessionFactory() as session:
        task_id = await create_ragflow_upload_sync_task(session=session, file_id=file_id)
        await session.commit()

    storage = _FakeReadableStorage(b"remote upload reconciliation body")
    client = _LostFirstUploadResponseClient()
    monkeypatch.setattr(tasks, "build_document_storage", lambda _settings: storage)

    async def _fake_build_ragflow_client() -> object:
        return client

    monkeypatch.setattr(
        tasks,
        "build_ragflow_client_from_runtime_config",
        _fake_build_ragflow_client,
    )

    await tasks.run_ragflow_upload_task_async(str(task_id))
    async with AsyncSessionFactory() as session:
        deferred_task = await session.get(SyncTask, task_id)
        assert deferred_task is not None
        assert deferred_task.status == "queued"
        assert deferred_task.reconcile_attempt_count == 1
        deferred_task.reconcile_not_before = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    await tasks.run_ragflow_upload_task_async(str(task_id))

    async with AsyncSessionFactory() as session:
        task = await session.get(SyncTask, task_id)
        file = await session.get(File, file_id)
        log_result = await session.execute(
            select(SyncTaskLog).where(SyncTaskLog.task_id == task_id)
        )
        messages = [log.message for log in log_result.scalars()]
    assert task is not None
    assert task.status == "succeeded"
    assert task.lease_token is None
    assert file is not None
    assert file.status == "parsed"
    assert file.ragflow_document_id == "ragflow-reconciled-doc"
    assert len(client.uploads) == 1
    assert len(client.find_requests) == 2
    assert len(storage.reads) == 1
    expected_document_name = f"{file_id}-phase4-handbook.pdf"
    assert client.uploads[0]["filename"] == expected_document_name
    assert client.find_requests == [
        ("ragflow-reconcile", expected_document_name),
        ("ragflow-reconcile", expected_document_name),
    ]
    assert client.metadata_updates[0]["name"] == expected_document_name
    assert "ragflow document reconciled after interrupted upload" in messages


async def test_deduplicated_local_rows_keep_distinct_remote_document_identities(
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
        email="ragflow-deduplicated-owner@company.com",
        password="password123",
    )
    _, mapping_id = await _create_category_and_mapping(
        task_client,
        token,
        ragflow_dataset_id="ragflow-deduplicated",
        ragflow_dataset_name="去重隔离知识库",
    )
    first_file_id = await _create_file(
        uploader_id=uploader_id,
        status_value="queued",
        review_status="approved",
        hash_value="d" * 64,
        dataset_mapping_id=UUID(mapping_id),
        ragflow_dataset_id="ragflow-deduplicated",
    )
    second_file_id = await _create_file(
        uploader_id=uploader_id,
        status_value="queued",
        review_status="approved",
        hash_value="d" * 64,
        dataset_mapping_id=UUID(mapping_id),
        ragflow_dataset_id="ragflow-deduplicated",
    )
    async with AsyncSessionFactory() as session:
        first = await session.get(File, first_file_id)
        second = await session.get(File, second_file_id)
        assert first is not None and second is not None
        # Local SHA256 deduplication deliberately reuses the physical MinIO object.
        second.stored_name = first.stored_name
        second.object_key = first.object_key
        first_task_id = await create_ragflow_upload_sync_task(
            session=session,
            file_id=first_file_id,
        )
        second_task_id = await create_ragflow_upload_sync_task(
            session=session,
            file_id=second_file_id,
        )
        await session.commit()

    storage = _FakeReadableStorage(b"one physical object, two logical files")
    client = _PerNameRagflowClient()
    monkeypatch.setattr(tasks, "build_document_storage", lambda _settings: storage)

    async def _fake_build_ragflow_client() -> object:
        return client

    monkeypatch.setattr(
        tasks,
        "build_ragflow_client_from_runtime_config",
        _fake_build_ragflow_client,
    )

    await tasks.run_ragflow_upload_task_async(str(first_task_id))
    await tasks.run_ragflow_upload_task_async(str(second_task_id))

    async with AsyncSessionFactory() as session:
        first = await session.get(File, first_file_id)
        second = await session.get(File, second_file_id)
        first_task = await session.get(SyncTask, first_task_id)
        second_task = await session.get(SyncTask, second_task_id)
        assert first is not None and second is not None
        assert first_task is not None and second_task is not None

    first_name = f"{first_file_id}-phase4-handbook.pdf"
    second_name = f"{second_file_id}-phase4-handbook.pdf"
    assert first.object_key == second.object_key
    assert [upload["filename"] for upload in client.uploads] == [first_name, second_name]
    assert first.ragflow_document_id == "ragflow-doc-1"
    assert second.ragflow_document_id == "ragflow-doc-2"
    assert first.ragflow_document_id != second.ragflow_document_id
    assert [update["name"] for update in client.metadata_updates] == [first_name, second_name]
    metadata_file_ids: list[object] = []
    for update in client.metadata_updates:
        metadata = update["metadata"]
        assert isinstance(metadata, dict)
        metadata_file_ids.append(metadata["file_id"])
    assert metadata_file_ids == [str(first_file_id), str(second_file_id)]
    assert first_task.status == second_task.status == "succeeded"


async def test_timeout_after_accept_automatically_reconciles_without_second_upload(
    task_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.document.models import File
    from app.modules.ragflow import tasks
    from app.modules.ragflow.exceptions import RagflowTaskAlreadyRunningError
    from app.modules.ragflow.models import SyncTask
    from app.modules.ragflow.tasks import create_ragflow_upload_sync_task

    token = await _create_admin_token(task_client)
    uploader_id = await _create_user(
        email="ragflow-eventual-owner@company.com",
        password="password123",
    )
    _, mapping_id = await _create_category_and_mapping(
        task_client,
        token,
        ragflow_dataset_id="ragflow-eventual",
        ragflow_dataset_name="最终一致性知识库",
    )
    file_id = await _create_file(
        uploader_id=uploader_id,
        status_value="queued",
        review_status="approved",
        dataset_mapping_id=UUID(mapping_id),
        ragflow_dataset_id="ragflow-eventual",
    )
    async with AsyncSessionFactory() as session:
        task_id = await create_ragflow_upload_sync_task(session=session, file_id=file_id)
        await session.commit()

    storage = _FakeReadableStorage(b"eventually consistent upload")
    client = _EventuallyConsistentLostUploadClient()
    monkeypatch.setattr(tasks, "build_document_storage", lambda _settings: storage)

    async def _fake_build_ragflow_client() -> object:
        return client

    monkeypatch.setattr(
        tasks,
        "build_ragflow_client_from_runtime_config",
        _fake_build_ragflow_client,
    )

    await tasks.run_ragflow_upload_task_async(str(task_id))

    async with AsyncSessionFactory() as session:
        task = await session.get(SyncTask, task_id)
        file = await session.get(File, file_id)
        assert task is not None
        assert file is not None
        assert task.status == "queued"
        assert task.reconcile_attempt_count == 1
        assert file.status == "syncing"
        assert file.ragflow_parse_status == "UPLOADING"
        assert len(client.uploads) == 1
        await session.commit()

    with pytest.raises(RagflowTaskAlreadyRunningError):
        await tasks.run_ragflow_upload_task_async(str(task_id))
    assert len(client.find_requests) == 1
    assert len(client.uploads) == 1

    async with AsyncSessionFactory() as session:
        task = await session.get(SyncTask, task_id)
        assert task is not None
        task.reconcile_not_before = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    await tasks.run_ragflow_upload_task_async(str(task_id))

    async with AsyncSessionFactory() as session:
        task = await session.get(SyncTask, task_id)
        assert task is not None
        assert task.status == "queued"
        assert task.reconcile_attempt_count == 2
        task.reconcile_not_before = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    await tasks.run_ragflow_upload_task_async(str(task_id))

    async with AsyncSessionFactory() as session:
        task = await session.get(SyncTask, task_id)
        file = await session.get(File, file_id)
        event_result = await session.execute(
            select(EventOutbox).where(
                EventOutbox.aggregate_id == str(task_id),
                EventOutbox.event_type == "ragflow.sync_task.queued",
            )
        )
        countdowns = [
            event.payload.get("countdown_seconds")
            for event in event_result.scalars()
            if event.payload.get("countdown_seconds") is not None
        ]
        assert task is not None
        assert file is not None

    assert task.status == "failed"
    assert task.error_message == "RagflowUploadOutcomeUnknownError"
    assert file.status == "failed"
    assert file.ragflow_parse_status == "UPLOADING"
    assert file.ragflow_document_id is None
    assert len(client.uploads) == 1
    assert len(client.find_requests) == 3
    assert len(storage.reads) == 1
    assert countdowns == [5, 30]


async def test_explicit_upload_rejection_fails_without_reconciliation_or_secret_leak(
    task_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.document.models import File
    from app.modules.ragflow import tasks
    from app.modules.ragflow.models import SyncTask, SyncTaskLog
    from app.modules.ragflow.tasks import create_ragflow_upload_sync_task

    token = await _create_admin_token(task_client)
    uploader_id = await _create_user(
        email="ragflow-rejected-owner@company.com",
        password="password123",
    )
    _, mapping_id = await _create_category_and_mapping(
        task_client,
        token,
        ragflow_dataset_id="ragflow-rejected",
        ragflow_dataset_name="明确拒绝知识库",
    )
    file_id = await _create_file(
        uploader_id=uploader_id,
        status_value="queued",
        review_status="approved",
        dataset_mapping_id=UUID(mapping_id),
        ragflow_dataset_id="ragflow-rejected",
    )
    async with AsyncSessionFactory() as session:
        task_id = await create_ragflow_upload_sync_task(session=session, file_id=file_id)
        await session.commit()

    client = _ExplicitlyRejectedUploadClient()
    monkeypatch.setattr(
        tasks,
        "build_document_storage",
        lambda _settings: _FakeReadableStorage(b"explicit rejection"),
    )

    async def _fake_build_ragflow_client() -> object:
        return client

    monkeypatch.setattr(
        tasks,
        "build_ragflow_client_from_runtime_config",
        _fake_build_ragflow_client,
    )
    await tasks.run_ragflow_upload_task_async(str(task_id))

    async with AsyncSessionFactory() as session:
        task = await session.get(SyncTask, task_id)
        file = await session.get(File, file_id)
        messages = list(
            (
                await session.execute(
                    select(SyncTaskLog.message).where(SyncTaskLog.task_id == task_id)
                )
            ).scalars()
        )
        queued_payloads = list(
            (
                await session.execute(
                    select(EventOutbox.payload).where(
                        EventOutbox.aggregate_id == str(task_id),
                        EventOutbox.event_type == "ragflow.sync_task.queued",
                    )
                )
            ).scalars()
        )
    assert task is not None
    assert file is not None
    assert task.status == "failed"
    assert task.error_message == "RagflowClientError"
    assert task.reconcile_attempt_count == 0
    assert task.reconcile_not_before is None
    assert file.status == "failed"
    assert file.ragflow_error_message == "RagflowClientError"
    assert len(client.find_requests) == 1
    assert len(client.uploads) == 1
    assert all(payload.get("countdown_seconds") is None for payload in queued_payloads)
    persisted_text = " ".join(messages + [str(payload) for payload in queued_payloads])
    assert "private-body" not in persisted_text
    assert "sk-live-secret" not in persisted_text


async def test_remote_reconciliation_does_not_hold_task_or_file_row_locks(
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
        email="ragflow-short-transaction@company.com",
        password="password123",
    )
    _, mapping_id = await _create_category_and_mapping(
        task_client,
        token,
        ragflow_dataset_id="ragflow-short-transaction",
        ragflow_dataset_name="短事务知识库",
    )
    file_id = await _create_file(
        uploader_id=uploader_id,
        status_value="queued",
        review_status="approved",
        dataset_mapping_id=UUID(mapping_id),
        ragflow_dataset_id="ragflow-short-transaction",
    )
    async with AsyncSessionFactory() as session:
        task_id = await create_ragflow_upload_sync_task(session=session, file_id=file_id)
        await session.commit()

    client = _BlockingReconcileClient()
    monkeypatch.setattr(
        tasks,
        "build_document_storage",
        lambda _settings: _FakeReadableStorage(b"short transaction"),
    )

    async def _fake_build_ragflow_client() -> object:
        return client

    monkeypatch.setattr(
        tasks,
        "build_ragflow_client_from_runtime_config",
        _fake_build_ragflow_client,
    )
    worker = asyncio.create_task(tasks.run_ragflow_upload_task_async(str(task_id)))
    await asyncio.wait_for(client.reconcile_entered.wait(), timeout=3)
    try:
        async with AsyncSessionFactory() as session:
            await session.execute(
                select(SyncTask).where(SyncTask.id == task_id).with_for_update(nowait=True)
            )
            await session.execute(
                select(File).where(File.id == file_id).with_for_update(nowait=True)
            )
            await session.rollback()
    finally:
        client.reconcile_release.set()
    await asyncio.wait_for(worker, timeout=5)

    async with AsyncSessionFactory() as session:
        task = await session.get(SyncTask, task_id)
    assert task is not None
    assert task.status == "succeeded"


async def test_execution_heartbeat_token_mismatch_cancels_external_operation(
    task_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ragflow import tasks
    from app.modules.ragflow.exceptions import RagflowTaskLeaseLostError
    from app.modules.ragflow.models import SyncTask
    from app.modules.ragflow.tasks import create_ragflow_upload_sync_task

    await _create_admin_token(task_client)
    uploader_id = await _create_user(
        email="ragflow-heartbeat-mismatch@company.com",
        password="password123",
    )
    file_id = await _create_file(uploader_id=uploader_id)
    async with AsyncSessionFactory() as session:
        task_id = await create_ragflow_upload_sync_task(session=session, file_id=file_id)
        task = await session.get(SyncTask, task_id)
        assert task is not None
        task.status = "running"
        task.lease_token = "current-execution"
        task.started_at = datetime.now(UTC)
        await session.commit()

    operation_canceled = asyncio.Event()

    async def blocked_operation() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            operation_canceled.set()

    monkeypatch.setattr(tasks, "RAGFLOW_HEARTBEAT_INTERVAL_SECONDS", 0.01)
    with pytest.raises(RagflowTaskLeaseLostError):
        await tasks._run_with_execution_heartbeat(
            task_id=task_id,
            execution_token="stale-execution",
            operation=blocked_operation,
        )

    assert operation_canceled.is_set()
    async with AsyncSessionFactory() as session:
        task = await session.get(SyncTask, task_id)
    assert task is not None
    assert task.status == "running"
    assert task.lease_token == "current-execution"


async def test_execution_heartbeat_is_canceled_when_operation_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.ragflow import tasks

    heartbeat_canceled = asyncio.Event()

    async def pending_heartbeat(**_kwargs: object) -> None:
        try:
            await asyncio.Event().wait()
        finally:
            heartbeat_canceled.set()

    async def completed_operation() -> None:
        return None

    monkeypatch.setattr(tasks, "_maintain_ragflow_execution_lease", pending_heartbeat)
    await tasks._run_with_execution_heartbeat(
        task_id=UUID("00000000-0000-0000-0000-000000000099"),
        execution_token="finished-execution",
        operation=completed_operation,
    )

    assert heartbeat_canceled.is_set()


async def test_heartbeat_timestamp_survives_stale_business_session_commit(
    task_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ragflow.models import SyncTask
    from app.modules.ragflow.repository import (  # noqa: TID251 - same-module test
        RagflowTaskRepository,
    )
    from app.modules.ragflow.service import (  # noqa: TID251 - same-module test
        RAGFLOW_EXECUTION_LEASE_SECONDS,
        RagflowTaskService,
    )
    from app.modules.ragflow.tasks import create_ragflow_upload_sync_task

    await _create_admin_token(task_client)
    uploader_id = await _create_user(
        email="ragflow-heartbeat-stale-session@company.com",
        password="password123",
    )
    file_id = await _create_file(uploader_id=uploader_id)
    async with AsyncSessionFactory() as session:
        task_id = await create_ragflow_upload_sync_task(session=session, file_id=file_id)
        task = await session.get(SyncTask, task_id)
        assert task is not None
        initial_started_at = datetime.now(UTC) - timedelta(
            seconds=RAGFLOW_EXECUTION_LEASE_SECONDS + 60
        )
        task.status = "running"
        task.lease_token = "heartbeat-owner"
        task.started_at = initial_started_at
        task.lease_heartbeat_at = initial_started_at
        await session.commit()

    async with AsyncSessionFactory() as business_session:
        stale_task = await business_session.get(SyncTask, task_id)
        assert stale_task is not None
        async with AsyncSessionFactory() as heartbeat_session:
            heartbeat_service = RagflowTaskService(
                session=heartbeat_session,
                repository=RagflowTaskRepository(heartbeat_session),
            )
            await heartbeat_service.heartbeat_execution_lease(
                task_id=task_id,
                execution_token="heartbeat-owner",
            )
            await heartbeat_service.heartbeat_execution_lease(
                task_id=task_id,
                execution_token="heartbeat-owner",
            )
        stale_task.error_message = "business session progress"
        await business_session.commit()

    async with AsyncSessionFactory() as session:
        claim_service = RagflowTaskService(
            session=session,
            repository=RagflowTaskRepository(session),
        )
        reclaimed = await claim_service.claim_running(
            task_id,
            execution_token="would-be-stealer",
        )
    assert reclaimed is False

    async with AsyncSessionFactory() as session:
        task = await session.get(SyncTask, task_id)
    assert task is not None
    assert task.started_at == initial_started_at
    assert task.lease_heartbeat_at is not None
    assert task.lease_heartbeat_at > initial_started_at
    assert task.lease_token == "heartbeat-owner"
    assert task.error_message == "business session progress"


async def test_exhausted_redelivery_schedules_unique_probe_and_stale_task_recovers(
    task_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.ragflow import tasks
    from app.modules.ragflow.models import SyncTask
    from app.modules.ragflow.repository import (  # noqa: TID251 - same-module test
        RagflowTaskRepository,
    )
    from app.modules.ragflow.service import (  # noqa: TID251 - same-module test
        RAGFLOW_EXECUTION_LEASE_SECONDS,
        RagflowTaskService,
    )
    from app.modules.ragflow.tasks import create_ragflow_upload_sync_task

    token = await _create_admin_token(task_client)
    uploader_id = await _create_user(
        email="ragflow-recovery-probe@company.com",
        password="password123",
    )
    _, mapping_id = await _create_category_and_mapping(
        task_client,
        token,
        ragflow_dataset_id="ragflow-recovery-probe",
        ragflow_dataset_name="恢复探针知识库",
    )
    file_id = await _create_file(
        uploader_id=uploader_id,
        status_value="queued",
        review_status="approved",
        dataset_mapping_id=UUID(mapping_id),
        ragflow_dataset_id="ragflow-recovery-probe",
    )
    async with AsyncSessionFactory() as session:
        task_id = await create_ragflow_upload_sync_task(session=session, file_id=file_id)
        task = await session.get(SyncTask, task_id)
        assert task is not None
        task.status = "running"
        task.lease_token = "dead-worker"
        task.started_at = datetime.now(UTC)
        await session.commit()

    async with AsyncSessionFactory() as session:
        service = RagflowTaskService(
            session=session,
            repository=RagflowTaskRepository(session),
        )
        assert await service.schedule_execution_recovery_probe(task_id) is True
        assert await service.schedule_execution_recovery_probe(task_id) is False

    async with AsyncSessionFactory() as session:
        task = await session.get(SyncTask, task_id)
        assert task is not None
        assert task.status == "running"
        stale_at = datetime.now(UTC) - timedelta(seconds=RAGFLOW_EXECUTION_LEASE_SECONDS + 1)
        task.lease_heartbeat_at = stale_at
        await session.commit()

    client = _FakeRagflowClient()
    monkeypatch.setattr(
        tasks,
        "build_document_storage",
        lambda _settings: _FakeReadableStorage(b"recovered upload"),
    )

    async def _fake_build_ragflow_client() -> object:
        return client

    monkeypatch.setattr(
        tasks,
        "build_ragflow_client_from_runtime_config",
        _fake_build_ragflow_client,
    )
    await tasks.run_ragflow_upload_task_async(str(task_id))

    async with AsyncSessionFactory() as session:
        task = await session.get(SyncTask, task_id)
        event_result = await session.execute(
            select(EventOutbox).where(
                EventOutbox.aggregate_id == str(task_id),
                EventOutbox.event_type == "ragflow.sync_task.queued",
            )
        )
        probe_events = [
            event
            for event in event_result.scalars()
            if event.payload.get("countdown_seconds") == 300
        ]
    assert task is not None
    assert task.status == "succeeded"
    assert task.recovery_probe_due_at is None
    assert len(probe_events) == 1


async def test_stale_execution_cannot_persist_success_or_failure(
    task_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.document.models import File
    from app.modules.ragflow import events as ragflow_events
    from app.modules.ragflow.models import SyncTask
    from app.modules.ragflow.repository import (  # noqa: TID251 - same-module test
        RagflowTaskRepository,
    )
    from app.modules.ragflow.service import RagflowTaskService  # noqa: TID251 - same-module test
    from app.modules.ragflow.tasks import create_ragflow_upload_sync_task

    await _create_admin_token(task_client)
    uploader_id = await _create_user(
        email="ragflow-fencing-owner@company.com",
        password="password123",
    )
    file_id = await _create_file(
        uploader_id=uploader_id,
        status_value="queued",
        review_status="approved",
    )
    async with AsyncSessionFactory() as session:
        task_id = await create_ragflow_upload_sync_task(session=session, file_id=file_id)
        task = await session.get(SyncTask, task_id)
        assert task is not None
        task.status = "running"
        task.started_at = datetime.now(UTC)
        task.lease_token = "current-execution"
        await session.commit()

    async with AsyncSessionFactory() as session:
        service = RagflowTaskService(
            session=session,
            repository=RagflowTaskRepository(session),
        )
        await service.mark_succeeded(
            task_id,
            expected_lease_token="stale-execution",
            publish_sync_success=True,
        )
        await service.mark_failed(
            task_id,
            "stale worker failure",
            expected_lease_token="stale-execution",
        )
        task = await session.get(SyncTask, task_id)
        file = await session.get(File, file_id)
        event_result = await session.execute(
            select(EventOutbox).where(
                EventOutbox.event_type == ragflow_events.RAGFLOW_SYNC_TASK_SUCCEEDED
            )
        )
        success_event = event_result.scalar_one_or_none()
        assert task is not None
        assert file is not None

    assert task.status == "running"
    assert task.lease_token == "current-execution"
    assert task.finished_at is None
    assert task.error_message is None
    assert file.status == "queued"
    assert file.ragflow_error_message is None
    assert success_event is None


async def test_ragflow_success_event_rolls_back_with_task_state(
    task_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.ragflow import events as ragflow_events
    from app.modules.ragflow.models import SyncTask
    from app.modules.ragflow.repository import (  # noqa: TID251 - same-module test
        RagflowTaskRepository,
    )
    from app.modules.ragflow.service import RagflowTaskService  # noqa: TID251 - same-module test
    from app.modules.ragflow.tasks import create_ragflow_upload_sync_task

    await _create_admin_token(task_client)
    uploader_id = await _create_user(
        email="ragflow-success-rollback@company.com",
        password="password123",
    )
    file_id = await _create_file(
        uploader_id=uploader_id,
        status_value="parsed",
        review_status="approved",
        ragflow_parse_status="DONE",
    )
    async with AsyncSessionFactory() as session:
        task_id = await create_ragflow_upload_sync_task(session=session, file_id=file_id)
        task = await session.get(SyncTask, task_id)
        assert task is not None
        task.status = "running"
        task.started_at = datetime.now(UTC)
        task.lease_token = "current-execution"
        await session.commit()

    async with AsyncSessionFactory() as session:
        service = RagflowTaskService(
            session=session,
            repository=RagflowTaskRepository(session),
        )

        async def _fail_commit() -> None:
            await session.flush()
            raise RuntimeError("commit unavailable")

        monkeypatch.setattr(session, "commit", _fail_commit)
        with pytest.raises(RuntimeError, match="commit unavailable"):
            await service.mark_succeeded(
                task_id,
                expected_lease_token="current-execution",
                publish_sync_success=True,
            )
        await session.rollback()

    async with AsyncSessionFactory() as session:
        task = await session.get(SyncTask, task_id)
        event_result = await session.execute(
            select(EventOutbox).where(
                EventOutbox.event_type == ragflow_events.RAGFLOW_SYNC_TASK_SUCCEEDED
            )
        )
        success_event = event_result.scalar_one_or_none()
    assert task is not None
    assert task.status == "running"
    assert task.lease_token == "current-execution"
    assert success_event is None


async def test_failure_persistence_redelivery_waits_then_reclaims_stale_lease(
    task_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ragflow import tasks
    from app.modules.ragflow.exceptions import RagflowTaskAlreadyRunningError
    from app.modules.ragflow.models import SyncTask, SyncTaskLog
    from app.modules.ragflow.repository import (  # noqa: TID251 - same-module test
        RagflowTaskRepository,
    )
    from app.modules.ragflow.service import RagflowTaskService  # noqa: TID251 - same-module test

    await _create_admin_token(task_client)
    uploader_id = await _create_user(
        email="failure-persistence-redelivery@company.com",
        password="password123",
    )
    file_id = await _create_file(uploader_id=uploader_id, status_value="queued")
    async with AsyncSessionFactory() as session:
        task = SyncTask(
            file_id=file_id,
            task_type="ragflow_upload",
            status="queued",
            retry_count=0,
            max_retry_count=3,
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        task_id = task.id

    successful_claims = 0

    async def claim_and_run(
        claimed_task_id: UUID,
        *,
        execution_token: str,
    ) -> None:
        nonlocal successful_claims
        async with AsyncSessionFactory() as session:
            service = RagflowTaskService(
                session=session,
                repository=RagflowTaskRepository(session),
            )
            claimed = await service.claim_running(
                claimed_task_id,
                expected_task_types={"ragflow_upload"},
                execution_token=execution_token,
            )
            if not claimed:
                raise RagflowTaskAlreadyRunningError
            successful_claims += 1
            if successful_claims == 1:
                raise tasks._ClaimedRagflowExecutionError("RagflowClientError")
            await service.mark_succeeded(
                claimed_task_id,
                expected_lease_token=execution_token,
            )

    async def persistence_unavailable(
        *_args: object,
        **_kwargs: object,
    ) -> bool:
        raise ConnectionError

    monkeypatch.setattr(tasks, "_run_ragflow_upload_task", claim_and_run)
    original_mark_failed = tasks._mark_ragflow_upload_task_failed
    monkeypatch.setattr(tasks, "_mark_ragflow_upload_task_failed", persistence_unavailable)

    with pytest.raises(ConnectionError):
        await tasks.run_ragflow_upload_task_async(str(task_id))

    async with AsyncSessionFactory() as session:
        persisted = await session.get(SyncTask, task_id)
        assert persisted is not None
        assert persisted.status == "running"
        assert persisted.lease_token is not None

    with pytest.raises(RagflowTaskAlreadyRunningError):
        await tasks.run_ragflow_upload_task_async(str(task_id))

    async with AsyncSessionFactory() as session:
        persisted = await session.get(SyncTask, task_id)
        assert persisted is not None
        persisted.lease_heartbeat_at = datetime.now(UTC) - timedelta(hours=2)
        await session.commit()

    monkeypatch.setattr(tasks, "_mark_ragflow_upload_task_failed", original_mark_failed)
    await tasks.run_ragflow_upload_task_async(str(task_id))

    async with AsyncSessionFactory() as session:
        persisted = await session.get(SyncTask, task_id)
        log_result = await session.execute(
            select(SyncTaskLog.message).where(SyncTaskLog.task_id == task_id)
        )
        messages = list(log_result.scalars())
    assert persisted is not None
    assert persisted.status == "succeeded"
    assert persisted.lease_token is None
    assert successful_claims == 2
    assert "stale ragflow execution lease reclaimed" in messages


async def test_manual_upload_retry_restarts_failed_existing_parse(
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
    client = _FakeRagflowClient(
        document_id="existing-ragflow-doc",
        run_statuses=["FAIL", "DONE"],
    )
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
    assert client.metadata_updates[0]["document_id"] == "existing-ragflow-doc"
    assert client.parse_requests == [("ragflow-phase5", "existing-ragflow-doc")]
    assert client.status_requests == [
        ("ragflow-phase5", "existing-ragflow-doc"),
        ("ragflow-phase5", "existing-ragflow-doc"),
    ]


async def test_ragflow_upload_worker_queues_status_check_for_nonterminal_status(
    task_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.document.models import File
    from app.modules.ragflow import events as ragflow_events
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
        event_result = await session.execute(
            select(EventOutbox).where(
                EventOutbox.event_type == ragflow_events.RAGFLOW_SYNC_TASK_SUCCEEDED
            )
        )
        success_event = event_result.scalar_one_or_none()
        assert task is not None
        assert file is not None

    assert task.status == "succeeded"
    assert task.error_message is None
    assert status_check_task.status == "queued"
    assert status_check_task.retry_count == 1
    assert status_check_task.max_retry_count == 120
    assert file.status == "parsing"
    assert file.ragflow_document_id == "ragflow-doc-phase5"
    assert file.ragflow_parse_status == "RUNNING"
    assert file.ragflow_error_message is None
    assert client.uploads != []
    assert client.parse_requests == [("ragflow-phase5", "ragflow-doc-phase5")]
    assert client.status_requests == [("ragflow-phase5", "ragflow-doc-phase5")]
    assert success_event is None


async def test_ragflow_status_polling_budget_exhaustion_fails_closed(
    task_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File
    from app.modules.ragflow import tasks
    from app.modules.ragflow.models import SyncTask
    from app.modules.ragflow.service import (  # noqa: TID251 - same-module test
        RAGFLOW_PARSE_POLL_EXHAUSTED_ERROR,
    )

    token = await _create_admin_token(task_client)
    uploader_id = await _create_user(
        email="status-poll-exhausted@company.com",
        password="password123",
    )
    _, mapping_id = await _create_category_and_mapping(
        task_client,
        token,
        ragflow_dataset_id="ragflow-poll-budget",
        ragflow_dataset_name="轮询预算知识库",
    )
    file_id = await _create_file(
        uploader_id=uploader_id,
        status_value="parsing",
        review_status="approved",
        dataset_mapping_id=UUID(mapping_id),
        ragflow_dataset_id="ragflow-poll-budget",
        ragflow_document_id="poll-budget-doc",
        ragflow_parse_status="RUNNING",
    )
    async with AsyncSessionFactory() as session:
        task = SyncTask(
            file_id=file_id,
            task_type="ragflow_status_check",
            status="queued",
            retry_count=3,
            max_retry_count=3,
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        task_id = task.id

    storage = _FakeReadableStorage(b"must not be read")
    client = _FakeRagflowClient(document_id="poll-budget-doc", run_statuses=["RUNNING"])
    monkeypatch.setattr(tasks, "build_document_storage", lambda _settings: storage)

    async def _fake_build_ragflow_client() -> object:
        return client

    monkeypatch.setattr(
        tasks,
        "build_ragflow_client_from_runtime_config",
        _fake_build_ragflow_client,
    )

    await tasks.run_ragflow_upload_task_async(str(task_id))

    async with AsyncSessionFactory() as session:
        loaded_task = await session.get(SyncTask, task_id)
        loaded_file = await session.get(File, file_id)
        result = await session.execute(
            select(SyncTask).where(
                SyncTask.file_id == file_id,
                SyncTask.task_type == "ragflow_status_check",
            )
        )
        status_tasks = list(result.scalars())
    assert loaded_task is not None
    assert loaded_task.status == "failed"
    assert loaded_task.error_message == RAGFLOW_PARSE_POLL_EXHAUSTED_ERROR
    assert loaded_file is not None
    assert loaded_file.status == "failed"
    assert len(status_tasks) == 1
    assert client.status_requests == [("ragflow-poll-budget", "poll-budget-doc")]


async def test_ragflow_status_check_worker_marks_done_as_parsed(
    task_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.document.models import File
    from app.modules.ragflow import events as ragflow_events
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
    await tasks.run_ragflow_upload_task_async(str(task_id))

    async with AsyncSessionFactory() as session:
        loaded_task = await session.get(SyncTask, task_id)
        loaded_file = await session.get(File, file_id)
        event_result = await session.execute(
            select(EventOutbox).where(
                EventOutbox.event_type == ragflow_events.RAGFLOW_SYNC_TASK_SUCCEEDED,
                EventOutbox.aggregate_id == str(task_id),
            )
        )
        success_events = list(event_result.scalars())
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
    assert len(success_events) == 1
    assert success_events[0].payload == {
        "sync_task_id": str(task_id),
        "file_id": str(file_id),
        "task_type": "ragflow_status_check",
        "status": "succeeded",
    }


async def test_reconciled_failed_version_status_check_transitions_via_parsing_and_completes_switch(
    task_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File
    from app.modules.ragflow import tasks
    from app.modules.ragflow.models import SyncTask, SyncTaskLog

    token = await _create_admin_token(task_client)
    uploader_id = await _create_user(
        email="reconcile-failed-status-check@company.com",
        password="password123",
    )
    category_id, mapping_id = await _create_category_and_mapping(
        task_client,
        token,
        ragflow_dataset_id="reconcile-dataset",
        ragflow_dataset_name="版本协调知识库",
    )
    predecessor_id, candidate_id, task_id = await _create_incomplete_version_switch_task(
        uploader_id=uploader_id
    )
    async with AsyncSessionFactory() as session:
        candidate = await session.get(File, candidate_id)
        task = await session.get(SyncTask, task_id)
        assert candidate is not None and task is not None
        candidate.category_id = UUID(category_id)
        candidate.dataset_mapping_id = UUID(mapping_id)
        candidate.status = "failed"
        candidate.ragflow_error_message = "previous status poll exhausted"
        task.task_type = "ragflow_status_check"
        task.error_message = "previous status poll exhausted"
        await session.commit()

    response = await task_client.post(
        f"/api/tasks/{task_id}/reconcile-version-switch",
        headers={"Authorization": f"Bearer {token}"},
        json={"reason": "remote parsing completed after local polling exhausted"},
    )
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "queued"

    storage = _FakeReadableStorage(b"must not be read")
    client = _FakeRagflowClient(document_id="reconcile-v2", run_statuses=["DONE"])
    monkeypatch.setattr(tasks, "build_document_storage", lambda _settings: storage)

    async def _fake_build_ragflow_client() -> object:
        return client

    monkeypatch.setattr(
        tasks,
        "build_ragflow_client_from_runtime_config",
        _fake_build_ragflow_client,
    )

    await tasks.run_ragflow_upload_task_async(str(task_id))

    async with AsyncSessionFactory() as session:
        predecessor = await session.get(File, predecessor_id)
        candidate = await session.get(File, candidate_id)
        task = await session.get(SyncTask, task_id)
        log_result = await session.execute(
            select(SyncTaskLog)
            .where(SyncTaskLog.task_id == task_id)
            .order_by(SyncTaskLog.created_at.asc(), SyncTaskLog.id.asc())
        )
        messages = [log.message for log in log_result.scalars()]

    assert predecessor is not None and candidate is not None and task is not None
    assert task.status == "succeeded"
    assert candidate.status == "parsed"
    assert candidate.ragflow_parse_status == "DONE"
    assert candidate.ragflow_error_message is None
    assert candidate.is_current_version is True
    assert candidate.version_switch_status == "completed"
    assert predecessor.is_current_version is False
    assert "ragflow existing document status check started" in messages
    assert storage.reads == []
    assert client.status_requests == [("reconcile-dataset", "reconcile-v2")]


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
    from app.modules.ragflow.exceptions import RagflowTaskAlreadyRunningError
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
            started_at=datetime.now(UTC),
            retry_count=0,
            max_retry_count=3,
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        task_id = task.id

    with pytest.raises(RagflowTaskAlreadyRunningError):
        await run_ragflow_upload_task_async(str(task_id))

    async with AsyncSessionFactory() as session:
        loaded_task = await session.get(SyncTask, task_id)
        assert loaded_task is not None

    assert loaded_task.status == "running"
    assert loaded_task.finished_at is None


async def test_stale_status_check_lease_is_reclaimed_and_completed(
    task_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File
    from app.modules.ragflow import tasks
    from app.modules.ragflow.models import SyncTask
    from app.modules.ragflow.service import (  # noqa: TID251 - same-module test
        RAGFLOW_EXECUTION_LEASE_SECONDS,
    )

    token = await _create_admin_token(task_client)
    uploader_id = await _create_user(
        email="stale-status-check@company.com",
        password="password123",
    )
    _, mapping_id = await _create_category_and_mapping(
        task_client,
        token,
        ragflow_dataset_id="ragflow-stale-lease",
        ragflow_dataset_name="过期租约知识库",
    )
    file_id = await _create_file(
        uploader_id=uploader_id,
        status_value="parsing",
        review_status="approved",
        dataset_mapping_id=UUID(mapping_id),
        ragflow_dataset_id="ragflow-stale-lease",
        ragflow_document_id="stale-lease-doc",
        ragflow_parse_status="RUNNING",
    )
    async with AsyncSessionFactory() as session:
        task = SyncTask(
            file_id=file_id,
            task_type="ragflow_status_check",
            status="running",
            started_at=datetime.now(UTC) - timedelta(seconds=RAGFLOW_EXECUTION_LEASE_SECONDS + 1),
            retry_count=1,
            max_retry_count=3,
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        task_id = task.id

    storage = _FakeReadableStorage(b"must not be read")
    client = _FakeRagflowClient(document_id="stale-lease-doc", run_statuses=["DONE"])
    monkeypatch.setattr(tasks, "build_document_storage", lambda _settings: storage)

    async def _fake_build_ragflow_client() -> object:
        return client

    monkeypatch.setattr(
        tasks,
        "build_ragflow_client_from_runtime_config",
        _fake_build_ragflow_client,
    )

    await tasks.run_ragflow_upload_task_async(str(task_id))

    async with AsyncSessionFactory() as session:
        loaded_task = await session.get(SyncTask, task_id)
        loaded_file = await session.get(File, file_id)
    assert loaded_task is not None
    assert loaded_task.status == "succeeded"
    assert loaded_file is not None
    assert loaded_file.status == "parsed"
    assert storage.reads == []
    assert client.status_requests == [("ragflow-stale-lease", "stale-lease-doc")]


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
        review_status="pending",
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
                        "action": "flag",
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


async def test_ragflow_upload_worker_blocks_critical_even_when_config_disabled(
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

    # critical 是不可配置绕过的安全红线; 保留旧配置行也不能放行.
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

    assert task.status == "failed"
    assert task.error_message == "RagflowSyncPreconditionError"
    assert storage.reads == []
    assert client.uploads == []


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

    await tasks.run_ragflow_upload_task_async(str(task_id))
    get_settings.cache_clear()

    async with AsyncSessionFactory() as session:
        result = await session.execute(select(SyncTask).where(SyncTask.id == task_id))
        task = result.scalar_one()

    assert task.status == "failed"
    assert task.error_message == "RagflowSyncPreconditionError"
    assert storage.reads == []
    assert client.uploads == []


async def test_ragflow_upload_worker_requires_allowlist_for_runtime_api_key(
    task_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    set_secret_system_config: Callable[[str, str], Awaitable[None]],
) -> None:
    from app.core.config import get_settings
    from app.core.database import AsyncSessionFactory
    from app.modules.ragflow import tasks
    from app.modules.ragflow.models import SyncTask
    from app.modules.ragflow.tasks import create_ragflow_upload_sync_task

    token = await _create_admin_token(task_client)
    uploader_id = await _create_user(
        email="phase5-runtime-key-allowlist@company.com",
        password="password123",
    )
    _, mapping_id = await _create_category_and_mapping(
        task_client,
        token,
        ragflow_dataset_id="ragflow-runtime-no-allowlist",
        ragflow_dataset_name="运行时 Key 知识库",
    )
    monkeypatch.delenv("RAGFLOW_ALLOWED_DATASET_IDS", raising=False)
    get_settings.cache_clear()
    await set_secret_system_config("ragflow.api_key", "sk-runtime-worker-abcd")
    file_id = await _create_file(
        uploader_id=uploader_id,
        status_value="queued",
        review_status="approved",
        dataset_mapping_id=UUID(mapping_id),
        ragflow_dataset_id="ragflow-runtime-no-allowlist",
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

    await tasks.run_ragflow_upload_task_async(str(task_id))

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
    from app.modules.ragflow.exceptions import RagflowTaskAlreadyRunningError
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
            started_at=datetime.now(UTC),
            retry_count=0,
            max_retry_count=3,
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        task_id = task.id

    with pytest.raises(RagflowTaskAlreadyRunningError):
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

    async def fail_upload(
        sync_task_id: UUID,
        *,
        execution_token: str,
    ) -> None:
        async with AsyncSessionFactory() as session:
            running_task = await session.get(SyncTask, sync_task_id)
            assert running_task is not None
            running_task.status = "running"
            running_task.lease_token = execution_token
            running_task.started_at = datetime.now(UTC)
            await session.commit()
        raise RuntimeError("credential-value-should-not-be-stored")

    monkeypatch.setattr(tasks, "_run_ragflow_upload_task", fail_upload)

    await tasks.run_ragflow_upload_task_async(str(task_id))

    async with AsyncSessionFactory() as session:
        result = await session.execute(select(SyncTask).where(SyncTask.id == task_id))
        task = result.scalar_one()

    assert task.status == "failed"
    assert task.error_message == "RuntimeError"
    assert "credential-value" not in (task.error_message or "")


async def test_ragflow_worker_propagates_failure_when_domain_state_cannot_persist(
    task_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ragflow import tasks
    from app.modules.ragflow.tasks import create_ragflow_upload_sync_task

    await _create_admin_token(task_client)
    uploader_id = await _create_user(
        email="phase4-unpersisted-failure@company.com",
        password="password123",
    )
    file_id = await _create_file(uploader_id=uploader_id)
    async with AsyncSessionFactory() as session:
        task_id = await create_ragflow_upload_sync_task(session=session, file_id=file_id)
        await session.commit()

    async def fail_upload(
        _sync_task_id: UUID,
        *,
        execution_token: str,
    ) -> None:
        _ = execution_token
        raise RuntimeError("remote failure")

    async def fail_persistence(
        _sync_task_id: UUID,
        _error_type: str,
        *,
        expected_lease_token: str | None = None,
        was_claimed: bool = False,
    ) -> bool:
        _ = (expected_lease_token, was_claimed)
        raise OSError("database unavailable")

    monkeypatch.setattr(tasks, "_run_ragflow_upload_task", fail_upload)
    monkeypatch.setattr(tasks, "_mark_ragflow_upload_task_failed", fail_persistence)

    with pytest.raises(OSError, match="database unavailable"):
        await tasks.run_ragflow_upload_task_async(str(task_id))


@pytest.mark.parametrize("replacement_status", ["queued", "running"])
async def test_claimed_stale_delivery_is_acked_after_new_execution_takes_ownership(
    task_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    replacement_status: str,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File
    from app.modules.ragflow import tasks
    from app.modules.ragflow.models import SyncTask
    from app.modules.ragflow.tasks import create_ragflow_upload_sync_task

    await _create_admin_token(task_client)
    uploader_id = await _create_user(
        email=f"stale-{replacement_status}@company.com",
        password="password123",
    )
    file_id = await _create_file(uploader_id=uploader_id)
    async with AsyncSessionFactory() as session:
        task_id = await create_ragflow_upload_sync_task(session=session, file_id=file_id)
        await session.commit()

    async def stale_claimed_execution(
        sync_task_id: UUID,
        *,
        execution_token: str,
    ) -> None:
        async with AsyncSessionFactory() as session:
            task = await session.get(SyncTask, sync_task_id)
            assert task is not None
            task.status = replacement_status
            task.lease_token = "new-execution" if replacement_status == "running" else None
            task.started_at = datetime.now(UTC) if replacement_status == "running" else None
            await session.commit()
        _ = execution_token
        raise tasks._ClaimedRagflowExecutionError("RuntimeError")

    monkeypatch.setattr(tasks, "_run_ragflow_upload_task", stale_claimed_execution)

    await tasks.run_ragflow_upload_task_async(str(task_id))

    async with AsyncSessionFactory() as session:
        task = await session.get(SyncTask, task_id)
        file = await session.get(File, file_id)
        assert task is not None
        assert file is not None
    assert task.status == replacement_status
    assert task.error_message is None
    assert task.lease_token == ("new-execution" if replacement_status == "running" else None)
    assert file.ragflow_error_message is None


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
