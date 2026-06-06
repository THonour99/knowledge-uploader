from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from importlib import import_module
from uuid import UUID

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
) -> None:
    from app.core.config import Settings
    from app.core.deps import get_app_settings
    from app.main import app

    client, storage = document_client
    existing_settings = app.dependency_overrides[get_app_settings]()
    assert isinstance(existing_settings, Settings)
    app.dependency_overrides[get_app_settings] = lambda: existing_settings.model_copy(
        update={
            "upload_allowed_extensions": "pdf,txt,doc",
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
) -> None:
    client, storage = document_client
    await _create_user(email="large@company.com", password="password123")
    token = await _login(client, email="large@company.com", password="password123")

    response = await client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("large.txt", b"a" * 2048, "text/plain")},
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
