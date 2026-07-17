from __future__ import annotations

import json
import uuid

import pytest

from app.workers import rabbitmq_replay
from app.workers.rabbitmq_replay import (
    RabbitDeadLetterUnavailable,
    RabbitDeadLetterUnsafe,
    SafeRabbitDeadLetter,
    parse_safe_dead_letter,
)


def _headers(task_name: str) -> dict[str, str]:
    return {"task": task_name, "id": str(uuid.uuid4())}


def _payload(target_id: uuid.UUID) -> list[object]:
    return [
        [str(target_id)],
        {},
        {"callbacks": None, "errbacks": None, "chain": None, "chord": None},
    ]


def _body(target_id: uuid.UUID) -> bytes:
    return json.dumps(_payload(target_id), separators=(",", ":")).encode()


def _parse(*, queue_name: str, task_name: str, target_id: uuid.UUID) -> SafeRabbitDeadLetter:
    return parse_safe_dead_letter(
        queue_name=queue_name,
        headers=_headers(task_name),
        body=_body(target_id),
        content_type="application/json",
        content_encoding="utf-8",
    )


def test_parser_accepts_only_domain_reconstruction_task() -> None:
    target_id = uuid.uuid4()

    dead_letter = _parse(
        queue_name="ragflow_queue",
        task_name="ragflow.create_upload_task",
        target_id=target_id,
    )

    assert dead_letter.task_name == "ragflow.create_upload_task"
    assert dead_letter.target_id == target_id
    assert not hasattr(dead_letter, "payload")


def test_parser_accepts_bounded_utf8_text_body() -> None:
    target_id = uuid.uuid4()

    dead_letter = parse_safe_dead_letter(
        queue_name="ragflow_queue",
        headers=_headers("ragflow.create_upload_task"),
        body=_body(target_id).decode("utf-8"),
        content_type="application/json",
        content_encoding="UTF_8",
    )

    assert dead_letter.target_id == target_id


def test_parser_applies_byte_limit_to_utf8_text_body() -> None:
    oversized_text = "界" * rabbitmq_replay.MAX_MESSAGE_BODY_BYTES

    with pytest.raises(RabbitDeadLetterUnsafe, match="safe limit"):
        parse_safe_dead_letter(
            queue_name="ragflow_queue",
            headers=_headers("ragflow.create_upload_task"),
            body=oversized_text,
            content_type="application/json",
            content_encoding="utf-8",
        )


def test_parser_rejects_unencodable_utf8_text_without_echoing_payload() -> None:
    invalid_text = '["\ud800-sensitive-token"]'

    with pytest.raises(RabbitDeadLetterUnsafe, match="JSON body is invalid") as captured:
        parse_safe_dead_letter(
            queue_name="ragflow_queue",
            headers=_headers("ragflow.create_upload_task"),
            body=invalid_text,
            content_type="application/json",
            content_encoding="utf-8",
        )

    assert "sensitive-token" not in str(captured.value)


@pytest.mark.parametrize("non_finite_number", ("NaN", "1e999"))
def test_parser_rejects_non_finite_json_from_utf8_text(non_finite_number: str) -> None:
    target_id = uuid.uuid4()
    payload = json.dumps(_payload(target_id), separators=(",", ":")).replace(
        '{"callbacks":null',
        f'{{"unexpected":{non_finite_number},"callbacks":null',
    )

    with pytest.raises(RabbitDeadLetterUnsafe) as captured:
        parse_safe_dead_letter(
            queue_name="ragflow_queue",
            headers=_headers("ragflow.create_upload_task"),
            body=payload,
            content_type="application/json",
            content_encoding="utf-8",
        )

    assert non_finite_number not in str(captured.value)


@pytest.mark.parametrize(
    "task_name",
    (
        "ai.analyze_file",
        "ragflow.upload",
        "ragflow.delete",
        "notification.review_approved",
    ),
)
def test_parser_rejects_tasks_that_cannot_rebuild_domain_state(task_name: str) -> None:
    with pytest.raises(RabbitDeadLetterUnsafe, match="not replay-allowlisted"):
        _parse(
            queue_name="ragflow_queue",
            task_name=task_name,
            target_id=uuid.uuid4(),
        )


