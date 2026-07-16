"""Exercise RabbitMQ ack/requeue/DLX semantics with bounded safe Celery messages."""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Literal

from kombu import Connection, Queue

from app.core.config import get_settings
from app.workers.celery_app import celery_app
from app.workers.rabbitmq_replay import (
    SAFE_REPLAY_TASK_QUEUES,
    RabbitDeadLetterEmpty,
    RawBrokerMessage,
    _get_raw_message,
    parse_safe_dead_letter,
)
from app.workers.rabbitmq_topology import dead_letter_queues, task_queues

Mode = Literal["baseline", "observe-exhaustion", "verify-replay"]


@dataclass(frozen=True)
class QueueCounts:
    messages: int
    consumers: int


def _queue_entity(name: str, *, dead_letter: bool = False) -> Queue:
    entities = dead_letter_queues() if dead_letter else task_queues()
    for queue in entities:
        if queue.name == name:
            return queue
    raise RuntimeError(f"queue is not declared: {name}")


def _queue_counts(connection: Connection, queue_name: str) -> QueueCounts:
    result = connection.default_channel.queue_declare(queue=queue_name, passive=True)
    return QueueCounts(
        messages=int(result.message_count),
        consumers=int(result.consumer_count),
    )


def _publish_probe(
    *,
    task_name: str,
    queue_name: str,
    task_id: uuid.UUID,
    target_id: uuid.UUID,
    probe_run_id: uuid.UUID,
) -> None:
    celery_app.send_task(
        task_name,
        args=[str(target_id)],
        kwargs={},
        queue=queue_name,
        routing_key=queue_name,
        task_id=str(task_id),
        delivery_mode=2,
        retry=True,
        headers={"e2e_probe_run_id": str(probe_run_id)},
        argsrepr="(<e2e-target-id>,)",
    )


def _get_message(connection: Connection, queue_name: str) -> RawBrokerMessage:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            return _get_raw_message(connection, queue_name)
        except RabbitDeadLetterEmpty:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"timed out waiting for queue: {queue_name}")


def _validated_identity(
    message: RawBrokerMessage,
    *,
    queue_name: str,
    task_name: str,
    probe_run_id: uuid.UUID,
    expected_target_id: uuid.UUID | None = None,
    require_probe_header: bool = True,
) -> tuple[uuid.UUID, uuid.UUID, str]:
    headers = message.headers if isinstance(message.headers, dict) else {}
    dead_letter = parse_safe_dead_letter(
        queue_name=queue_name,
        headers=headers,
        body=message.body,
        content_type=message.content_type,
        content_encoding=message.content_encoding,
    )
    if dead_letter.task_name != task_name:
        raise RuntimeError("RabbitMQ probe consumed an unexpected task")
    if expected_target_id is not None and dead_letter.target_id != expected_target_id:
        raise RuntimeError("RabbitMQ probe consumed an unexpected target")
    if require_probe_header and headers.get("e2e_probe_run_id") != str(probe_run_id):
        raise RuntimeError("RabbitMQ probe run identity is missing")
    correlation_id = message.properties.get("correlation_id")
    if correlation_id != str(dead_letter.original_task_id):
        raise RuntimeError("RabbitMQ task correlation id does not match task id")
    delivery_mode = message.properties.get("delivery_mode")
    if delivery_mode not in {2, "2"}:
        raise RuntimeError("RabbitMQ task is not persistent")
    return dead_letter.original_task_id, dead_letter.target_id, str(correlation_id)


