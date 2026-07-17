from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from importlib import import_module
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient, Response
from redis.asyncio import from_url
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import Message, Scope

pytestmark = pytest.mark.asyncio

PDF_BYTES = (
    b"%PDF-1.4\n"
    b"1 0 obj\n<< /Type /Catalog >>\nendobj\n"
    b"xref\n0 1\n0000000000 65535 f \n"
    b"trailer\n<< /Root 1 0 R >>\n"
    b"startxref\n9\n%%EOF\n"
)
PNG_BYTES = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
OLE_BYTES = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1fake compound file"
UPLOAD_DRAFT_FORM = {"submit_after_upload": "false"}


@dataclass
class StoredObject:
    bucket: str
    object_key: str
    data: bytes
    content_type: str


class FakeObjectStream:
    def __init__(self, data: bytes, *, chunk_size: int = 8) -> None:
        self._data = data
        self._offset = 0
        self._chunk_size = chunk_size
        self.closed = False

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self

    async def __anext__(self) -> bytes:
        if self._offset >= len(self._data):
            await self.aclose()
            raise StopAsyncIteration
        end = min(self._offset + self._chunk_size, len(self._data))
        chunk = self._data[self._offset : end]
        self._offset = end
        return chunk

    async def aclose(self) -> None:
        self.closed = True


class BlockingObjectStream:
    def __init__(self) -> None:
        self.next_calls = 0
        self.close_calls = 0
        self.release_conn_calls = 0
        self.next_started = asyncio.Event()
        self._never = asyncio.Event()

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self

    async def __anext__(self) -> bytes:
        self.next_calls += 1
        self.next_started.set()
        await self._never.wait()
        raise StopAsyncIteration

    async def aclose(self) -> None:
        self.close_calls += 1
        self.release_conn_calls += 1


class FailingCloseObjectStream(BlockingObjectStream):
    async def aclose(self) -> None:
        self.close_calls += 1
        self.release_conn_calls += 1
        raise OSError("private cleanup detail must not escape")


@dataclass
class FakeDocumentStorage:
    objects: list[StoredObject] = field(default_factory=list)
    deleted_objects: list[tuple[str, str]] = field(default_factory=list)
    get_object_calls: int = 0
    open_calls: list[tuple[str, str, int, int | None]] = field(default_factory=list)
    opened_streams: list[FakeObjectStream] = field(default_factory=list)

    async def put_object(
        self,
        *,
        bucket: str,
        object_key: str,
        data: bytes,
        content_type: str,
    ) -> None:
        self.objects.append(
            StoredObject(
                bucket=bucket,
                object_key=object_key,
                data=data,
                content_type=content_type,
            )
        )

    async def delete_object(self, *, bucket: str, object_key: str) -> None:
        self.deleted_objects.append((bucket, object_key))
        self.objects = [
            stored
            for stored in self.objects
            if stored.bucket != bucket or stored.object_key != object_key
        ]

    async def get_object(self, *, bucket: str, object_key: str) -> bytes:
        self.get_object_calls += 1
        for stored in self.objects:
            if stored.bucket == bucket and stored.object_key == object_key:
                return stored.data
        raise FileNotFoundError(object_key)

    async def open_object(
        self,
        *,
        bucket: str,
        object_key: str,
        offset: int = 0,
        length: int | None = None,
    ) -> FakeObjectStream:
        self.open_calls.append((bucket, object_key, offset, length))
        for stored in self.objects:
            if stored.bucket == bucket and stored.object_key == object_key:
                end = None if length is None else offset + length
                stream = FakeObjectStream(stored.data[offset:end])
                self.opened_streams.append(stream)
                return stream
        raise FileNotFoundError(object_key)


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
async def document_client() -> AsyncGenerator[tuple[AsyncClient, FakeDocumentStorage], None]:
    from app.core.config import Settings
    from app.core.database import AsyncSessionFactory, get_session
    from app.core.deps import get_app_settings
    from app.main import app

    settings = Settings(
        allowed_email_domains="company.com",
        jwt_secret="test-jwt-secret-with-more-than-32-bytes",
        cache_redis_url=os.environ["CACHE_REDIS_URL"],
        require_email_verification=False,
        minio_bucket="test-knowledge-files",
        upload_max_file_size_bytes=1024,
        upload_rate_limit_per_minute=20,
        upload_allowed_extensions="pdf,txt",
        upload_allowed_mime_types="application/pdf,text/plain,image/png",
    )
    storage = FakeDocumentStorage()

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
        app.dependency_overrides[get_document_storage] = lambda: storage

    transport = ASGITransport(app=app)  # type: ignore[arg-type]
    async with AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client, storage
    app.dependency_overrides.clear()


async def _create_user(
    *,
    email: str,
    password: str,
    role: str = "employee",
    assigned_department: bool = True,
    department_code: str = "document-tests",
    department_name: str = "文档测试部",
) -> UUID:
    from app.core.database import AsyncSessionFactory
    from app.core.security import hash_password
    from app.modules.department.models import UNASSIGNED_DEPARTMENT_ID, Department
    from app.modules.user.models import User

    normalized_email = email.lower()
    async with AsyncSessionFactory() as session:
        department = (
            await session.execute(select(Department).where(Department.code == department_code))
        ).scalar_one_or_none()
        if department is None:
            department = Department(name=department_name, code=department_code, status="active")
            session.add(department)
            await session.flush()
        user = User(
            name=email.split("@", 1)[0],
            email=normalized_email,
            email_domain=normalized_email.rsplit("@", 1)[1],
            password_hash=hash_password(password),
            department_id=department.id if assigned_department else UNASSIGNED_DEPARTMENT_ID,
            department=department.name if assigned_department else None,
            role=role,
            status="active",
            email_verified=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user.id


async def _get_user_department_id(user_id: UUID) -> UUID:
    from app.core.database import AsyncSessionFactory
    from app.modules.user.models import User

    async with AsyncSessionFactory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        return user.department_id


async def _grant_managed_department(*, admin_id: UUID, department_id: UUID) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.department.models import UserManagedDepartment

    async with AsyncSessionFactory() as session:
        session.add(UserManagedDepartment(user_id=admin_id, department_id=department_id))
        await session.commit()


async def _login(client: AsyncClient, *, email: str, password: str) -> str:
    response = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    return str(response.json()["data"]["access_token"])


async def test_upload_stores_file_metadata_and_minio_object(
    document_client: tuple[AsyncClient, FakeDocumentStorage],
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.audit.models import AuditLog
    from app.modules.document.models import File

    client, storage = document_client
    user_id = await _create_user(email="uploader@company.com", password="password123")
    token = await _login(client, email="uploader@company.com", password="password123")

    response = await client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("Handbook.pdf", PDF_BYTES, "application/pdf")},
        data={
            "description": "员工手册",
            "visibility": "private",
            **UPLOAD_DRAFT_FORM,
        },
    )

    assert response.status_code == 201
    data = response.json()["data"]
    assert data["original_name"] == "Handbook.pdf"
    assert data["extension"] == "pdf"
    assert data["mime_type"] == "application/pdf"
    assert data["size"] == len(PDF_BYTES)
    assert data["uploader_id"] == str(user_id)
    assert data["status"] == "uploaded"
    assert data["review_status"] == "pending"
    assert data["expires_at"] is None
    assert data["expiry_status"] == "never"
    assert data["duplicate"] is False
    assert data["duplicate_file_id"] is None
    assert "bucket" not in data
    assert "object_key" not in data
    assert "hash" not in data
    assert "ai_config_snapshot" not in data
    assert "ragflow_error_message" not in data

    assert len(storage.objects) == 1
    assert storage.objects[0].bucket == "test-knowledge-files"
    assert storage.objects[0].object_key.startswith(f"uploads/{user_id}/")
    assert storage.objects[0].data == PDF_BYTES

    async with AsyncSessionFactory() as session:
        result = await session.execute(select(File).where(File.id == UUID(data["id"])))
        saved_file = result.scalar_one()
        audit_result = await session.execute(
            select(AuditLog).where(AuditLog.action == "file.upload")
        )
        audit_log = audit_result.scalar_one()

    assert saved_file.object_key == storage.objects[0].object_key
    assert saved_file.hash
    assert saved_file.description == "员工手册"
    assert saved_file.expires_at is None
    assert saved_file.expiry_status == "never"
    assert audit_log.actor_id == user_id
    assert audit_log.target_id == saved_file.id
    assert audit_log.target_type == "file"
    assert audit_log.metadata_json["original_name"] == "Handbook.pdf"
    assert audit_log.metadata_json["size"] == len(PDF_BYTES)
    assert audit_log.metadata_json["duplicate"] is False