def test_parser_rejects_allowlisted_task_on_wrong_queue() -> None:
    with pytest.raises(RabbitDeadLetterUnsafe, match="not replay-allowlisted"):
        _parse(
            queue_name="document_queue",
            task_name="ragflow.create_upload_task",
            target_id=uuid.uuid4(),
        )


def test_parser_rejects_kwargs_instead_of_copying_them() -> None:
    target_id = uuid.uuid4()

    with pytest.raises(RabbitDeadLetterUnsafe, match="keyword arguments are forbidden"):
        parse_safe_dead_letter(
            queue_name="ragflow_queue",
            headers=_headers("ragflow.create_delete_task"),
            body=json.dumps([[str(target_id)], {"api_key": "must-not-copy"}, {}]).encode(),
            content_type="application/json",
            content_encoding="utf-8",
        )


class _FakeMessage:
    def __init__(
        self,
        *,
        target_id: uuid.UUID,
        body: bytes | None = None,
        content_type: str = "application/json",
        content_encoding: str = "utf-8",
    ) -> None:
        self.headers = _headers("ragflow.create_upload_task")
        self.properties = {
            "application_headers": self.headers,
            "content_type": content_type,
            "content_encoding": content_encoding,
        }
        self.body = _body(target_id) if body is None else body
        self.delivery_tag = "delivery-tag-1"
        self.acked = False
        self.requeued = False
        self.payload_accesses = 0

    @property
    def payload(self) -> object:
        self.payload_accesses += 1
        raise AssertionError("Kombu payload deserialization must never be invoked")

    def ack(self) -> None:
        self.acked = True

    def reject(self, *, requeue: bool) -> None:
        self.requeued = requeue


class _FakeChannel:
    def __init__(self, message: _FakeMessage) -> None:
        self.message = message

    def basic_get(self, *, queue: str, no_ack: bool) -> _FakeMessage:
        assert queue == "ragflow_queue.dlq"
        assert no_ack is False
        return self.message

    def basic_ack(self, delivery_tag: object) -> None:
        assert delivery_tag == self.message.delivery_tag
        self.message.acked = True

    def basic_reject(self, delivery_tag: object, *, requeue: bool) -> None:
        assert delivery_tag == self.message.delivery_tag
        self.message.requeued = requeue


class _FakeConnection:
    def __init__(self, message: _FakeMessage) -> None:
        self.message = message
        self.default_channel = _FakeChannel(message)

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _FailingSender:
    def send_task(self, *_args: object, **_kwargs: object) -> object:
        raise RuntimeError("publish confirmation failed")


class _CapturingSender:
    def __init__(self) -> None:
        self.name = ""
        self.args: list[str] = []
        self.options: dict[str, object] = {}

    def send_task(
        self,
        name: str,
        args: list[str],
        **kwargs: object,
    ) -> object:
        self.name = name
        self.args = args
        self.options = kwargs
        return object()


def test_clean_room_replay_is_deterministic_json_safe_and_persistent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_id = uuid.uuid4()
    message = _FakeMessage(target_id=target_id)
    original_task_id = uuid.UUID(str(message.headers["id"]))
    sender = _CapturingSender()
    monkeypatch.setattr(
        rabbitmq_replay,
        "Connection",
        lambda *_args, **_kwargs: _FakeConnection(message),
    )

    result = rabbitmq_replay.replay_next_dead_letter(
        broker_url="amqp://redacted.invalid//",
        queue_name="ragflow_queue",
        expected_original_task_id=original_task_id,
        sender=sender,
    )

    expected_replay_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"rabbitmq-replay:ragflow_queue:{original_task_id}",
    )
    assert result.replay_task_id == expected_replay_id
    assert sender.name == "ragflow.create_upload_task"
    assert sender.args == [str(target_id)]
    assert sender.options == {
        "kwargs": {},
        "queue": "ragflow_queue",
        "routing_key": "ragflow_queue",
        "task_id": str(expected_replay_id),
        "delivery_mode": 2,
    }
    assert message.acked is True
    assert message.requeued is False
    assert message.payload_accesses == 0


