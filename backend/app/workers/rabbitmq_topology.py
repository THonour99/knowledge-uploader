from __future__ import annotations

from collections.abc import Iterable

from kombu import Connection, Exchange, Queue

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

TASK_EXCHANGE = Exchange("knowledge.tasks", type="direct", durable=True)
TASK_DEAD_LETTER_EXCHANGE = Exchange("knowledge.tasks.dlx", type="direct", durable=True)
TASK_QUEUE_NAMES: tuple[str, ...] = (
    "document_queue",
    "ai_queue",
    "ragflow_queue",
    "notification_queue",
)


def task_queues() -> tuple[Queue, ...]:
    queues: list[Queue] = []
    for queue_name in TASK_QUEUE_NAMES:
        queues.append(
            Queue(
                queue_name,
                exchange=TASK_EXCHANGE,
                routing_key=queue_name,
                durable=True,
                queue_arguments={
                    "x-dead-letter-exchange": TASK_DEAD_LETTER_EXCHANGE.name,
                    "x-dead-letter-routing-key": f"{queue_name}.dead",
                },
            )
        )
    return tuple(queues)


def dead_letter_queues() -> tuple[Queue, ...]:
    return tuple(
        Queue(
            f"{queue_name}.dlq",
            exchange=TASK_DEAD_LETTER_EXCHANGE,
            routing_key=f"{queue_name}.dead",
            durable=True,
        )
        for queue_name in TASK_QUEUE_NAMES
    )


def declare_topology(broker_url: str) -> None:
    with Connection(broker_url) as connection:
        channel = connection.channel()
        _declare_entities(channel, (TASK_EXCHANGE, TASK_DEAD_LETTER_EXCHANGE))
        _declare_entities(channel, (*task_queues(), *dead_letter_queues()))


def _declare_entities(channel: object, entities: Iterable[Exchange | Queue]) -> None:
    for entity in entities:
        entity.maybe_bind(channel)
        entity.declare()


def main() -> None:
    configure_logging()
    declare_topology(get_settings().celery_broker_url)
    logger.info(
        "rabbitmq_task_topology_declared",
        queue_count=len(TASK_QUEUE_NAMES),
        dead_letter_queue_count=len(TASK_QUEUE_NAMES),
    )


if __name__ == "__main__":
    main()
