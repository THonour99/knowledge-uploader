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

pytestmark = pytest.mark.asyncio

SetSystemConfig = Callable[[str, object], Awaitable[None]]
UPLOAD_DRAFT_FORM = {"submit_after_upload": "false"}


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
async def lifecycle_client() -> AsyncGenerator[tuple[AsyncClient, FakeDocumentStorage], None]:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.config import Settings
    from app.core.database import AsyncSessionFactory, get_session
    from app.core.deps import get_app_settings
    from app.main import app
    from app.modules.document.api import get_document_storage

    settings = Settings(
        allowed_email_domains="company.com",
        jwt_secret="test-jwt-secret-with-more-than-32-bytes",
        cache_redis_url=os.environ["CACHE_REDIS_URL"],
        require_email_verification=False,
        minio_bucket="test-knowledge-files",
        upload_max_file_size_bytes=4 * 1024 * 1024,
        upload_rate_limit_per_minute=50,
        upload_allowed_extensions="pdf,txt",
        upload_allowed_mime_types="application/pdf,text/plain",
        ai_analysis_enabled=True,
    )
    storage = FakeDocumentStorage()

    async def override_session() -> AsyncGenerator[AsyncSession, None]:
        async with AsyncSessionFactory() as session:
            yield session

    app.dependency_overrides[get_app_settings] = lambda: settings
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_document_storage] = lambda: storage

    transport = ASGITransport(app=app)  # type: ignore[arg-type]
    async with AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client, storage
    app.dependency_overrides.clear()


async def _create_user(*, email: str, password: str, role: str = "employee") -> UUID:
    from app.core.database import AsyncSessionFactory
    from app.core.security import hash_password
    from app.modules.department.models import Department
    from app.modules.user.models import User

    normalized_email = email.lower()
    async with AsyncSessionFactory() as session:
        department = (
            await session.execute(
                select(Department).where(Department.code == "document-lifecycle")
            )
        ).scalar_one_or_none()
        if department is None:
            department = Department(
                name="文档生命周期测试部",
                code="document-lifecycle",
                status="active",
            )
            session.add(department)
            await session.flush()
        user = User(
            name=email.split("@", 1)[0],
            email=normalized_email,
            email_domain=normalized_email.rsplit("@", 1)[1],
            password_hash=hash_password(password),
            department_id=department.id,
            department=department.name,
            role=role,
            status="active",
            email_verified=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user.id


async def _login(client: AsyncClient, *, email: str, password: str) -> str:
    response = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    return str(response.json()["data"]["access_token"])


async def _create_file_row(
    *,
    uploader_id: UUID,
    status: str,
    size: int = 1024,
    ragflow_document_id: str | None = None,
    ragflow_dataset_id: str | None = None,
    ragflow_parse_status: str | None = None,
    ai_enabled: bool = True,
) -> UUID:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File

    submitted_at = datetime.now(UTC) if status == "pending_review" else None
    file = File(
        original_name="lifecycle.txt",
        title="lifecycle.txt",
        stored_name="file-lifecycle.txt",
        extension="txt",
        mime_type="text/plain",
        size=size,
        hash=uuid4().hex + uuid4().hex,
        storage_type="minio",
        bucket="test-knowledge-files",
        object_key=f"uploads/{uploader_id}/{uuid4()}/file-lifecycle.txt",
        uploader_id=uploader_id,
        department="QA",
        visibility="private",
        description=None,
        tags=[],
        status=status,
        review_status="pending",
        submitted_at=submitted_at,
        review_due_at=(
            submitted_at + timedelta(hours=24) if submitted_at is not None else None
        ),
        ai_analysis_enabled_at_upload=ai_enabled,
        ragflow_document_id=ragflow_document_id,
        ragflow_dataset_id=ragflow_dataset_id,
        ragflow_parse_status=ragflow_parse_status,
    )
    async with AsyncSessionFactory() as session:
        session.add(file)
        await session.commit()
        await session.refresh(file)
        return file.id


async def _file_status(file_id: UUID) -> str:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File

    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        assert file is not None
        return file.status


async def _outbox_payloads(event_type: str) -> list[dict[str, object]]:
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox

    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(EventOutbox).where(EventOutbox.event_type == event_type)
        )
        return [dict(event.payload) for event in result.scalars()]