async def test_upload_api_cannot_bypass_runtime_disabled_gate(
    document_client: tuple[AsyncClient, FakeDocumentStorage],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from starlette.datastructures import UploadFile as StarletteUploadFile

    from app.modules.document import api as document_api

    client, storage = document_client
    await _create_user(email="disabled-upload@company.com", password="password123")
    token = await _login(client, email="disabled-upload@company.com", password="password123")

    async def upload_disabled() -> bool:
        return False

    async def unexpected_rate_limit(*_args: object, **_kwargs: object) -> None:
        pytest.fail("disabled upload must not consume rate-limit quota")

    async def unexpected_read(self: StarletteUploadFile, size: int = -1) -> bytes:
        pytest.fail(f"disabled upload must not read file content (size={size})")

    monkeypatch.setattr(
        "app.modules.document.service.resolve_upload_enabled",
        upload_disabled,
    )
    monkeypatch.setattr(document_api, "_enforce_upload_rate_limit", unexpected_rate_limit)
    monkeypatch.setattr(StarletteUploadFile, "read", unexpected_read)

    response = await client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("blocked.pdf", PDF_BYTES, "application/pdf")},
        data=UPLOAD_DRAFT_FORM,
    )

    assert response.status_code == 403
    assert response.json()["error_code"] == "UPLOAD_DISABLED"
    assert storage.objects == []
    owner_options = await client.get(
        "/api/files/owner-options",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert owner_options.status_code == 200, owner_options.text
    assert owner_options.json()["data"]["total"] == 1


async def test_unassigned_employee_cannot_upload(
    document_client: tuple[AsyncClient, FakeDocumentStorage],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from starlette.datastructures import UploadFile as StarletteUploadFile

    from app.modules.document import api as document_api

    client, storage = document_client
    await _create_user(
        email="unassigned-upload@company.com",
        password="password123",
        assigned_department=False,
    )
    token = await _login(client, email="unassigned-upload@company.com", password="password123")

    async def unexpected_rate_limit(*_args: object, **_kwargs: object) -> None:
        pytest.fail("unassigned upload must not consume rate-limit quota")

    async def unexpected_read(self: StarletteUploadFile, size: int = -1) -> bytes:
        pytest.fail(f"unassigned upload must not read file content (size={size})")

    monkeypatch.setattr(document_api, "_enforce_upload_rate_limit", unexpected_rate_limit)
    monkeypatch.setattr(StarletteUploadFile, "read", unexpected_read)

    response = await client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("blocked.pdf", PDF_BYTES, "application/pdf")},
        data=UPLOAD_DRAFT_FORM,
    )

    assert response.status_code == 403
    assert response.json()["error_code"] == "DEPARTMENT_ASSIGNMENT_REQUIRED"
    assert storage.objects == []


@pytest.mark.parametrize("role", ["dept_admin", "system_admin"])
async def test_unassigned_admin_cannot_upload(
    document_client: tuple[AsyncClient, FakeDocumentStorage],
    role: str,
) -> None:
    client, storage = document_client
    email = f"unassigned-{role}@company.com"
    await _create_user(
        email=email,
        password="password123",
        role=role,
        assigned_department=False,
    )
    token = await _login(client, email=email, password="password123")

    response = await client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("blocked.pdf", PDF_BYTES, "application/pdf")},
        data=UPLOAD_DRAFT_FORM,
    )

    assert response.status_code == 403
    assert response.json()["error_code"] == "DEPARTMENT_ASSIGNMENT_REQUIRED"
    assert storage.objects == []


async def test_upload_requires_explicit_submit_after_upload_choice(
    document_client: tuple[AsyncClient, FakeDocumentStorage],
) -> None:
    client, storage = document_client
    await _create_user(email="explicit-upload-choice@company.com", password="password123")
    token = await _login(
        client,
        email="explicit-upload-choice@company.com",
        password="password123",
    )

    response = await client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("choice.pdf", PDF_BYTES, "application/pdf")},
    )

    assert response.status_code == 422
    assert storage.objects == []


async def test_upload_can_submit_after_upload_when_ai_is_skipped(
    document_client: tuple[AsyncClient, FakeDocumentStorage],
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.audit.models import AuditLog
    from app.modules.document.models import File

    client, _storage = document_client
    user_id = await _create_user(email="submit-after-upload@company.com", password="password123")
    token = await _login(client, email="submit-after-upload@company.com", password="password123")

    response = await client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("submit.pdf", PDF_BYTES, "application/pdf")},
        data={
            "visibility": "private",
            "submit_after_upload": "true",
            "ai_analysis_enabled": "false",
        },
    )

    assert response.status_code == 201
    data = response.json()["data"]
    file_id = UUID(data["id"])
    assert data["status"] == "pending_review"
    assert data["review_status"] == "pending"
    assert data["ai_analysis_enabled_at_upload"] is False

    async with AsyncSessionFactory() as session:
        saved_file = await session.get(File, file_id)
        audit_result = await session.execute(
            select(AuditLog)
            .where(AuditLog.target_id == file_id)
            .where(AuditLog.action.in_(["file.upload", "file.submit_review"]))
        )
        audit_logs = list(audit_result.scalars())
        event_result = await session.execute(
            select(EventOutbox)
            .where(EventOutbox.aggregate_id == str(file_id))
            .order_by(EventOutbox.id)
        )
        outbox_events = list(event_result.scalars())

    assert saved_file is not None
    assert saved_file.status == "pending_review"
    assert saved_file.review_status == "pending"
    assert saved_file.ai_analysis_enabled_at_upload is False
    assert {log.action for log in audit_logs} == {"file.upload", "file.submit_review"}
    submit_audit = next(log for log in audit_logs if log.action == "file.submit_review")
    assert submit_audit.actor_id == user_id
    assert submit_audit.metadata_json["previous_status"] == "uploaded"
    assert [event.event_type for event in outbox_events] == [
        "document.file.uploaded",
        "review.file.submitted",
    ]
    assert outbox_events[0].payload["status"] == "uploaded"
    assert outbox_events[0].payload["ai_analysis_enabled_at_upload"] is False
    assert outbox_events[1].payload["status"] == "pending_review"


async def test_upload_save_draft_keeps_uploaded_while_ai_pipeline_runs(
    document_client: tuple[AsyncClient, FakeDocumentStorage],
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.document.models import File

    client, _storage = document_client
    await _create_user(email="draft@company.com", password="password123")
    token = await _login(client, email="draft@company.com", password="password123")

    response = await client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("draft.pdf", PDF_BYTES, "application/pdf")},
        data={
            "visibility": "private",
            "submit_after_upload": "false",
            "ai_analysis_enabled": "true",
        },
    )

    assert response.status_code == 201
    data = response.json()["data"]
    file_id = UUID(data["id"])
    assert data["status"] == "uploaded"
    assert data["ai_analysis_enabled_at_upload"] is True

    async with AsyncSessionFactory() as session:
        saved_file = await session.get(File, file_id)
        event_result = await session.execute(
            select(EventOutbox).where(EventOutbox.aggregate_id == str(file_id))
        )
        outbox_event = event_result.scalar_one()

    assert saved_file is not None
    assert saved_file.status == "uploaded"
    assert saved_file.ai_analysis_enabled_at_upload is True
    assert saved_file.ai_config_snapshot is not None
    assert saved_file.ai_config_snapshot["submit_after_upload"] is False
    assert outbox_event.event_type == "document.file.uploaded"
    assert outbox_event.payload["ai_analysis_enabled_at_upload"] is True
    assert outbox_event.payload["submit_after_upload"] is False


async def test_upload_save_draft_can_explicitly_skip_ai(
    document_client: tuple[AsyncClient, FakeDocumentStorage],
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File

    client, _storage = document_client
    await _create_user(email="draft-no-ai@company.com", password="password123")
    token = await _login(client, email="draft-no-ai@company.com", password="password123")

    response = await client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("draft-no-ai.pdf", PDF_BYTES, "application/pdf")},
        data={
            "submit_after_upload": "false",
            "ai_analysis_enabled": "false",
        },
    )

    assert response.status_code == 201
    file_id = UUID(response.json()["data"]["id"])
    assert response.json()["data"]["status"] == "uploaded"
    assert response.json()["data"]["ai_analysis_enabled_at_upload"] is False
    async with AsyncSessionFactory() as session:
        saved_file = await session.get(File, file_id)
    assert saved_file is not None
    assert saved_file.ai_config_snapshot is not None
    assert saved_file.ai_config_snapshot["submit_after_upload"] is False


async def test_upload_auto_submit_waits_for_enabled_ai_analysis(
    document_client: tuple[AsyncClient, FakeDocumentStorage],
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.document.models import File

    client, _storage = document_client
    await _create_user(email="submit-with-ai@company.com", password="password123")
    token = await _login(client, email="submit-with-ai@company.com", password="password123")

    response = await client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("submit-with-ai.pdf", PDF_BYTES, "application/pdf")},
        data={
            "submit_after_upload": "true",
            "ai_analysis_enabled": "true",
        },
    )

    assert response.status_code == 201
    file_id = UUID(response.json()["data"]["id"])
    assert response.json()["data"]["status"] == "uploaded"
    assert response.json()["data"]["ai_analysis_enabled_at_upload"] is True
    async with AsyncSessionFactory() as session:
        saved_file = await session.get(File, file_id)
        result = await session.execute(
            select(EventOutbox).where(EventOutbox.aggregate_id == str(file_id))
        )
        events = list(result.scalars())
    assert saved_file is not None
    assert saved_file.ai_config_snapshot is not None
    assert saved_file.ai_config_snapshot["submit_after_upload"] is True
    assert [event.event_type for event in events] == ["document.file.uploaded"]
    assert events[0].payload["submit_after_upload"] is True


async def test_duplicate_upload_is_identified_without_reuploading_object(
    document_client: tuple[AsyncClient, FakeDocumentStorage],
) -> None:
    client, storage = document_client
    await _create_user(email="duplicate@company.com", password="password123")
    token = await _login(client, email="duplicate@company.com", password="password123")

    first = await client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("first.pdf", PDF_BYTES, "application/pdf")},
        data=UPLOAD_DRAFT_FORM,
    )
    second = await client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("second.pdf", PDF_BYTES, "application/pdf")},
        data=UPLOAD_DRAFT_FORM,
    )

    assert first.status_code == 201
    assert second.status_code == 201
    first_data = first.json()["data"]
    second_data = second.json()["data"]
    assert first_data["duplicate"] is False
    assert second_data["duplicate"] is True
    assert second_data["duplicate_file_id"] == first_data["id"]
    assert len(storage.objects) == 1


