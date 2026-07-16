from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Protocol

from kombu import Connection

from app.workers.rabbitmq_topology import TASK_QUEUE_NAMES

SAFE_REPLAY_TASK_QUEUES: dict[str, str] = {
    "ragflow.create_upload_task": "ragflow_queue",
    "ragflow.create_delete_task": "ragflow_queue",
}
JSON_CONTENT_TYPE = "application/json"
JSON_CONTENT_ENCODING = "utf-8"
MAX_MESSAGE_BODY_BYTES = 16 * 1024
MAX_JSON_DEPTH = 6
MAX_JSON_NODES = 128
MAX_CONTAINER_ITEMS = 32
MAX_STRING_LENGTH = 1024
_CELERY_EMBEDDED_OPTION_KEYS = frozenset({"callbacks", "errbacks", "chain", "chord"})


class RabbitDeadLetterError(RuntimeError):
    """Base error whose messages never include broker payloads or credentials."""


class RabbitDeadLetterEmpty(RabbitDeadLetterError):
    pass


class RabbitDeadLetterUnsafe(RabbitDeadLetterError):
    pass


class RabbitDeadLetterChanged(RabbitDeadLetterError):
    pass


class RabbitDeadLetterUnavailable(RabbitDeadLetterError):
    pass


class CeleryTaskSender(Protocol):
    def send_task(
        self,
        name: str,
        args: list[str],
        *,
        kwargs: dict[str, object],
        queue: str,
        routing_key: str,
        task_id: str,
        delivery_mode: int,
    ) -> object: ...


class RawBrokerMessage:
    """Minimal AMQP message wrapper that never runs Kombu decode/decompress hooks."""

    def __init__(self, raw_message: object, channel: object) -> None:
        raw_properties: object = getattr(raw_message, "properties", None)
        self.properties: dict[str, object] = (
            raw_properties if isinstance(raw_properties, dict) else {}
        )
        raw_headers = self.properties.get("application_headers")
        self.headers: object = raw_headers if isinstance(raw_headers, dict) else {}
        self.body: object = getattr(raw_message, "body", None)
        self.content_type: object = self.properties.get("content_type")
        self.content_encoding: object = self.properties.get("content_encoding")
        self._delivery_tag: object = getattr(raw_message, "delivery_tag", None)
        self._channel = channel

    def ack(self) -> None:
        acknowledge = getattr(self._channel, "basic_ack", None)
        if not callable(acknowledge) or self._delivery_tag is None:
            raise RabbitDeadLetterUnavailable("broker acknowledgement is unavailable")
        acknowledge(self._delivery_tag)

    def reject(self, *, requeue: bool) -> None:
        reject = getattr(self._channel, "basic_reject", None)
        if not callable(reject) or self._delivery_tag is None:
            raise RabbitDeadLetterUnavailable("broker rejection is unavailable")
        reject(self._delivery_tag, requeue=requeue)


@dataclass(frozen=True)
class SafeRabbitDeadLetter:
    queue_name: str
    task_name: str
    original_task_id: uuid.UUID
    target_id: uuid.UUID


@dataclass(frozen=True)
class RabbitReplayResult:
    dead_letter: SafeRabbitDeadLetter
    replay_task_id: uuid.UUID
    raw_payload_copied: bool = False


def inspect_next_dead_letter(*, broker_url: str, queue_name: str) -> SafeRabbitDeadLetter:
    _require_queue(queue_name)
    try:
        with Connection(broker_url, connect_timeout=5) as connection:
            message = _get_raw_message(connection, f"{queue_name}.dlq")
            try:
                return parse_safe_dead_letter(
                    queue_name=queue_name,
                    headers=message.headers,
                    body=message.body,
                    content_type=message.content_type,
                    content_encoding=message.content_encoding,
                )
            finally:
                message.reject(requeue=True)
    except RabbitDeadLetterError:
        raise
    except Exception as error:
        raise RabbitDeadLetterUnavailable(type(error).__name__) from None


def replay_next_dead_letter(
    *,
    broker_url: str,
    queue_name: str,
    expected_original_task_id: uuid.UUID,
    sender: CeleryTaskSender,
) -> RabbitReplayResult:
    _require_queue(queue_name)
    try:
        with Connection(broker_url, connect_timeout=5) as connection:
            message = _get_raw_message(connection, f"{queue_name}.dlq")
            try:
                dead_letter = parse_safe_dead_letter(
                    queue_name=queue_name,
                    headers=message.headers,
                    body=message.body,
                    content_type=message.content_type,
                    content_encoding=message.content_encoding,
                )
                if dead_letter.original_task_id != expected_original_task_id:
                    raise RabbitDeadLetterChanged("dead-letter head changed before replay")
                replay_task_id = uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"rabbitmq-replay:{queue_name}:{dead_letter.original_task_id}",
                )
                sender.send_task(
                    dead_letter.task_name,
                    [str(dead_letter.target_id)],
                    kwargs={},
                    queue=queue_name,
                    routing_key=queue_name,
                    task_id=str(replay_task_id),
                    delivery_mode=2,
                )
            except Exception:
                message.reject(requeue=True)
                raise
            message.ack()
            return RabbitReplayResult(
                dead_letter=dead_letter,
                replay_task_id=replay_task_id,
            )
    except RabbitDeadLetterError:
        raise
    except Exception as error:
        raise RabbitDeadLetterUnavailable(type(error).__name__) from None