def _baseline(
    *,
    connection: Connection,
    queue_name: str,
    task_name: str,
    probe_run_id: uuid.UUID,
) -> dict[str, object]:
    dlq_name = f"{queue_name}.dlq"
    before = _queue_counts(connection, queue_name)
    dlq_before = _queue_counts(connection, dlq_name)
    if before.messages != 0 or before.consumers != 0 or dlq_before.messages != 0:
        raise RuntimeError("RabbitMQ baseline requires empty queues and zero consumers")

    success_task_id = uuid.uuid4()
    _publish_probe(
        task_name=task_name,
        queue_name=queue_name,
        task_id=success_task_id,
        target_id=uuid.uuid4(),
        probe_run_id=probe_run_id,
    )
    success_message = _get_message(connection, queue_name)
    try:
        observed_task_id, _target_id, success_correlation = _validated_identity(
            success_message,
            queue_name=queue_name,
            task_name=task_name,
            probe_run_id=probe_run_id,
        )
        if observed_task_id != success_task_id:
            raise RuntimeError("RabbitMQ success probe task identity changed")
    except Exception:
        success_message.reject(requeue=True)
        raise
    success_message.ack()
    success_dlq_count = _queue_counts(connection, dlq_name).messages

    retry_task_id = uuid.uuid4()
    _publish_probe(
        task_name=task_name,
        queue_name=queue_name,
        task_id=retry_task_id,
        target_id=uuid.uuid4(),
        probe_run_id=probe_run_id,
    )
    first_attempt = _get_message(connection, queue_name)
    try:
        observed_task_id, _target_id, retry_correlation = _validated_identity(
            first_attempt,
            queue_name=queue_name,
            task_name=task_name,
            probe_run_id=probe_run_id,
        )
        if observed_task_id != retry_task_id:
            raise RuntimeError("RabbitMQ retry probe task identity changed")
    except Exception:
        first_attempt.reject(requeue=True)
        raise
    first_attempt.reject(requeue=True)
    second_attempt = _get_message(connection, queue_name)
    try:
        second_task_id, _target_id, second_correlation = _validated_identity(
            second_attempt,
            queue_name=queue_name,
            task_name=task_name,
            probe_run_id=probe_run_id,
        )
        if second_task_id != retry_task_id or second_correlation != retry_correlation:
            raise RuntimeError("RabbitMQ retry did not preserve message identity")
    except Exception:
        second_attempt.reject(requeue=True)
        raise
    second_attempt.ack()
    retry_dlq_count = _queue_counts(connection, dlq_name).messages
    if _queue_counts(connection, queue_name).messages != 0:
        raise RuntimeError("RabbitMQ baseline left messages in the task queue")

    return {
        "success": {
            "task_id": str(success_task_id),
            "correlation_id": success_correlation,
            "probe_run_id": str(probe_run_id),
            "task_name": task_name,
            "queue_name": queue_name,
            "result": "passed",
            "dlq_count_after": success_dlq_count,
        },
        "intermediate_retry": {
            "task_id": str(retry_task_id),
            "correlation_id": retry_correlation,
            "probe_run_id": str(probe_run_id),
            "task_name": task_name,
            "queue_name": queue_name,
            "result": "passed",
            "retries_observed": 1,
            "dlq_count_during_retry": retry_dlq_count,
        },
    }


