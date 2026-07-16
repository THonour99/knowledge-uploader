from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from importlib import import_module
from typing import Any, ClassVar

import pytest
from sqlalchemy import select

from app.core.events import (
    EVENT_HANDLERS,
    DomainEvent,
    EventDispatchContext,
    EventEnvelope,
    event_handler,
    load_event_handlers,
)
from app.core.outbox import EventOutbox, OutboxDeadLetter, OutboxRepository
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
    configured_max_attempts,
    dispatch_celery_task_for_event,
    dispatch_once,
)

pytestmark = pytest.mark.asyncio
TRACE_ID = str(uuid.uuid4())


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

    def send_task(
        self,
        name: str,
        args: list[str],
        queue: str,
        *,
        countdown: int | None = None,
    ) -> object:
        sent: dict[str, object] = {"name": name, "args": args, "queue": queue}
        if countdown is not None:
            sent["countdown"] = countdown
        self.sent.append(sent)
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
        id=1,
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


async def test_loading_event_handlers_restores_declared_order_after_early_import() -> None:
    load_event_handlers(DEFAULT_EVENT_HANDLER_MODULES)
    handlers = EVENT_HANDLERS[REVIEW_FILE_APPROVED]
    original = list(handlers)
    try:
        handlers.reverse()

        load_event_handlers(DEFAULT_EVENT_HANDLER_MODULES)

        modules = [handler.__module__ for handler in handlers]
        assert modules.index("app.modules.ragflow.handlers") < modules.index(
            "app.modules.notification.handlers"
        )
    finally:
        handlers[:] = original


