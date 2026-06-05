from __future__ import annotations

from collections.abc import AsyncGenerator
from importlib import import_module
from typing import Any

import pytest

from app.core.outbox import EventOutbox
from app.workers.outbox_dispatcher import dispatch_celery_task_for_event, dispatch_once

pytestmark = pytest.mark.asyncio


async def _reset_database() -> None:
    import_module("app.db.models")

    from app.core.database import engine
    from app.db.base import Base

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()


@pytest.fixture(autouse=True)
async def clean_database() -> AsyncGenerator[None, None]:
    await _reset_database()
    yield
    from app.core.database import engine
    from app.db.base import Base

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()


class FakePublisher:
    def __init__(self, *, fail: bool = False, error_message: str = "broker unavailable") -> None:
        self.fail = fail
        self.error_message = error_message
        self.published: list[dict[str, Any]] = []

    def publish(self, event: EventOutbox) -> None:
        if self.fail:
            raise RuntimeError(self.error_message)
        self.published.append(
            {
                "id": event.id,
                "event_type": event.event_type,
                "payload": event.payload,
                "trace_id": event.trace_id,
            }
        )


class FakeCelerySender:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    def send_task(self, name: str, args: list[str], queue: str) -> object:
        self.sent.append({"name": name, "args": args, "queue": queue})
        return object()


async def _create_outbox_event() -> int:
    from app.core.database import AsyncSessionFactory

    event = EventOutbox(
        event_type="auth.user.registered",
        aggregate_type="user",
        aggregate_id="user-1",
        payload={"user_id": "user-1"},
        trace_id="trace-1",
    )
    async with AsyncSessionFactory() as session:
        session.add(event)
        await session.commit()
        await session.refresh(event)
        return event.id


async def test_dispatch_once_publishes_and_marks_event_published() -> None:
    from app.core.database import AsyncSessionFactory

    event_id = await _create_outbox_event()
    publisher = FakePublisher()

    dispatched = await dispatch_once(publisher=publisher)

    assert dispatched == 1
    assert publisher.published == [
        {
            "id": event_id,
            "event_type": "auth.user.registered",
            "payload": {"user_id": "user-1"},
            "trace_id": "trace-1",
        }
    ]

    async with AsyncSessionFactory() as session:
        event = await session.get(EventOutbox, event_id)
        assert event is not None
        assert event.published_at is not None
        assert event.publish_attempts == 0
        assert event.last_error is None


async def test_dispatch_once_marks_failed_event_attempt() -> None:
    from app.core.database import AsyncSessionFactory

    event_id = await _create_outbox_event()
    publisher = FakePublisher(fail=True)

    dispatched = await dispatch_once(publisher=publisher)

    assert dispatched == 0
    async with AsyncSessionFactory() as session:
        event = await session.get(EventOutbox, event_id)
        assert event is not None
        assert event.published_at is None
        assert event.publish_attempts == 1
        assert event.last_error == "RuntimeError"


async def test_dispatch_once_does_not_persist_sensitive_exception_text() -> None:
    from app.core.database import AsyncSessionFactory

    event_id = await _create_outbox_event()
    publisher = FakePublisher(
        fail=True,
        error_message="amqp://user:secret-password@rabbitmq token=reset-token-value",
    )

    dispatched = await dispatch_once(publisher=publisher)

    assert dispatched == 0
    async with AsyncSessionFactory() as session:
        event = await session.get(EventOutbox, event_id)
        assert event is not None
        assert event.last_error == "RuntimeError"
        assert "secret-password" not in event.last_error
        assert "reset-token-value" not in event.last_error


async def test_ragflow_sync_task_outbox_event_dispatches_celery_task() -> None:
    event = EventOutbox(
        event_type="ragflow.sync_task.queued",
        aggregate_type="sync_task",
        aggregate_id="task-1",
        payload={"sync_task_id": "task-1"},
    )
    sender = FakeCelerySender()

    dispatch_celery_task_for_event(event, sender=sender)

    assert sender.sent == [
        {"name": "ragflow.upload", "args": ["task-1"], "queue": "ragflow_queue"}
    ]


async def test_ragflow_sync_task_outbox_event_requires_task_id() -> None:
    event = EventOutbox(
        event_type="ragflow.sync_task.queued",
        aggregate_type="sync_task",
        aggregate_id="task-1",
        payload={},
    )

    with pytest.raises(RuntimeError, match="missing sync_task_id"):
        dispatch_celery_task_for_event(event, sender=FakeCelerySender())


async def test_review_approved_event_with_dataset_dispatches_ragflow_task_creation() -> None:
    event = EventOutbox(
        event_type="review.file.approved",
        aggregate_type="file",
        aggregate_id="file-1",
        payload={"file_id": "file-1", "ragflow_dataset_id": "dataset-1"},
    )
    sender = FakeCelerySender()

    dispatch_celery_task_for_event(event, sender=sender)

    assert sender.sent == [
        {"name": "ragflow.create_upload_task", "args": ["file-1"], "queue": "ragflow_queue"}
    ]


async def test_review_approved_event_without_dataset_does_not_dispatch_ragflow_task() -> None:
    event = EventOutbox(
        event_type="review.file.approved",
        aggregate_type="file",
        aggregate_id="file-1",
        payload={"file_id": "file-1", "ragflow_dataset_id": None},
    )
    sender = FakeCelerySender()

    dispatch_celery_task_for_event(event, sender=sender)

    assert sender.sent == []


async def test_review_approved_event_with_dataset_requires_file_id() -> None:
    event = EventOutbox(
        event_type="review.file.approved",
        aggregate_type="file",
        aggregate_id="file-1",
        payload={"ragflow_dataset_id": "dataset-1"},
    )

    with pytest.raises(RuntimeError, match="missing file_id"):
        dispatch_celery_task_for_event(event, sender=FakeCelerySender())