def _death_value(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _is_rejected_death(value: object, *, queue_name: str) -> bool:
    if not isinstance(value, dict):
        return False
    count = value.get("count")
    return (
        _death_value(value.get("queue")) == queue_name
        and _death_value(value.get("reason")) == "rejected"
        and isinstance(count, int)
        and not isinstance(count, bool)
        and count >= 1
    )


def _observe_exhaustion(
    *,
    connection: Connection,
    queue_name: str,
    task_name: str,
    probe_run_id: uuid.UUID,
    expected_target_id: uuid.UUID,
    expected_retries: int,
) -> dict[str, object]:
    dlq_name = f"{queue_name}.dlq"
    before = _queue_counts(connection, queue_name)
    dlq_before = _queue_counts(connection, dlq_name)
    if before.messages != 0 or before.consumers != 0 or dlq_before.messages != 1:
        raise RuntimeError(
            "RabbitMQ exhaustion observation requires an empty task queue, "
            "one dead letter, and zero consumers"
        )

    message = _get_message(connection, dlq_name)
    try:
        task_id, target_id, correlation_id = _validated_identity(
            message,
            queue_name=queue_name,
            task_name=task_name,
            probe_run_id=probe_run_id,
            expected_target_id=expected_target_id,
            require_probe_header=False,
        )
        headers = message.headers if isinstance(message.headers, dict) else {}
        retries = headers.get("retries")
        if (
            not isinstance(retries, int)
            or isinstance(retries, bool)
            or retries != expected_retries
        ):
            raise RuntimeError("Celery retry exhaustion count is invalid")
        deaths = headers.get("x-death")
        if not isinstance(deaths, list):
            raise RuntimeError("RabbitMQ x-death history is missing")
        rejected = any(_is_rejected_death(death, queue_name=queue_name) for death in deaths)
        if not rejected:
            raise RuntimeError("RabbitMQ x-death does not prove a rejected final attempt")
    finally:
        message.reject(requeue=True)

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if _queue_counts(connection, dlq_name).messages == 1:
            break
        time.sleep(0.1)
    if (
        _queue_counts(connection, dlq_name).messages != 1
        or _queue_counts(connection, queue_name).messages != 0
    ):
        raise RuntimeError("RabbitMQ exhaustion observation changed queue state")
    return {
        "task_id": str(task_id),
        "correlation_id": correlation_id,
        "probe_run_id": str(probe_run_id),
        "task_name": task_name,
        "queue_name": queue_name,
        "target_id": str(target_id),
        "result": "dead_lettered",
        "attempts": expected_retries + 1,
        "retry_count": expected_retries,
        "dead_letter_reason": "rejected",
        "delivery_path": "celery_worker_retry_exhaustion",
        "dlq_count_after": 1,
    }


def _verify_replay(
    *,
    connection: Connection,
    queue_name: str,
    task_name: str,
    probe_run_id: uuid.UUID,
    expected_target_id: uuid.UUID,
    expected_task_id: uuid.UUID,
) -> dict[str, object]:
    dlq_name = f"{queue_name}.dlq"
    before = _queue_counts(connection, queue_name)
    dlq_before = _queue_counts(connection, dlq_name)
    if before.messages != 1 or before.consumers != 0 or dlq_before.messages != 0:
        raise RuntimeError(
            "RabbitMQ replay verification requires one task, an empty DLQ, and zero consumers"
        )
    message = _get_message(connection, queue_name)
    try:
        task_id, target_id, correlation_id = _validated_identity(
            message,
            queue_name=queue_name,
            task_name=task_name,
            probe_run_id=probe_run_id,
            expected_target_id=expected_target_id,
            require_probe_header=False,
        )
        if task_id != expected_task_id or correlation_id != str(expected_task_id):
            raise RuntimeError("RabbitMQ clean-room replay identity changed")
    finally:
        message.reject(requeue=True)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if _queue_counts(connection, queue_name).messages == 1:
            break
        time.sleep(0.1)
    if _queue_counts(connection, queue_name).messages != 1:
        raise RuntimeError("RabbitMQ replay verification did not preserve the queued task")
    return {
        "task_id": str(task_id),
        "correlation_id": correlation_id,
        "target_id": str(target_id),
        "probe_run_id": str(probe_run_id),
        "task_name": task_name,
        "queue_name": queue_name,
        "persistent_message": True,
        "dlq_count_after": 0,
        "result": "passed",
    }


def exercise(
    *,
    mode: Mode,
    queue_name: str,
    task_name: str,
    probe_run_id: uuid.UUID,
    expected_target_id: uuid.UUID | None,
    expected_task_id: uuid.UUID | None,
    expected_retries: int,
) -> dict[str, object]:
    if SAFE_REPLAY_TASK_QUEUES.get(task_name) != queue_name:
        raise RuntimeError("E2E probe task is not in the clean-room replay allowlist")
    broker_url = get_settings().celery_broker_url
    with Connection(broker_url, connect_timeout=5) as connection:
        _queue_entity(queue_name).bind(connection.default_channel).declare()
        _queue_entity(f"{queue_name}.dlq", dead_letter=True).bind(
            connection.default_channel
        ).declare()
        if mode == "baseline":
            return _baseline(
                connection=connection,
                queue_name=queue_name,
                task_name=task_name,
                probe_run_id=probe_run_id,
            )
        if expected_target_id is None:
            raise RuntimeError("--expected-target-id is required")
        if mode == "verify-replay":
            if expected_task_id is None:
                raise RuntimeError("--expected-task-id is required for replay verification")
            return {
                "verified_replay": _verify_replay(
                    connection=connection,
                    queue_name=queue_name,
                    task_name=task_name,
                    probe_run_id=probe_run_id,
                    expected_target_id=expected_target_id,
                    expected_task_id=expected_task_id,
                )
            }
        return {
            "exhausted": _observe_exhaustion(
                connection=connection,
                queue_name=queue_name,
                task_name=task_name,
                probe_run_id=probe_run_id,
                expected_target_id=expected_target_id,
                expected_retries=expected_retries,
            )
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("baseline", "observe-exhaustion", "verify-replay"),
        required=True,
    )
    parser.add_argument("--queue", choices=("ragflow_queue",), default="ragflow_queue")
    parser.add_argument(
        "--task",
        choices=tuple(sorted(SAFE_REPLAY_TASK_QUEUES)),
        default="ragflow.create_upload_task",
    )
    parser.add_argument("--probe-run-id", type=uuid.UUID, required=True)
    parser.add_argument("--expected-target-id", type=uuid.UUID)
    parser.add_argument("--expected-task-id", type=uuid.UUID)
    parser.add_argument("--expected-retries", type=int, default=3)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = exercise(
        mode=args.mode,
        queue_name=args.queue,
        task_name=args.task,
        probe_run_id=args.probe_run_id,
        expected_target_id=args.expected_target_id,
        expected_task_id=args.expected_task_id,
        expected_retries=args.expected_retries,
    )
    sys.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