async def _create_outbox_event() -> int:
    from app.core.database import AsyncSessionFactory

    event = EventOutbox(
        event_type="auth.user.registered",
        aggregate_type="user",
        aggregate_id="user-1",
        payload={"user_id": "user-1"},
        trace_id=TRACE_ID,
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
            "trace_id": TRACE_ID,
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
        assert event.first_publish_failed_at is not None
        assert event.last_publish_failed_at is not None


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


async def test_dispatch_once_moves_poison_event_to_dead_letter_at_limit() -> None:
    from app.core.config import get_settings
    from app.core.database import AsyncSessionFactory

    event_id = await _create_outbox_event()
    publisher = FakePublisher(
        fail=True,
        error_message="password=must-never-be-persisted",
    )

    assert await dispatch_once(publisher=publisher, max_attempts=2) == 0
    assert await dispatch_once(publisher=publisher, max_attempts=2) == 0
    assert await dispatch_once(publisher=publisher, max_attempts=2) == 0

    async with AsyncSessionFactory() as session:
        event = await session.get(EventOutbox, event_id)
        result = await session.execute(
            select(OutboxDeadLetter).where(OutboxDeadLetter.event_id == event_id)
        )
        dead_letters = list(result.scalars())

    assert event is not None
    assert event.publish_attempts == 2
    assert event.last_error == "RuntimeError"
    assert len(dead_letters) == 1
    assert dead_letters[0].status == "pending"
    assert dead_letters[0].attempts == 2
    assert dead_letters[0].error_type == "RuntimeError"
    assert dead_letters[0].first_failed_at <= dead_letters[0].last_failed_at
    assert dead_letters[0].correlation_id == f"outbox:{event_id}"
    assert dead_letters[0].trace_id == TRACE_ID
    assert dead_letters[0].payload_summary["field_names"] == ["user_id"]
    assert dead_letters[0].payload_summary["field_count"] == 1
    assert len(str(dead_letters[0].payload_summary["hmac_sha256"])) == 64
    assert "user-1" not in repr(dead_letters[0].payload_summary)
    assert get_settings().encryption_key not in repr(dead_letters[0].payload_summary)
    assert "must-never-be-persisted" not in repr(dead_letters[0].__dict__)


async def test_fetch_pending_locks_only_outbox_rows_across_left_join() -> None:
    """PostgreSQL rejects FOR UPDATE on the nullable side of a LEFT JOIN."""
    from app.core.database import AsyncSessionFactory

    event_id = await _create_outbox_event()

    async with AsyncSessionFactory() as session:
        async with session.begin():
            events = await OutboxRepository(session).fetch_pending(
                limit=1,
                max_attempts=3,
            )

    assert [event.id for event in events] == [event_id]


async def test_lowered_retry_limit_quarantines_exhausted_backlog_in_bounded_batches() -> None:
    from app.core.database import AsyncSessionFactory

    async with AsyncSessionFactory() as session:
        session.add_all(
            [
                EventOutbox(
                    event_type="auth.user.registered",
                    aggregate_type="user",
                    aggregate_id=f"user-{index}",
                    payload={"user_id": f"user-{index}"},
                    publish_attempts=4,
                    last_error="RuntimeError",
                )
                for index in range(5)
            ]
        )
        await session.commit()

    batch_counts: list[int] = []
    async with AsyncSessionFactory() as session:
        async with session.begin():
            repository = OutboxRepository(session)
            for _ in range(4):
                batch_counts.append(
                    await repository.quarantine_exhausted(
                        max_attempts=2,
                        limit=2,
                    )
                )

    async with AsyncSessionFactory() as session:
        dead_letters = (
            (await session.execute(select(OutboxDeadLetter).order_by(OutboxDeadLetter.event_id)))
            .scalars()
            .all()
        )

    assert batch_counts == [2, 2, 1, 0]
    assert len(dead_letters) == 5
    assert {dead_letter.status for dead_letter in dead_letters} == {"pending"}


async def test_legacy_free_text_error_is_never_exposed_by_dead_letter_record() -> None:
    from app.core.database import AsyncSessionFactory

    leaked_values = (
        "sk-live-abcdefghijklmnopqrstuvwxyz",
        "skAbc123",
        "BearerToken",
        "employee@example.com",
        "https://user:password@internal.example/private",
    )
    legacy_errors = (
        "publish failed " + " ".join(leaked_values),
        "skAbc123",
        "BearerToken",
    )
    legacy_trace_ids = (
        "employee@example.com",
        "skAbc123",
        "https://internal.example/private",
    )
    async with AsyncSessionFactory() as session:
        session.add_all(
            [
                EventOutbox(
                    event_type="auth.user.registered",
                    aggregate_type="user",
                    aggregate_id=f"legacy-sensitive-error-{index}",
                    payload={"user_id": f"legacy-sensitive-error-{index}"},
                    publish_attempts=4,
                    last_error=legacy_error,
                    trace_id=legacy_trace_ids[index],
                )
                for index, legacy_error in enumerate(legacy_errors)
            ]
        )
        await session.commit()

    async with AsyncSessionFactory() as session:
        repository = OutboxRepository(session)
        quarantined = await repository.quarantine_exhausted(max_attempts=2, limit=10)
        await session.commit()
        records, total = await repository.list_dead_letters(page=1, page_size=20, status=None)

        from app.modules.config.dlq_service import DeadLetterService

        public_items = [
            DeadLetterService(session=session)._item_response(record).model_dump(mode="json")
            for record in records
        ]

    assert quarantined == 3
    assert total == 3
    assert {item["error_type"] for item in public_items} == {"LegacyPublishError"}
    assert {item["trace_id"] for item in public_items} == {None}
    serialized_api_response = repr(public_items)
    assert all(value not in serialized_api_response for value in leaked_values)


async def test_dispatch_metrics_are_not_updated_when_database_commit_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    import app.workers.outbox_dispatcher as dispatcher
    from app.core.database import AsyncSessionFactory

    event_id = await _create_outbox_event()
    observations: list[tuple[str, object]] = []

    async def fail_commit(_session: AsyncSession) -> None:
        raise RuntimeError("simulated commit failure")

    monkeypatch.setattr(AsyncSession, "commit", fail_commit)
    monkeypatch.setattr(
        dispatcher,
        "observe_outbox_publish",
        lambda event_type, result: observations.append(("publish", (event_type, result))),
    )
    monkeypatch.setattr(
        dispatcher,
        "observe_external_request",
        lambda service, result: observations.append(("external", (service, result))),
    )
    monkeypatch.setattr(
        dispatcher,
        "observe_task_result",
        lambda family, result: observations.append(("task", (family, result))),
    )
    monkeypatch.setattr(
        dispatcher,
        "update_outbox_health",
        lambda **values: observations.append(("health", values)),
    )

    with pytest.raises(RuntimeError, match="simulated commit failure"):
        await dispatch_once(publisher=FakePublisher())

    assert observations == []
    async with AsyncSessionFactory() as session:
        event = await session.get(EventOutbox, event_id)
    assert event is not None and event.published_at is None


async def test_raising_retry_limit_does_not_implicitly_replay_quarantined_event() -> None:
    from app.core.database import AsyncSessionFactory

    async with AsyncSessionFactory() as session:
        event = EventOutbox(
            event_type="auth.user.registered",
            aggregate_type="user",
            aggregate_id="lowered-limit-user",
            payload={"user_id": "lowered-limit-user"},
            publish_attempts=3,
            last_error="RuntimeError",
        )
        session.add(event)
        await session.commit()
        await session.refresh(event)
        event_id = event.id

    async with AsyncSessionFactory() as session:
        async with session.begin():
            lowered = await OutboxRepository(session).fetch_pending(
                limit=1,
                max_attempts=2,
            )
    assert lowered == []

    async with AsyncSessionFactory() as session:
        async with session.begin():
            raised = await OutboxRepository(session).fetch_pending(
                limit=1,
                max_attempts=4,
            )
        dead_letter = (
            await session.execute(
                select(OutboxDeadLetter).where(OutboxDeadLetter.event_id == event_id)
            )
        ).scalar_one()

    assert raised == []
    assert dead_letter.status == "pending"


async def test_dead_letter_replay_is_idempotent_and_resets_delivery() -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.user.models import User

    event_id = await _create_outbox_event()
    publisher = FakePublisher(fail=True)
    await dispatch_once(publisher=publisher, max_attempts=1)

    async with AsyncSessionFactory() as session:
        actor = User(
            name="DLQ Admin",
            email="dlq-replay-admin@company.com",
            email_domain="company.com",
            password_hash="not-used-in-this-test",
            role="system_admin",
            status="active",
            email_verified=True,
        )
        session.add(actor)
        await session.flush()
        actor_id = actor.id
        result = await session.execute(
            select(OutboxDeadLetter).where(OutboxDeadLetter.event_id == event_id)
        )
        dead_letter_id = result.scalar_one().id
        repository = OutboxRepository(session)
        first = await repository.replay_dead_letter(
            dead_letter_id=dead_letter_id,
            actor_id=actor_id,
            reason="确认依赖恢复后重放",
        )
        await session.commit()

    assert first is not None
    assert first.queued is True

    async with AsyncSessionFactory() as session:
        repository = OutboxRepository(session)
        second = await repository.replay_dead_letter(
            dead_letter_id=dead_letter_id,
            actor_id=actor_id,
            reason="重复请求不应再次入队",
        )
        await session.commit()
        event = await session.get(EventOutbox, event_id)
        dead_letter = await session.get(OutboxDeadLetter, dead_letter_id)

    assert second is not None
    assert second.queued is False
    assert event is not None
    assert event.publish_attempts == 0
    assert event.last_error is None
    assert dead_letter is not None
    assert dead_letter.status == "requeued"
    assert dead_letter.replay_count == 1
    assert dead_letter.last_replayed_by == actor_id
    assert dead_letter.last_replay_reason == "确认依赖恢复后重放"
    assert event.published_at is None


async def test_replayed_dead_letter_resolves_only_after_successful_publish() -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.user.models import User

    event_id = await _create_outbox_event()
    await dispatch_once(publisher=FakePublisher(fail=True), max_attempts=1)
    async with AsyncSessionFactory() as session:
        actor = User(
            name="DLQ Resolve Admin",
            email="dlq-resolve-admin@company.com",
            email_domain="company.com",
            password_hash="not-used-in-this-test",
            role="system_admin",
            status="active",
            email_verified=True,
        )
        session.add(actor)
        await session.flush()
        result = await session.execute(
            select(OutboxDeadLetter).where(OutboxDeadLetter.event_id == event_id)
        )
        dead_letter = result.scalar_one()
        dead_letter_id = dead_letter.id
        repository = OutboxRepository(session)
        replay = await repository.replay_dead_letter(
            dead_letter_id=dead_letter_id,
            actor_id=actor.id,
            reason="依赖恢复并验证成功闭环",
        )
        await session.commit()
    assert replay is not None and replay.queued is True

    assert await dispatch_once(publisher=FakePublisher(), max_attempts=1) == 1
    async with AsyncSessionFactory() as session:
        resolved = await session.get(OutboxDeadLetter, dead_letter_id)
        event = await session.get(EventOutbox, event_id)
    assert resolved is not None
    assert resolved.status == "resolved"
    assert resolved.resolved_at is not None
    assert event is not None and event.published_at is not None


async def test_replayed_dead_letter_returns_to_pending_after_retry_exhaustion() -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.user.models import User

    event_id = await _create_outbox_event()
    await dispatch_once(publisher=FakePublisher(fail=True), max_attempts=1)
    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(OutboxDeadLetter).where(OutboxDeadLetter.event_id == event_id)
        )
        dead_letter = result.scalar_one()
        dead_letter_id = dead_letter.id
        original_first_failed_at = dead_letter.first_failed_at
        actor = User(
            name="DLQ Retry Admin",
            email="dlq-retry-admin@company.com",
            email_domain="company.com",
            password_hash="not-used-in-this-test",
            role="system_admin",
            status="active",
            email_verified=True,
        )
        session.add(actor)
        await session.flush()
        repository = OutboxRepository(session)
        await repository.replay_dead_letter(
            dead_letter_id=dead_letter_id,
            actor_id=actor.id,
            reason="验证再次失败可追踪",
        )
        await session.commit()

    assert await dispatch_once(publisher=FakePublisher(fail=True), max_attempts=1) == 0
    async with AsyncSessionFactory() as session:
        failed_again = await session.get(OutboxDeadLetter, dead_letter_id)
    assert failed_again is not None
    assert failed_again.status == "pending"
    assert failed_again.first_failed_at == original_first_failed_at
    assert failed_again.last_failed_at >= original_first_failed_at
    assert failed_again.resolved_at is None


