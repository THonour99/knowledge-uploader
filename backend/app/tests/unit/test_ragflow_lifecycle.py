from __future__ import annotations

import os
from collections.abc import AsyncGenerator, Awaitable, Callable
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from redis.asyncio import from_url
from sqlalchemy import select

from app.core.outbox import EventOutbox
from app.workers.outbox_dispatcher import dispatch_celery_task_for_event

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
async def lifecycle_client() -> AsyncGenerator[AsyncClient, None]:
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


async def _create_admin_token(client: AsyncClient) -> str:
    await _create_user(
        email="r4-lifecycle-admin@company.com",
        password="password123",
        role="system_admin",
    )
    return await _login(client, email="r4-lifecycle-admin@company.com", password="password123")


async def _create_file(
    *,
    uploader_id: UUID,
    status_value: str = "deleted",
    review_status: str = "approved",
    hash_value: str = "c" * 64,
    ragflow_dataset_id: str | None = "ragflow-r4-dataset",
    ragflow_document_id: str | None = "ragflow-r4-doc",
) -> UUID:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File

    file = File(
        original_name="r4-lifecycle.pdf",
        stored_name="file-r4-lifecycle.pdf",
        extension="pdf",
        mime_type="application/pdf",
        size=64,
        hash=hash_value,
        storage_type="minio",
        bucket="knowledge-files",
        object_key=f"uploads/{uploader_id}/file-r4-lifecycle.pdf",
        uploader_id=uploader_id,
        department="QA",
        visibility="private",
        description="r4 lifecycle target",
        tags=[],
        status=status_value,
        review_status=review_status,
        ragflow_dataset_id=ragflow_dataset_id,
        ragflow_document_id=ragflow_document_id,
        ai_analysis_enabled_at_upload=False,
    )
    async with AsyncSessionFactory() as session:
        session.add(file)
        await session.commit()
        await session.refresh(file)
        return file.id


class FakeCelerySender:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    def send_task(self, name: str, args: list[str], queue: str) -> object:
        self.sent.append({"name": name, "args": args, "queue": queue})
        return object()


class FakeDeleteRagflowClient:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.deletes: list[tuple[str, str]] = []

    async def delete_document(self, *, dataset_id: str, document_id: str) -> None:
        self.deletes.append((dataset_id, document_id))
        if self.error is not None:
            raise self.error


def _lifecycle_event(
    *,
    event_type: str,
    payload: dict[str, object],
) -> EventOutbox:
    return EventOutbox(
        event_type=event_type,
        aggregate_type="file",
        aggregate_id=str(payload.get("file_id", "file-1")),
        payload=payload,
    )


# ---------------------------------------------------------------------------
# 事件决策矩阵: document.file.deleted / document.file.archived
# ---------------------------------------------------------------------------


async def test_file_deleted_event_dispatches_delete_task_creation() -> None:
    event = _lifecycle_event(
        event_type="document.file.deleted",
        payload={
            "file_id": "file-1",
            "ragflow_document_id": "doc-1",
            "ragflow_dataset_id": "ds-1",
            "delete_remote": True,
        },
    )
    sender = FakeCelerySender()

    dispatch_celery_task_for_event(event, sender=sender)

    assert sender.sent == [
        {"name": "ragflow.create_delete_task", "args": ["file-1"], "queue": "ragflow_queue"}
    ]


async def test_file_deleted_event_skips_when_delete_remote_disabled() -> None:
    event = _lifecycle_event(
        event_type="document.file.deleted",
        payload={
            "file_id": "file-1",
            "ragflow_document_id": "doc-1",
            "ragflow_dataset_id": "ds-1",
            "delete_remote": False,
        },
    )
    sender = FakeCelerySender()

    dispatch_celery_task_for_event(event, sender=sender)

    assert sender.sent == []


async def test_file_deleted_event_skips_when_document_id_missing() -> None:
    event = _lifecycle_event(
        event_type="document.file.deleted",
        payload={
            "file_id": "file-1",
            "ragflow_document_id": None,
            "ragflow_dataset_id": None,
            "delete_remote": True,
        },
    )
    sender = FakeCelerySender()

    dispatch_celery_task_for_event(event, sender=sender)

    assert sender.sent == []


