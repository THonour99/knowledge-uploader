from __future__ import annotations

from collections.abc import AsyncGenerator
from importlib import import_module
from typing import Any, ClassVar

import pytest

from app.core.events import (
    EVENT_HANDLERS,
    DomainEvent,
    EventDispatchContext,
    EventEnvelope,
    event_handler,
    load_event_handlers,
)
from app.core.outbox import EventOutbox
from app.modules.document.events import (
    DOCUMENT_FILE_ARCHIVED,
    DOCUMENT_FILE_DELETED,
    DOCUMENT_FILE_REANALYZE_REQUESTED,
    DOCUMENT_FILE_UPLOADED,
)
from app.modules.ragflow.events import RAGFLOW_SYNC_TASK_FAILED
from app.modules.review.events import REVIEW_FILE_APPROVED, REVIEW_FILE_REJECTED
from app.modules.user.events import USER_PASSWORD_RESET_REQUESTED
from app.workers.outbox_dispatcher import (
    DEFAULT_EVENT_HANDLER_MODULES,
    dispatch_celery_task_for_event,
    dispatch_once,
)

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


class DuplicateRegistryEvent(DomainEvent):
    ROUTING_KEY: ClassVar[str] = "test.registry.duplicate"


async def test_event_handler_registration_is_idempotent() -> None:
    def handler(_event: EventEnvelope, _context: EventDispatchContext) -> None:
        return None

    try:
        event_handler(DuplicateRegistryEvent)(handler)
        event_handler(DuplicateRegistryEvent)(handler)

        assert EVENT_HANDLERS[DuplicateRegistryEvent.ROUTING_KEY] == [handler]
    finally:
        EVENT_HANDLERS.pop(DuplicateRegistryEvent.ROUTING_KEY, None)


async def test_loading_event_handlers_is_idempotent_for_dispatch() -> None:
    event = EventOutbox(
        event_type=DOCUMENT_FILE_REANALYZE_REQUESTED,
        aggregate_type="file",
        aggregate_id="file-1",
        payload={"file_id": "file-1"},
    )
    sender = FakeCelerySender()

    load_event_handlers(DEFAULT_EVENT_HANDLER_MODULES)
    load_event_handlers(DEFAULT_EVENT_HANDLER_MODULES)
    handled = dispatch_celery_task_for_event(event, sender=sender)

    assert handled == 1
    assert sender.sent == [{"name": "ai.analyze_file", "args": ["file-1"], "queue": "ai_queue"}]


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

    assert sender.sent == [{"name": "ragflow.upload", "args": ["task-1"], "queue": "ragflow_queue"}]


async def test_ragflow_delete_sync_task_outbox_event_dispatches_celery_task() -> None:
    event = EventOutbox(
        event_type="ragflow.sync_task.queued",
        aggregate_type="sync_task",
        aggregate_id="task-1",
        payload={"sync_task_id": "task-1", "task_type": "ragflow_delete"},
    )
    sender = FakeCelerySender()

    handled = dispatch_celery_task_for_event(event, sender=sender)

    assert handled == 1
    assert sender.sent == [{"name": "ragflow.delete", "args": ["task-1"], "queue": "ragflow_queue"}]


async def test_ragflow_sync_task_outbox_event_requires_task_id() -> None:
    event = EventOutbox(
        event_type="ragflow.sync_task.queued",
        aggregate_type="sync_task",
        aggregate_id="task-1",
        payload={},
    )

    with pytest.raises(RuntimeError, match="missing sync_task_id"):
        dispatch_celery_task_for_event(event, sender=FakeCelerySender())


async def test_unknown_outbox_event_does_not_dispatch_celery_task() -> None:
    event = EventOutbox(
        event_type="unknown.event",
        aggregate_type="unknown",
        aggregate_id="unknown-1",
        payload={},
    )
    sender = FakeCelerySender()

    handled = dispatch_celery_task_for_event(event, sender=sender)

    assert handled == 0
    assert sender.sent == []


