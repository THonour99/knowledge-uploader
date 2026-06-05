from __future__ import annotations

import asyncio
import signal
from collections.abc import Callable
from typing import Protocol

from kombu import Connection, Exchange, Producer

from app.core.config import get_settings
from app.core.database import AsyncSessionFactory
from app.core.logging import configure_logging, get_logger
from app.core.outbox import EventOutbox, OutboxRepository
from app.modules.ragflow.events import RAGFLOW_SYNC_TASK_QUEUED
from app.modules.review.events import REVIEW_FILE_APPROVED
from app.workers.celery_app import celery_app

_running = True
logger = get_logger(__name__)
EVENT_EXCHANGE = "knowledge.events"
DEFAULT_BATCH_SIZE = 100
DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_POLL_INTERVAL_SECONDS = 0.5


class EventPublisher(Protocol):
    def publish(self, event: EventOutbox) -> None:
        pass


class CeleryTaskSender(Protocol):
    def send_task(self, name: str, args: list[str], queue: str) -> object:
        pass


def dispatch_celery_task_for_event(
    event: EventOutbox,
    *,
    sender: CeleryTaskSender = celery_app,
) -> None:
    if event.event_type != RAGFLOW_SYNC_TASK_QUEUED:
        if event.event_type != REVIEW_FILE_APPROVED:
            return
        ragflow_dataset_id = event.payload.get("ragflow_dataset_id")
        if not isinstance(ragflow_dataset_id, str) or not ragflow_dataset_id:
            return
        file_id = event.payload.get("file_id")
        if not isinstance(file_id, str) or not file_id:
            msg = "file approved event missing file_id"
            raise RuntimeError(msg)
        sender.send_task("ragflow.create_upload_task", args=[file_id], queue="ragflow_queue")
        return

    sync_task_id = event.payload.get("sync_task_id")
    if not isinstance(sync_task_id, str) or not sync_task_id:
        msg = "sync task event missing sync_task_id"
        raise RuntimeError(msg)
    sender.send_task("ragflow.upload", args=[sync_task_id], queue="ragflow_queue")


class KombuEventPublisher:
    def __init__(self, broker_url: str) -> None:
        self._broker_url = broker_url
        self._connection: Connection | None = None
        self._producer: Producer | None = None
        self._exchange = Exchange(EVENT_EXCHANGE, type="topic", durable=True)

    def __enter__(self) -> KombuEventPublisher:
        self._connection = Connection(self._broker_url)
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
    async with AsyncSessionFactory() as session:
        async with session.begin():
            repository = OutboxRepository(session)
            events = await repository.fetch_pending(limit=batch_size, max_attempts=max_attempts)
            for event in events:
                try:
                    publisher.publish(event)
                except Exception as exc:
                    error_type = type(exc).__name__
                    await repository.mark_failed(event, error_type)
                    logger.warning(
                        "outbox_publish_failed",
                        event_id=event.id,
                        event_type=event.event_type,
                        error_type=error_type,
                    )
                    continue
                await repository.mark_published(event)
                dispatched += 1
    return dispatched


async def dispatch_loop(
    *,
    publisher_factory: Callable[[], KombuEventPublisher],
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
) -> None:
    with publisher_factory() as publisher:
        while _running:
            dispatched = await dispatch_once(publisher=publisher)
            if dispatched == 0:
                await asyncio.sleep(poll_interval_seconds)


def main() -> None:
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    configure_logging()
    settings = get_settings()
    asyncio.run(
        dispatch_loop(
            publisher_factory=lambda: KombuEventPublisher(settings.celery_broker_url),
        )
    )


if __name__ == "__main__":
    main()