async def test_configured_max_attempts_counts_retries_after_first_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.workers.outbox_dispatcher as dispatcher

    async def zero_retries(_key: str) -> object:
        return 0

    monkeypatch.setattr(dispatcher, "get_config", zero_retries)
    assert await configured_max_attempts() == 1

    async def ten_retries(_key: str) -> object:
        return 10

    monkeypatch.setattr(dispatcher, "get_config", ten_retries)
    assert await configured_max_attempts() == 11

    async def invalid_retries(_key: str) -> object:
        return True

    monkeypatch.setattr(dispatcher, "get_config", invalid_retries)
    assert await configured_max_attempts() == 4


async def test_ragflow_sync_task_outbox_event_dispatches_celery_task() -> None:
    event = EventOutbox(
        id=1,
        event_type="ragflow.sync_task.queued",
        aggregate_type="sync_task",
        aggregate_id="task-1",
        payload={"sync_task_id": "task-1", "task_type": "ragflow_upload"},
    )
    sender = FakeCelerySender()

    dispatch_celery_task_for_event(event, sender=sender)

    assert sender.sent == [{"name": "ragflow.upload", "args": ["task-1"], "queue": "ragflow_queue"}]


@pytest.mark.parametrize("task_type", [None, "ragflow_parse", "legacy_unknown"])
async def test_ragflow_sync_task_outbox_rejects_unsupported_task_type(
    task_type: str | None,
) -> None:
    event = EventOutbox(
        id=1,
        event_type="ragflow.sync_task.queued",
        aggregate_type="sync_task",
        aggregate_id="task-1",
        payload={"sync_task_id": "task-1", "task_type": task_type},
    )
    sender = FakeCelerySender()

    with pytest.raises(RuntimeError, match="unsupported task_type"):
        dispatch_celery_task_for_event(event, sender=sender)

    assert sender.sent == []