async def test_file_deleted_event_requires_file_id_when_actionable() -> None:
    event = _lifecycle_event(
        event_type="document.file.deleted",
        payload={
            "ragflow_document_id": "doc-1",
            "ragflow_dataset_id": "ds-1",
            "delete_remote": True,
        },
    )

    with pytest.raises(RuntimeError, match="missing file_id"):
        dispatch_celery_task_for_event(event, sender=FakeCelerySender())


async def test_file_archived_event_dispatches_delete_when_keep_remote_disabled() -> None:
    event = _lifecycle_event(
        event_type="document.file.archived",
        payload={
            "file_id": "file-2",
            "ragflow_document_id": "doc-2",
            "keep_remote": False,
        },
    )
    sender = FakeCelerySender()

    dispatch_celery_task_for_event(event, sender=sender)

    assert sender.sent == [
        {"name": "ragflow.create_delete_task", "args": ["file-2"], "queue": "ragflow_queue"}
    ]


async def test_file_archived_event_skips_when_keep_remote_enabled() -> None:
    event = _lifecycle_event(
        event_type="document.file.archived",
        payload={
            "file_id": "file-2",
            "ragflow_document_id": "doc-2",
            "keep_remote": True,
        },
    )
    sender = FakeCelerySender()

    dispatch_celery_task_for_event(event, sender=sender)

    assert sender.sent == []


async def test_delete_sync_task_queued_event_routes_to_delete_worker() -> None:
    event = EventOutbox(
        event_type="ragflow.sync_task.queued",
        aggregate_type="sync_task",
        aggregate_id="task-9",
        payload={
            "sync_task_id": "task-9",
            "file_id": "file-9",
            "task_type": "ragflow_delete",
            "status": "queued",
        },
    )
    sender = FakeCelerySender()

    dispatch_celery_task_for_event(event, sender=sender)

    assert sender.sent == [{"name": "ragflow.delete", "args": ["task-9"], "queue": "ragflow_queue"}]


# ---------------------------------------------------------------------------
# ragflow_delete 任务创建
# ---------------------------------------------------------------------------