async def test_same_hash_from_another_user_is_not_reported_as_duplicate(
    document_client: tuple[AsyncClient, FakeDocumentStorage],
) -> None:
    client, storage = document_client
    await _create_user(email="first-owner@company.com", password="password123")
    await _create_user(email="second-owner@company.com", password="password123")
    first_token = await _login(client, email="first-owner@company.com", password="password123")
    second_token = await _login(client, email="second-owner@company.com", password="password123")

    first = await client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {first_token}"},
        files={"file": ("first.pdf", PDF_BYTES, "application/pdf")},
        data=UPLOAD_DRAFT_FORM,
    )
    second = await client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {second_token}"},
        files={"file": ("second.pdf", PDF_BYTES, "application/pdf")},
        data=UPLOAD_DRAFT_FORM,
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["data"]["duplicate"] is False
    assert second.json()["data"]["duplicate"] is False
    assert second.json()["data"]["duplicate_file_id"] is None
    assert len(storage.objects) == 2
    assert storage.objects[0].object_key != storage.objects[1].object_key


async def test_upload_deletes_new_object_when_database_commit_fails(
    document_client: tuple[AsyncClient, FakeDocumentStorage],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    client, storage = document_client
    user_id = await _create_user(email="commit-fail@company.com", password="password123")
    token = await _login(client, email="commit-fail@company.com", password="password123")

    async def fail_commit(self: AsyncSession) -> None:
        raise RuntimeError("commit failed")

    monkeypatch.setattr(AsyncSession, "commit", fail_commit)

    with pytest.raises(RuntimeError, match="commit failed"):
        await client.post(
            "/api/files/upload",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("handbook.pdf", PDF_BYTES, "application/pdf")},
            data=UPLOAD_DRAFT_FORM,
        )

    assert storage.objects == []
    assert len(storage.deleted_objects) == 1
    deleted_bucket, deleted_key = storage.deleted_objects[0]
    assert deleted_bucket == "test-knowledge-files"
    assert deleted_key.startswith(f"uploads/{user_id}/")
    assert deleted_key.endswith("-handbook.pdf")


async def test_duplicate_upload_does_not_delete_reused_object_when_commit_fails(
    document_client: tuple[AsyncClient, FakeDocumentStorage],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    client, storage = document_client
    await _create_user(email="duplicate-commit-fail@company.com", password="password123")
    token = await _login(client, email="duplicate-commit-fail@company.com", password="password123")

    first = await client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("first.pdf", PDF_BYTES, "application/pdf")},
        data=UPLOAD_DRAFT_FORM,
    )
    assert first.status_code == 201
    assert len(storage.objects) == 1

    async def fail_commit(self: AsyncSession) -> None:
        raise RuntimeError("commit failed")

    monkeypatch.setattr(AsyncSession, "commit", fail_commit)

    with pytest.raises(RuntimeError, match="commit failed"):
        await client.post(
            "/api/files/upload",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("second.pdf", PDF_BYTES, "application/pdf")},
            data=UPLOAD_DRAFT_FORM,
        )

    assert len(storage.objects) == 1
    assert storage.deleted_objects == []


async def test_upload_rejects_disallowed_extension(
    document_client: tuple[AsyncClient, FakeDocumentStorage],
) -> None:
    client, storage = document_client
    await _create_user(email="extension@company.com", password="password123")
    token = await _login(client, email="extension@company.com", password="password123")

    response = await client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("setup.exe", b"MZ fake executable", "application/octet-stream")},
        data=UPLOAD_DRAFT_FORM,
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "FILE_EXTENSION_NOT_ALLOWED"
    assert storage.objects == []


async def test_upload_rejects_mime_mismatch(
    document_client: tuple[AsyncClient, FakeDocumentStorage],
) -> None:
    client, storage = document_client
    await _create_user(email="mime@company.com", password="password123")
    token = await _login(client, email="mime@company.com", password="password123")

    response = await client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("fake.pdf", PNG_BYTES, "application/pdf")},
        data=UPLOAD_DRAFT_FORM,
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "FILE_MIME_MISMATCH"
    assert storage.objects == []


async def test_upload_rejects_unrecognized_binary_disguised_as_pdf(
    document_client: tuple[AsyncClient, FakeDocumentStorage],
) -> None:
    client, storage = document_client
    await _create_user(email="binary@company.com", password="password123")
    token = await _login(client, email="binary@company.com", password="password123")

    response = await client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("fake.pdf", b"not a pdf", "application/pdf")},
        data=UPLOAD_DRAFT_FORM,
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "FILE_MIME_MISMATCH"
    assert storage.objects == []


async def test_upload_rejects_pdf_with_only_magic_header(
    document_client: tuple[AsyncClient, FakeDocumentStorage],
) -> None:
    client, storage = document_client
    await _create_user(email="fake-pdf@company.com", password="password123")
    token = await _login(client, email="fake-pdf@company.com", password="password123")

    response = await client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("fake.pdf", b"%PDF-1.4\nplain payload", "application/pdf")},
        data=UPLOAD_DRAFT_FORM,
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "FILE_MIME_MISMATCH"
    assert storage.objects == []


async def test_upload_rejects_legacy_ole_extension_even_if_configured(
    document_client: tuple[AsyncClient, FakeDocumentStorage],
    set_system_config: Callable[[str, object], Awaitable[None]],
) -> None:
    from app.core.config import Settings
    from app.core.deps import get_app_settings
    from app.main import app

    client, storage = document_client
    # 扩展名白名单改由 runtime_config (DB 优先) 提供, mime 白名单仍走 settings
    await set_system_config("upload.allowed_extensions", ["pdf", "txt", "doc"])
    existing_settings = app.dependency_overrides[get_app_settings]()
    assert isinstance(existing_settings, Settings)
    app.dependency_overrides[get_app_settings] = lambda: existing_settings.model_copy(
        update={
            "upload_allowed_mime_types": "application/pdf,text/plain,application/msword",
        }
    )
    await _create_user(email="legacy-doc@company.com", password="password123")
    token = await _login(client, email="legacy-doc@company.com", password="password123")

    response = await client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("legacy.doc", OLE_BYTES, "application/msword")},
        data=UPLOAD_DRAFT_FORM,
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "FILE_EXTENSION_NOT_ALLOWED"
    assert storage.objects == []


async def test_upload_rejects_empty_file(
    document_client: tuple[AsyncClient, FakeDocumentStorage],
) -> None:
    client, storage = document_client
    await _create_user(email="empty@company.com", password="password123")
    token = await _login(client, email="empty@company.com", password="password123")

    response = await client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("empty.txt", b"", "text/plain")},
        data=UPLOAD_DRAFT_FORM,
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "FILE_EMPTY"
    assert storage.objects == []


async def test_upload_rejects_file_over_size_limit_before_storage(
    document_client: tuple[AsyncClient, FakeDocumentStorage],
    set_system_config: Callable[[str, object], Awaitable[None]],
) -> None:
    client, storage = document_client
    # 大小上限改由 runtime_config 的 upload.max_file_size_mb (单位 MB) 提供
    await set_system_config("upload.max_file_size_mb", 1)
    await _create_user(email="large@company.com", password="password123")
    token = await _login(client, email="large@company.com", password="password123")

    response = await client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("large.txt", b"a" * (1024 * 1024 + 1), "text/plain")},
        data=UPLOAD_DRAFT_FORM,
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "FILE_TOO_LARGE"
    assert storage.objects == []


async def test_upload_sanitizes_reserved_filename_for_metadata_and_storage(
    document_client: tuple[AsyncClient, FakeDocumentStorage],
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File

    client, storage = document_client
    await _create_user(email="reserved@company.com", password="password123")
    token = await _login(client, email="reserved@company.com", password="password123")

    response = await client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("CON.txt", b"safe text", "text/plain")},
        data=UPLOAD_DRAFT_FORM,
    )

    assert response.status_code == 201
    data = response.json()["data"]
    assert data["original_name"] == "CON_file.txt"

    async with AsyncSessionFactory() as session:
        result = await session.execute(select(File).where(File.id == UUID(data["id"])))
        saved_file = result.scalar_one()

    assert saved_file.original_name == "CON_file.txt"
    assert saved_file.stored_name.endswith("-CON_file.txt")
    assert storage.objects[0].object_key.endswith("-CON_file.txt")


async def test_upload_is_rate_limited_per_user(
    document_client: tuple[AsyncClient, FakeDocumentStorage],
) -> None:
    from app.core.config import Settings
    from app.core.database import AsyncSessionFactory
    from app.core.deps import get_app_settings
    from app.main import app
    from app.modules.audit.models import AuditLog

    client, storage = document_client
    existing_settings = app.dependency_overrides[get_app_settings]()
    assert isinstance(existing_settings, Settings)
    app.dependency_overrides[get_app_settings] = lambda: existing_settings.model_copy(
        update={"upload_rate_limit_per_minute": 1}
    )
    await _create_user(email="limited@company.com", password="password123")
    token = await _login(client, email="limited@company.com", password="password123")

    first = await client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("first.txt", b"first text", "text/plain")},
        data=UPLOAD_DRAFT_FORM,
    )
    second = await client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("second.txt", b"second text", "text/plain")},
        data=UPLOAD_DRAFT_FORM,
    )

    assert first.status_code == 201
    assert second.status_code == 429
    assert second.json()["error_code"] == "RATE_LIMITED"
    assert len(storage.objects) == 1

    async with AsyncSessionFactory() as session:
        result = await session.execute(select(AuditLog).where(AuditLog.action == "file.upload"))
        audit_logs = list(result.scalars())

    assert len(audit_logs) == 1