async def test_user_password_reset_event_dispatches_auth_task() -> None:
    event = EventOutbox(
        event_type=USER_PASSWORD_RESET_REQUESTED,
        aggregate_type="user",
        aggregate_id="user-1",
        payload={"user_id": "user-1"},
    )
    sender = FakeCelerySender()

    handled = dispatch_celery_task_for_event(event, sender=sender)

    assert handled == 1
    assert sender.sent == [
        {
            "name": "auth.trigger_password_reset",
            "args": ["user-1"],
            "queue": "notification_queue",
        }
    ]


async def test_user_password_reset_event_requires_user_id() -> None:
    event = EventOutbox(
        event_type=USER_PASSWORD_RESET_REQUESTED,
        aggregate_type="user",
        aggregate_id="user-1",
        payload={},
    )

    with pytest.raises(RuntimeError, match="missing user_id"):
        dispatch_celery_task_for_event(event, sender=FakeCelerySender())


async def test_review_approved_event_with_dataset_dispatches_ragflow_task_creation() -> None:
    event = EventOutbox(
        event_type=REVIEW_FILE_APPROVED,
        aggregate_type="file",
        aggregate_id="file-1",
        payload={"file_id": "file-1", "ragflow_dataset_id": "dataset-1"},
    )
    sender = FakeCelerySender()

    dispatch_celery_task_for_event(event, sender=sender)

    assert sender.sent == [
        {
            "name": "notification.review_approved",
            "args": ["file-1"],
            "queue": "notification_queue",
        },
        {"name": "ragflow.create_upload_task", "args": ["file-1"], "queue": "ragflow_queue"},
    ]


async def test_review_approved_event_without_dataset_dispatches_only_notification() -> None:
    event = EventOutbox(
        event_type=REVIEW_FILE_APPROVED,
        aggregate_type="file",
        aggregate_id="file-1",
        payload={"file_id": "file-1", "ragflow_dataset_id": None},
    )
    sender = FakeCelerySender()

    dispatch_celery_task_for_event(event, sender=sender)

    assert sender.sent == [
        {
            "name": "notification.review_approved",
            "args": ["file-1"],
            "queue": "notification_queue",
        },
    ]


async def test_review_approved_event_with_dataset_requires_file_id() -> None:
    event = EventOutbox(
        event_type=REVIEW_FILE_APPROVED,
        aggregate_type="file",
        aggregate_id="file-1",
        payload={"ragflow_dataset_id": "dataset-1"},
    )

    with pytest.raises(RuntimeError, match="missing file_id"):
        dispatch_celery_task_for_event(event, sender=FakeCelerySender())


async def test_review_rejected_event_dispatches_notification_task() -> None:
    event = EventOutbox(
        event_type=REVIEW_FILE_REJECTED,
        aggregate_type="file",
        aggregate_id="file-1",
        payload={"file_id": "file-1", "reason": "内容不合规"},
    )
    sender = FakeCelerySender()

    handled = dispatch_celery_task_for_event(event, sender=sender)

    assert handled == 1
    assert sender.sent == [
        {
            "name": "notification.review_rejected",
            "args": ["file-1", "内容不合规"],
            "queue": "notification_queue",
        }
    ]


async def test_ragflow_sync_failed_event_dispatches_notification_task() -> None:
    event = EventOutbox(
        event_type=RAGFLOW_SYNC_TASK_FAILED,
        aggregate_type="sync_task",
        aggregate_id="task-1",
        payload={"sync_task_id": "task-1", "error_message": "RagflowClientError"},
    )
    sender = FakeCelerySender()

    handled = dispatch_celery_task_for_event(event, sender=sender)

    assert handled == 1
    assert sender.sent == [
        {
            "name": "notification.ragflow_sync_failed",
            "args": ["task-1", "RagflowClientError"],
            "queue": "notification_queue",
        }
    ]


async def test_document_deleted_event_dispatches_ragflow_delete_creation() -> None:
    event = EventOutbox(
        event_type=DOCUMENT_FILE_DELETED,
        aggregate_type="file",
        aggregate_id="file-1",
        payload={
            "file_id": "file-1",
            "ragflow_document_id": "doc-1",
            "delete_remote": True,
        },
    )
    sender = FakeCelerySender()

    handled = dispatch_celery_task_for_event(event, sender=sender)

    assert handled == 1
    assert sender.sent == [
        {"name": "ragflow.create_delete_task", "args": ["file-1"], "queue": "ragflow_queue"}
    ]