def parse_safe_dead_letter(
    *,
    queue_name: str,
    headers: object,
    body: object,
    content_type: object,
    content_encoding: object,
) -> SafeRabbitDeadLetter:
    _require_queue(queue_name)
    if not isinstance(headers, dict):
        raise RabbitDeadLetterUnsafe("dead-letter headers are invalid")
    task_name = headers.get("task")
    original_task_id = headers.get("id")
    if not isinstance(task_name, str) or SAFE_REPLAY_TASK_QUEUES.get(task_name) != queue_name:
        raise RabbitDeadLetterUnsafe("dead-letter task is not replay-allowlisted for this queue")
    if not isinstance(original_task_id, str):
        raise RabbitDeadLetterUnsafe("dead-letter task id is invalid")
    try:
        parsed_original_task_id = uuid.UUID(original_task_id)
    except ValueError as error:
        raise RabbitDeadLetterUnsafe("dead-letter task id is invalid") from error

    payload = _decode_json_body(
        body=body,
        content_type=content_type,
        content_encoding=content_encoding,
    )
    if not isinstance(payload, list) or len(payload) != 3:
        raise RabbitDeadLetterUnsafe("dead-letter payload shape is invalid")
    args, kwargs, embedded_options = payload
    if not isinstance(args, list) or len(args) != 1:
        raise RabbitDeadLetterUnsafe("dead-letter task arguments are invalid")
    if not isinstance(kwargs, dict) or kwargs:
        raise RabbitDeadLetterUnsafe("dead-letter task keyword arguments are forbidden")
    if (
        not isinstance(embedded_options, dict)
        or not set(embedded_options) <= _CELERY_EMBEDDED_OPTION_KEYS
        or any(value is not None for value in embedded_options.values())
    ):
        raise RabbitDeadLetterUnsafe("dead-letter embedded task options are forbidden")
    target_id = args[0]
    if not isinstance(target_id, str):
        raise RabbitDeadLetterUnsafe("dead-letter target id is invalid")
    try:
        parsed_target_id = uuid.UUID(target_id)
    except ValueError as error:
        raise RabbitDeadLetterUnsafe("dead-letter target id is invalid") from error
    return SafeRabbitDeadLetter(
        queue_name=queue_name,
        task_name=task_name,
        original_task_id=parsed_original_task_id,
        target_id=parsed_target_id,
    )


def _get_raw_message(connection: Connection, queue_name: str) -> RawBrokerMessage:
    """Use AMQP basic.get so Kombu never invokes a registered deserializer."""
    channel = connection.default_channel
    raw_message = channel.basic_get(queue=queue_name, no_ack=False)
    if raw_message is None:
        raise RabbitDeadLetterEmpty("dead-letter queue is empty")
    return RawBrokerMessage(raw_message, channel)


def _decode_json_body(
    *,
    body: object,
    content_type: object,
    content_encoding: object,
) -> object:
    if content_type != JSON_CONTENT_TYPE:
        raise RabbitDeadLetterUnsafe("dead-letter content type is forbidden")
    if (
        not isinstance(content_encoding, str)
        or content_encoding.strip().lower().replace("_", "-") != JSON_CONTENT_ENCODING
    ):
        raise RabbitDeadLetterUnsafe("dead-letter content encoding is forbidden")
    if not isinstance(body, bytes | bytearray | memoryview):
        raise RabbitDeadLetterUnsafe("dead-letter body must be raw bytes")
    raw_body = bytes(body)
    if len(raw_body) > MAX_MESSAGE_BODY_BYTES:
        raise RabbitDeadLetterUnsafe("dead-letter body exceeds the safe limit")
    try:
        text_body = raw_body.decode(JSON_CONTENT_ENCODING, errors="strict")
        payload = json.loads(text_body, parse_constant=_reject_json_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise RabbitDeadLetterUnsafe("dead-letter JSON body is invalid") from None
    _validate_json_tree(payload, depth=0, remaining_nodes=[MAX_JSON_NODES])
    return payload


def _reject_json_constant(_value: str) -> object:
    raise ValueError("non-finite JSON constant")


def _validate_json_tree(value: object, *, depth: int, remaining_nodes: list[int]) -> None:
    remaining_nodes[0] -= 1
    if remaining_nodes[0] < 0:
        raise RabbitDeadLetterUnsafe("dead-letter JSON structure exceeds the safe limit")
    if depth > MAX_JSON_DEPTH:
        raise RabbitDeadLetterUnsafe("dead-letter JSON nesting exceeds the safe limit")
    if isinstance(value, str):
        if len(value) > MAX_STRING_LENGTH:
            raise RabbitDeadLetterUnsafe("dead-letter JSON string exceeds the safe limit")
        return
    if value is None or isinstance(value, bool | int | float):
        return
    if isinstance(value, list):
        if len(value) > MAX_CONTAINER_ITEMS:
            raise RabbitDeadLetterUnsafe("dead-letter JSON list exceeds the safe limit")
        for item in value:
            _validate_json_tree(item, depth=depth + 1, remaining_nodes=remaining_nodes)
        return
    if isinstance(value, dict):
        if len(value) > MAX_CONTAINER_ITEMS:
            raise RabbitDeadLetterUnsafe("dead-letter JSON object exceeds the safe limit")
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > MAX_STRING_LENGTH:
                raise RabbitDeadLetterUnsafe("dead-letter JSON key exceeds the safe limit")
            _validate_json_tree(item, depth=depth + 1, remaining_nodes=remaining_nodes)
        return
    raise RabbitDeadLetterUnsafe("dead-letter JSON contains an unsupported value")


def _require_queue(queue_name: str) -> None:
    if queue_name not in TASK_QUEUE_NAMES:
        raise RabbitDeadLetterUnsafe("unknown task queue")
