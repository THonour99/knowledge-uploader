from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from importlib import import_module

import pytest
from httpx import ASGITransport, AsyncClient
from redis.asyncio import from_url
from sqlalchemy import select

pytestmark = pytest.mark.asyncio


async def _reset_database() -> None:
    import_module("app.db.models")
    import_module("app.modules.notification.models")

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


async def _create_department(name: str) -> uuid.UUID:
    from app.core.database import AsyncSessionFactory
    from app.modules.department.models import Department

    department = Department(name=name, code=f"dept-{uuid.uuid4().hex[:8]}")
    async with AsyncSessionFactory() as session:
        session.add(department)
        await session.commit()
        await session.refresh(department)
        return department.id


async def _create_user(
    email: str,
    *,
    role: str = "employee",
    department_id: uuid.UUID | None = None,
) -> uuid.UUID:
    from app.core.database import AsyncSessionFactory
    from app.core.security import hash_password
    from app.modules.department.models import UNASSIGNED_DEPARTMENT_ID
    from app.modules.user.models import User

    user = User(
        name="Notify User",
        email=email,
        email_domain=email.rsplit("@", 1)[1],
        password_hash=hash_password("password123"),
        department_id=department_id or UNASSIGNED_DEPARTMENT_ID,
        role=role,
        status="active",
        email_verified=True,
    )
    async with AsyncSessionFactory() as session:
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user.id


