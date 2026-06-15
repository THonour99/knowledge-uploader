from __future__ import annotations

import os
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from importlib import import_module
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from redis.asyncio import from_url
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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


@dataclass
class StoredObject:
    bucket: str
    object_key: str
    data: bytes
    content_type: str


@dataclass
class FakeDocumentStorage:
    objects: list[StoredObject] = field(default_factory=list)
    deleted_objects: list[tuple[str, str]] = field(default_factory=list)

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
        data={"description": "员工手册", "visibility": "private"},
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
    assert audit_log.actor_id == user_id
    assert audit_log.target_id == saved_file.id
    assert audit_log.target_type == "file"
    assert audit_log.metadata_json["original_name"] == "Handbook.pdf"
    assert audit_log.metadata_json["size"] == len(PDF_BYTES)
    assert audit_log.metadata_json["duplicate"] is False


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


async def test_upload_save_draft_keeps_uploaded_and_disables_ai_pipeline(
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
    assert data["ai_analysis_enabled_at_upload"] is False

    async with AsyncSessionFactory() as session:
        saved_file = await session.get(File, file_id)
        event_result = await session.execute(
            select(EventOutbox).where(EventOutbox.aggregate_id == str(file_id))
        )
        outbox_event = event_result.scalar_one()

    assert saved_file is not None
    assert saved_file.status == "uploaded"
    assert saved_file.ai_analysis_enabled_at_upload is False
    assert outbox_event.event_type == "document.file.uploaded"
    assert outbox_event.payload["ai_analysis_enabled_at_upload"] is False


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
    )
    second = await client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("second.pdf", PDF_BYTES, "application/pdf")},
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
    )
    second = await client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {second_token}"},
        files={"file": ("second.pdf", PDF_BYTES, "application/pdf")},
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
    )
    second = await client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("second.txt", b"second text", "text/plain")},
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
    )
    other_upload = await client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {other_token}"},
        files={"file": ("other.pdf", PDF_BYTES, "application/pdf")},
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


@pytest.mark.parametrize("admin_role", ["knowledge_admin", "system_admin"])
async def test_admin_views_any_file_detail_and_writes_audit(
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