def test_replay_publish_failure_keeps_original_message_in_dlq(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_id = uuid.uuid4()
    message = _FakeMessage(target_id=target_id)
    original_task_id = uuid.UUID(str(message.headers["id"]))
    monkeypatch.setattr(
        rabbitmq_replay,
        "Connection",
        lambda *_args, **_kwargs: _FakeConnection(message),
    )

    with pytest.raises(RabbitDeadLetterUnavailable):
        rabbitmq_replay.replay_next_dead_letter(
            broker_url="amqp://redacted.invalid//",
            queue_name="ragflow_queue",
            expected_original_task_id=original_task_id,
            sender=_FailingSender(),
        )

    assert message.requeued is True
    assert message.acked is False
    assert message.payload_accesses == 0


@pytest.mark.parametrize(
    ("content_type", "body"),
    (
        ("application/x-python-serialize", b"pickle-secret-api-key"),
        ("application/x-yaml", b"password: yaml-secret"),
        ("text/yaml", b"email: person@example.com"),
    ),
)
def test_inspection_rejects_non_json_without_triggering_registered_deserializers(
    monkeypatch: pytest.MonkeyPatch,
    content_type: str,
    body: bytes,
) -> None:
    message = _FakeMessage(
        target_id=uuid.uuid4(),
        body=body,
        content_type=content_type,
    )
    monkeypatch.setattr(
        rabbitmq_replay,
        "Connection",
        lambda *_args, **_kwargs: _FakeConnection(message),
    )

    with pytest.raises(RabbitDeadLetterUnsafe, match="content type") as captured:
        rabbitmq_replay.inspect_next_dead_letter(
            broker_url="amqp://redacted.invalid//",
            queue_name="ragflow_queue",
        )

    assert body.decode(errors="ignore") not in str(captured.value)
    assert message.payload_accesses == 0
    assert message.requeued is True
    assert message.acked is False


def test_oversized_json_is_rejected_before_stdlib_json_decode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = b"[" + b"0," * rabbitmq_replay.MAX_MESSAGE_BODY_BYTES + b"0]"
    message = _FakeMessage(target_id=uuid.uuid4(), body=body)
    decode_calls = 0

    def forbidden_decode(*_args: object, **_kwargs: object) -> object:
        nonlocal decode_calls
        decode_calls += 1
        raise AssertionError("oversized JSON must be rejected before decoding")

    monkeypatch.setattr(json, "loads", forbidden_decode)
    monkeypatch.setattr(
        rabbitmq_replay,
        "Connection",
        lambda *_args, **_kwargs: _FakeConnection(message),
    )

    with pytest.raises(RabbitDeadLetterUnsafe, match="safe limit"):
        rabbitmq_replay.inspect_next_dead_letter(
            broker_url="amqp://redacted.invalid//",
            queue_name="ragflow_queue",
        )

    assert decode_calls == 0
    assert message.payload_accesses == 0
    assert message.requeued is True
    assert message.acked is False


def test_json_nesting_and_embedded_callbacks_are_rejected() -> None:
    target_id = uuid.uuid4()
    deeply_nested: object = None
    for _ in range(rabbitmq_replay.MAX_JSON_DEPTH + 2):
        deeply_nested = [deeply_nested]
    unsafe_payloads = (
        [[str(target_id)], {}, {"callbacks": ["untrusted"]}],
        [[str(target_id)], {}, {"callbacks": deeply_nested}],
    )

    for payload in unsafe_payloads:
        with pytest.raises(RabbitDeadLetterUnsafe):
            parse_safe_dead_letter(
                queue_name="ragflow_queue",
                headers=_headers("ragflow.create_upload_task"),
                body=json.dumps(payload).encode(),
                content_type="application/json",
                content_encoding="utf-8",
            )


def test_celery_publish_uses_confirms_and_bounded_retry() -> None:
    from app.workers.celery_app import celery_app

    assert celery_app.conf.broker_transport_options["confirm_publish"] is True
    assert celery_app.conf.task_publish_retry is True
    assert celery_app.conf.task_publish_retry_policy["max_retries"] == 3
    assert celery_app.conf.task_serializer == "json"
    assert celery_app.conf.accept_content == ["json"]