async def _assign_managed_department(
    *,
    user_id: uuid.UUID,
    department_id: uuid.UUID,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.department.models import UserManagedDepartment

    async with AsyncSessionFactory() as session:
        session.add(
            UserManagedDepartment(
                user_id=user_id,
                department_id=department_id,
            )
        )
        await session.commit()


async def _create_file(
    *,
    uploader_id: uuid.UUID,
    department_id: uuid.UUID,
    name: str,
    expires_at: datetime | None = None,
    owner_id: uuid.UUID | None = None,
) -> uuid.UUID:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File

    submitted_at = datetime.now(UTC)
    file = File(
        original_name=name,
        title=name,
        stored_name=f"{uuid.uuid4()}.pdf",
        extension="pdf",
        mime_type="application/pdf",
        size=1,
        hash=uuid.uuid4().hex * 2,
        storage_type="minio",
        bucket="knowledge-files",
        object_key=f"test/{uuid.uuid4()}.pdf",
        uploader_id=uploader_id,
        department_id=department_id,
        owner_id=owner_id or uploader_id,
        status="pending_review",
        review_status="pending",
        submitted_at=submitted_at,
        review_due_at=submitted_at + timedelta(hours=24),
        expires_at=expires_at,
        expiry_status="expiring" if expires_at is not None else "never",
    )
    async with AsyncSessionFactory() as session:
        session.add(file)
        await session.commit()
        await session.refresh(file)
        return file.id


async def _create_sync_task(file_id: uuid.UUID) -> uuid.UUID:
    from app.core.database import AsyncSessionFactory
    from app.modules.ragflow.models import SyncTask

    task = SyncTask(
        file_id=file_id,
        task_type="ragflow_upload",
        status="succeeded",
    )
    async with AsyncSessionFactory() as session:
        session.add(task)
        await session.commit()
        await session.refresh(task)
        return task.id


async def _create_source_event(
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    payload: dict[str, object] | None = None,
) -> int:
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import OutboxRepository

    async with AsyncSessionFactory() as session:
        event = await OutboxRepository(session).append(
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=str(aggregate_id),
            payload=payload or {},
        )
        await session.commit()
        await session.refresh(event)
        return event.id


def _expiry_payload(expires_at: datetime, *, kind: str = "warning") -> dict[str, object]:
    return {
        "expected_expires_at": expires_at.isoformat(),
        "notification_kind": kind,
    }


async def _process_source_event(event_id: int) -> int:
    from app.core.database import AsyncSessionFactory
    from app.modules.notification.handlers import handle_source_event_id

    async with AsyncSessionFactory() as session:
        return await handle_source_event_id(event_id, session=session)


async def test_in_app_notification_listing_and_reads_are_channel_and_user_scoped() -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.notification.repository import NotificationRepository  # noqa: TID251
    from app.modules.notification.service import (  # noqa: TID251
        NotificationPage,
        NotificationService,
    )

    user_id = await _create_user("notify@company.com")
    other_user_id = await _create_user("other@company.com")
    file_id = uuid.uuid4()

    async with AsyncSessionFactory() as session:
        repository = NotificationRepository(session)
        service = NotificationService(session=session, repository=repository)
        created = await service.create_in_app(
            user_id=user_id,
            type="review_approved",
            title="文件审核通过",
            body="文件已通过审核",
            metadata={"resource_type": "file", "resource_id": str(file_id)},
        )
        await repository.create(
            user_id=user_id,
            type="review_approved",
            title="邮件副本",
            body="邮件副本不能出现在站内列表",
            channel="email",
        )
        await session.commit()

        page = await service.list_user_notifications(
            user_id=user_id,
            page=NotificationPage(page=1, page_size=20),
        )
        assert page.total == 1
        assert page.unread_count == 1
        assert page.items[0].id == created.id
        assert page.items[0].metadata == {
            "resource_type": "file",
            "resource_id": str(file_id),
        }

        assert (
            await service.mark_read(
                notification_id=created.id,
                user_id=other_user_id,
            )
            is None
        )
        marked = await service.mark_read(notification_id=created.id, user_id=user_id)
        assert marked is not None
        assert marked.read_at is not None
        assert await service.mark_all_read(user_id=user_id) == 0


async def test_legacy_metadata_is_fail_safe_and_never_forwards_urls_or_unknown_keys() -> None:
    from app.core.database import AsyncSessionFactory
    from app.main import app
    from app.modules.notification.models import Notification
    from app.modules.notification.repository import NotificationRepository  # noqa: TID251
    from app.modules.notification.service import (  # noqa: TID251
        NotificationPage,
        NotificationService,
    )

    user_id = await _create_user("legacy@company.com")
    file_id = uuid.uuid4()
    async with AsyncSessionFactory() as session:
        session.add_all(
            [
                Notification(
                    user_id=user_id,
                    type="legacy_file",
                    channel="in_app",
                    title="旧文件通知",
                    body="旧数据",
                    metadata_json={
                        "file_id": str(file_id),
                        "review_status": "approved",
                        "idempotency_key": "legacy-secret",
                        "url": "https://evil.example/steal",
                        "path": "../../settings",
                    },
                    delivery_status="not_applicable",
                ),
                Notification(
                    user_id=user_id,
                    type="malformed",
                    channel="in_app",
                    title="恶意结构",
                    body="旧数据",
                    metadata_json={
                        "resource_type": "user",
                        "resource_id": str(file_id),
                        "file_id": str(file_id),
                        "status": "pending",
                        "url": "https://evil.example/steal",
                    },
                    delivery_status="not_applicable",
                ),
            ]
        )
        await session.commit()

        page = await NotificationService(
            session=session,
            repository=NotificationRepository(session),
        ).list_user_notifications(
            user_id=user_id,
            page=NotificationPage(page=1, page_size=20),
        )

    metadata_by_type = {item.type: item.metadata for item in page.items}
    assert metadata_by_type["legacy_file"] == {
        "resource_type": "file",
        "resource_id": str(file_id),
        "status": "approved",
    }
    assert metadata_by_type["malformed"] == {"status": "pending"}
    assert "evil.example" not in str(metadata_by_type)
    assert "idempotency_key" not in str(metadata_by_type)

    transport = ASGITransport(app=app)  # type: ignore[arg-type]
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        login_response = await client.post(
            "/api/auth/login",
            json={"email": "legacy@company.com", "password": "password123"},
        )
        assert login_response.status_code == 200
        token = str(login_response.json()["data"]["access_token"])
        api_response = await client.get(
            "/api/notifications",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert api_response.status_code == 200
    api_metadata_by_type = {
        item["type"]: item["metadata"] for item in api_response.json()["data"]["items"]
    }
    assert api_metadata_by_type == metadata_by_type
    assert "evil.example" not in api_response.text
    assert "idempotency_key" not in api_response.text


async def test_concurrent_replay_creates_one_pair_and_one_id_only_email_outbox() -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.notification import events
    from app.modules.notification.models import Notification
    from app.modules.review.events import ReviewFileRejected

    department_id = await _create_department("Policy")
    user_id = await _create_user(
        "review-target@company.com",
        department_id=department_id,
    )
    file_id = await _create_file(
        uploader_id=user_id,
        department_id=department_id,
        name="policy.pdf",
    )
    event_id = await _create_source_event(
        event_type=ReviewFileRejected.ROUTING_KEY,
        aggregate_type="file",
        aggregate_id=file_id,
        payload={
            "file_id": str(file_id),
            "reason": "内容不合规 reviewer@company.test",
            "error_message": "must-not-enter-email-request",
        },
    )

    results = await asyncio.gather(*(_process_source_event(event_id) for _ in range(8)))

    async with AsyncSessionFactory() as session:
        notifications = list(
            (
                await session.execute(
                    select(Notification)
                    .where(Notification.source_event_id == event_id)
                    .order_by(Notification.channel)
                )
            ).scalars()
        )
        email_requests = list(
            (
                await session.execute(
                    select(EventOutbox).where(
                        EventOutbox.event_type == events.NOTIFICATION_EMAIL_REQUESTED
                    )
                )
            ).scalars()
        )

    assert sorted(results) == [0, 0, 0, 0, 0, 0, 0, 1]
    assert [item.channel for item in notifications] == ["email", "in_app"]
    assert all(item.user_id == user_id for item in notifications)
    assert all("内容不合规" in item.body for item in notifications)
    assert len(email_requests) == 1
    email_notification = next(item for item in notifications if item.channel == "email")
    assert email_requests[0].payload == {"notification_id": str(email_notification.id)}
    assert "reviewer@company.test" not in str(email_requests[0].payload)
    assert "must-not-enter-email-request" not in str(email_requests[0].payload)


async def test_review_submission_notifies_only_active_department_admin_scope() -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.notification.models import Notification
    from app.modules.review.events import ReviewFileSubmitted

    department_id = await _create_department("Knowledge")
    other_department_id = await _create_department("Other")
    uploader_id = await _create_user(
        "uploader@company.com",
        department_id=department_id,
    )
    direct_admin_id = await _create_user(
        "direct-admin@company.com",
        role="dept_admin",
        department_id=department_id,
    )
    managed_admin_id = await _create_user(
        "managed-admin@company.com",
        role="dept_admin",
        department_id=other_department_id,
    )
    other_admin_id = await _create_user(
        "other-admin@company.com",
        role="dept_admin",
        department_id=other_department_id,
    )
    await _assign_managed_department(
        user_id=managed_admin_id,
        department_id=department_id,
    )
    file_id = await _create_file(
        uploader_id=uploader_id,
        department_id=department_id,
        name="handbook.pdf",
    )
    event_id = await _create_source_event(
        event_type=ReviewFileSubmitted.ROUTING_KEY,
        aggregate_type="file",
        aggregate_id=file_id,
    )

    assert await _process_source_event(event_id) == 2

    async with AsyncSessionFactory() as session:
        recipients = set(
            (
                await session.execute(
                    select(Notification.user_id).where(
                        Notification.source_event_id == event_id,
                        Notification.channel == "in_app",
                    )
                )
            ).scalars()
        )
    assert recipients == {direct_admin_id, managed_admin_id}
    assert uploader_id not in recipients
    assert other_admin_id not in recipients


async def test_expiry_uses_explicit_uploader_fallback_and_copies_department_admins() -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.notification import events
    from app.modules.notification.models import Notification
    from app.modules.user.models import User

    department_id = await _create_department("Expiry")
    moved_department_id = await _create_department("Expiry uploader moved")
    uploader_id = await _create_user(
        "owner-fallback@company.com",
        department_id=department_id,
    )
    admin_id = await _create_user(
        "expiry-admin@company.com",
        role="dept_admin",
        department_id=department_id,
    )
    expires_at = datetime.now(UTC) + timedelta(days=7)
    file_id = await _create_file(
        uploader_id=uploader_id,
        department_id=department_id,
        name="standard.pdf",
        expires_at=expires_at,
    )
    event_id = await _create_source_event(
        event_type=events.DOCUMENT_FILE_EXPIRING,
        aggregate_type="file",
        aggregate_id=file_id,
        payload=_expiry_payload(expires_at),
    )
    async with AsyncSessionFactory() as session:
        uploader = await session.get(User, uploader_id)
        assert uploader is not None
        uploader.department_id = moved_department_id
        await session.commit()

    assert await _process_source_event(event_id) == 2

    async with AsyncSessionFactory() as session:
        rows = list(
            (
                await session.execute(
                    select(Notification).where(
                        Notification.source_event_id == event_id,
                        Notification.channel == "in_app",
                    )
                )
            ).scalars()
        )
    assert {row.user_id for row in rows} == {uploader_id, admin_id}
    assert all(row.metadata_json["resource_type"] == "file" for row in rows)
    assert all(row.metadata_json["expiry_status"] == "expiring" for row in rows)


async def test_expiry_prefers_active_owner_falls_back_and_deduplicates_admin() -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.notification import events
    from app.modules.notification.models import Notification
    from app.modules.user.models import User

    department_id = await _create_department("Expiry ownership")
    moved_department_id = await _create_department("Expiry owner moved")
    uploader_id = await _create_user(
        "expiry-source@company.com",
        department_id=department_id,
    )
    fallback_uploader_id = await _create_user(
        "expiry-fallback@company.com",
        department_id=department_id,
    )
    owner_id = await _create_user(
        "expiry-primary-owner@company.com",
        department_id=department_id,
    )
    moved_uploader_id = await _create_user(
        "expiry-moved-owner-uploader@company.com",
        department_id=department_id,
    )
    moved_owner_id = await _create_user(
        "expiry-moved-owner@company.com",
        department_id=department_id,
    )
    invalid_owner_id = await _create_user(
        "expiry-invalid-owner@company.com",
        department_id=department_id,
    )
    admin_id = await _create_user(
        "expiry-owner-admin@company.com",
        role="dept_admin",
        department_id=department_id,
    )
    async with AsyncSessionFactory() as session:
        invalid_owner = await session.get(User, invalid_owner_id)
        assert invalid_owner is not None
        invalid_owner.email_verified = False
        await session.commit()

    expires_at = datetime.now(UTC) + timedelta(days=3)
    owned_file_id = await _create_file(
        uploader_id=uploader_id,
        owner_id=owner_id,
        department_id=department_id,
        name="owned-expiry.pdf",
        expires_at=expires_at,
    )
    fallback_file_id = await _create_file(
        uploader_id=fallback_uploader_id,
        owner_id=invalid_owner_id,
        department_id=department_id,
        name="fallback-expiry.pdf",
        expires_at=expires_at,
    )
    admin_owned_file_id = await _create_file(
        uploader_id=uploader_id,
        owner_id=admin_id,
        department_id=department_id,
        name="admin-owned-expiry.pdf",
        expires_at=expires_at,
    )
    moved_owner_file_id = await _create_file(
        uploader_id=moved_uploader_id,
        owner_id=moved_owner_id,
        department_id=department_id,
        name="moved-owner-expiry.pdf",
        expires_at=expires_at,
    )
    owned_event_id = await _create_source_event(
        event_type=events.DOCUMENT_FILE_EXPIRING,
        aggregate_type="file",
        aggregate_id=owned_file_id,
        payload=_expiry_payload(expires_at),
    )
    fallback_event_id = await _create_source_event(
        event_type=events.DOCUMENT_FILE_EXPIRING,
        aggregate_type="file",
        aggregate_id=fallback_file_id,
        payload=_expiry_payload(expires_at),
    )
    admin_owned_event_id = await _create_source_event(
        event_type=events.DOCUMENT_FILE_EXPIRING,
        aggregate_type="file",
        aggregate_id=admin_owned_file_id,
        payload=_expiry_payload(expires_at),
    )
    moved_owner_event_id = await _create_source_event(
        event_type=events.DOCUMENT_FILE_EXPIRING,
        aggregate_type="file",
        aggregate_id=moved_owner_file_id,
        payload=_expiry_payload(expires_at),
    )
    async with AsyncSessionFactory() as session:
        moved_owner = await session.get(User, moved_owner_id)
        assert moved_owner is not None
        moved_owner.department_id = moved_department_id
        await session.commit()

    assert await _process_source_event(owned_event_id) == 2
    assert await _process_source_event(fallback_event_id) == 2
    assert await _process_source_event(admin_owned_event_id) == 1
    assert await _process_source_event(moved_owner_event_id) == 2

    async with AsyncSessionFactory() as session:
        rows = list(
            (
                await session.execute(
                    select(Notification.source_event_id, Notification.user_id).where(
                        Notification.source_event_id.in_(
                            {
                                owned_event_id,
                                fallback_event_id,
                                admin_owned_event_id,
                                moved_owner_event_id,
                            }
                        ),
                        Notification.channel == "in_app",
                    )
                )
            ).all()
        )
    recipients: dict[int, set[uuid.UUID]] = {}
    for source_event_id, user_id in rows:
        recipients.setdefault(source_event_id, set()).add(user_id)
    assert recipients[owned_event_id] == {owner_id, admin_id}
    assert uploader_id not in recipients[owned_event_id]
    assert recipients[fallback_event_id] == {fallback_uploader_id, admin_id}
    assert invalid_owner_id not in recipients[fallback_event_id]
    assert recipients[admin_owned_event_id] == {admin_id}
    assert recipients[moved_owner_event_id] == {moved_uploader_id, admin_id}
    assert moved_owner_id not in recipients[moved_owner_event_id]


async def test_expiry_event_snapshot_skips_delayed_patch_archive_and_historical_rows() -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File
    from app.modules.notification import events
    from app.modules.notification.models import Notification

    department_id = await _create_department("Expiry event CAS")
    uploader_id = await _create_user(
        "expiry-event-cas@company.com",
        department_id=department_id,
    )
    original_expires_at = datetime.now(UTC) + timedelta(days=3)
    patched_expires_at = original_expires_at + timedelta(days=1)
    patched_file_id = await _create_file(
        uploader_id=uploader_id,
        department_id=department_id,
        name="patched-expiry.pdf",
        expires_at=original_expires_at,
    )
    archived_file_id = await _create_file(
        uploader_id=uploader_id,
        department_id=department_id,
        name="archived-expiry.pdf",
        expires_at=original_expires_at,
    )
    historical_file_id = await _create_file(
        uploader_id=uploader_id,
        department_id=department_id,
        name="historical-expiry.pdf",
        expires_at=original_expires_at,
    )
    stale_event_id = await _create_source_event(
        event_type=events.DOCUMENT_FILE_EXPIRING,
        aggregate_type="file",
        aggregate_id=patched_file_id,
        payload=_expiry_payload(original_expires_at),
    )
    archived_event_id = await _create_source_event(
        event_type=events.DOCUMENT_FILE_EXPIRING,
        aggregate_type="file",
        aggregate_id=archived_file_id,
        payload=_expiry_payload(original_expires_at),
    )
    historical_event_id = await _create_source_event(
        event_type=events.DOCUMENT_FILE_EXPIRING,
        aggregate_type="file",
        aggregate_id=historical_file_id,
        payload=_expiry_payload(original_expires_at),
    )
    malformed_event_id = await _create_source_event(
        event_type=events.DOCUMENT_FILE_EXPIRING,
        aggregate_type="file",
        aggregate_id=patched_file_id,
        payload={},
    )

    async with AsyncSessionFactory() as session:
        patched = await session.get(File, patched_file_id)
        archived = await session.get(File, archived_file_id)
        historical = await session.get(File, historical_file_id)
        assert patched is not None and archived is not None and historical is not None
        patched.expires_at = patched_expires_at
        patched.expiry_status = "expiring"
        patched.expiry_warning_sent_at = None
        archived.status = "disabled"
        historical.is_current_version = False
        await session.commit()

    assert await _process_source_event(stale_event_id) == 0
    assert await _process_source_event(archived_event_id) == 0
    assert await _process_source_event(historical_event_id) == 0
    assert await _process_source_event(malformed_event_id) == 0

    fresh_event_id = await _create_source_event(
        event_type=events.DOCUMENT_FILE_EXPIRING,
        aggregate_type="file",
        aggregate_id=patched_file_id,
        payload=_expiry_payload(patched_expires_at),
    )
    assert await _process_source_event(fresh_event_id) == 1
    assert await _process_source_event(fresh_event_id) == 0

    async with AsyncSessionFactory() as session:
        notifications = list(
            (
                await session.execute(
                    select(Notification).where(
                        Notification.source_event_id.in_(
                            {
                                stale_event_id,
                                archived_event_id,
                                historical_event_id,
                                malformed_event_id,
                                fresh_event_id,
                            }
                        ),
                        Notification.channel == "in_app",
                    )
                )
            ).scalars()
        )
    assert len(notifications) == 1
    assert notifications[0].source_event_id == fresh_event_id


async def test_ragflow_and_ai_results_notify_uploader_without_raw_error() -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ai.events import AiFileAnalysisFailed
    from app.modules.notification.models import Notification
    from app.modules.ragflow.events import RagflowSyncTaskFailed

    department_id = await _create_department("Automation")
    uploader_id = await _create_user(
        "automation@company.com",
        department_id=department_id,
    )
    file_id = await _create_file(
        uploader_id=uploader_id,
        department_id=department_id,
        name="automation.pdf",
    )
    sync_task_id = await _create_sync_task(file_id)
    ragflow_event_id = await _create_source_event(
        event_type=RagflowSyncTaskFailed.ROUTING_KEY,
        aggregate_type="sync_task",
        aggregate_id=sync_task_id,
        payload={"error_message": "https://secret.example/?key=credential"},
    )
    ai_event_id = await _create_source_event(
        event_type=AiFileAnalysisFailed.ROUTING_KEY,
        aggregate_type="file",
        aggregate_id=file_id,
        payload={"error_message": "provider raw response"},
    )

    assert await _process_source_event(ragflow_event_id) == 1
    assert await _process_source_event(ai_event_id) == 1

    async with AsyncSessionFactory() as session:
        rows = list(
            (
                await session.execute(
                    select(Notification).where(
                        Notification.source_event_id.in_([ragflow_event_id, ai_event_id]),
                        Notification.channel == "in_app",
                    )
                )
            ).scalars()
        )
    assert {row.user_id for row in rows} == {uploader_id}
    rendered = " ".join(row.body for row in rows)
    assert "secret.example" not in rendered
    assert "credential" not in rendered
    assert "provider raw response" not in rendered


async def test_persisted_email_loads_recipient_from_db_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.notification.models import Notification
    from app.modules.notification.tasks import _send_persisted_email
    from app.modules.review.events import ReviewFileApproved

    department_id = await _create_department("Email")
    uploader_id = await _create_user(
        "persisted-email@company.com",
        department_id=department_id,
    )
    file_id = await _create_file(
        uploader_id=uploader_id,
        department_id=department_id,
        name="email.pdf",
    )
    event_id = await _create_source_event(
        event_type=ReviewFileApproved.ROUTING_KEY,
        aggregate_type="file",
        aggregate_id=file_id,
    )
    await _process_source_event(event_id)

    async with AsyncSessionFactory() as session:
        email_notification = (
            await session.execute(
                select(Notification).where(
                    Notification.source_event_id == event_id,
                    Notification.channel == "email",
                )
            )
        ).scalar_one()
        notification_id = email_notification.id

    sent: list[tuple[str, str, str]] = []

    async def fake_send_email(*, recipient: str, subject: str, body: str) -> None:
        sent.append((recipient, subject, body))

    monkeypatch.setattr(
        "app.modules.notification.tasks._send_email",
        fake_send_email,
    )

    assert await _send_persisted_email(notification_id) == "sent"
    assert await _send_persisted_email(notification_id) == "already_sent"

    async with AsyncSessionFactory() as session:
        delivered = await session.get(Notification, notification_id)
    assert delivered is not None
    assert delivered.delivery_status == "sent"
    assert delivered.delivery_attempts == 1
    assert delivered.delivered_at is not None
    assert sent == [
        (
            "persisted-email@company.com",
            "文件审核通过",
            "文件《email.pdf》已审核通过。",
        )
    ]