async def test_ragflow_delete_sync_task_outbox_event_dispatches_celery_task() -> None:
    event = EventOutbox(
        id=1,
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
        id=1,
        event_type="ragflow.sync_task.queued",
        aggregate_type="sync_task",
        aggregate_id="task-1",
        payload={},
    )

    with pytest.raises(RuntimeError, match="missing sync_task_id"):
        dispatch_celery_task_for_event(event, sender=FakeCelerySender())


async def test_unknown_outbox_event_does_not_dispatch_celery_task() -> None:
    event = EventOutbox(
        id=1,
        event_type="unknown.event",
        aggregate_type="unknown",
        aggregate_id="unknown-1",
        payload={},
    )
    sender = FakeCelerySender()

    handled = dispatch_celery_task_for_event(event, sender=sender)

    assert handled == 0
    assert sender.sent == []


async def test_unpersisted_outbox_event_is_rejected_before_dispatch() -> None:
    event = EventOutbox(
        event_type="unknown.event",
        aggregate_type="unknown",
        aggregate_id="unknown-1",
        payload={},
    )
    sender = FakeCelerySender()

    with pytest.raises(RuntimeError, match="must be persisted"):
        dispatch_celery_task_for_event(event, sender=sender)

    assert sender.sent == []


async def test_user_password_reset_event_dispatches_auth_task() -> None:
    event = EventOutbox(
        id=1,
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
        id=1,
        event_type=USER_PASSWORD_RESET_REQUESTED,
        aggregate_type="user",
        aggregate_id="user-1",
        payload={},
    )

    with pytest.raises(RuntimeError, match="missing user_id"):
        dispatch_celery_task_for_event(event, sender=FakeCelerySender())


