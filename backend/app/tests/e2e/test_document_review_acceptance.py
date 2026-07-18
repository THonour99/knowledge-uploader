"""Local DOC/REV acceptance probes over PostgreSQL and the ASGI API.

These checks deliberately exercise the authorization and review state boundaries
without a RAGFlow or LLM endpoint.  The file store is an in-memory protocol
implementation; PostgreSQL, Redis and all HTTP authorization paths are real.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from importlib import import_module
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient, Response
from redis.asyncio import from_url
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.tests.safety import require_safe_test_database_reset, require_safe_test_redis_reset

pytestmark = pytest.mark.asyncio

CONTENT = b"document review acceptance content"


@dataclass
class _ObjectStream:
    data: bytes
    closed: bool = False

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[bytes]:
        yield self.data

    async def aclose(self) -> None:
        self.closed = True


@dataclass
class MemoryDocumentStorage:
    objects: dict[tuple[str, str], bytes] = field(default_factory=dict)
    open_calls: list[tuple[str, str, int, int | None]] = field(default_factory=list)

    async def put_object(
        self,
        *,
        bucket: str,
        object_key: str,
        data: bytes,
        content_type: str,
    ) -> None:
        del content_type
        self.objects[(bucket, object_key)] = data

    async def delete_object(self, *, bucket: str, object_key: str) -> None:
        self.objects.pop((bucket, object_key), None)

    async def get_object(self, *, bucket: str, object_key: str) -> bytes:
        return self.objects[(bucket, object_key)]

    async def open_object(
        self,
        *,
        bucket: str,
        object_key: str,
        offset: int = 0,
        length: int | None = None,
    ) -> _ObjectStream:
        self.open_calls.append((bucket, object_key, offset, length))
        data = self.objects[(bucket, object_key)]
        return _ObjectStream(data[offset:] if length is None else data[offset : offset + length])


async def _reset_database() -> None:
    require_safe_test_database_reset()
    require_safe_test_redis_reset()
    import_module("app.db.models")

    from app.core.database import engine
    from app.db.base import Base

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()

    redis_client = from_url(  # type: ignore[no-untyped-call]
        os.environ["CACHE_REDIS_URL"], encoding="utf-8", decode_responses=True
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


@asynccontextmanager
async def _api_client(storage: MemoryDocumentStorage) -> AsyncIterator[AsyncClient]:
    from app.core.config import Settings
    from app.core.database import AsyncSessionFactory, get_session
    from app.core.deps import get_app_settings
    from app.main import app
    from app.modules.document.api import get_document_storage

    settings = Settings(
        allowed_email_domains="company.com",
        jwt_secret="document-review-acceptance-secret-over-32-bytes",
        cache_redis_url=os.environ["CACHE_REDIS_URL"],
        require_email_verification=False,
        minio_bucket="document-review-acceptance-files",
        upload_max_file_size_bytes=1024,
        upload_rate_limit_per_minute=100,
        upload_allowed_extensions="txt",
        upload_allowed_mime_types="text/plain",
        ai_analysis_enabled=False,
        ragflow_allowed_dataset_ids="document-review-acceptance-dataset",
    )

    async def override_session() -> AsyncGenerator[AsyncSession, None]:
        async with AsyncSessionFactory() as session:
            yield session

    app.dependency_overrides[get_app_settings] = lambda: settings
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_document_storage] = lambda: storage
    transport = ASGITransport(app=app)  # type: ignore[arg-type]
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client
    finally:
        app.dependency_overrides.clear()


async def _create_department(*, name: str, code: str) -> UUID:
    from app.core.database import AsyncSessionFactory
    from app.modules.department.models import Department

    async with AsyncSessionFactory() as session:
        department = Department(name=name, code=code, status="active")
        session.add(department)
        await session.commit()
        return department.id


async def _create_user(
    *, email: str, password: str, role: str, department_id: UUID, department_name: str
) -> UUID:
    from app.core.database import AsyncSessionFactory
    from app.core.security import hash_password
    from app.modules.user.models import User

    async with AsyncSessionFactory() as session:
        user = User(
            name=email.split("@", 1)[0],
            email=email,
            email_domain="company.com",
            password_hash=hash_password(password),
            department_id=department_id,
            department=department_name,
            role=role,
            status="active",
            email_verified=True,
        )
        session.add(user)
        await session.commit()
        return user.id


async def _grant_managed_department(*, user_id: UUID, department_id: UUID) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.department.models import UserManagedDepartment

    async with AsyncSessionFactory() as session:
        session.add(UserManagedDepartment(user_id=user_id, department_id=department_id))
        await session.commit()


async def _login(client: AsyncClient, *, email: str, password: str) -> str:
    response = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return str(response.json()["data"]["access_token"])


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _upload_and_submit(client: AsyncClient, *, token: str, name: str) -> UUID:
    uploaded = await client.post(
        "/api/files/upload",
        headers=_headers(token),
        files={"file": (name, CONTENT, "text/plain")},
        data={"submit_after_upload": "false", "visibility": "department"},
    )
    assert uploaded.status_code == 201, uploaded.text
    file_id = UUID(uploaded.json()["data"]["id"])
    submitted = await client.post(f"/api/files/{file_id}/submit-review", headers=_headers(token))
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["data"]["status"] == "pending_review"
    return file_id


async def _audit_actions(file_id: UUID) -> list[str]:
    from app.core.database import AsyncSessionFactory
    from app.modules.audit.models import AuditLog

    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(AuditLog.action)
            .where(AuditLog.target_id == file_id)
            .order_by(AuditLog.created_at)
        )
        return [str(action) for action in result.scalars()]


async def test_doc_001_manual_submission_with_ai_disabled_has_no_ai_state_or_task() -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.ai.models import AiUsageLog, DocumentAnalysis
    from app.modules.document.models import File
    from app.modules.ragflow.models import SyncTask

    storage = MemoryDocumentStorage()
    async with _api_client(storage) as client:
        department_id = await _create_department(name="文档验收部", code="doc-acceptance")
        await _create_user(
            email="doc-owner@company.com",
            password="password123",
            role="employee",
            department_id=department_id,
            department_name="文档验收部",
        )
        owner_token = await _login(client, email="doc-owner@company.com", password="password123")
        file_id = await _upload_and_submit(client, token=owner_token, name="manual.txt")

    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        assert file is not None
        assert file.ai_analysis_enabled_at_upload is False
        assert file.status == "pending_review"
        assert file.status not in {
            "extracting_text",
            "analysis_queued",
            "analyzing",
            "analysis_failed",
            "analyzed",
        }
        event_types = list(
            (
                await session.execute(
                    select(EventOutbox.event_type)
                    .where(EventOutbox.aggregate_id == str(file_id))
                    .order_by(EventOutbox.id)
                )
            ).scalars()
        )
        analysis_count = await session.scalar(
            select(func.count())
            .select_from(DocumentAnalysis)
            .where(DocumentAnalysis.file_id == file_id)
        )
        usage_count = await session.scalar(
            select(func.count())
            .select_from(AiUsageLog)
            .join(DocumentAnalysis, AiUsageLog.analysis_id == DocumentAnalysis.id)
            .where(DocumentAnalysis.file_id == file_id)
        )
        sync_task_count = await session.scalar(
            select(func.count()).select_from(SyncTask).where(SyncTask.file_id == file_id)
        )
    assert event_types == ["document.file.uploaded", "review.file.submitted"]
    assert all(not event_type.startswith("ai.") for event_type in event_types)
    assert analysis_count == 0
    assert usage_count == 0
    assert sync_task_count == 0
    assert await _audit_actions(file_id) == ["file.upload", "file.submit_review"]


async def test_doc_004_005_content_scope_range_and_admin_audit() -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.audit.models import AuditLog

    storage = MemoryDocumentStorage()
    async with _api_client(storage) as client:
        department_a = await _create_department(name="归属部门A", code="scope-a")
        department_b = await _create_department(name="越域部门B", code="scope-b")
        owner_id = await _create_user(
            email="content-owner@company.com",
            password="password123",
            role="employee",
            department_id=department_a,
            department_name="归属部门A",
        )
        same_admin_id = await _create_user(
            email="content-admin-a@company.com",
            password="password123",
            role="dept_admin",
            department_id=department_a,
            department_name="归属部门A",
        )
        other_admin_id = await _create_user(
            email="content-admin-b@company.com",
            password="password123",
            role="dept_admin",
            department_id=department_b,
            department_name="越域部门B",
        )
        await _grant_managed_department(user_id=same_admin_id, department_id=department_a)
        await _grant_managed_department(user_id=other_admin_id, department_id=department_b)
        owner_token = await _login(
            client, email="content-owner@company.com", password="password123"
        )
        same_admin_token = await _login(
            client, email="content-admin-a@company.com", password="password123"
        )
        other_admin_token = await _login(
            client, email="content-admin-b@company.com", password="password123"
        )

        uploaded = await client.post(
            "/api/files/upload",
            headers=_headers(owner_token),
            files={"file": ("content.txt", CONTENT, "text/plain")},
            data={"submit_after_upload": "false", "visibility": "department"},
        )
        assert uploaded.status_code == 201, uploaded.text
        file_id = UUID(uploaded.json()["data"]["id"])

        owner_inline = await client.get(
            f"/api/files/{file_id}/content", headers=_headers(owner_token)
        )
        assert owner_inline.status_code == 200
        assert owner_inline.content == CONTENT
        assert owner_inline.headers["content-disposition"].startswith("inline;")

        admin_download = await client.get(
            f"/api/files/{file_id}/content?disposition=attachment",
            headers={**_headers(same_admin_token), "Range": "bytes=3-10"},
        )
        assert admin_download.status_code == 206
        assert admin_download.content == CONTENT[3:11]
        assert admin_download.headers["content-range"] == f"bytes 3-10/{len(CONTENT)}"
        assert admin_download.headers["content-disposition"].startswith("attachment;")
        assert admin_download.headers["accept-ranges"] == "bytes"
        assert admin_download.headers["cache-control"] == "private, no-store"

        forbidden = await client.get(
            f"/api/files/{file_id}/content", headers=_headers(other_admin_token)
        )
        assert forbidden.status_code == 404
        assert forbidden.json()["error_code"] == "FILE_NOT_FOUND"

    assert len(storage.open_calls) == 2
    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(AuditLog)
            .where(AuditLog.target_id == file_id, AuditLog.action == "file.view_content")
            .order_by(AuditLog.created_at)
        )
        audits = list(result.scalars())
    assert len(audits) == 1
    assert audits[0].actor_id == same_admin_id
    assert audits[0].metadata_json["access_role"] == "administrator"
    assert audits[0].metadata_json["disposition"] == "attachment"
    assert owner_id != same_admin_id


async def test_rev_001_rejection_resubmission_and_approve_only_contract() -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.document.models import File

    storage = MemoryDocumentStorage()
    async with _api_client(storage) as client:
        department_id = await _create_department(name="审核闭环部", code="review-loop")
        await _create_user(
            email="review-owner@company.com",
            password="password123",
            role="employee",
            department_id=department_id,
            department_name="审核闭环部",
        )
        admin_id = await _create_user(
            email="review-admin@company.com",
            password="password123",
            role="dept_admin",
            department_id=department_id,
            department_name="审核闭环部",
        )
        await _grant_managed_department(user_id=admin_id, department_id=department_id)
        owner_token = await _login(client, email="review-owner@company.com", password="password123")
        admin_token = await _login(client, email="review-admin@company.com", password="password123")
        file_id = await _upload_and_submit(client, token=owner_token, name="resubmit.txt")
        claimed = await client.post(
            f"/api/review/files/{file_id}/claim", headers=_headers(admin_token)
        )
        assert claimed.status_code == 200, claimed.text
        rejected = await client.post(
            f"/api/files/{file_id}/reject",
            headers=_headers(admin_token),
            json={"reason": "请补充批准日期"},
        )
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["data"]["status"] == "rejected"

        rejected_version = int(rejected.json()["data"]["review_version"])
        revised = await client.patch(
            f"/api/files/{file_id}",
            headers=_headers(owner_token),
            json={
                "expected_version": rejected_version,
                "title": "已修订的审核材料",
                "description": "已补充批准日期",
                "visibility": "department",
            },
        )
        assert revised.status_code == 200, revised.text
        assert revised.json()["data"]["status"] == "rejected"
        assert revised.json()["data"]["title"] == "已修订的审核材料"
        assert revised.json()["data"]["description"] == "已补充批准日期"
        assert revised.json()["data"]["review_version"] == rejected_version + 1

        resubmitted = await client.post(
            f"/api/files/{file_id}/submit-review", headers=_headers(owner_token)
        )
        assert resubmitted.status_code == 200, resubmitted.text
        assert resubmitted.json()["data"]["status"] == "pending_review"
        claimed_again = await client.post(
            f"/api/review/files/{file_id}/claim", headers=_headers(admin_token)
        )
        assert claimed_again.status_code == 200, claimed_again.text

        invalid_sync = await client.post(
            f"/api/files/{file_id}/approve",
            headers=_headers(admin_token),
            json={"sync_decision": "sync"},
        )
        assert invalid_sync.status_code == 422
        assert invalid_sync.json()["message"] == "dataset mapping is required when sync is selected"
        invalid_approve_only = await client.post(
            f"/api/files/{file_id}/approve",
            headers=_headers(admin_token),
            json={"sync_decision": "approve_only", "dataset_mapping_id": str(file_id)},
        )
        assert invalid_approve_only.status_code == 422
        assert invalid_approve_only.json()["message"] == (
            "dataset mapping must not be provided when approve_only is selected"
        )
        approved = await client.post(
            f"/api/files/{file_id}/approve",
            headers=_headers(admin_token),
            json={"sync_decision": "approve_only", "reason": "本机验收通过"},
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["data"]["status"] == "approved"
        assert approved.json()["data"]["ragflow_dataset_id"] is None

    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        assert file is not None
        assert file.review_status == "approved"
        assert file.dataset_mapping_id is None
        assert file.ragflow_dataset_id is None
        events = list(
            (
                await session.execute(
                    select(EventOutbox)
                    .where(EventOutbox.aggregate_id == str(file_id))
                    .order_by(EventOutbox.id)
                )
            ).scalars()
        )
    assert [event.event_type for event in events] == [
        "document.file.uploaded",
        "review.file.submitted",
        "review.file.rejected",
        "review.file.submitted",
        "review.file.approved",
    ]
    rejected_event = next(event for event in events if event.event_type == "review.file.rejected")
    assert rejected_event.payload["file_id"] == str(file_id)
    assert rejected_event.payload["reason"] == "请补充批准日期"
    assert rejected_event.payload["status"] == "rejected"
    assert file.title == "已修订的审核材料"
    assert await _audit_actions(file_id) == [
        "file.upload",
        "file.submit_review",
        "file.review_claim",
        "file.reject",
        "file.update_draft",
        "file.submit_review",
        "file.review_claim",
        "file.approve",
    ]


async def test_rev_002_concurrent_two_admin_decisions_allow_one_and_keep_one_audit() -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox

    storage = MemoryDocumentStorage()
    async with _api_client(storage) as client:
        department_id = await _create_department(name="并发审核部", code="review-race")
        await _create_user(
            email="race-owner@company.com",
            password="password123",
            role="employee",
            department_id=department_id,
            department_name="并发审核部",
        )
        admin_a_id = await _create_user(
            email="race-admin-a@company.com",
            password="password123",
            role="dept_admin",
            department_id=department_id,
            department_name="并发审核部",
        )
        admin_b_id = await _create_user(
            email="race-admin-b@company.com",
            password="password123",
            role="dept_admin",
            department_id=department_id,
            department_name="并发审核部",
        )
        await _grant_managed_department(user_id=admin_a_id, department_id=department_id)
        await _grant_managed_department(user_id=admin_b_id, department_id=department_id)
        owner_token = await _login(client, email="race-owner@company.com", password="password123")
        admin_a_token = await _login(
            client, email="race-admin-a@company.com", password="password123"
        )
        admin_b_token = await _login(
            client, email="race-admin-b@company.com", password="password123"
        )
        file_id = await _upload_and_submit(client, token=owner_token, name="race.txt")

        async def claim(token: str) -> Response:
            return await client.post(f"/api/review/files/{file_id}/claim", headers=_headers(token))

        claim_a, claim_b = await asyncio.gather(claim(admin_a_token), claim(admin_b_token))
        claim_responses = [claim_a, claim_b]
        assert sorted(response.status_code for response in claim_responses) == [200, 409]
        winner_index = 0 if claim_a.status_code == 200 else 1
        winner_token = [admin_a_token, admin_b_token][winner_index]
        loser_token = [admin_a_token, admin_b_token][1 - winner_index]
        claim_loser = claim_responses[1 - winner_index]
        assert claim_loser.json()["error_code"] == "REVIEW_CLAIM_CONFLICT"
        assert str(file_id) not in claim_loser.text
        assert str([admin_a_id, admin_b_id][winner_index]) not in claim_loser.text

        async def decide(token: str) -> Response:
            return await client.post(
                f"/api/files/{file_id}/approve",
                headers=_headers(token),
                json={"sync_decision": "approve_only"},
            )

        first, second = await asyncio.gather(decide(winner_token), decide(loser_token))
        responses = [first, second]
        assert sorted(response.status_code for response in responses) == [200, 409]
        conflict = next(response for response in responses if response.status_code == 409)
        assert conflict.json()["error_code"] in {"REVIEW_CLAIM_REQUIRED", "REVIEW_ALREADY_DECIDED"}

    actions = await _audit_actions(file_id)
    assert actions.count("file.approve") == 1
    assert actions.count("file.review_claim") == 1
    async with AsyncSessionFactory() as session:
        events = list(
            (
                await session.execute(
                    select(EventOutbox.event_type)
                    .where(EventOutbox.aggregate_id == str(file_id))
                    .order_by(EventOutbox.id)
                )
            ).scalars()
        )
    assert events.count("review.file.approved") == 1
    assert all(not event.startswith("ragflow.") for event in events)


async def test_rev_001_sync_branch_records_explicit_decision_payload() -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.document.models import File

    storage = MemoryDocumentStorage()
    async with _api_client(storage) as client:
        department_id = await _create_department(name="同步分支部", code="sync-branch")
        await _create_user(
            email="sync-owner@company.com",
            password="password123",
            role="employee",
            department_id=department_id,
            department_name="同步分支部",
        )
        reviewer_id = await _create_user(
            email="sync-reviewer@company.com",
            password="password123",
            role="dept_admin",
            department_id=department_id,
            department_name="同步分支部",
        )
        await _create_user(
            email="sync-system@company.com",
            password="password123",
            role="system_admin",
            department_id=department_id,
            department_name="同步分支部",
        )
        await _grant_managed_department(user_id=reviewer_id, department_id=department_id)
        owner_token = await _login(client, email="sync-owner@company.com", password="password123")
        reviewer_token = await _login(
            client, email="sync-reviewer@company.com", password="password123"
        )
        system_token = await _login(client, email="sync-system@company.com", password="password123")
        category = await client.post(
            "/api/categories",
            headers=_headers(system_token),
            json={
                "name": "同步验收分类",
                "code": "sync-acceptance",
                "default_visibility": "department",
                "ai_analysis_enabled": False,
            },
        )
        assert category.status_code == 201, category.text
        category_id = str(category.json()["data"]["id"])
        mapping = await client.post(
            "/api/datasets",
            headers=_headers(system_token),
            json={
                "name": "同步验收数据集",
                "category_id": category_id,
                "ragflow_dataset_id": "document-review-acceptance-dataset",
                "ragflow_dataset_name": "Document Review Acceptance",
                "enabled": True,
            },
        )
        assert mapping.status_code == 201, mapping.text
        mapping_id = str(mapping.json()["data"]["id"])
        file_id = await _upload_and_submit(client, token=owner_token, name="sync.txt")
        claim = await client.post(
            f"/api/review/files/{file_id}/claim", headers=_headers(reviewer_token)
        )
        assert claim.status_code == 200, claim.text
        missing = await client.post(
            f"/api/files/{file_id}/approve",
            headers=_headers(reviewer_token),
            json={"sync_decision": "sync"},
        )
        assert missing.status_code == 422
        pending = await client.get(f"/api/files/{file_id}", headers=_headers(owner_token))
        assert pending.status_code == 200
        assert pending.json()["data"]["status"] == "pending_review"
        approved = await client.post(
            f"/api/files/{file_id}/approve",
            headers=_headers(reviewer_token),
            json={
                "sync_decision": "sync",
                "category_id": category_id,
                "dataset_mapping_id": mapping_id,
            },
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["data"]["status"] == "queued"
        assert approved.json()["data"]["ragflow_dataset_id"] == "document-review-acceptance-dataset"

    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        assert file is not None and file.status == "queued"
        event = (
            await session.execute(
                select(EventOutbox).where(
                    EventOutbox.aggregate_id == str(file_id),
                    EventOutbox.event_type == "review.file.approved",
                )
            )
        ).scalar_one()
    assert event.payload["sync_decision"] == "sync"
    assert event.payload["dataset_mapping_id"] == mapping_id
    assert event.payload["ragflow_dataset_id"] == "document-review-acceptance-dataset"


async def test_sec_001_scope_negative_responses_do_not_disclose_files_or_totals() -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.audit.models import AuditLog

    storage = MemoryDocumentStorage()
    async with _api_client(storage) as client:
        department_a = await _create_department(name="安全范围A", code="security-scope-a")
        department_b = await _create_department(name="安全范围B", code="security-scope-b")
        await _create_user(
            email="sec-owner@company.com",
            password="password123",
            role="employee",
            department_id=department_a,
            department_name="安全范围A",
        )
        await _create_user(
            email="sec-peer@company.com",
            password="password123",
            role="employee",
            department_id=department_a,
            department_name="安全范围A",
        )
        same_admin_id = await _create_user(
            email="sec-admin-a@company.com",
            password="password123",
            role="dept_admin",
            department_id=department_a,
            department_name="安全范围A",
        )
        await _create_user(
            email="sec-admin-b@company.com",
            password="password123",
            role="dept_admin",
            department_id=department_b,
            department_name="安全范围B",
        )
        await _grant_managed_department(user_id=same_admin_id, department_id=department_a)
        owner_token = await _login(client, email="sec-owner@company.com", password="password123")
        peer_token = await _login(client, email="sec-peer@company.com", password="password123")
        same_admin_token = await _login(
            client, email="sec-admin-a@company.com", password="password123"
        )
        other_admin_token = await _login(
            client, email="sec-admin-b@company.com", password="password123"
        )
        file_id = await _upload_and_submit(client, token=owner_token, name="scope-secret.txt")
        random_id = uuid4()
        for token in (peer_token, same_admin_token, other_admin_token):
            random_detail = await client.get(f"/api/files/{random_id}", headers=_headers(token))
            random_content = await client.get(
                f"/api/files/{random_id}/content", headers=_headers(token)
            )
            assert random_detail.status_code == random_content.status_code == 404
        for token in (peer_token, other_admin_token):
            detail = await client.get(f"/api/files/{file_id}", headers=_headers(token))
            content = await client.get(f"/api/files/{file_id}/content", headers=_headers(token))
            assert detail.status_code == content.status_code == 404
        same_detail = await client.get(f"/api/files/{file_id}", headers=_headers(same_admin_token))
        assert same_detail.status_code == 200
        same_content = await client.get(
            f"/api/files/{file_id}/content", headers=_headers(same_admin_token)
        )
        assert same_content.status_code == 200
        peer_list = await client.get(
            "/api/files?q=scope-secret&page=1&page_size=1", headers=_headers(peer_token)
        )
        assert peer_list.status_code == 200 and peer_list.json()["data"]["total"] == 0
        scoped_list = await client.get(
            "/api/review/files?q=scope-secret&page=1&page_size=1",
            headers=_headers(same_admin_token),
        )
        hidden_list = await client.get(
            "/api/review/files?q=scope-secret&page=1&page_size=1",
            headers=_headers(other_admin_token),
        )
        assert scoped_list.status_code == hidden_list.status_code == 200
        assert scoped_list.json()["data"]["total"] == 1
        assert hidden_list.json()["data"]["total"] == 0
        forbidden_claim = await client.post(
            f"/api/review/files/{file_id}/claim", headers=_headers(other_admin_token)
        )
        assert forbidden_claim.status_code == 404

    async with AsyncSessionFactory() as session:
        audits = list(
            (
                await session.execute(
                    select(AuditLog)
                    .where(AuditLog.target_id == file_id)
                    .order_by(AuditLog.created_at)
                )
            ).scalars()
        )
    assert any(
        audit.action == "file.view_content" and audit.actor_id == same_admin_id for audit in audits
    )
