from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from importlib import import_module
from typing import Any, cast
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from redis.asyncio import from_url
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

TEXT_BYTES = b"handbook onboarding policy and employee benefits"


@dataclass
class StoredObject:
    bucket: str
    object_key: str
    data: bytes
    content_type: str


@dataclass
class MemoryDocumentStorage:
    objects: dict[tuple[str, str], StoredObject] = field(default_factory=dict)
    deleted_objects: list[tuple[str, str]] = field(default_factory=list)
    reads: list[tuple[str, str]] = field(default_factory=list)

    async def put_object(
        self,
        *,
        bucket: str,
        object_key: str,
        data: bytes,
        content_type: str,
    ) -> None:
        self.objects[(bucket, object_key)] = StoredObject(
            bucket=bucket,
            object_key=object_key,
            data=data,
            content_type=content_type,
        )

    async def delete_object(self, *, bucket: str, object_key: str) -> None:
        self.deleted_objects.append((bucket, object_key))
        self.objects.pop((bucket, object_key), None)

    async def get_object(self, *, bucket: str, object_key: str) -> bytes:
        self.reads.append((bucket, object_key))
        return self.objects[(bucket, object_key)].data


class FakeCelerySender:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    def send_task(self, name: str, args: list[str], queue: str) -> object:
        self.sent.append({"name": name, "args": args, "queue": queue})
        return object()


class DispatchingPublisher:
    def __init__(self, sender: FakeCelerySender) -> None:
        self.sender = sender
        self.published: list[str] = []

    def publish(self, event: Any) -> None:
        from app.workers.outbox_dispatcher import dispatch_celery_task_for_event

        self.published.append(str(event.event_type))
        dispatch_celery_task_for_event(event, sender=self.sender)


class FakeRagflowClient:
    def __init__(self) -> None:
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
        return RagflowUploadResult(document_id="ragflow-e2e-document", raw={})

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
        return RagflowDocumentStatus(
            document_id=document_id,
            run="DONE",
            progress=1.0,
            raw={"id": document_id, "run": "DONE"},
        )

    async def delete_document(self, *, dataset_id: str, document_id: str) -> None:
        self.metadata_updates.append(
            {
                "dataset_id": dataset_id,
                "document_id": document_id,
                "deleted": True,
            }
        )


async def _reset_database() -> None:
    import_module("app.db.models")

    from app.core.database import engine
    from app.db.base import Base

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()

    redis_client = from_url(
        os.environ["CACHE_REDIS_URL"],
        encoding="utf-8",
        decode_responses=True,
    )
    try:
        await redis_client.flushdb()
    finally:
        await redis_client.aclose()