async def test_employee_lists_and_views_only_own_files(
    document_client: tuple[AsyncClient, FakeDocumentStorage],
) -> None:
    client, _storage = document_client
    owner_id = await _create_user(email="owner@company.com", password="password123")
    other_id = await _create_user(email="other@company.com", password="password123")
    owner_token = await _login(client, email="owner@company.com", password="password123")
    other_token = await _login(client, email="other@company.com", password="password123")

    owner_upload = await client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {owner_token}"},
        files={"file": ("owner.pdf", PDF_BYTES, "application/pdf")},
        data=UPLOAD_DRAFT_FORM,
    )
    other_upload = await client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {other_token}"},
        files={"file": ("other.pdf", PDF_BYTES, "application/pdf")},
        data=UPLOAD_DRAFT_FORM,
    )

    assert owner_upload.status_code == 201
    assert other_upload.status_code == 201
    owner_file_id = owner_upload.json()["data"]["id"]
    other_file_id = other_upload.json()["data"]["id"]

    owner_list = await client.get(
        "/api/files",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    owner_detail = await client.get(
        f"/api/files/{owner_file_id}",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    denied_detail = await client.get(
        f"/api/files/{other_file_id}",
        headers={"Authorization": f"Bearer {owner_token}"},
    )

    assert owner_list.status_code == 200
    listed = owner_list.json()["data"]["items"]
    assert [item["id"] for item in listed] == [owner_file_id]
    assert listed[0]["uploader_id"] == str(owner_id)
    assert owner_detail.status_code == 200
    assert owner_detail.json()["data"]["id"] == owner_file_id
    assert owner_detail.json()["data"]["uploader_id"] == str(owner_id)
    assert denied_detail.status_code == 404
    assert denied_detail.json()["error_code"] == "FILE_NOT_FOUND"
    assert other_id != owner_id


async def test_my_files_uses_server_pagination_search_status_and_sort(
    document_client: tuple[AsyncClient, FakeDocumentStorage],
) -> None:
    client, _storage = document_client
    await _create_user(email="paged-owner@company.com", password="password123")
    token = await _login(client, email="paged-owner@company.com", password="password123")
    for filename in ("policy-beta.pdf", "notes.pdf", "policy-alpha.pdf"):
        response = await client.post(
            "/api/files/upload",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": (filename, PDF_BYTES, "application/pdf")},
            data={"submit_after_upload": "false"},
        )
        assert response.status_code == 201

    response = await client.get(
        "/api/files",
        headers={"Authorization": f"Bearer {token}"},
        params={
            "q": "policy",
            "status": "uploaded",
            "page": 2,
            "page_size": 1,
            "sort": "original_name",
            "order": "asc",
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 2
    assert data["page"] == 2
    assert data["page_size"] == 1
    assert data["total_pages"] == 2
    assert [item["original_name"] for item in data["items"]] == ["policy-beta.pdf"]


async def test_my_files_search_treats_percent_and_underscore_as_literals(
    document_client: tuple[AsyncClient, FakeDocumentStorage],
) -> None:
    client, _storage = document_client
    await _create_user(email="literal-search-owner@company.com", password="password123")
    token = await _login(
        client,
        email="literal-search-owner@company.com",
        password="password123",
    )
    file_ids: list[str] = []
    for index, title in enumerate(("预算 100%_最终版", "预算 100AX最终版")):
        upload = await client.post(
            "/api/files/upload",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": (f"literal-{index}.pdf", PDF_BYTES, "application/pdf")},
            data=UPLOAD_DRAFT_FORM,
        )
        assert upload.status_code == 201
        file_id = upload.json()["data"]["id"]
        file_ids.append(file_id)
        update = await client.patch(
            f"/api/files/{file_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"expected_version": 0, "title": title},
        )
        assert update.status_code == 200

    response = await client.get(
        "/api/files",
        headers={"Authorization": f"Bearer {token}"},
        params={"q": "%_"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 1
    assert [item["id"] for item in data["items"]] == [file_ids[0]]


@pytest.mark.parametrize("invalid_version", [True, 2_147_483_648])
async def test_draft_patch_rejects_non_int32_expected_version(
    document_client: tuple[AsyncClient, FakeDocumentStorage],
    invalid_version: object,
) -> None:
    client, _storage = document_client
    await _create_user(
        email="strict-version@company.com",
        password="password123",
    )
    token = await _login(
        client,
        email="strict-version@company.com",
        password="password123",
    )
    upload = await client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("strict-version.pdf", PDF_BYTES, "application/pdf")},
        data=UPLOAD_DRAFT_FORM,
    )
    assert upload.status_code == 201
    file_id = upload.json()["data"]["id"]

    response = await client.patch(
        f"/api/files/{file_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"expected_version": invalid_version, "title": "不得写入"},
    )

    assert response.status_code == 422


async def test_owner_updates_draft_metadata_with_version_audit_and_title_search(
    document_client: tuple[AsyncClient, FakeDocumentStorage],
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.audit.models import AuditLog

    client, _storage = document_client
    owner_id = await _create_user(email="draft-editor@company.com", password="password123")
    token = await _login(client, email="draft-editor@company.com", password="password123")
    upload = await client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("legacy-name.pdf", PDF_BYTES, "application/pdf")},
        data=UPLOAD_DRAFT_FORM,
    )
    assert upload.status_code == 201
    uploaded = upload.json()["data"]
    assert uploaded["title"] == "legacy-name.pdf"
    assert uploaded["review_version"] == 0

    response = await client.patch(
        f"/api/files/{uploaded['id']}",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "expected_version": 0,
            "title": "  新版安全手册  ",
            "description": "  员工可见说明  ",
            "visibility": "department",
        },
    )

    assert response.status_code == 200, response.text
    updated = response.json()["data"]
    assert updated["title"] == "新版安全手册"
    assert updated["description"] == "员工可见说明"
    assert updated["visibility"] == "department"
    assert updated["review_version"] == 1
    search = await client.get(
        "/api/files",
        headers={"Authorization": f"Bearer {token}"},
        params={"q": "新版安全"},
    )
    assert search.status_code == 200
    assert [item["id"] for item in search.json()["data"]["items"]] == [uploaded["id"]]

    async with AsyncSessionFactory() as session:
        audit = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.actor_id == owner_id,
                    AuditLog.action == "file.update_draft",
                    AuditLog.target_id == UUID(uploaded["id"]),
                )
            )
        ).scalar_one()
    assert audit.metadata_json["changed_fields"] == ["description", "title", "visibility"]
    assert audit.metadata_json["expected_version"] == 0
    assert audit.metadata_json["review_version"] == 1