async def _audit_logs(action: str) -> list[tuple[UUID, UUID, dict[str, object]]]:
    from app.core.database import AsyncSessionFactory
    from app.modules.audit.models import AuditLog

    async with AsyncSessionFactory() as session:
        result = await session.execute(select(AuditLog).where(AuditLog.action == action))
        return [(log.actor_id, log.target_id, dict(log.metadata_json)) for log in result.scalars()]


async def _upload_txt(client: AsyncClient, *, token: str, filename: str, size: int = 64) -> str:
    response = await client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": (filename, b"a" * size, "text/plain")},
        data=UPLOAD_DRAFT_FORM,
    )
    assert response.status_code == 201
    return str(response.json()["data"]["id"])


# ---------------------------------------------------------------------------
# 删除
# ---------------------------------------------------------------------------


async def test_employee_deletes_own_file_when_config_allows(
    lifecycle_client: tuple[AsyncClient, FakeDocumentStorage],
    set_system_config: SetSystemConfig,
) -> None:
    client, storage = lifecycle_client
    await set_system_config("upload.allow_user_delete", True)
    user_id = await _create_user(email="deleter@company.com", password="password123")
    token = await _login(client, email="deleter@company.com", password="password123")
    file_id = await _upload_txt(client, token=token, filename="mine.txt")

    response = await client.delete(
        f"/api/files/{file_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["data"] == {}
    assert await _file_status(UUID(file_id)) == "deleted"
    # 软删: MinIO 对象保留
    assert storage.deleted_objects == []
    assert len(storage.objects) == 1
    list_response = await client.get("/api/files", headers={"Authorization": f"Bearer {token}"})
    assert list_response.status_code == 200
    assert file_id not in {item["id"] for item in list_response.json()["data"]["items"]}
    detail_response = await client.get(
        f"/api/files/{file_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert detail_response.status_code == 404

    payloads = await _outbox_payloads("document.file.deleted")
    assert payloads == [
        {
            "file_id": file_id,
            "ragflow_document_id": None,
            "ragflow_dataset_id": None,
            "delete_remote": False,
        }
    ]
    audit_logs = await _audit_logs("file.delete")
    assert len(audit_logs) == 1
    assert audit_logs[0][0] == user_id
    assert audit_logs[0][1] == UUID(file_id)


async def test_employee_delete_rejected_when_config_disabled(
    lifecycle_client: tuple[AsyncClient, FakeDocumentStorage],
) -> None:
    client, _storage = lifecycle_client
    await _create_user(email="forbidden@company.com", password="password123")
    token = await _login(client, email="forbidden@company.com", password="password123")
    file_id = await _upload_txt(client, token=token, filename="locked.txt")

    response = await client.delete(
        f"/api/files/{file_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert response.json()["error_code"] == "PERMISSION_DENIED"
    assert await _file_status(UUID(file_id)) == "uploaded"
    assert await _outbox_payloads("document.file.deleted") == []


async def test_employee_cannot_delete_others_file(
    lifecycle_client: tuple[AsyncClient, FakeDocumentStorage],
    set_system_config: SetSystemConfig,
) -> None:
    client, _storage = lifecycle_client
    await set_system_config("upload.allow_user_delete", True)
    owner_id = await _create_user(email="owner@company.com", password="password123")
    await _create_user(email="intruder@company.com", password="password123")
    intruder_token = await _login(client, email="intruder@company.com", password="password123")
    file_id = await _create_file_row(uploader_id=owner_id, status="uploaded")

    response = await client.delete(
        f"/api/files/{file_id}",
        headers={"Authorization": f"Bearer {intruder_token}"},
    )

    assert response.status_code == 404
    assert response.json()["error_code"] == "FILE_NOT_FOUND"
    assert await _file_status(file_id) == "uploaded"


async def test_admin_deletes_any_file_with_remote_decision_and_audit(
    lifecycle_client: tuple[AsyncClient, FakeDocumentStorage],
    set_system_config: SetSystemConfig,
) -> None:
    client, _storage = lifecycle_client
    await set_system_config("ragflow.delete_remote_on_file_delete", True)
    owner_id = await _create_user(email="staff@company.com", password="password123")
    admin_id = await _create_user(
        email="admin@company.com", password="password123", role="system_admin"
    )
    admin_token = await _login(client, email="admin@company.com", password="password123")
    file_id = await _create_file_row(
        uploader_id=owner_id,
        status="parsed",
        ragflow_document_id="rf-doc-1",
        ragflow_dataset_id="rf-ds-1",
    )

    response = await client.delete(
        f"/api/files/{file_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 200
    assert await _file_status(file_id) == "deleted"
    payloads = await _outbox_payloads("document.file.deleted")
    assert payloads == [
        {
            "file_id": str(file_id),
            "ragflow_document_id": "rf-doc-1",
            "ragflow_dataset_id": "rf-ds-1",
            "delete_remote": True,
        }
    ]
    audit_logs = await _audit_logs("file.delete")
    assert len(audit_logs) == 1
    assert audit_logs[0][0] == admin_id
    assert audit_logs[0][1] == file_id


async def test_delete_remote_is_false_when_file_never_synced(
    lifecycle_client: tuple[AsyncClient, FakeDocumentStorage],
    set_system_config: SetSystemConfig,
) -> None:
    client, _storage = lifecycle_client
    await set_system_config("ragflow.delete_remote_on_file_delete", True)
    owner_id = await _create_user(email="nosync@company.com", password="password123")
    await _create_user(email="admin2@company.com", password="password123", role="system_admin")
    admin_token = await _login(client, email="admin2@company.com", password="password123")
    file_id = await _create_file_row(uploader_id=owner_id, status="uploaded")

    response = await client.delete(
        f"/api/files/{file_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 200
    payloads = await _outbox_payloads("document.file.deleted")
    assert payloads[0]["delete_remote"] is False
    assert payloads[0]["ragflow_document_id"] is None


@pytest.mark.parametrize("operation", ["delete", "archive"])
async def test_unknown_remote_upload_outcome_blocks_destructive_file_action(
    lifecycle_client: tuple[AsyncClient, FakeDocumentStorage],
    operation: str,
) -> None:
    client, _storage = lifecycle_client
    owner_id = await _create_user(
        email=f"unknown-outcome-owner-{operation}@company.com",
        password="password123",
    )
    await _create_user(
        email=f"unknown-outcome-admin-{operation}@company.com",
        password="password123",
        role="system_admin",
    )
    admin_token = await _login(
        client,
        email=f"unknown-outcome-admin-{operation}@company.com",
        password="password123",
    )
    file_id = await _create_file_row(
        uploader_id=owner_id,
        status="failed",
        ragflow_document_id=None,
        ragflow_dataset_id="rf-unknown-outcome",
        ragflow_parse_status="UPLOADING",
    )

    if operation == "delete":
        response = await client.delete(
            f"/api/files/{file_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    else:
        response = await client.post(
            f"/api/admin/files/{file_id}/archive",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert response.status_code == 409
    assert response.json()["error_code"] == "VALIDATION_ERROR"
    assert "reconciliation completes" in response.json()["message"]
    assert await _file_status(file_id) == "failed"
    assert await _outbox_payloads("document.file.deleted") == []
    assert await _outbox_payloads("document.file.archived") == []
    assert await _audit_logs("file.delete") == []
    assert await _audit_logs("file.archive") == []


async def test_delete_rejects_mid_pipeline_status(
    lifecycle_client: tuple[AsyncClient, FakeDocumentStorage],
) -> None:
    client, _storage = lifecycle_client
    owner_id = await _create_user(email="syncing@company.com", password="password123")
    await _create_user(email="admin3@company.com", password="password123", role="system_admin")
    admin_token = await _login(client, email="admin3@company.com", password="password123")
    file_id = await _create_file_row(uploader_id=owner_id, status="syncing")

    response = await client.delete(
        f"/api/files/{file_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "VALIDATION_ERROR"
    assert await _file_status(file_id) == "syncing"


async def test_deleted_file_cannot_be_deleted_again(
    lifecycle_client: tuple[AsyncClient, FakeDocumentStorage],
) -> None:
    client, _storage = lifecycle_client
    owner_id = await _create_user(email="twice@company.com", password="password123")
    await _create_user(email="admin4@company.com", password="password123", role="system_admin")
    admin_token = await _login(client, email="admin4@company.com", password="password123")
    file_id = await _create_file_row(uploader_id=owner_id, status="deleted")

    response = await client.delete(
        f"/api/files/{file_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 404
    assert response.json()["error_code"] == "FILE_NOT_FOUND"


# ---------------------------------------------------------------------------
# 归档
# ---------------------------------------------------------------------------


async def test_admin_archives_approved_file_and_emits_event(
    lifecycle_client: tuple[AsyncClient, FakeDocumentStorage],
) -> None:
    client, _storage = lifecycle_client
    owner_id = await _create_user(email="archived@company.com", password="password123")
    admin_id = await _create_user(
        email="archiver@company.com", password="password123", role="system_admin"
    )
    admin_token = await _login(client, email="archiver@company.com", password="password123")
    file_id = await _create_file_row(
        uploader_id=owner_id,
        status="approved",
        ragflow_document_id="rf-doc-9",
    )

    response = await client.post(
        f"/api/admin/files/{file_id}/archive",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "disabled"
    assert await _file_status(file_id) == "disabled"
    payloads = await _outbox_payloads("document.file.archived")
    assert len(payloads) == 1
    payload = payloads[0]
    assert payload["file_id"] == str(file_id)
    assert payload["ragflow_document_id"] == "rf-doc-9"
    assert payload["keep_remote"] is True
    assert payload["actor_role"] == "system_admin"
    assert payload["scope_all_departments"] is True
    assert payload["actor_department_ids"] == []
    assert payload["file_department_id"] == "00000000-0000-0000-0000-000000000001"
    audit_logs = await _audit_logs("file.archive")
    assert len(audit_logs) == 1
    assert audit_logs[0][0] == admin_id


async def test_archive_respects_keep_remote_config(
    lifecycle_client: tuple[AsyncClient, FakeDocumentStorage],
    set_system_config: SetSystemConfig,
) -> None:
    client, _storage = lifecycle_client
    await set_system_config("ragflow.keep_remote_on_archive", False)
    owner_id = await _create_user(email="dropremote@company.com", password="password123")
    await _create_user(email="admin5@company.com", password="password123", role="system_admin")
    admin_token = await _login(client, email="admin5@company.com", password="password123")
    file_id = await _create_file_row(
        uploader_id=owner_id,
        status="parsed",
        ragflow_document_id="rf-doc-2",
    )

    response = await client.post(
        f"/api/admin/files/{file_id}/archive",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 200
    payloads = await _outbox_payloads("document.file.archived")
    assert payloads[0]["keep_remote"] is False


async def test_employee_cannot_archive_file(
    lifecycle_client: tuple[AsyncClient, FakeDocumentStorage],
) -> None:
    client, _storage = lifecycle_client
    owner_id = await _create_user(email="plain@company.com", password="password123")
    token = await _login(client, email="plain@company.com", password="password123")
    file_id = await _create_file_row(uploader_id=owner_id, status="approved")

    response = await client.post(
        f"/api/admin/files/{file_id}/archive",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert await _file_status(file_id) == "approved"


async def test_archive_rejects_invalid_source_status(
    lifecycle_client: tuple[AsyncClient, FakeDocumentStorage],
) -> None:
    client, _storage = lifecycle_client
    owner_id = await _create_user(email="fresh@company.com", password="password123")
    await _create_user(email="admin6@company.com", password="password123", role="system_admin")
    admin_token = await _login(client, email="admin6@company.com", password="password123")
    file_id = await _create_file_row(uploader_id=owner_id, status="uploaded")

    response = await client.post(
        f"/api/admin/files/{file_id}/archive",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 400
    assert await _file_status(file_id) == "uploaded"


async def test_disabled_file_can_be_deleted(
    lifecycle_client: tuple[AsyncClient, FakeDocumentStorage],
) -> None:
    client, _storage = lifecycle_client
    owner_id = await _create_user(email="staged@company.com", password="password123")
    await _create_user(email="admin7@company.com", password="password123", role="system_admin")
    admin_token = await _login(client, email="admin7@company.com", password="password123")
    file_id = await _create_file_row(uploader_id=owner_id, status="disabled")

    response = await client.delete(
        f"/api/files/{file_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 200
    assert await _file_status(file_id) == "deleted"


# ---------------------------------------------------------------------------
# 上传配额
# ---------------------------------------------------------------------------


async def test_upload_rejected_when_quota_exceeded_with_usage_details(
    lifecycle_client: tuple[AsyncClient, FakeDocumentStorage],
    set_system_config: SetSystemConfig,
) -> None:
    client, storage = lifecycle_client
    await set_system_config("upload.user_quota_mb", 1)
    user_id = await _create_user(email="quota@company.com", password="password123")
    token = await _login(client, email="quota@company.com", password="password123")
    await _create_file_row(uploader_id=user_id, status="uploaded", size=786432)

    response = await client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("big.txt", b"a" * 512000, "text/plain")},
        data=UPLOAD_DRAFT_FORM,
    )

    assert response.status_code == 400
    body = response.json()
    assert body["error_code"] == "FILE_QUOTA_EXCEEDED"
    assert "0.75" in body["message"]
    assert "1.00" in body["message"]
    assert "0.25" in body["message"]
    assert storage.objects == []


async def test_deleted_files_do_not_count_toward_quota(
    lifecycle_client: tuple[AsyncClient, FakeDocumentStorage],
    set_system_config: SetSystemConfig,
) -> None:
    client, _storage = lifecycle_client
    await set_system_config("upload.user_quota_mb", 1)
    user_id = await _create_user(email="reclaimed@company.com", password="password123")
    token = await _login(client, email="reclaimed@company.com", password="password123")
    await _create_file_row(uploader_id=user_id, status="deleted", size=786432)

    response = await client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("ok.txt", b"a" * 512000, "text/plain")},
        data=UPLOAD_DRAFT_FORM,
    )

    assert response.status_code == 201


async def test_cleanup_failed_deleted_files_do_not_count_toward_quota(
    lifecycle_client: tuple[AsyncClient, FakeDocumentStorage],
    set_system_config: SetSystemConfig,
) -> None:
    client, _storage = lifecycle_client
    await set_system_config("upload.user_quota_mb", 1)
    user_id = await _create_user(email="cleanup-reclaimed@company.com", password="password123")
    token = await _login(client, email="cleanup-reclaimed@company.com", password="password123")
    await _create_file_row(uploader_id=user_id, status="ragflow_cleanup_failed", size=786432)

    response = await client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("ok.txt", b"a" * 512000, "text/plain")},
        data=UPLOAD_DRAFT_FORM,
    )

    assert response.status_code == 201


async def test_quota_zero_means_unlimited(
    lifecycle_client: tuple[AsyncClient, FakeDocumentStorage],
    set_system_config: SetSystemConfig,
) -> None:
    client, _storage = lifecycle_client
    await set_system_config("upload.user_quota_mb", 0)
    user_id = await _create_user(email="nolimit@company.com", password="password123")
    token = await _login(client, email="nolimit@company.com", password="password123")
    await _create_file_row(uploader_id=user_id, status="uploaded", size=3 * 1024 * 1024)

    response = await client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("free.txt", b"a" * 512000, "text/plain")},
        data=UPLOAD_DRAFT_FORM,
    )

    assert response.status_code == 201


# ---------------------------------------------------------------------------
# 重新分析 / 重新解析
# ---------------------------------------------------------------------------


async def test_admin_reanalyze_failed_file_enqueues_event_and_audit(
    lifecycle_client: tuple[AsyncClient, FakeDocumentStorage],
) -> None:
    client, _storage = lifecycle_client
    owner_id = await _create_user(email="retry@company.com", password="password123")
    admin_id = await _create_user(
        email="admin8@company.com", password="password123", role="system_admin"
    )
    admin_token = await _login(client, email="admin8@company.com", password="password123")
    file_id = await _create_file_row(uploader_id=owner_id, status="analysis_failed")

    response = await client.post(
        f"/api/admin/files/{file_id}/reanalyze",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 200
    assert response.json()["data"] == {}
    assert await _file_status(file_id) == "analysis_queued"
    payloads = await _outbox_payloads("document.file.reanalyze_requested")
    assert payloads == [{"file_id": str(file_id)}]
    audit_logs = await _audit_logs("file.reanalyze")
    assert len(audit_logs) == 1
    assert audit_logs[0][0] == admin_id


async def test_reanalyze_resets_analyzed_file_to_analysis_queue(
    lifecycle_client: tuple[AsyncClient, FakeDocumentStorage],
) -> None:
    client, _storage = lifecycle_client
    owner_id = await _create_user(email="again@company.com", password="password123")
    await _create_user(email="admin9@company.com", password="password123", role="system_admin")
    admin_token = await _login(client, email="admin9@company.com", password="password123")
    file_id = await _create_file_row(uploader_id=owner_id, status="analyzed")

    response = await client.post(
        f"/api/admin/files/{file_id}/reanalyze",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 200
    assert await _file_status(file_id) == "analysis_queued"
    assert len(await _outbox_payloads("document.file.reanalyze_requested")) == 1


@pytest.mark.parametrize("stuck_status", ["extracting_text", "analysis_queued", "analyzing"])
async def test_reanalyze_recovers_stuck_intermediate_file(
    lifecycle_client: tuple[AsyncClient, FakeDocumentStorage],
    stuck_status: str,
) -> None:
    client, _storage = lifecycle_client
    owner_id = await _create_user(email="stuck@company.com", password="password123")
    await _create_user(email="admin10@company.com", password="password123", role="system_admin")
    admin_token = await _login(client, email="admin10@company.com", password="password123")
    file_id = await _create_file_row(uploader_id=owner_id, status=stuck_status)

    response = await client.post(
        f"/api/admin/files/{file_id}/reanalyze",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 200
    assert await _file_status(file_id) == "analysis_queued"
    assert len(await _outbox_payloads("document.file.reanalyze_requested")) == 1


async def test_reanalyze_rejects_invalid_source_status(
    lifecycle_client: tuple[AsyncClient, FakeDocumentStorage],
) -> None:
    client, _storage = lifecycle_client
    owner_id = await _create_user(email="invalid@company.com", password="password123")
    await _create_user(email="admin11@company.com", password="password123", role="system_admin")
    admin_token = await _login(client, email="admin11@company.com", password="password123")
    file_id = await _create_file_row(uploader_id=owner_id, status="uploaded")

    response = await client.post(
        f"/api/admin/files/{file_id}/reanalyze",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 400
    assert await _outbox_payloads("document.file.reanalyze_requested") == []


async def test_reanalyze_returns_409_when_ai_settings_disabled(
    lifecycle_client: tuple[AsyncClient, FakeDocumentStorage],
) -> None:
    from app.core.config import Settings
    from app.core.deps import get_app_settings
    from app.main import app

    client, _storage = lifecycle_client
    existing_settings = app.dependency_overrides[get_app_settings]()
    assert isinstance(existing_settings, Settings)
    app.dependency_overrides[get_app_settings] = lambda: existing_settings.model_copy(
        update={"ai_analysis_enabled": False}
    )
    owner_id = await _create_user(email="aioff@company.com", password="password123")
    await _create_user(email="admin12@company.com", password="password123", role="system_admin")
    admin_token = await _login(client, email="admin12@company.com", password="password123")
    file_id = await _create_file_row(uploader_id=owner_id, status="analysis_failed")

    response = await client.post(
        f"/api/admin/files/{file_id}/reanalyze",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 409
    assert await _outbox_payloads("document.file.reanalyze_requested") == []


async def test_reanalyze_returns_409_when_ai_feature_disabled_in_db(
    lifecycle_client: tuple[AsyncClient, FakeDocumentStorage],
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ai.models import AiFeatureConfig

    client, _storage = lifecycle_client
    async with AsyncSessionFactory() as session:
        session.add(AiFeatureConfig(feature_name="ai_analysis", enabled=False, config_json={}))
        await session.commit()
    owner_id = await _create_user(email="featureoff@company.com", password="password123")
    await _create_user(email="admin13@company.com", password="password123", role="system_admin")
    admin_token = await _login(client, email="admin13@company.com", password="password123")
    file_id = await _create_file_row(uploader_id=owner_id, status="analysis_failed")

    response = await client.post(
        f"/api/admin/files/{file_id}/reanalyze",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 409
    assert await _outbox_payloads("document.file.reanalyze_requested") == []


async def test_employee_cannot_reanalyze(
    lifecycle_client: tuple[AsyncClient, FakeDocumentStorage],
) -> None:
    client, _storage = lifecycle_client
    owner_id = await _create_user(email="worker@company.com", password="password123")
    token = await _login(client, email="worker@company.com", password="password123")
    file_id = await _create_file_row(uploader_id=owner_id, status="analysis_failed")

    response = await client.post(
        f"/api/admin/files/{file_id}/reanalyze",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403


async def test_reparse_reenters_analysis_queue_with_own_audit_action(
    lifecycle_client: tuple[AsyncClient, FakeDocumentStorage],
) -> None:
    client, _storage = lifecycle_client
    owner_id = await _create_user(email="reparse@company.com", password="password123")
    admin_id = await _create_user(
        email="admin14@company.com", password="password123", role="system_admin"
    )
    admin_token = await _login(client, email="admin14@company.com", password="password123")
    file_id = await _create_file_row(uploader_id=owner_id, status="analysis_failed")

    response = await client.post(
        f"/api/admin/files/{file_id}/reparse",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 200
    assert response.json()["data"] == {}
    payloads = await _outbox_payloads("document.file.reanalyze_requested")
    assert payloads == [{"file_id": str(file_id)}]
    audit_logs = await _audit_logs("file.reparse")
    assert len(audit_logs) == 1
    assert audit_logs[0][0] == admin_id


# ---------------------------------------------------------------------------
# R1 遗留自愈: 前置条件失效时补标 analysis_failed
# ---------------------------------------------------------------------------


async def _create_running_analysis(file_id: UUID) -> None:
    from datetime import UTC, datetime

    from app.core.database import AsyncSessionFactory
    from app.modules.ai.models import DocumentAnalysis

    analysis = DocumentAnalysis(
        file_id=file_id,
        status="running",
        extracted_text=None,
        summary=None,
        suggested_tags=[],
        sensitive_risk_level="none",
        sensitive_hits=[],
        error_message=None,
        started_at=datetime.now(UTC),
        finished_at=None,
    )
    async with AsyncSessionFactory() as session:
        session.add(analysis)
        await session.commit()


def _disabled_ai_settings() -> object:
    from app.core.config import Settings

    return Settings(
        allowed_email_domains="company.com",
        jwt_secret="test-jwt-secret-with-more-than-32-bytes",
        cache_redis_url=os.environ["CACHE_REDIS_URL"],
        require_email_verification=False,
        ai_analysis_enabled=False,
    )


async def test_hard_disabled_ai_recovers_stuck_intermediate_file_to_uploaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ai.models import DocumentAnalysis
    from app.modules.ai.tasks import run_ai_analyze_file_task_async

    user_id = await _create_user(email="healer@company.com", password="password123")
    file_id = await _create_file_row(uploader_id=user_id, status="extracting_text")
    await _create_running_analysis(file_id)
    settings = _disabled_ai_settings()
    monkeypatch.setattr("app.modules.ai.tasks.get_settings", lambda: settings)

    await run_ai_analyze_file_task_async(str(file_id))

    assert await _file_status(file_id) == "uploaded"
    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(DocumentAnalysis).where(DocumentAnalysis.file_id == file_id)
        )
        analysis = result.scalar_one()
    assert analysis.status == "failed"
    assert analysis.error_message is not None
    assert analysis.error_message == "AI analysis disabled by environment"


async def test_precondition_failure_keeps_non_intermediate_file_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ai.models import DocumentAnalysis
    from app.modules.ai.tasks import run_ai_analyze_file_task_async

    user_id = await _create_user(email="silent@company.com", password="password123")
    file_id = await _create_file_row(uploader_id=user_id, status="uploaded")
    settings = _disabled_ai_settings()
    monkeypatch.setattr("app.modules.ai.tasks.get_settings", lambda: settings)

    await run_ai_analyze_file_task_async(str(file_id))

    assert await _file_status(file_id) == "uploaded"
    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(DocumentAnalysis).where(DocumentAnalysis.file_id == file_id)
        )
        assert result.scalar_one_or_none() is None