@pytest.fixture(autouse=True)
async def clean_database(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[None, None]:
    from app.core.config import get_settings

    monkeypatch.setenv("AI_ANALYSIS_ENABLED", "true")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("RAGFLOW_ALLOWED_DATASET_IDS", "ragflow-e2e")
    monkeypatch.setenv("RAGFLOW_MAX_RETRY_COUNT", "3")
    monkeypatch.setenv("MINIO_BUCKET", "test-knowledge-files")
    get_settings.cache_clear()
    await _reset_database()
    yield

    from app.core.database import engine
    from app.db.base import Base

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()
    get_settings.cache_clear()


@pytest.fixture
async def full_pipeline_client(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[tuple[AsyncClient, MemoryDocumentStorage, FakeRagflowClient], None]:
    from app.core.config import Settings
    from app.core.database import AsyncSessionFactory, get_session
    from app.core.deps import get_app_settings
    from app.main import app
    from app.modules.ai import tasks as ai_tasks
    from app.modules.document.api import get_document_storage
    from app.modules.ragflow import tasks as ragflow_tasks

    settings = Settings(
        allowed_email_domains="company.com",
        jwt_secret="test-jwt-secret-with-more-than-32-bytes",
        cache_redis_url=os.environ["CACHE_REDIS_URL"],
        require_email_verification=False,
        minio_bucket="test-knowledge-files",
        upload_max_file_size_bytes=1024,
        upload_rate_limit_per_minute=20,
        upload_allowed_extensions="txt,pdf",
        upload_allowed_mime_types="text/plain,application/pdf",
        ai_analysis_enabled=True,
        llm_provider="mock",
        ragflow_allowed_dataset_ids="ragflow-e2e",
    )
    storage = MemoryDocumentStorage()
    ragflow_client = FakeRagflowClient()

    async def override_session() -> AsyncGenerator[AsyncSession, None]:
        async with AsyncSessionFactory() as session:
            yield session

    app.dependency_overrides[get_app_settings] = lambda: settings
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_document_storage] = lambda: storage
    monkeypatch.setattr(ai_tasks, "build_ai_storage", lambda _settings: storage)
    monkeypatch.setattr(ragflow_tasks, "build_document_storage", lambda _settings: storage)
    monkeypatch.setattr(ragflow_tasks, "build_ragflow_client", lambda _settings: ragflow_client)

    transport = ASGITransport(app=app)  # type: ignore[arg-type]
    async with AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client, storage, ragflow_client
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


async def _dispatch_pending_events(sender: FakeCelerySender) -> list[dict[str, object]]:
    from app.workers.outbox_dispatcher import dispatch_once

    start = len(sender.sent)
    await dispatch_once(publisher=DispatchingPublisher(sender))
    return sender.sent[start:]


async def _run_sent_tasks(sent_tasks: list[dict[str, object]]) -> None:
    from app.modules.ai.tasks import run_ai_analyze_file_task_async
    from app.modules.ragflow.tasks import (
        run_create_ragflow_upload_task_async,
        run_ragflow_upload_task_async,
    )

    for sent_task in sent_tasks:
        args = sent_task["args"]
        assert isinstance(args, list)
        assert len(args) == 1
        task_arg = str(args[0])
        if sent_task["name"] == "ai.analyze_file":
            await run_ai_analyze_file_task_async(task_arg)
        elif sent_task["name"] == "ragflow.create_upload_task":
            await run_create_ragflow_upload_task_async(task_arg)
        elif sent_task["name"] == "ragflow.upload":
            await run_ragflow_upload_task_async(task_arg)
        else:
            raise AssertionError(f"unexpected task {sent_task['name']}")


async def test_full_pipeline_upload_analyze_approve_syncs_to_ragflow(
    full_pipeline_client: tuple[AsyncClient, MemoryDocumentStorage, FakeRagflowClient],
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.ai.models import DocumentAnalysis
    from app.modules.audit.models import AuditLog
    from app.modules.document.models import File
    from app.modules.ragflow.models import SyncTask

    client, storage, ragflow_client = full_pipeline_client
    sender = FakeCelerySender()
    uploader_id = await _create_user(email="e2e-uploader@company.com", password="password123")
    await _create_user(
        email="e2e-admin@company.com",
        password="password123",
        role="system_admin",
    )
    uploader_token = await _login(client, email="e2e-uploader@company.com", password="password123")
    admin_token = await _login(client, email="e2e-admin@company.com", password="password123")

    category = (
        await client.post(
            "/api/categories",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "name": "员工手册",
                "code": "handbook",
                "default_visibility": "company",
                "keywords": ["handbook", "benefits"],
                "allow_ai_recommend": True,
                "ai_analysis_enabled": True,
            },
        )
    ).json()["data"]
    mapping = (
        await client.post(
            "/api/datasets",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "name": "E2E Dataset",
                "category_id": category["id"],
                "ragflow_dataset_id": "ragflow-e2e",
                "ragflow_dataset_name": "RAGFlow E2E",
                "enabled": True,
            },
        )
    ).json()["data"]

    upload_response = await client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {uploader_token}"},
        files={"file": ("handbook.txt", TEXT_BYTES, "text/plain")},
        data={"description": "E2E handbook", "visibility": "department"},
    )
    assert upload_response.status_code == 201
    uploaded_file = upload_response.json()["data"]
    file_id = UUID(uploaded_file["id"])
    assert uploaded_file["status"] == "uploaded"
    assert len(storage.objects) == 1

    upload_tasks = await _dispatch_pending_events(sender)
    assert upload_tasks == [
        {"name": "ai.analyze_file", "args": [str(file_id)], "queue": "ai_queue"}
    ]
    await _run_sent_tasks(upload_tasks)

    async with AsyncSessionFactory() as session:
        analyzed_file = await session.get(File, file_id)
        assert analyzed_file is not None
        assert analyzed_file.status == "analyzed"
        assert analyzed_file.category_id == UUID(category["id"])
        assert "handbook" in analyzed_file.tags

        analysis_result = await session.execute(
            select(DocumentAnalysis).where(DocumentAnalysis.file_id == file_id)
        )
        analysis = analysis_result.scalar_one()
        assert analysis.status == "succeeded"
        assert analysis.summary == TEXT_BYTES.decode("utf-8")

    submit_response = await client.post(
        f"/api/files/{file_id}/submit-review",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert submit_response.status_code == 200
    assert submit_response.json()["data"]["status"] == "pending_review"

    approve_response = await client.post(
        f"/api/files/{file_id}/approve",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "category_id": category["id"],
            "dataset_mapping_id": mapping["id"],
            "reason": "E2E 通过",
        },
    )
    assert approve_response.status_code == 200
    approved_file = approve_response.json()["data"]
    assert approved_file["status"] == "queued"
    assert approved_file["review_status"] == "approved"
    assert approved_file["ragflow_dataset_id"] == "ragflow-e2e"

    review_tasks = await _dispatch_pending_events(sender)
    assert review_tasks == [
        {
            "name": "ragflow.create_upload_task",
            "args": [str(file_id)],
            "queue": "ragflow_queue",
        }
    ]
    await _run_sent_tasks(review_tasks)

    ragflow_tasks = await _dispatch_pending_events(sender)
    assert len(ragflow_tasks) == 1
    assert ragflow_tasks[0]["name"] == "ragflow.upload"
    await _run_sent_tasks(ragflow_tasks)

    async with AsyncSessionFactory() as session:
        final_file = await session.get(File, file_id)
        assert final_file is not None
        sync_task_result = await session.execute(
            select(SyncTask).where(SyncTask.file_id == file_id)
        )
        sync_task = sync_task_result.scalar_one()
        outbox_result = await session.execute(
            select(EventOutbox).where(EventOutbox.aggregate_id == str(file_id))
        )
        file_events = list(outbox_result.scalars())
        audit_result = await session.execute(
            select(AuditLog).where(AuditLog.target_id == file_id).order_by(AuditLog.created_at)
        )
        audit_logs = list(audit_result.scalars())

    assert final_file.status == "parsed"
    assert final_file.review_status == "approved"
    assert final_file.ragflow_document_id == "ragflow-e2e-document"
    assert final_file.ragflow_parse_status == "DONE"
    assert final_file.last_sync_at is not None
    assert sync_task.status == "succeeded"
    assert sync_task.finished_at is not None
    assert [event.event_type for event in file_events] == [
        "document.file.uploaded",
        "review.file.submitted",
        "review.file.approved",
    ]
    assert [log.action for log in audit_logs] == [
        "file.upload",
        "file.submit_review",
        "file.approve",
    ]
    assert storage.reads == [
        (final_file.bucket, final_file.object_key),
        (final_file.bucket, final_file.object_key),
    ]
    assert ragflow_client.uploads == [
        {
            "dataset_id": "ragflow-e2e",
            "filename": final_file.stored_name,
            "content": TEXT_BYTES,
            "content_type": "text/plain",
        }
    ]
    assert ragflow_client.parse_requests == [("ragflow-e2e", "ragflow-e2e-document")]
    assert ragflow_client.status_requests == [("ragflow-e2e", "ragflow-e2e-document")]
    metadata = cast(dict[str, object], ragflow_client.metadata_updates[0]["metadata"])
    assert metadata["file_id"] == str(file_id)
    assert metadata["uploader"] == str(uploader_id)
    assert metadata["category"] == category["id"]
    assert "handbook" in cast(list[str], metadata["tags"])