async def test_concurrent_replacement_upload_creates_one_contiguous_version_chain(
    document_client: tuple[AsyncClient, FakeDocumentStorage],
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File

    client, _storage = document_client
    uploader_id = await _create_user(
        email="version-uploader@company.com",
        password="password123",
    )
    token = await _login(client, email="version-uploader@company.com", password="password123")
    predecessor_id = await _upload_pdf(client, token=token, filename="policy-v1.pdf")
    async with AsyncSessionFactory() as session:
        predecessor = await session.get(File, UUID(predecessor_id))
        assert predecessor is not None
        predecessor.status = "parsed"
        predecessor.review_status = "approved"
        predecessor.ragflow_dataset_id = "dataset-version-api"
        predecessor.ragflow_document_id = "remote-version-api-v1"
        predecessor.ragflow_parse_status = "DONE"
        predecessor.remote_visibility = "current"
        predecessor.version_switch_status = "not_required"
        await session.commit()

    async def upload_replacement(filename: str) -> Response:
        return await client.post(
            "/api/files/upload",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": (filename, PDF_BYTES, "application/pdf")},
            data={
                **UPLOAD_DRAFT_FORM,
                "replaces_file_id": predecessor_id,
            },
        )

    first, second = await asyncio.gather(
        upload_replacement("policy-v2-a.pdf"),
        upload_replacement("policy-v2-b.pdf"),
    )
    assert sorted([first.status_code, second.status_code]) == [201, 409]
    succeeded = first if first.status_code == 201 else second
    conflicted = first if first.status_code == 409 else second
    assert conflicted.json()["error_code"] == "FILE_REPLACEMENT_CONFLICT"

    candidate = succeeded.json()["data"]
    assert candidate["uploader_id"] == str(uploader_id)
    assert candidate["series_id"] == predecessor_id
    assert candidate["version_number"] == 2
    assert candidate["replaces_file_id"] == predecessor_id
    assert candidate["is_current_version"] is False
    assert candidate["remote_visibility"] == "candidate"
    assert candidate["version_switch_status"] == "pending"
    assert candidate["replacement_remote_action"] == "archive"
    patched = await client.patch(
        f"/api/files/{candidate['id']}",
        headers={"Authorization": f"Bearer {token}"},
        json={"expected_version": 0, "title": "policy-v2"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["data"]["replacement_remote_action"] == "archive"
    assert patched.json()["data"]["review_version"] == 1

    detail = await client.get(
        f"/api/files/{candidate['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert detail.status_code == 200, detail.text
    chain = detail.json()["data"]["version_chain"]
    assert [(item["id"], item["version_number"]) for item in chain] == [
        (candidate["id"], 2),
        (predecessor_id, 1),
    ]
    assert [item["is_current_version"] for item in chain] == [False, True]


async def test_existing_candidate_conflict_does_not_deadlock_with_version_worker_lock_order(
    document_client: tuple[AsyncClient, FakeDocumentStorage],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File
    from app.modules.document.repository import (  # noqa: TID251 - focused lock test
        DocumentRepository,
    )

    client, _storage = document_client
    await _create_user(
        email="version-deadlock-uploader@company.com",
        password="password123",
    )
    token = await _login(
        client,
        email="version-deadlock-uploader@company.com",
        password="password123",
    )
    v1_id = await _upload_pdf(client, token=token, filename="deadlock-v1.pdf")
    async with AsyncSessionFactory() as session:
        v1 = await session.get(File, UUID(v1_id))
        assert v1 is not None
        v1.status = "parsed"
        v1.review_status = "approved"
        v1.ragflow_dataset_id = "dataset-version-deadlock"
        v1.ragflow_document_id = "remote-version-deadlock-v1"
        v1.ragflow_parse_status = "DONE"
        v1.remote_visibility = "current"
        v1.version_switch_status = "not_required"
        await session.commit()

    v2_response = await client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("deadlock-v2.pdf", PDF_BYTES + b"v2", "application/pdf")},
        data={**UPLOAD_DRAFT_FORM, "replaces_file_id": v1_id},
    )
    assert v2_response.status_code == 201, v2_response.text
    v2_id = v2_response.json()["data"]["id"]
    async with AsyncSessionFactory() as session:
        v1 = await session.get(File, UUID(v1_id))
        v2 = await session.get(File, UUID(v2_id))
        assert v1 is not None and v2 is not None
        v1.is_current_version = False
        v1.remote_visibility = "not_current"
        await session.flush()
        v2.status = "parsed"
        v2.review_status = "approved"
        v2.ragflow_dataset_id = "dataset-version-deadlock"
        v2.ragflow_document_id = "remote-version-deadlock-v2"
        v2.ragflow_parse_status = "DONE"
        v2.is_current_version = True
        v2.remote_visibility = "current"
        v2.version_switch_status = "completed"
        await session.commit()

    v3_response = await client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("deadlock-v3.pdf", PDF_BYTES + b"v3", "application/pdf")},
        data={**UPLOAD_DRAFT_FORM, "replaces_file_id": v2_id},
    )
    assert v3_response.status_code == 201, v3_response.text

    worker_locked_root = asyncio.Event()
    upload_locked_current = asyncio.Event()
    worker_attempting_current = asyncio.Event()
    original_get_for_update = DocumentRepository.get_by_id_for_update

    async def gated_get_for_update(
        repository: DocumentRepository,
        file_id: UUID,
    ) -> File | None:
        file = await original_get_for_update(repository, file_id)
        if file_id == UUID(v2_id):
            upload_locked_current.set()
            await asyncio.wait_for(worker_attempting_current.wait(), timeout=5)
        return file

    monkeypatch.setattr(
        DocumentRepository,
        "get_by_id_for_update",
        gated_get_for_update,
    )

    async def emulate_worker_lock_order() -> None:
        async with AsyncSessionFactory() as session:
            await session.execute(select(File).where(File.id == UUID(v1_id)).with_for_update())
            worker_locked_root.set()
            await asyncio.wait_for(upload_locked_current.wait(), timeout=5)
            worker_attempting_current.set()
            await session.execute(select(File).where(File.id == UUID(v2_id)).with_for_update())
            await session.rollback()

    worker = asyncio.create_task(emulate_worker_lock_order())
    await asyncio.wait_for(worker_locked_root.wait(), timeout=5)
    response = await asyncio.wait_for(
        client.post(
            "/api/files/upload",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("deadlock-v4.pdf", PDF_BYTES + b"v4", "application/pdf")},
            data={**UPLOAD_DRAFT_FORM, "replaces_file_id": v2_id},
        ),
        timeout=10,
    )
    await asyncio.wait_for(worker, timeout=10)

    assert response.status_code == 409
    assert response.json()["error_code"] == "FILE_REPLACEMENT_CONFLICT"
    async with AsyncSessionFactory() as session:
        chain = list(
            (
                await session.execute(
                    select(File)
                    .where(File.series_id == UUID(v1_id))
                    .order_by(File.version_number.asc())
                )
            ).scalars()
        )
    assert [file.version_number for file in chain] == [1, 2, 3]


async def test_owner_options_and_expiry_owner_draft_contract(
    document_client: tuple[AsyncClient, FakeDocumentStorage],
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.document import tasks as document_tasks
    from app.modules.document.models import File
    from app.modules.user.models import User

    client, _storage = document_client
    uploader_id = await _create_user(email="expiry-uploader@company.com", password="password123")
    owner_id = await _create_user(email="expiry-owner@company.com", password="password123")
    inactive_id = await _create_user(
        email="expiry-inactive@company.com",
        password="password123",
    )
    unverified_id = await _create_user(
        email="expiry-unverified@company.com",
        password="password123",
    )
    cross_department_id = await _create_user(
        email="expiry-cross@company.com",
        password="password123",
        department_code="expiry-cross-department",
        department_name="到期跨部门",
    )
    async with AsyncSessionFactory() as session:
        inactive = await session.get(User, inactive_id)
        unverified = await session.get(User, unverified_id)
        assert inactive is not None and unverified is not None
        inactive.status = "disabled"
        unverified.email_verified = False
        await session.commit()

    token = await _login(client, email="expiry-uploader@company.com", password="password123")
    options = await client.get(
        "/api/files/owner-options",
        params={"page": 1, "page_size": 2, "q": "expiry"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert options.status_code == 200, options.text
    options_data = options.json()["data"]
    assert options_data["page"] == 1
    assert options_data["page_size"] == 2
    assert options_data["total"] >= 2
    assert options_data["total_pages"] >= 1
    visible_ids = {item["id"] for item in options_data["items"]}
    assert str(uploader_id) in visible_ids
    assert str(owner_id) in visible_ids
    assert str(inactive_id) not in visible_ids
    assert str(unverified_id) not in visible_ids
    assert str(cross_department_id) not in visible_ids

    upload = await client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("expiry-owner.pdf", PDF_BYTES, "application/pdf")},
        data=UPLOAD_DRAFT_FORM,
    )
    assert upload.status_code == 201, upload.text
    uploaded = upload.json()["data"]
    assert uploaded["owner_id"] == str(uploader_id)
    marker_sent_at = datetime.now(UTC) - timedelta(hours=1)
    async with AsyncSessionFactory() as session:
        stored = await session.get(File, UUID(uploaded["id"]))
        assert stored is not None
        stored.expiry_warning_sent_at = marker_sent_at
        stored.expiry_expired_sent_at = marker_sent_at
        await session.commit()

    expires_at = datetime.now(UTC) + timedelta(days=20)
    updated = await client.patch(
        f"/api/files/{uploaded['id']}",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "expected_version": 0,
            "owner_id": str(owner_id),
            "expires_at": expires_at.isoformat(),
        },
    )
    assert updated.status_code == 200, updated.text
    data = updated.json()["data"]
    assert data["owner_id"] == str(owner_id)
    assert data["owner_name"] == "expiry-owner"
    assert data["expiry_status"] == "active"
    assert data["expires_at"] is not None
    assert data["review_version"] == 1

    async with AsyncSessionFactory() as session:
        stored = await session.get(File, UUID(uploaded["id"]))
        assert stored is not None
        assert stored.expiry_warning_sent_at is None
        assert stored.expiry_expired_sent_at is None
        stored.expiry_status = "expiring"
        stored.expiry_warning_sent_at = marker_sent_at
        stored.expiry_expired_sent_at = marker_sent_at
        await session.commit()

    same_values = await client.patch(
        f"/api/files/{uploaded['id']}",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "expected_version": 1,
            "owner_id": str(owner_id),
            "expires_at": expires_at.isoformat(),
        },
    )
    assert same_values.status_code == 200, same_values.text
    assert same_values.json()["data"]["review_version"] == 2
    assert same_values.json()["data"]["expiry_status"] == "expiring"
    queued = await document_tasks.run_scan_expiring_files_task_async(
        lookahead_days=30,
        batch_size=10,
        max_batches=1,
    )
    assert queued == 0
    async with AsyncSessionFactory() as session:
        stored = await session.get(File, UUID(uploaded["id"]))
        expiry_events = list(
            (
                await session.execute(
                    select(EventOutbox).where(
                        EventOutbox.aggregate_id == uploaded["id"],
                        EventOutbox.event_type.in_(
                            ("document.file.expiring", "document.file.expired")
                        ),
                    )
                )
            ).scalars()
        )
    assert stored is not None
    assert stored.expiry_status == "expiring"
    assert stored.expiry_warning_sent_at == marker_sent_at
    assert stored.expiry_expired_sent_at == marker_sent_at
    assert expiry_events == []

    rejected_owner = await client.patch(
        f"/api/files/{uploaded['id']}",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "expected_version": 2,
            "owner_id": str(cross_department_id),
        },
    )
    assert rejected_owner.status_code == 422
    assert rejected_owner.json()["error_code"] == "INVALID_DOCUMENT_OWNER"
    rejected_unverified = await client.patch(
        f"/api/files/{uploaded['id']}",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "expected_version": 2,
            "owner_id": str(unverified_id),
        },
    )
    assert rejected_unverified.status_code == 422
    assert rejected_unverified.json()["error_code"] == "INVALID_DOCUMENT_OWNER"
    naive_expiry = await client.patch(
        f"/api/files/{uploaded['id']}",
        headers={"Authorization": f"Bearer {token}"},
        json={"expected_version": 2, "expires_at": "2026-07-30T08:00:00"},
    )
    assert naive_expiry.status_code == 422
    assert naive_expiry.json()["error_code"] == "VALIDATION_ERROR"
    empty_patch = await client.patch(
        f"/api/files/{uploaded['id']}",
        headers={"Authorization": f"Bearer {token}"},
        json={"expected_version": 2},
    )
    assert empty_patch.status_code == 422
    assert empty_patch.json()["error_code"] == "VALIDATION_ERROR"

    cleared = await client.patch(
        f"/api/files/{uploaded['id']}",
        headers={"Authorization": f"Bearer {token}"},
        json={"expected_version": 2, "expires_at": None},
    )
    assert cleared.status_code == 200, cleared.text
    cleared_data = cleared.json()["data"]
    assert cleared_data["owner_id"] == str(owner_id)
    assert cleared_data["expires_at"] is None
    assert cleared_data["expiry_status"] == "never"
    assert cleared_data["review_version"] == 3