async def test_document_deleted_event_skips_ragflow_delete_when_disabled() -> None:
    event = EventOutbox(
        event_type=DOCUMENT_FILE_DELETED,
        aggregate_type="file",
        aggregate_id="file-1",
        payload={
            "file_id": "file-1",
            "ragflow_document_id": "doc-1",
            "delete_remote": False,
        },
    )
    sender = FakeCelerySender()

    handled = dispatch_celery_task_for_event(event, sender=sender)

    assert handled == 1
    assert sender.sent == []


async def test_document_archived_event_dispatches_ragflow_delete_when_remote_not_kept() -> None:
    event = EventOutbox(
        event_type=DOCUMENT_FILE_ARCHIVED,
        aggregate_type="file",
        aggregate_id="file-1",
        payload={
            "file_id": "file-1",
            "ragflow_document_id": "doc-1",
            "keep_remote": False,
        },
    )
    sender = FakeCelerySender()

    handled = dispatch_celery_task_for_event(event, sender=sender)

    assert handled == 1
    assert sender.sent == [
        {"name": "ragflow.create_delete_task", "args": ["file-1"], "queue": "ragflow_queue"}
    ]


async def test_document_lifecycle_event_requires_file_id_when_remote_delete_needed() -> None:
    event = EventOutbox(
        event_type=DOCUMENT_FILE_ARCHIVED,
        aggregate_type="file",
        aggregate_id="file-1",
        payload={"ragflow_document_id": "doc-1", "keep_remote": False},
    )

    with pytest.raises(RuntimeError, match="missing file_id"):
        dispatch_celery_task_for_event(event, sender=FakeCelerySender())


async def test_file_uploaded_event_dispatches_ai_task_when_enabled() -> None:
    event = EventOutbox(
        event_type=DOCUMENT_FILE_UPLOADED,
        aggregate_type="file",
        aggregate_id="file-1",
        payload={"file_id": "file-1", "ai_analysis_enabled_at_upload": True},
    )
    sender = FakeCelerySender()

    dispatch_celery_task_for_event(event, sender=sender)

    assert sender.sent == [{"name": "ai.analyze_file", "args": ["file-1"], "queue": "ai_queue"}]


async def test_file_uploaded_event_does_not_dispatch_ai_task_when_upload_disabled() -> None:
    event = EventOutbox(
        event_type=DOCUMENT_FILE_UPLOADED,
        aggregate_type="file",
        aggregate_id="file-1",
        payload={"file_id": "file-1", "ai_analysis_enabled_at_upload": False},
    )
    sender = FakeCelerySender()

    dispatch_celery_task_for_event(event, sender=sender)

    assert sender.sent == []


async def test_file_uploaded_event_requires_file_id_when_ai_enabled() -> None:
    event = EventOutbox(
        event_type=DOCUMENT_FILE_UPLOADED,
        aggregate_type="file",
        aggregate_id="file-1",
        payload={"ai_analysis_enabled_at_upload": True},
    )

    with pytest.raises(RuntimeError, match="missing file_id"):
        dispatch_celery_task_for_event(event, sender=FakeCelerySender())


async def test_reanalyze_requested_event_dispatches_ai_task() -> None:
    event = EventOutbox(
        event_type=DOCUMENT_FILE_REANALYZE_REQUESTED,
        aggregate_type="file",
        aggregate_id="file-1",
        payload={"file_id": "file-1"},
    )
    sender = FakeCelerySender()

    dispatch_celery_task_for_event(event, sender=sender)

    assert sender.sent == [{"name": "ai.analyze_file", "args": ["file-1"], "queue": "ai_queue"}]


async def test_reanalyze_requested_event_requires_file_id() -> None:
    event = EventOutbox(
        event_type=DOCUMENT_FILE_REANALYZE_REQUESTED,
        aggregate_type="file",
        aggregate_id="file-1",
        payload={},
    )

    with pytest.raises(RuntimeError, match="missing file_id"):
        dispatch_celery_task_for_event(event, sender=FakeCelerySender())