async def test_review_approved_event_with_dataset_dispatches_ragflow_task_creation() -> None:
    event = EventOutbox(
        id=101,
        event_type=REVIEW_FILE_APPROVED,
        aggregate_type="file",
        aggregate_id="file-1",
        payload={
            "file_id": "file-1",
            "ragflow_dataset_id": "dataset-1",
            "dataset_mapping_id": "mapping-1",
            "sync_decision": "sync",
        },
    )
    sender = FakeCelerySender()

    dispatch_celery_task_for_event(event, sender=sender)

    assert sender.sent == [
        {"name": "ragflow.create_upload_task", "args": ["file-1"], "queue": "ragflow_queue"},
        {
            "name": "notification.process_domain_event",
            "args": ["101"],
            "queue": "notification_queue",
        },
    ]


async def test_review_approved_sync_event_without_dataset_fails_for_outbox_retry() -> None:
    event = EventOutbox(
        id=1,
        event_type=REVIEW_FILE_APPROVED,
        aggregate_type="file",
        aggregate_id="file-1",
        payload={
            "file_id": "file-1",
            "ragflow_dataset_id": None,
            "dataset_mapping_id": "mapping-1",
            "sync_decision": "sync",
        },
    )
    sender = FakeCelerySender()

    with pytest.raises(RuntimeError, match="missing explicit ragflow target"):
        dispatch_celery_task_for_event(event, sender=sender)

    assert sender.sent == []


async def test_review_approved_event_with_dataset_requires_file_id() -> None:
    event = EventOutbox(
        id=1,
        event_type=REVIEW_FILE_APPROVED,
        aggregate_type="file",
        aggregate_id="file-1",
        payload={
            "ragflow_dataset_id": "dataset-1",
            "dataset_mapping_id": "mapping-1",
            "sync_decision": "sync",
        },
    )

    with pytest.raises(RuntimeError, match="missing file_id"):
        dispatch_celery_task_for_event(event, sender=FakeCelerySender())


async def test_review_approved_sync_event_without_mapping_fails_for_outbox_retry() -> None:
    event = EventOutbox(
        id=1,
        event_type=REVIEW_FILE_APPROVED,
        aggregate_type="file",
        aggregate_id="file-1",
        payload={
            "file_id": "file-1",
            "ragflow_dataset_id": "dataset-1",
            "sync_decision": "sync",
        },
    )
    sender = FakeCelerySender()

    with pytest.raises(RuntimeError, match="missing explicit ragflow target"):
        dispatch_celery_task_for_event(event, sender=sender)

    assert sender.sent == []