@pytest.mark.parametrize("owner_role", ["employee", "dept_admin"])
async def test_delegated_owner_access_is_read_only_department_scoped_and_audited(
    document_client: tuple[AsyncClient, FakeDocumentStorage],
    owner_role: str,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.audit.models import AuditLog
    from app.modules.document.models import File
    from app.modules.user.models import User

    client, _storage = document_client
    uploader_id = await _create_user(
        email="delegated-uploader@company.com",
        password="password123",
    )
    owner_id = await _create_user(
        email=f"delegated-owner-{owner_role}@company.com",
        password="password123",
        role=owner_role,
    )
    new_department_user_id = await _create_user(
        email="delegated-new-department@company.com",
        password="password123",
        department_code="delegated-new-department",
        department_name="负责人调入部门",
    )
    uploader_token = await _login(
        client,
        email="delegated-uploader@company.com",
        password="password123",
    )
    owner_token = await _login(
        client,
        email=f"delegated-owner-{owner_role}@company.com",
        password="password123",
    )
    upload = await client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {uploader_token}"},
        files={"file": ("delegated-root.pdf", PDF_BYTES, "application/pdf")},
        data=UPLOAD_DRAFT_FORM,
    )
    assert upload.status_code == 201, upload.text
    root_id = upload.json()["data"]["id"]
    delegated = await client.patch(
        f"/api/files/{root_id}",
        headers={"Authorization": f"Bearer {uploader_token}"},
        json={"expected_version": 0, "owner_id": str(owner_id)},
    )
    assert delegated.status_code == 200, delegated.text

    async with AsyncSessionFactory() as session:
        root = await session.get(File, UUID(root_id))
        assert root is not None
        root.status = "parsed"
        root.review_status = "approved"
        root.ragflow_dataset_id = "dataset-delegated"
        root.ragflow_document_id = "remote-delegated-root"
        root.ragflow_parse_status = "DONE"
        root.remote_visibility = "current"
        await session.commit()

    replacement = await client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {uploader_token}"},
        files={"file": ("delegated-v2.pdf", PDF_BYTES + b"v2", "application/pdf")},
        data={**UPLOAD_DRAFT_FORM, "replaces_file_id": root_id},
    )
    assert replacement.status_code == 201, replacement.text
    replacement_id = replacement.json()["data"]["id"]

    async with AsyncSessionFactory() as session:
        root = await session.get(File, UUID(root_id))
        candidate = await session.get(File, UUID(replacement_id))
        assert root is not None and candidate is not None
        root.is_current_version = False
        root.remote_visibility = "not_current"
        await session.flush()
        candidate.is_current_version = True
        candidate.status = "parsed"
        candidate.review_status = "approved"
        candidate.ragflow_dataset_id = "dataset-delegated"
        candidate.ragflow_document_id = "remote-delegated-v2"
        candidate.ragflow_parse_status = "DONE"
        candidate.remote_visibility = "current"
        candidate.version_switch_status = "completed"
        await session.commit()

    responsible = await client.get(
        "/api/files/responsible",
        params={"page": 1, "page_size": 1, "q": "delegated"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert responsible.status_code == 200, responsible.text
    responsible_data = responsible.json()["data"]
    assert responsible_data["total"] == 1
    assert [item["id"] for item in responsible_data["items"]] == [replacement_id]

    historical_detail = await client.get(
        f"/api/files/{root_id}",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    historical_inline = await client.get(
        f"/api/files/{root_id}/content",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    historical_download = await client.get(
        f"/api/files/{root_id}/content?disposition=attachment",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert historical_detail.status_code == 404
    assert historical_inline.status_code == 404
    assert historical_download.status_code == 404

    current_detail = await client.get(
        f"/api/files/{replacement_id}",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert current_detail.status_code == 200, current_detail.text
    assert [item["id"] for item in current_detail.json()["data"]["version_chain"]] == [
        replacement_id,
    ]
    current_content = await client.get(
        f"/api/files/{replacement_id}/content?disposition=attachment",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert current_content.status_code == 200
    assert current_content.content == PDF_BYTES + b"v2"

    uploader_history_detail = await client.get(
        f"/api/files/{root_id}",
        headers={"Authorization": f"Bearer {uploader_token}"},
    )
    uploader_history_content = await client.get(
        f"/api/files/{root_id}/content",
        headers={"Authorization": f"Bearer {uploader_token}"},
    )
    assert uploader_history_detail.status_code == 200
    assert [item["id"] for item in uploader_history_detail.json()["data"]["version_chain"]] == [
        replacement_id,
        root_id,
    ]
    assert uploader_history_content.status_code == 200
    assert uploader_history_content.content == PDF_BYTES

    forbidden_delete = await client.delete(
        f"/api/files/{replacement_id}",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert forbidden_delete.status_code == 404

    async with AsyncSessionFactory() as session:
        historical_audit_actions = set(
            (
                await session.execute(
                    select(AuditLog.action).where(
                        AuditLog.actor_id == owner_id,
                        AuditLog.target_id == UUID(root_id),
                    )
                )
            ).scalars()
        )
        current_audit_actions = set(
            (
                await session.execute(
                    select(AuditLog.action).where(
                        AuditLog.actor_id == owner_id,
                        AuditLog.target_id == UUID(replacement_id),
                    )
                )
            ).scalars()
        )
        assert historical_audit_actions == set()
        assert {"file.view_detail", "file.view_content"} <= current_audit_actions
        owner = await session.get(User, owner_id)
        assert owner is not None
        owner.department_id = await _get_user_department_id(new_department_user_id)
        owner.department = "负责人调入部门"
        await session.commit()

    moved_responsible = await client.get(
        "/api/files/responsible",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert moved_responsible.status_code == 200
    assert moved_responsible.json()["data"]["total"] == 0
    moved_detail = await client.get(
        f"/api/files/{replacement_id}",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert moved_detail.status_code == 404
    moved_content = await client.get(
        f"/api/files/{replacement_id}/content",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert moved_content.status_code == 404
    assert str(uploader_id) != str(owner_id)


async def test_draft_patch_is_owner_only_hides_deleted_and_locks_reviewed_files(
    document_client: tuple[AsyncClient, FakeDocumentStorage],
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File

    client, _storage = document_client
    await _create_user(email="draft-owner@company.com", password="password123")
    await _create_user(email="draft-attacker@company.com", password="password123")
    owner_token = await _login(client, email="draft-owner@company.com", password="password123")
    attacker_token = await _login(
        client,
        email="draft-attacker@company.com",
        password="password123",
    )
    upload = await client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {owner_token}"},
        files={"file": ("owner-only.pdf", PDF_BYTES, "application/pdf")},
        data=UPLOAD_DRAFT_FORM,
    )
    file_id = UUID(upload.json()["data"]["id"])

    denied = await client.patch(
        f"/api/files/{file_id}",
        headers={"Authorization": f"Bearer {attacker_token}"},
        json={"expected_version": 0, "title": "越权标题"},
    )
    assert denied.status_code == 404
    assert denied.json()["error_code"] == "FILE_NOT_FOUND"

    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        assert file is not None
        file.status = "pending_review"
        file.submitted_at = datetime.now(UTC)
        file.review_due_at = file.submitted_at + timedelta(hours=24)
        await session.commit()
    locked = await client.patch(
        f"/api/files/{file_id}",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"expected_version": 0, "title": "审核中不可改"},
    )
    assert locked.status_code == 409

    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        assert file is not None
        file.status = "deleted"
        file.submitted_at = None
        file.review_due_at = None
        await session.commit()
    hidden = await client.patch(
        f"/api/files/{file_id}",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"expected_version": 0, "title": "删除后不可见"},
    )
    assert hidden.status_code == 404
    assert hidden.json()["error_code"] == "FILE_NOT_FOUND"


async def test_unassigned_uploader_cannot_patch_an_existing_draft(
    document_client: tuple[AsyncClient, FakeDocumentStorage],
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.department.models import UNASSIGNED_DEPARTMENT_ID
    from app.modules.document.models import File
    from app.modules.user.models import User

    client, _storage = document_client
    uploader_id = await _create_user(
        email="draft-unassigned@company.com",
        password="password123",
    )
    token = await _login(
        client,
        email="draft-unassigned@company.com",
        password="password123",
    )
    upload = await client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("unassigned-after-upload.pdf", PDF_BYTES, "application/pdf")},
        data=UPLOAD_DRAFT_FORM,
    )
    assert upload.status_code == 201, upload.text
    file_id = UUID(upload.json()["data"]["id"])
    async with AsyncSessionFactory() as session:
        uploader = await session.get(User, uploader_id)
        assert uploader is not None
        uploader.department_id = UNASSIGNED_DEPARTMENT_ID
        uploader.department = None
        await session.commit()

    denied = await client.patch(
        f"/api/files/{file_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"expected_version": 0, "title": "不应写入"},
    )
    assert denied.status_code == 403
    assert denied.json()["error_code"] == "DEPARTMENT_ASSIGNMENT_REQUIRED"
    async with AsyncSessionFactory() as session:
        stored = await session.get(File, file_id)
        assert stored is not None
        assert stored.review_version == 0
        assert stored.title == "unassigned-after-upload.pdf"


async def test_concurrent_draft_patches_with_same_version_return_one_conflict(
    document_client: tuple[AsyncClient, FakeDocumentStorage],
) -> None:
    client, _storage = document_client
    await _create_user(email="draft-race@company.com", password="password123")
    token = await _login(client, email="draft-race@company.com", password="password123")
    upload = await client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("race.pdf", PDF_BYTES, "application/pdf")},
        data=UPLOAD_DRAFT_FORM,
    )
    file_id = upload.json()["data"]["id"]

    first, second = await asyncio.gather(
        client.patch(
            f"/api/files/{file_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"expected_version": 0, "title": "并发标题甲"},
        ),
        client.patch(
            f"/api/files/{file_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"expected_version": 0, "title": "并发标题乙"},
        ),
    )

    assert sorted([first.status_code, second.status_code]) == [200, 409]
    conflict = first if first.status_code == 409 else second
    success = first if first.status_code == 200 else second
    assert conflict.json()["error_code"] == "FILE_VERSION_CONFLICT"
    assert success.json()["data"]["review_version"] == 1


async def _create_category_named(name: str) -> UUID:
    from app.core.database import AsyncSessionFactory
    from app.modules.review.models import Category

    category = Category(name=name, code=f"cat-{uuid4().hex[:8]}", keywords=[])
    async with AsyncSessionFactory() as session:
        session.add(category)
        await session.commit()
        await session.refresh(category)
        return category.id


async def _assign_file_category(file_id: UUID, category_id: UUID) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File

    async with AsyncSessionFactory() as session:
        result = await session.execute(select(File).where(File.id == file_id))
        file = result.scalar_one()
        file.category_id = category_id
        await session.commit()


async def _create_analysis(
    *,
    file_id: UUID,
    status: str = "succeeded",
    summary: str | None = None,
    sensitive_risk_level: str = "none",
    extracted_text: str | None = None,
    error_message: str | None = None,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ai.models import DocumentAnalysis

    now = datetime.now(UTC)
    analysis = DocumentAnalysis(
        file_id=file_id,
        status=status,
        extracted_text=extracted_text,
        summary=summary,
        suggested_tags=[],
        sensitive_risk_level=sensitive_risk_level,
        sensitive_hits=[],
        error_message=error_message,
        started_at=now,
        finished_at=None if status == "running" else now,
    )
    async with AsyncSessionFactory() as session:
        session.add(analysis)
        await session.commit()


async def _create_failed_sync_task(
    *,
    file_id: UUID,
    error_message: str,
    created_at: datetime,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ragflow.models import SyncTask

    task = SyncTask(
        file_id=file_id,
        task_type="ragflow_upload",
        status="failed",
        error_message=error_message,
        created_at=created_at,
    )
    async with AsyncSessionFactory() as session:
        session.add(task)
        await session.commit()


async def _upload_pdf(client: AsyncClient, *, token: str, filename: str) -> str:
    response = await client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": (filename, PDF_BYTES, "application/pdf")},
        data=UPLOAD_DRAFT_FORM,
    )
    assert response.status_code == 201
    return str(response.json()["data"]["id"])


async def test_owner_file_detail_includes_category_analysis_and_sync_error(
    document_client: tuple[AsyncClient, FakeDocumentStorage],
) -> None:
    client, _storage = document_client
    await _create_user(email="detail-owner@company.com", password="password123")
    token = await _login(client, email="detail-owner@company.com", password="password123")
    file_id = await _upload_pdf(client, token=token, filename="detail.pdf")

    category_id = await _create_category_named("制度文档")
    await _assign_file_category(UUID(file_id), category_id)
    await _create_analysis(
        file_id=UUID(file_id),
        status="succeeded",
        summary="文档摘要内容",
        sensitive_risk_level="medium",
        extracted_text="a" * 600,
    )
    now = datetime.now(UTC)
    await _create_failed_sync_task(
        file_id=UUID(file_id),
        error_message="older sync error",
        created_at=now - timedelta(minutes=5),
    )
    await _create_failed_sync_task(
        file_id=UUID(file_id),
        error_message="latest sync error",
        created_at=now,
    )

    response = await client.get(
        f"/api/files/{file_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["category_name"] == "制度文档"
    analysis = data["analysis"]
    assert analysis is not None
    assert analysis["status"] == "succeeded"
    assert analysis["summary"] == "文档摘要内容"
    assert analysis["sensitive_risk_level"] == "medium"
    assert analysis["quality_score"] is None
    assert analysis["extracted_text_preview"] == "a" * 500
    assert analysis["error_message"] is None
    assert analysis["finished_at"] is not None
    assert data["sync_error"] == "latest sync error"


async def test_file_detail_returns_null_extras_without_records(
    document_client: tuple[AsyncClient, FakeDocumentStorage],
) -> None:
    client, _storage = document_client
    await _create_user(email="detail-empty@company.com", password="password123")
    token = await _login(client, email="detail-empty@company.com", password="password123")
    file_id = await _upload_pdf(client, token=token, filename="empty-detail.pdf")

    response = await client.get(
        f"/api/files/{file_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["category_name"] is None
    assert data["analysis"] is None
    assert data["sync_error"] is None


async def test_owner_reads_original_content_inline_and_with_byte_range(
    document_client: tuple[AsyncClient, FakeDocumentStorage],
) -> None:
    client, storage = document_client
    await _create_user(email="content-owner@company.com", password="password123")
    token = await _login(client, email="content-owner@company.com", password="password123")
    file_id = await _upload_pdf(client, token=token, filename="原件报告.pdf")

    inline_response = await client.get(
        f"/api/files/{file_id}/content",
        headers={"Authorization": f"Bearer {token}"},
    )
    range_response = await client.get(
        f"/api/files/{file_id}/content?disposition=attachment",
        headers={
            "Authorization": f"Bearer {token}",
            "Range": "bytes=0-3",
        },
    )

    assert inline_response.status_code == 200
    assert inline_response.content == PDF_BYTES
    assert inline_response.headers["content-type"] == "application/pdf"
    assert inline_response.headers["accept-ranges"] == "bytes"
    assert inline_response.headers["cache-control"] == "private, no-store"
    assert inline_response.headers["content-length"] == str(len(PDF_BYTES))
    assert inline_response.headers["content-security-policy"] == "sandbox"
    assert inline_response.headers["etag"] == f'"{sha256(PDF_BYTES).hexdigest()}"'
    assert inline_response.headers["x-content-type-options"] == "nosniff"
    assert inline_response.headers["content-disposition"].startswith("inline;")
    assert (
        "filename*=UTF-8''%E5%8E%9F%E4%BB%B6%E6%8A%A5%E5%91%8A.pdf"
        in (inline_response.headers["content-disposition"])
    )
    assert range_response.status_code == 206
    assert range_response.content == PDF_BYTES[:4]
    assert range_response.headers["content-range"] == f"bytes 0-3/{len(PDF_BYTES)}"
    assert range_response.headers["content-length"] == "4"
    assert range_response.headers["etag"] == inline_response.headers["etag"]
    assert range_response.headers["content-disposition"].startswith("attachment;")
    assert storage.get_object_calls == 0
    assert storage.open_calls[0][2:] == (0, len(PDF_BYTES))
    assert storage.open_calls[-1][2:] == (0, 4)
    assert all(stream.closed for stream in storage.opened_streams)


def _test_content_result() -> object:
    return SimpleNamespace(
        file=SimpleNamespace(
            size=8,
            mime_type="application/pdf",
            extension="pdf",
            original_name="preview.pdf",
            hash="a" * 64,
        )
    )


async def test_content_response_closes_stream_when_body_iteration_never_starts() -> None:
    from app.modules.document import api as document_api
    from app.modules.document.service import FileContentResult  # noqa: TID251 - same-module test

    stream = BlockingObjectStream()
    response = document_api._content_response(
        cast(FileContentResult, _test_content_result()),
        stream,
        disposition="inline",
        requested_range=None,
    )
    receive_blocked = asyncio.Event()

    async def receive() -> Message:
        await receive_blocked.wait()
        return {"type": "http.disconnect"}

    async def send(_message: Message) -> None:
        raise RuntimeError("client disconnected before response body")

    with pytest.raises(BaseExceptionGroup):
        await response(cast(Scope, {"type": "http"}), receive, send)

    assert stream.next_calls == 0
    assert stream.close_calls == 1
    assert stream.release_conn_calls == 1


async def test_content_response_closes_stream_once_when_disconnected_before_first_chunk() -> None:
    from app.modules.document import api as document_api
    from app.modules.document.service import FileContentResult  # noqa: TID251 - same-module test

    stream = BlockingObjectStream()
    response = document_api._content_response(
        cast(FileContentResult, _test_content_result()),
        stream,
        disposition="inline",
        requested_range=None,
    )
    sent: list[Message] = []

    async def receive() -> Message:
        await stream.next_started.wait()
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        sent.append(message)

    await response(cast(Scope, {"type": "http"}), receive, send)

    assert sent[0]["type"] == "http.response.start"
    assert stream.next_calls == 1
    assert stream.close_calls == 1
    assert stream.release_conn_calls == 1


async def test_content_cleanup_failure_does_not_replace_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.document import api as document_api
    from app.modules.document.service import FileContentResult  # noqa: TID251 - same-module test

    warnings: list[tuple[str, dict[str, object]]] = []

    class FakeLogger:
        def warning(self, event_name: str, **fields: object) -> None:
            warnings.append((event_name, fields))

    monkeypatch.setattr(document_api, "logger", FakeLogger())
    stream = FailingCloseObjectStream()
    response = document_api._content_response(
        cast(FileContentResult, _test_content_result()),
        stream,
        disposition="inline",
        requested_range=None,
    )
    receive_blocked = asyncio.Event()

    async def receive() -> Message:
        await receive_blocked.wait()
        return {"type": "http.disconnect"}

    async def send(_message: Message) -> None:
        raise RuntimeError("primary transport failure")

    with pytest.raises(BaseExceptionGroup) as raised:
        await response(cast(Scope, {"type": "http"}), receive, send)

    assert "primary transport failure" in repr(raised.value)
    assert "private cleanup detail" not in repr(raised.value)
    assert stream.next_calls == 0
    assert stream.close_calls == 1
    assert stream.release_conn_calls == 1
    assert warnings == [
        (
            "document_content_stream_close_failed",
            {"error_type": "OSError"},
        )
    ]


async def test_original_content_rejects_invalid_or_multi_range(
    document_client: tuple[AsyncClient, FakeDocumentStorage],
) -> None:
    client, _storage = document_client
    await _create_user(email="range-owner@company.com", password="password123")
    token = await _login(client, email="range-owner@company.com", password="password123")
    file_id = await _upload_pdf(client, token=token, filename="range.pdf")

    response = await client.get(
        f"/api/files/{file_id}/content",
        headers={
            "Authorization": f"Bearer {token}",
            "Range": "bytes=0-1,4-5",
        },
    )

    assert response.status_code == 416
    assert response.headers["content-range"] == f"bytes */{len(PDF_BYTES)}"


@pytest.mark.parametrize(
    ("extension", "mime_type"),
    [
        ("html", "text/html"),
        ("xhtml", "application/xhtml+xml"),
        ("bin", "application/octet-stream"),
    ],
)
async def test_unsafe_or_unknown_content_type_is_never_served_inline(
    document_client: tuple[AsyncClient, FakeDocumentStorage],
    extension: str,
    mime_type: str,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File

    client, _storage = document_client
    await _create_user(email="unsafe-inline@company.com", password="password123")
    token = await _login(client, email="unsafe-inline@company.com", password="password123")
    file_id = await _upload_pdf(client, token=token, filename="unsafe.pdf")
    async with AsyncSessionFactory() as session:
        file = await session.get(File, UUID(file_id))
        assert file is not None
        file.extension = extension
        file.mime_type = mime_type
        await session.commit()

    response = await client.get(
        f"/api/files/{file_id}/content?disposition=inline",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("attachment;")


async def test_employee_cannot_read_another_users_original_content(
    document_client: tuple[AsyncClient, FakeDocumentStorage],
) -> None:
    client, _storage = document_client
    await _create_user(email="content-owner-2@company.com", password="password123")
    owner_token = await _login(
        client,
        email="content-owner-2@company.com",
        password="password123",
    )
    file_id = await _upload_pdf(client, token=owner_token, filename="private.pdf")
    await _create_user(email="content-other@company.com", password="password123")
    other_token = await _login(
        client,
        email="content-other@company.com",
        password="password123",
    )

    response = await client.get(
        f"/api/files/{file_id}/content",
        headers={"Authorization": f"Bearer {other_token}"},
    )

    assert response.status_code == 404
    assert response.json()["error_code"] == "FILE_NOT_FOUND"


async def test_system_admin_downloads_original_and_writes_audit(
    document_client: tuple[AsyncClient, FakeDocumentStorage],
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.audit.models import AuditLog

    client, _storage = document_client
    owner_id = await _create_user(email="content-owner-3@company.com", password="password123")
    owner_token = await _login(
        client,
        email="content-owner-3@company.com",
        password="password123",
    )
    file_id = await _upload_pdf(client, token=owner_token, filename="admin-content.pdf")
    admin_id = await _create_user(
        email="content-admin@company.com",
        password="password123",
        role="system_admin",
    )
    admin_token = await _login(
        client,
        email="content-admin@company.com",
        password="password123",
    )

    response = await client.get(
        f"/api/files/{file_id}/content?disposition=attachment",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 200
    assert response.content == PDF_BYTES
    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(AuditLog).where(AuditLog.action == "file.view_content")
        )
        audit_log = result.scalar_one()
    assert audit_log.actor_id == admin_id
    assert audit_log.target_id == UUID(file_id)
    assert audit_log.metadata_json["uploader_id"] == str(owner_id)
    assert audit_log.metadata_json["disposition"] == "attachment"
    assert audit_log.metadata_json["audit_semantics"] == "access_authorized_before_stream_open"
    assert audit_log.metadata_json["stream_completion_confirmed"] is False


async def test_admin_accessing_own_file_still_writes_detail_and_content_audits(
    document_client: tuple[AsyncClient, FakeDocumentStorage],
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.audit.models import AuditLog

    client, _storage = document_client
    admin_id = await _create_user(
        email="admin-own-file@company.com",
        password="password123",
        role="system_admin",
    )
    token = await _login(client, email="admin-own-file@company.com", password="password123")
    file_id = await _upload_pdf(client, token=token, filename="admin-owned.pdf")

    detail_response = await client.get(
        f"/api/files/{file_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    content_response = await client.get(
        f"/api/files/{file_id}/content?disposition=inline",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert detail_response.status_code == 200
    assert content_response.status_code == 200
    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(AuditLog).where(
                AuditLog.actor_id == admin_id,
                AuditLog.target_id == UUID(file_id),
                AuditLog.action.in_(("file.view_detail", "file.view_content")),
            )
        )
        logs = list(result.scalars())
    assert {log.action for log in logs} == {"file.view_detail", "file.view_content"}
    logs_by_action = {log.action: log for log in logs}
    assert logs_by_action["file.view_detail"].metadata_json["access_role"] == "uploader"
    assert logs_by_action["file.view_content"].metadata_json["access_role"] == "uploader"
    content_log = logs_by_action["file.view_content"]
    assert content_log.metadata_json["audit_semantics"] == ("access_authorized_before_stream_open")
    assert content_log.metadata_json["stream_completion_confirmed"] is False


@pytest.mark.parametrize("admin_role", ["system_admin", "dept_admin"])
async def test_admin_views_authorized_file_detail_and_writes_audit(
    document_client: tuple[AsyncClient, FakeDocumentStorage],
    admin_role: str,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.audit.models import AuditLog

    client, _storage = document_client
    owner_id = await _create_user(email="detail-employee@company.com", password="password123")
    owner_token = await _login(client, email="detail-employee@company.com", password="password123")
    file_id = await _upload_pdf(client, token=owner_token, filename="admin-view.pdf")

    admin_email = f"{admin_role.replace('_', '-')}@company.com"
    admin_id = await _create_user(email=admin_email, password="password123", role=admin_role)
    if admin_role == "dept_admin":
        await _grant_managed_department(
            admin_id=admin_id,
            department_id=await _get_user_department_id(owner_id),
        )
    admin_token = await _login(client, email=admin_email, password="password123")

    response = await client.get(
        f"/api/files/{file_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["id"] == file_id
    assert data["uploader_id"] == str(owner_id)

    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(AuditLog).where(AuditLog.action == "file.view_detail")
        )
        audit_logs = list(result.scalars())

    assert len(audit_logs) == 1
    assert audit_logs[0].actor_id == admin_id
    assert audit_logs[0].target_id == UUID(file_id)
    assert audit_logs[0].target_type == "file"


async def test_dept_admin_cannot_view_unmanaged_department_file_detail(
    document_client: tuple[AsyncClient, FakeDocumentStorage],
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.audit.models import AuditLog

    client, _storage = document_client
    _owner_id = await _create_user(
        email="detail-cross-dept-owner@company.com",
        password="password123",
    )
    owner_token = await _login(
        client,
        email="detail-cross-dept-owner@company.com",
        password="password123",
    )
    file_id = await _upload_pdf(client, token=owner_token, filename="cross-dept-view.pdf")
    admin_id = await _create_user(
        email="detail-cross-dept-admin@company.com",
        password="password123",
        role="dept_admin",
        department_code="document-tests-other",
        department_name="文档测试其他部门",
    )
    await _grant_managed_department(
        admin_id=admin_id,
        department_id=await _get_user_department_id(admin_id),
    )
    admin_token = await _login(
        client,
        email="detail-cross-dept-admin@company.com",
        password="password123",
    )

    response = await client.get(
        f"/api/files/{file_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 404
    assert response.json()["error_code"] == "FILE_NOT_FOUND"
    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(AuditLog).where(
                AuditLog.action == "file.view_detail",
                AuditLog.target_id == UUID(file_id),
            )
        )
        assert list(result.scalars()) == []


async def test_file_detail_returns_analysis_fields(
    document_client: tuple[AsyncClient, FakeDocumentStorage],
) -> None:
    from app.core.database import AsyncSessionFactory

    client, _storage = document_client
    await _create_user(email="analyst@company.com", password="password123")
    token = await _login(client, email="analyst@company.com", password="password123")

    upload_response = await client.post(
        "/api/files/upload",
        files={"file": ("test.pdf", PDF_BYTES, "application/pdf")},
        data={"visibility": "private", **UPLOAD_DRAFT_FORM},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert upload_response.status_code == 201
    file_id = upload_response.json()["data"]["id"]

    async with AsyncSessionFactory() as session:
        from app.modules.ai.models import DocumentAnalysis

        analysis = DocumentAnalysis(
            file_id=file_id,
            status="succeeded",
            summary="Test summary",
            sensitive_risk_level="low",
            quality_score=85,
            tables_json=[{"title": "Table 1", "markdown": "| A | B |"}],
            table_count=1,
            similar_file_ids=["some-file-id"],
            extracted_text="Sample extracted text for preview",
        )
        session.add(analysis)
        await session.commit()

    detail_response = await client.get(
        f"/api/files/{file_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert detail_response.status_code == 200
    data = detail_response.json()["data"]
    analysis_data = data["analysis"]
    assert analysis_data is not None
    assert analysis_data["quality_score"] == 85
    assert analysis_data["table_count"] == 1
    assert len(analysis_data["tables_json"]) == 1
    assert analysis_data["tables_json"][0]["title"] == "Table 1"
    assert analysis_data["similar_file_ids"] == ["some-file-id"]
    assert analysis_data["summary"] == "Test summary"