async def test_create_ragflow_delete_task_creates_task_and_queue_event(
    lifecycle_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ragflow.models import SyncTask
    from app.modules.ragflow.tasks import run_create_ragflow_delete_task_async

    uploader_id = await _create_user(email="r4-delete-create@company.com", password="password123")
    file_id = await _create_file(uploader_id=uploader_id)

    task_id = await run_create_ragflow_delete_task_async(str(file_id))

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
    assert task.task_type == "ragflow_delete"
    assert task.status == "queued"
    assert outbox_event.payload["task_type"] == "ragflow_delete"
    assert outbox_event.payload["sync_task_id"] == task_id


async def test_create_ragflow_delete_task_is_idempotent(
    lifecycle_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ragflow.tasks import create_ragflow_delete_sync_task

    uploader_id = await _create_user(
        email="r4-delete-idempotent@company.com",
        password="password123",
    )
    file_id = await _create_file(uploader_id=uploader_id)

    async with AsyncSessionFactory() as session:
        first_task_id = await create_ragflow_delete_sync_task(session=session, file_id=file_id)
        second_task_id = await create_ragflow_delete_sync_task(session=session, file_id=file_id)
        await session.commit()

    assert first_task_id == second_task_id


# ---------------------------------------------------------------------------
# ragflow_delete 任务执行
# ---------------------------------------------------------------------------


async def _setup_delete_task(
    *,
    status_value: str = "deleted",
    ragflow_document_id: str | None = "ragflow-r4-doc",
    ragflow_dataset_id: str | None = "ragflow-r4-dataset",
) -> tuple[UUID, UUID]:
    from app.core.database import AsyncSessionFactory
    from app.modules.ragflow.tasks import create_ragflow_delete_sync_task

    uploader_id = await _create_user(
        email=f"r4-delete-exec-{os.urandom(4).hex()}@company.com",
        password="password123",
    )
    file_id = await _create_file(
        uploader_id=uploader_id,
        status_value=status_value,
        ragflow_document_id=ragflow_document_id,
        ragflow_dataset_id=ragflow_dataset_id,
    )
    async with AsyncSessionFactory() as session:
        task_id = await create_ragflow_delete_sync_task(session=session, file_id=file_id)
        await session.commit()
    return file_id, task_id


def _patch_ragflow_client(
    monkeypatch: pytest.MonkeyPatch,
    client: FakeDeleteRagflowClient,
) -> None:
    from app.modules.ragflow import tasks

    async def _fake_build_ragflow_client() -> object:
        return client

    monkeypatch.setattr(
        tasks, "build_ragflow_client_from_runtime_config", _fake_build_ragflow_client
    )


async def test_ragflow_delete_worker_deletes_remote_document(
    lifecycle_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File
    from app.modules.ragflow import tasks
    from app.modules.ragflow.models import SyncTask, SyncTaskLog

    file_id, task_id = await _setup_delete_task()
    client = FakeDeleteRagflowClient()
    _patch_ragflow_client(monkeypatch, client)

    await tasks.run_ragflow_delete_task_async(str(task_id))

    async with AsyncSessionFactory() as session:
        task = await session.get(SyncTask, task_id)
        file = await session.get(File, file_id)
        log_result = await session.execute(
            select(SyncTaskLog).where(SyncTaskLog.task_id == task_id).order_by(SyncTaskLog.id)
        )
        logs = list(log_result.scalars())
        assert task is not None
        assert file is not None

    assert client.deletes == [("ragflow-r4-dataset", "ragflow-r4-doc")]
    assert task.status == "succeeded"
    assert file.status == "deleted"
    assert file.ragflow_document_id is None
    assert [log.message for log in logs] == [
        "ragflow delete task queued",
        "ragflow delete task started",
        "ragflow document delete started",
        "ragflow document deleted",
        "ragflow delete task completed",
    ]


@pytest.mark.parametrize(
    ("task_type", "task_status"),
    [
        ("ragflow_upload", "queued"),
        ("ragflow_upload", "running"),
        ("ragflow_status_check", "queued"),
        ("ragflow_status_check", "running"),
    ],
)
async def test_ragflow_delete_worker_does_not_claim_non_delete_task_id(
    lifecycle_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    task_type: str,
    task_status: str,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ragflow import tasks
    from app.modules.ragflow.models import SyncTask

    uploader_id = await _create_user(
        email=f"r4-delete-{task_type}-{task_status}@company.com",
        password="password123",
    )
    file_id = await _create_file(uploader_id=uploader_id)
    async with AsyncSessionFactory() as session:
        task = SyncTask(
            file_id=file_id,
            task_type=task_type,
            status=task_status,
            retry_count=0,
            max_retry_count=3,
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        task_id = task.id
    client = FakeDeleteRagflowClient()
    _patch_ragflow_client(monkeypatch, client)

    await tasks.run_ragflow_delete_task_async(str(task_id))

    async with AsyncSessionFactory() as session:
        task = await session.get(SyncTask, task_id)
        assert task is not None

    assert task.status == task_status
    assert task.finished_at is None
    assert client.deletes == []


async def test_ragflow_delete_worker_treats_remote_404_as_success(
    lifecycle_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.adapters.ragflow.base import RagflowDocumentNotFoundError
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File
    from app.modules.ragflow import tasks
    from app.modules.ragflow.models import SyncTask

    file_id, task_id = await _setup_delete_task()
    client = FakeDeleteRagflowClient(
        error=RagflowDocumentNotFoundError("RAGFlow request failed: HTTP 404")
    )
    _patch_ragflow_client(monkeypatch, client)

    await tasks.run_ragflow_delete_task_async(str(task_id))

    async with AsyncSessionFactory() as session:
        task = await session.get(SyncTask, task_id)
        file = await session.get(File, file_id)
        assert task is not None
        assert file is not None

    assert client.deletes == [("ragflow-r4-dataset", "ragflow-r4-doc")]
    assert task.status == "succeeded"
    assert task.error_message is None
    assert file.ragflow_document_id is None


async def test_ragflow_delete_worker_succeeds_without_remote_call_when_pointer_cleared(
    lifecycle_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ragflow import tasks
    from app.modules.ragflow.models import SyncTask

    _, task_id = await _setup_delete_task(ragflow_document_id=None)
    client = FakeDeleteRagflowClient()
    _patch_ragflow_client(monkeypatch, client)

    await tasks.run_ragflow_delete_task_async(str(task_id))

    async with AsyncSessionFactory() as session:
        task = await session.get(SyncTask, task_id)
        assert task is not None

    assert client.deletes == []
    assert task.status == "succeeded"


async def test_ragflow_delete_worker_marks_file_cleanup_failed_on_error(
    lifecycle_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.adapters.ragflow.base import RagflowClientError
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File
    from app.modules.ragflow import tasks
    from app.modules.ragflow.models import SyncTask

    file_id, task_id = await _setup_delete_task()
    client = FakeDeleteRagflowClient(error=RagflowClientError("RAGFlow request failed: HTTP 500"))
    _patch_ragflow_client(monkeypatch, client)

    with pytest.raises(RuntimeError, match="RagflowClientError"):
        await tasks.run_ragflow_delete_task_async(str(task_id))

    async with AsyncSessionFactory() as session:
        task = await session.get(SyncTask, task_id)
        file = await session.get(File, file_id)
        assert task is not None
        assert file is not None

    assert task.status == "failed"
    assert task.error_message == "RagflowClientError"
    assert file.status == "ragflow_cleanup_failed"
    assert file.ragflow_document_id == "ragflow-r4-doc"
    assert file.ragflow_error_message == "RagflowClientError"


async def test_ragflow_delete_worker_resets_cleanup_failed_after_success(
    lifecycle_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File
    from app.modules.ragflow import tasks
    from app.modules.ragflow.models import SyncTask

    file_id, task_id = await _setup_delete_task(status_value="ragflow_cleanup_failed")
    client = FakeDeleteRagflowClient()
    _patch_ragflow_client(monkeypatch, client)

    await tasks.run_ragflow_delete_task_async(str(task_id))

    async with AsyncSessionFactory() as session:
        task = await session.get(SyncTask, task_id)
        file = await session.get(File, file_id)
        assert task is not None
        assert file is not None

    assert task.status == "succeeded"
    assert file.status == "deleted"
    assert file.ragflow_document_id is None
    assert file.ragflow_error_message is None


async def test_failed_delete_task_can_be_retried_from_task_api(
    lifecycle_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.adapters.ragflow.base import RagflowClientError
    from app.core.database import AsyncSessionFactory
    from app.modules.ragflow import tasks

    token = await _create_admin_token(lifecycle_client)
    file_id, task_id = await _setup_delete_task()
    client = FakeDeleteRagflowClient(error=RagflowClientError("RAGFlow request failed: HTTP 500"))
    _patch_ragflow_client(monkeypatch, client)
    with pytest.raises(RuntimeError, match="RagflowClientError"):
        await tasks.run_ragflow_delete_task_async(str(task_id))

    response = await lifecycle_client.post(
        f"/api/tasks/{task_id}/retry",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["task_type"] == "ragflow_delete"
    assert data["status"] == "queued"
    assert data["retry_count"] == 1

    async with AsyncSessionFactory() as session:
        event_result = await session.execute(
            select(EventOutbox).where(
                EventOutbox.event_type == "ragflow.sync_task.queued",
                EventOutbox.aggregate_id == str(task_id),
            )
        )
        events = list(event_result.scalars())
    assert len(events) == 2
    assert all(event.payload["task_type"] == "ragflow_delete" for event in events)
    assert file_id is not None


# ---------------------------------------------------------------------------
# 手动同步 API: POST /api/admin/files/{id}/sync
# ---------------------------------------------------------------------------


async def test_admin_manual_sync_approved_file_creates_task_and_audit(
    lifecycle_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.audit.models import AuditLog
    from app.modules.document.models import File

    token = await _create_admin_token(lifecycle_client)
    uploader_id = await _create_user(email="r4-manual-sync@company.com", password="password123")
    file_id = await _create_file(
        uploader_id=uploader_id,
        status_value="approved",
        review_status="approved",
        ragflow_document_id=None,
    )

    response = await lifecycle_client.post(
        f"/api/admin/files/{file_id}/sync",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["file_id"] == str(file_id)
    assert data["task_type"] == "ragflow_upload"
    assert data["status"] == "queued"

    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        assert file is not None
        audit_result = await session.execute(
            select(AuditLog).where(AuditLog.action == "file.manual_sync")
        )
        audit_log = audit_result.scalar_one()

    assert file.status == "queued"
    assert audit_log.target_type == "file"
    assert audit_log.target_id == file_id


async def test_admin_manual_sync_failed_file_creates_task(
    lifecycle_client: AsyncClient,
) -> None:
    token = await _create_admin_token(lifecycle_client)
    uploader_id = await _create_user(
        email="r4-manual-sync-failed@company.com",
        password="password123",
    )
    file_id = await _create_file(
        uploader_id=uploader_id,
        status_value="failed",
        review_status="approved",
    )

    response = await lifecycle_client.post(
        f"/api/admin/files/{file_id}/sync",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "queued"


async def test_manual_sync_requires_allowlist_for_runtime_api_key(
    lifecycle_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    set_secret_system_config: Callable[[str, str], Awaitable[None]],
) -> None:
    from app.core.config import get_settings
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File
    from app.modules.ragflow.models import SyncTask

    monkeypatch.delenv("RAGFLOW_ALLOWED_DATASET_IDS", raising=False)
    get_settings.cache_clear()
    await set_secret_system_config("ragflow.api_key", "sk-runtime-manual-abcd")
    token = await _create_admin_token(lifecycle_client)
    uploader_id = await _create_user(
        email="r4-manual-sync-runtime-key@company.com",
        password="password123",
    )
    file_id = await _create_file(
        uploader_id=uploader_id,
        status_value="approved",
        review_status="approved",
        ragflow_document_id=None,
    )

    response = await lifecycle_client.post(
        f"/api/admin/files/{file_id}/sync",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 400
    assert response.json()["message"] == "ragflow dataset id is not allowed"
    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        task_result = await session.execute(select(SyncTask).where(SyncTask.file_id == file_id))
        tasks = list(task_result.scalars())
        assert file is not None

    assert file.status == "approved"
    assert tasks == []


async def test_manual_sync_allows_runtime_api_key_when_dataset_is_allowed(
    lifecycle_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    set_secret_system_config: Callable[[str, str], Awaitable[None]],
) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("RAGFLOW_ALLOWED_DATASET_IDS", "ragflow-r4-dataset")
    get_settings.cache_clear()
    await set_secret_system_config("ragflow.api_key", "sk-runtime-manual-allowed")
    token = await _create_admin_token(lifecycle_client)
    uploader_id = await _create_user(
        email="r4-manual-sync-runtime-allowed@company.com",
        password="password123",
    )
    file_id = await _create_file(
        uploader_id=uploader_id,
        status_value="approved",
        review_status="approved",
        ragflow_document_id=None,
    )

    response = await lifecycle_client.post(
        f"/api/admin/files/{file_id}/sync",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["task_type"] == "ragflow_upload"
    assert response.json()["data"]["status"] == "queued"


async def test_manual_sync_rejects_file_not_in_syncable_state(
    lifecycle_client: AsyncClient,
) -> None:
    token = await _create_admin_token(lifecycle_client)
    uploader_id = await _create_user(
        email="r4-manual-sync-pending@company.com",
        password="password123",
    )
    file_id = await _create_file(
        uploader_id=uploader_id,
        status_value="pending_review",
        review_status="in_review",
        ragflow_document_id=None,
    )

    response = await lifecycle_client.post(
        f"/api/admin/files/{file_id}/sync",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 409
    assert response.json()["error_code"] == "VALIDATION_ERROR"


async def test_manual_sync_rejects_duplicate_active_task(
    lifecycle_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ragflow.models import SyncTask

    token = await _create_admin_token(lifecycle_client)
    uploader_id = await _create_user(
        email="r4-manual-sync-dup@company.com",
        password="password123",
    )
    file_id = await _create_file(
        uploader_id=uploader_id,
        status_value="failed",
        review_status="approved",
    )
    async with AsyncSessionFactory() as session:
        session.add(
            SyncTask(
                file_id=file_id,
                task_type="ragflow_upload",
                status="queued",
                retry_count=0,
                max_retry_count=3,
            )
        )
        await session.commit()

    response = await lifecycle_client.post(
        f"/api/admin/files/{file_id}/sync",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 409


async def test_manual_sync_rejects_when_sync_lock_is_busy(
    lifecycle_client: AsyncClient,
) -> None:
    token = await _create_admin_token(lifecycle_client)
    uploader_id = await _create_user(
        email="r4-manual-sync-lock@company.com",
        password="password123",
    )
    file_id = await _create_file(
        uploader_id=uploader_id,
        status_value="failed",
        review_status="approved",
    )
    redis_client = from_url(  # type: ignore[no-untyped-call]
        os.environ["CACHE_REDIS_URL"],
        encoding="utf-8",
        decode_responses=True,
    )
    await redis_client.set(f"lock:sync:{file_id}", "busy", ex=30)
    try:
        response = await lifecycle_client.post(
            f"/api/admin/files/{file_id}/sync",
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        await redis_client.delete(f"lock:sync:{file_id}")
        await redis_client.aclose()

    assert response.status_code == 409


async def test_manual_sync_blocked_for_critical_sensitive_file(
    lifecycle_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ai.models import DocumentAnalysis

    token = await _create_admin_token(lifecycle_client)
    uploader_id = await _create_user(
        email="r4-manual-sync-critical@company.com",
        password="password123",
    )
    file_id = await _create_file(
        uploader_id=uploader_id,
        status_value="approved",
        review_status="approved",
        ragflow_document_id=None,
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
        await session.commit()

    response = await lifecycle_client.post(
        f"/api/admin/files/{file_id}/sync",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 409


async def test_employee_cannot_manual_sync(lifecycle_client: AsyncClient) -> None:
    await _create_user(
        email="r4-manual-sync-employee@company.com",
        password="password123",
        role="employee",
    )
    token = await _login(
        lifecycle_client,
        email="r4-manual-sync-employee@company.com",
        password="password123",
    )
    uploader_id = await _create_user(
        email="r4-manual-sync-owner@company.com",
        password="password123",
    )
    file_id = await _create_file(
        uploader_id=uploader_id,
        status_value="failed",
        review_status="approved",
    )

    response = await lifecycle_client.post(
        f"/api/admin/files/{file_id}/sync",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403


async def test_manual_sync_returns_404_for_unknown_file(
    lifecycle_client: AsyncClient,
) -> None:
    token = await _create_admin_token(lifecycle_client)

    response = await lifecycle_client.post(
        "/api/admin/files/00000000-0000-0000-0000-000000000000/sync",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/tasks?file_id= 筛选
# ---------------------------------------------------------------------------


async def test_list_tasks_filters_by_file_id(lifecycle_client: AsyncClient) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ragflow.models import SyncTask

    token = await _create_admin_token(lifecycle_client)
    uploader_id = await _create_user(email="r4-task-filter@company.com", password="password123")
    first_file_id = await _create_file(uploader_id=uploader_id, hash_value="d" * 64)
    second_file_id = await _create_file(uploader_id=uploader_id, hash_value="e" * 64)
    async with AsyncSessionFactory() as session:
        session.add_all(
            [
                SyncTask(
                    file_id=first_file_id,
                    task_type="ragflow_upload",
                    status="succeeded",
                    retry_count=0,
                    max_retry_count=3,
                ),
                SyncTask(
                    file_id=second_file_id,
                    task_type="ragflow_delete",
                    status="queued",
                    retry_count=0,
                    max_retry_count=3,
                ),
            ]
        )
        await session.commit()

    filtered_response = await lifecycle_client.get(
        f"/api/tasks?file_id={second_file_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    full_response = await lifecycle_client.get(
        "/api/tasks",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert filtered_response.status_code == 200
    filtered_data = filtered_response.json()["data"]
    assert filtered_data["total"] == 1
    assert filtered_data["items"][0]["file_id"] == str(second_file_id)
    assert filtered_data["items"][0]["task_type"] == "ragflow_delete"
    assert full_response.json()["data"]["total"] == 2