@pytest.mark.parametrize("sync_decision", [None, "legacy", 1])
async def test_review_approved_event_requires_explicit_sync_decision(
    monkeypatch: pytest.MonkeyPatch,
    sync_decision: object,
) -> None:
    from app.modules.ragflow import handlers as ragflow_handlers

    warnings: list[tuple[str, dict[str, object]]] = []

    class FakeLogger:
        def warning(self, event_name: str, **fields: object) -> None:
            warnings.append((event_name, fields))

    monkeypatch.setattr(ragflow_handlers, "logger", FakeLogger())
    payload: dict[str, object] = {
        "file_id": "file-1",
        "ragflow_dataset_id": "dataset-1",
    }
    if sync_decision is not None:
        payload["sync_decision"] = sync_decision
    event = EventOutbox(
        id=1,
        event_type=REVIEW_FILE_APPROVED,
        aggregate_type="file",
        aggregate_id="file-1",
        payload=payload,
    )
    sender = FakeCelerySender()

    with pytest.raises(RuntimeError, match="missing explicit sync decision"):
        dispatch_celery_task_for_event(event, sender=sender)

    assert sender.sent == []
    assert warnings == [
        (
            "ragflow_upload_task_creation_rejected",
            {
                "event_type": REVIEW_FILE_APPROVED,
                "reason": "explicit_sync_decision_required",
            },
        )
    ]


async def test_review_approved_approve_only_event_dispatches_only_notification() -> None:
    event = EventOutbox(
        id=102,
        event_type=REVIEW_FILE_APPROVED,
        aggregate_type="file",
        aggregate_id="file-1",
        payload={
            "file_id": "file-1",
            "sync_decision": "approve_only",
        },
    )
    sender = FakeCelerySender()

    dispatch_celery_task_for_event(event, sender=sender)

    assert sender.sent == [
        {
            "name": "notification.process_domain_event",
            "args": ["102"],
            "queue": "notification_queue",
        },
    ]


async def test_review_rejected_event_dispatches_notification_task() -> None:
    event = EventOutbox(
        id=103,
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
            "name": "notification.process_domain_event",
            "args": ["103"],
            "queue": "notification_queue",
        }
    ]


async def test_ragflow_sync_failed_event_dispatches_notification_task() -> None:
    event = EventOutbox(
        id=104,
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
            "name": "notification.process_domain_event",
            "args": ["104"],
            "queue": "notification_queue",
        }
    ]


async def test_document_deleted_event_dispatches_ragflow_delete_creation() -> None:
    event = EventOutbox(
        id=1,
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
        id=1,
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
        id=1,
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
        id=1,
        event_type=DOCUMENT_FILE_ARCHIVED,
        aggregate_type="file",
        aggregate_id="file-1",
        payload={"ragflow_document_id": "doc-1", "keep_remote": False},
    )

    with pytest.raises(RuntimeError, match="missing file_id"):
        dispatch_celery_task_for_event(event, sender=FakeCelerySender())


async def test_file_uploaded_event_dispatches_ai_task_when_enabled() -> None:
    event = EventOutbox(
        id=1,
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
        id=1,
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
        id=1,
        event_type=DOCUMENT_FILE_UPLOADED,
        aggregate_type="file",
        aggregate_id="file-1",
        payload={"ai_analysis_enabled_at_upload": True},
    )

    with pytest.raises(RuntimeError, match="missing file_id"):
        dispatch_celery_task_for_event(event, sender=FakeCelerySender())


async def test_reanalyze_requested_event_dispatches_ai_task() -> None:
    event = EventOutbox(
        id=1,
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
        id=1,
        event_type=DOCUMENT_FILE_REANALYZE_REQUESTED,
        aggregate_type="file",
        aggregate_id="file-1",
        payload={},
    )

    with pytest.raises(RuntimeError, match="missing file_id"):
        dispatch_celery_task_for_event(event, sender=FakeCelerySender())
