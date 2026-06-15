from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from importlib import import_module
from uuid import UUID

import pytest
from redis.asyncio import from_url

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


async def _create_user(email: str) -> UUID:
    from app.core.database import AsyncSessionFactory
    from app.core.security import hash_password
    from app.modules.user.models import User

    user = User(
        name="Notify User",
        email=email,
        email_domain=email.rsplit("@", 1)[1],
        password_hash=hash_password("password123"),
        status="active",
        email_verified=True,
    )
    async with AsyncSessionFactory() as session:
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user.id


async def _create_file(*, uploader_id: UUID, name: str) -> UUID:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File

    file = File(
        original_name=name,
        stored_name="stored.pdf",
        extension="pdf",
        mime_type="application/pdf",
        size=1,
        hash="a" * 64,
        storage_type="minio",
        bucket="knowledge-files",
        object_key="test/stored.pdf",
        uploader_id=uploader_id,
        status="pending_review",
        review_status="pending",
    )
    async with AsyncSessionFactory() as session:
        session.add(file)
        await session.commit()
        await session.refresh(file)
        return file.id


async def test_in_app_notification_can_be_listed_and_marked_read() -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.notification.repository import NotificationRepository  # noqa: TID251
    from app.modules.notification.service import (  # noqa: TID251
        NotificationPage,
        NotificationService,
    )

    user_id = await _create_user("notify@company.com")

    async with AsyncSessionFactory() as session:
        service = NotificationService(
            session=session,
            repository=NotificationRepository(session),
        )
        created = await service.create_in_app(
            user_id=user_id,
            type="review_approved",
            title="文件审核通过",
            body="文件已通过审核",
            metadata={"file_id": "file-1"},
        )

        page = await service.list_user_notifications(
            user_id=user_id,
            page=NotificationPage(page=1, page_size=20),
        )

        assert page.total == 1
        assert page.unread_count == 1
        assert page.items[0].id == created.id
        assert page.items[0].metadata == {"file_id": "file-1"}

        marked = await service.mark_read(notification_id=created.id, user_id=user_id)
        assert marked is not None
        assert marked.read_at is not None

        unread_page = await service.list_user_notifications(
            user_id=user_id,
            page=NotificationPage(page=1, page_size=20, unread_only=True),
        )
        assert unread_page.total == 0
        assert unread_page.unread_count == 0


async def test_review_handler_creates_in_app_and_email_notification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy import select

    from app.core.database import AsyncSessionFactory
    from app.modules.notification import handlers
    from app.modules.notification.models import Notification

    user_id = await _create_user("review-target@company.com")
    file_id = await _create_file(uploader_id=user_id, name="policy.pdf")
    emails: list[dict[str, str]] = []

    def fake_enqueue_email(*, recipient: str, subject: str, body: str) -> None:
        emails.append({"recipient": recipient, "subject": subject, "body": body})

    monkeypatch.setattr(handlers, "enqueue_email", fake_enqueue_email)

    async with AsyncSessionFactory() as session:
        await handlers.handle_review_file_rejected(
            {"file_id": str(file_id), "reason": "内容不合规"},
            session=session,
        )

    async with AsyncSessionFactory() as session:
        notification = (await session.execute(select(Notification))).scalar_one()

    assert notification.user_id == user_id
    assert notification.type == "review_rejected"
    assert "policy.pdf" in notification.body
    assert "内容不合规" in notification.body
    assert emails == [
        {
            "recipient": "review-target@company.com",
            "subject": "文件审核被拒绝",
            "body": notification.body,
        }
    ]
