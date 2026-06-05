from __future__ import annotations

from collections.abc import AsyncGenerator
from importlib import import_module
from typing import Any

import pytest

from app.core.outbox import EventOutbox
from app.workers.outbox_dispatcher import dispatch_once

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
