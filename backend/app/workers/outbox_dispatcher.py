from __future__ import annotations

import asyncio
import os
import signal
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from kombu import Connection, Exchange, Producer

from app.core.config import get_settings
from app.core.database import AsyncSessionFactory
from app.core.events import EventDispatchContext, TaskSender, dispatch_event, load_event_handlers
from app.core.logging import configure_logging, get_logger
from app.core.metrics import (
    observe_external_request,
    observe_outbox_publish,
    observe_task_result,
    start_metrics_server,
    update_outbox_health,
)
from app.core.outbox import EventOutbox, OutboxRepository
from app.core.runtime_config import get_config
from app.workers.celery_app import celery_app

_running = True
logger = get_logger(__name__)
EVENT_EXCHANGE = "knowledge.events"
DEFAULT_BATCH_SIZE = 100
DEFAULT_MAX_RETRIES = 3
DEFAULT_MAX_ATTEMPTS = DEFAULT_MAX_RETRIES + 1
DEFAULT_POLL_INTERVAL_SECONDS = 0.5
PUBLISH_RETRY_POLICY: dict[str, int | float] = {
    "max_retries": 3,
    "interval_start": 0,
    "interval_step": 0.5,
    "interval_max": 2,
}
DEFAULT_EVENT_HANDLER_MODULES = (
    "app.modules.ai.handlers",
    "app.modules.audit.handlers",
    "app.modules.auth.handlers",
    "app.modules.config.handlers",
    "app.modules.document.handlers",
    "app.modules.ragflow.handlers",
    "app.modules.notification.handlers",
    "app.modules.review.handlers",
    "app.modules.statistics.handlers",
    "app.modules.user.handlers",
)


class EventPublisher(Protocol):
    def publish(self, event: EventOutbox) -> None:
        pass


@dataclass
class OutboxEventEnvelope:
    event_id: int
    event_type: str
    payload: dict[str, object]


def dispatch_celery_task_for_event(
    event: EventOutbox,
    *,
    sender: TaskSender | None = None,
) -> int:
    load_event_handlers(DEFAULT_EVENT_HANDLER_MODULES)
    event_id = event.id
    if not isinstance(event_id, int) or isinstance(event_id, bool) or event_id < 1:
        raise RuntimeError("outbox event must be persisted before dispatch")
    envelope = OutboxEventEnvelope(
        event_id=event_id,
        event_type=event.event_type,
        payload=event.payload,
    )
    return dispatch_event(
        envelope,
        EventDispatchContext(sender=sender if sender is not None else celery_app),
    )


class KombuEventPublisher:
    def __init__(self, broker_url: str) -> None:
        self._broker_url = broker_url
        self._connection: Connection | None = None
        self._producer: Producer | None = None
        self._exchange = Exchange(EVENT_EXCHANGE, type="topic", durable=True)

    def __enter__(self) -> KombuEventPublisher:
        self._connection = Connection(
            self._broker_url,
            transport_options={"confirm_publish": True},
        )
        self._connection.connect()
        self._producer = Producer(self._connection)
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        if self._connection is not None:
            self._connection.release()
        self._producer = None
        self._connection = None

    def publish(self, event: EventOutbox) -> None:
        if self._producer is None:
            msg = "publisher is not connected"
            raise RuntimeError(msg)
        self._producer.publish(
            event.payload,
            exchange=self._exchange,
            routing_key=event.event_type,
            serializer="json",
            delivery_mode=2,
            retry=True,
            retry_policy=PUBLISH_RETRY_POLICY,
            declare=[self._exchange],
            headers={
                "event_id": str(event.id),
                "trace_id": event.trace_id,
                "aggregate_type": event.aggregate_type,
                "aggregate_id": event.aggregate_id,
            },
        )
        dispatch_celery_task_for_event(event)


def _stop(_signum: int, _frame: object) -> None:
    global _running
    _running = False


async def dispatch_once(
    *,
    publisher: EventPublisher,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> int:
    dispatched = 0
    outcomes: list[tuple[str, str]] = []
    async with AsyncSessionFactory() as session:
        repository = OutboxRepository(session)
        events = await repository.fetch_pending(limit=batch_size, max_attempts=max_attempts)
        for event in events:
            try:
                publisher.publish(event)
            except Exception as exc:
                error_type = type(exc).__name__
                await repository.mark_failed(
                    event,
                    error_type,
                    max_attempts=max_attempts,
                )
                logger.warning(
                    "outbox_publish_failed",
                    event_id=event.id,
                    event_type=event.event_type,
                    error_type=error_type,
                )
                outcomes.append((event.event_type, "failure"))
                continue
            await repository.mark_published(event)
            outcomes.append((event.event_type, "success"))
            dispatched += 1
        health = await repository.health(max_attempts=max_attempts)
        # Metrics describe committed dispatcher state. Publishing necessarily
        # precedes this commit, but no success/failure/health gauge is advanced
        # when persistence fails and the transaction is rolled back.
        await session.commit()

    for event_type, result in outcomes:
        observe_outbox_publish(event_type, result)
        observe_external_request("rabbitmq", result)
        observe_task_result("outbox", result)
    update_outbox_health(
        pending=health.pending,
        oldest_seconds=health.oldest_seconds,
        dead_letter_pending=health.dead_letter_pending,
        dead_letter_requeued=health.dead_letter_requeued,
        dead_letter_resolved=health.dead_letter_resolved,
    )
    return dispatched


async def configured_max_attempts() -> int:
    """Resolve retries as extra attempts, so zero still permits one delivery."""
    value = await get_config("outbox.publish_max_retries")
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 10:
        return value + 1
    logger.error(
        "outbox_invalid_publish_max_retries",
        value_type=type(value).__name__,
    )
    return DEFAULT_MAX_ATTEMPTS


async def dispatch_loop(
    *,
    publisher_factory: Callable[[], KombuEventPublisher],
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
) -> None:
    with publisher_factory() as publisher:
        while _running:
            dispatched = await dispatch_once(
                publisher=publisher,
                max_attempts=await configured_max_attempts(),
            )
            if dispatched == 0:
                await asyncio.sleep(poll_interval_seconds)


def main() -> None:
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    configure_logging()
    settings = get_settings()
    start_metrics_server(_metrics_port())
    asyncio.run(
        dispatch_loop(
            publisher_factory=lambda: KombuEventPublisher(settings.celery_broker_url),
        )
    )


def _metrics_port() -> int:
    raw_value = os.environ.get("OUTBOX_METRICS_PORT", "9101")
    try:
        port = int(raw_value)
    except ValueError as error:
        raise RuntimeError("OUTBOX_METRICS_PORT must be an integer") from error
    if port < 1 or port > 65535:
        raise RuntimeError("OUTBOX_METRICS_PORT must be between 1 and 65535")
    return port


if __name__ == "__main__":
    main()
