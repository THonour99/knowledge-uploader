from __future__ import annotations

from collections.abc import Iterable
from typing import Final

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
TOPOLOGY_CONNECT_ATTEMPT_TIMEOUT_SECONDS: Final = 5
TOPOLOGY_CONNECT_MAX_RETRIES: Final = 10
TOPOLOGY_CONNECT_INTERVAL_START_SECONDS: Final = 1
TOPOLOGY_CONNECT_INTERVAL_STEP_SECONDS: Final = 1
TOPOLOGY_CONNECT_INTERVAL_MAX_SECONDS: Final = 5
TOPOLOGY_CONNECT_TOTAL_TIMEOUT_SECONDS: Final = 45


class RabbitmqTopologyError(RuntimeError):
    """Sanitized startup failure safe to expose through container stderr."""


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


def _log_connection_retry(error: BaseException, interval: float) -> None:
    logger.warning(
        "rabbitmq_topology_connection_retry",
        error_type=type(error).__name__,
        retry_in_seconds=float(interval),
    )


def declare_topology(broker_url: str) -> None:
    failure_type: str | None = None
    try:
        with Connection(
            broker_url,
            connect_timeout=TOPOLOGY_CONNECT_ATTEMPT_TIMEOUT_SECONDS,
        ) as connection:
            connection.ensure_connection(
                errback=_log_connection_retry,
                max_retries=TOPOLOGY_CONNECT_MAX_RETRIES,
                interval_start=TOPOLOGY_CONNECT_INTERVAL_START_SECONDS,
                interval_step=TOPOLOGY_CONNECT_INTERVAL_STEP_SECONDS,
                interval_max=TOPOLOGY_CONNECT_INTERVAL_MAX_SECONDS,
                timeout=TOPOLOGY_CONNECT_TOTAL_TIMEOUT_SECONDS,
            )
            channel = connection.channel()
            _declare_entities(channel, (TASK_EXCHANGE, TASK_DEAD_LETTER_EXCHANGE))
            _declare_entities(channel, (*task_queues(), *dead_letter_queues()))
    except Exception as error:
        failure_type = type(error).__name__
    if failure_type is not None:
        logger.error("rabbitmq_topology_declaration_failed", error_type=failure_type)
        raise RabbitmqTopologyError("RabbitMQ topology declaration failed") from None


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
