from __future__ import annotations

import importlib.util
import json
import sys
import uuid
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


def _load_exercise() -> ModuleType:
    backend_root = Path(__file__).parents[2] / "backend"
    script = backend_root / "scripts/exercise_rabbitmq_dlq.py"
    sys.path.insert(0, str(backend_root))
    try:
        spec = importlib.util.spec_from_file_location("exercise_rabbitmq_dlq", script)
        if spec is None or spec.loader is None:
            raise RuntimeError("could not load RabbitMQ DLQ exercise")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(backend_root))


class _Message:
    def __init__(self, *, retries: int, include_death: bool = True) -> None:
        self.headers: dict[str, object] = {"retries": retries}
        if include_death:
            self.headers["x-death"] = [
                {
                    "queue": "ragflow_queue",
                    "reason": "rejected",
                    "count": 1,
                }
            ]
        self.requeues: list[bool] = []

    def reject(self, *, requeue: bool) -> None:
        self.requeues.append(requeue)


class _IdentityMessage:
    def __init__(
        self,
        *,
        task_id: uuid.UUID,
        target_id: uuid.UUID,
        probe_run_id: uuid.UUID,
        body: object | None = None,
        retries: int | None = None,
        include_death: bool = False,
    ) -> None:
        self.headers: dict[str, object] = {
            "task": "ragflow.create_upload_task",
            "id": str(task_id),
            "e2e_probe_run_id": str(probe_run_id),
        }
        if retries is not None:
            self.headers["retries"] = retries
        if include_death:
            self.headers["x-death"] = [
                {
                    "queue": "ragflow_queue",
                    "reason": "rejected",
                    "count": 1,
                }
            ]
        self.body = (
            json.dumps(
                [
                    [str(target_id)],
                    {},
                    {
                        "callbacks": None,
                        "errbacks": None,
                        "chain": None,
                        "chord": None,
                    },
                ]
            )
            if body is None
            else body
        )
        self.content_type = "application/json"
        self.content_encoding = "utf-8"
        self.properties: dict[str, object] = {
            "correlation_id": str(task_id),
            "delivery_mode": 2,
        }
        self.acks = 0
        self.requeues: list[bool] = []

    def ack(self) -> None:
        self.acks += 1

    def reject(self, *, requeue: bool) -> None:
        self.requeues.append(requeue)


def _patch_queue_counts(
    exercise: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    *,
    main_messages: int,
    dead_letter_messages: int,
) -> None:
    def counts(_connection: object, queue_name: str) -> object:
        messages = dead_letter_messages if queue_name.endswith(".dlq") else main_messages
        return exercise.QueueCounts(messages=messages, consumers=0)

    monkeypatch.setattr(exercise, "_queue_counts", counts)


def _patch_observation(
    exercise: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    message: _Message,
) -> tuple[uuid.UUID, uuid.UUID]:
    task_id = uuid.uuid4()
    target_id = uuid.uuid4()

    def counts(_connection: object, queue_name: str) -> object:
        if queue_name == "ragflow_queue":
            return exercise.QueueCounts(messages=0, consumers=0)
        return exercise.QueueCounts(messages=1, consumers=0)

    monkeypatch.setattr(exercise, "_queue_counts", counts)
    monkeypatch.setattr(exercise, "_get_message", lambda *_args, **_kwargs: message)
    monkeypatch.setattr(
        exercise,
        "_validated_identity",
        lambda *_args, **_kwargs: (task_id, target_id, str(task_id)),
    )
    monkeypatch.setattr(
        exercise,
        "_validated_retry_identity",
        lambda *_args, **_kwargs: (task_id, target_id, str(task_id)),
    )
    return task_id, target_id


def test_retry_identity_parser_accepts_only_json_celery_retry_shape() -> None:
    exercise = _load_exercise()
    task_id = uuid.uuid4()
    target_id = uuid.uuid4()

    message = SimpleNamespace(
        headers={
            "task": "ragflow.create_upload_task",
            "id": str(task_id),
            "retries": 1,
        },
        body=json.dumps(
            [
                [str(target_id)],
                {},
                {
                    "callbacks": None,
                    "errbacks": None,
                    "chain": [],
                    "chord": None,
                },
            ]
        ).encode("utf-8"),
        content_type="application/json",
        content_encoding="utf-8",
        properties={
            "correlation_id": str(task_id),
            "delivery_mode": 2,
        },
    )

    observed = exercise._validated_retry_identity(
        message,
        queue_name="ragflow_queue",
        task_name="ragflow.create_upload_task",
        expected_target_id=target_id,
    )

    assert observed == (task_id, target_id, str(task_id))


def test_retry_identity_parser_accepts_bounded_json_safe_embedded_metadata() -> None:
    exercise = _load_exercise()
    task_id = uuid.uuid4()
    target_id = uuid.uuid4()
    embedded_options = {
        "callbacks": None,
        "delivery_metadata": {"attempt": 1, "labels": ["retry"]},
    }

    message = SimpleNamespace(
        headers={
            "task": "ragflow.create_upload_task",
            "id": str(task_id),
            "retries": 1,
        },
        body=json.dumps([[str(target_id)], {}, embedded_options]).encode("utf-8"),
        content_type="application/json",
        content_encoding="utf-8",
        properties={
            "correlation_id": str(task_id),
            "delivery_mode": 2,
        },
    )

    observed = exercise._validated_retry_identity(
        message,
        queue_name="ragflow_queue",
        task_name="ragflow.create_upload_task",
        expected_target_id=target_id,
    )

    assert observed == (task_id, target_id, str(task_id))


def test_retry_identity_parser_accepts_bounded_utf8_text_body() -> None:
    exercise = _load_exercise()
    task_id = uuid.uuid4()
    target_id = uuid.uuid4()
    message = SimpleNamespace(
        headers={
            "task": "ragflow.create_upload_task",
            "id": str(task_id),
            "retries": 1,
        },
        body=json.dumps([[str(target_id)], {}, {"callbacks": None}]),
        content_type="application/json",
        content_encoding="utf-8",
        properties={
            "correlation_id": str(task_id),
            "delivery_mode": 2,
        },
    )

    observed = exercise._validated_retry_identity(
        message,
        queue_name="ragflow_queue",
        task_name="ragflow.create_upload_task",
        expected_target_id=target_id,
    )

    assert observed == (task_id, target_id, str(task_id))


def test_retry_identity_parser_applies_byte_limit_to_utf8_text_body() -> None:
    exercise = _load_exercise()
    message = SimpleNamespace(
        content_type="application/json",
        content_encoding="utf-8",
        body="界" * exercise.RETRY_OBSERVATION_MAX_BODY_BYTES,
    )

    with pytest.raises(RuntimeError, match="observation limit"):
        exercise._decode_retry_observation_body(message)


@pytest.mark.parametrize("non_finite_number", ("NaN", "1e999"))
def test_retry_identity_parser_rejects_non_finite_text_without_echo(
    non_finite_number: str,
) -> None:
    exercise = _load_exercise()
    message = SimpleNamespace(
        content_type="application/json",
        content_encoding="utf-8",
        body=f'[["target"], {{}}, {{"unexpected": {non_finite_number}}}]',
    )

    with pytest.raises(RuntimeError) as captured:
        exercise._decode_retry_observation_body(message)

    assert non_finite_number not in str(captured.value)


def test_retry_identity_parser_rejects_excessive_json_depth() -> None:
    exercise = _load_exercise()
    nested: object = None
    for _index in range(exercise.RETRY_OBSERVATION_MAX_JSON_DEPTH + 2):
        nested = {"value": nested}
    message = SimpleNamespace(
        content_type="application/json",
        content_encoding="utf-8",
        body=json.dumps([[], {}, {"nested": nested}]).encode("utf-8"),
    )

    with pytest.raises(RuntimeError, match="depth limit"):
        exercise._decode_retry_observation_body(message)


def test_baseline_accepts_bounded_utf8_text_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exercise = _load_exercise()
    probe_run_id = uuid.uuid4()
    success_task_id = uuid.uuid4()
    success_target_id = uuid.uuid4()
    retry_task_id = uuid.uuid4()
    retry_target_id = uuid.uuid4()
    messages = iter(
        (
            _IdentityMessage(
                task_id=success_task_id,
                target_id=success_target_id,
                probe_run_id=probe_run_id,
            ),
            _IdentityMessage(
                task_id=retry_task_id,
                target_id=retry_target_id,
                probe_run_id=probe_run_id,
            ),
            _IdentityMessage(
                task_id=retry_task_id,
                target_id=retry_target_id,
                probe_run_id=probe_run_id,
            ),
        )
    )
    generated_ids = iter((success_task_id, success_target_id, retry_task_id, retry_target_id))
    _patch_queue_counts(
        exercise,
        monkeypatch,
        main_messages=0,
        dead_letter_messages=0,
    )
    monkeypatch.setattr(exercise, "_publish_probe", lambda **_kwargs: None)
    monkeypatch.setattr(exercise, "_get_message", lambda *_args, **_kwargs: next(messages))
    monkeypatch.setattr(exercise.uuid, "uuid4", lambda: next(generated_ids))

    result = exercise._baseline(
        connection=object(),
        queue_name="ragflow_queue",
        task_name="ragflow.create_upload_task",
        probe_run_id=probe_run_id,
    )

    assert result["success"]["result"] == "passed"
    assert result["intermediate_retry"]["result"] == "passed"


def test_baseline_requeues_malicious_utf8_text_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exercise = _load_exercise()
    probe_run_id = uuid.uuid4()
    task_id = uuid.uuid4()
    target_id = uuid.uuid4()
    message = _IdentityMessage(
        task_id=task_id,
        target_id=target_id,
        probe_run_id=probe_run_id,
        body="secret-marker-\ud800",
    )
    generated_ids = iter((task_id, target_id))
    _patch_queue_counts(
        exercise,
        monkeypatch,
        main_messages=0,
        dead_letter_messages=0,
    )
    monkeypatch.setattr(exercise, "_publish_probe", lambda **_kwargs: None)
    monkeypatch.setattr(exercise, "_get_message", lambda *_args, **_kwargs: message)
    monkeypatch.setattr(exercise.uuid, "uuid4", lambda: next(generated_ids))

    with pytest.raises(RuntimeError, match="dead-letter JSON body is invalid") as error:
        exercise._baseline(
            connection=object(),
            queue_name="ragflow_queue",
            task_name="ragflow.create_upload_task",
            probe_run_id=probe_run_id,
        )

    assert "secret-marker" not in str(error.value)
    assert message.acks == 0
    assert message.requeues == [True]


def test_exhaustion_accepts_bounded_utf8_text_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exercise = _load_exercise()
    probe_run_id = uuid.uuid4()
    task_id = uuid.uuid4()
    target_id = uuid.uuid4()
    message = _IdentityMessage(
        task_id=task_id,
        target_id=target_id,
        probe_run_id=probe_run_id,
        retries=3,
        include_death=True,
    )
    _patch_queue_counts(
        exercise,
        monkeypatch,
        main_messages=0,
        dead_letter_messages=1,
    )
    monkeypatch.setattr(exercise, "_get_message", lambda *_args, **_kwargs: message)

    result = exercise._observe_exhaustion(
        connection=object(),
        queue_name="ragflow_queue",
        task_name="ragflow.create_upload_task",
        probe_run_id=probe_run_id,
        expected_target_id=target_id,
        expected_retries=3,
    )

    assert result["task_id"] == str(task_id)
    assert result["result"] == "dead_lettered"
    assert message.requeues == [True]


def test_exhaustion_requeues_malicious_utf8_text_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exercise = _load_exercise()
    probe_run_id = uuid.uuid4()
    task_id = uuid.uuid4()
    target_id = uuid.uuid4()
    message = _IdentityMessage(
        task_id=task_id,
        target_id=target_id,
        probe_run_id=probe_run_id,
        body="secret-marker-\ud800",
        retries=3,
        include_death=True,
    )
    _patch_queue_counts(
        exercise,
        monkeypatch,
        main_messages=0,
        dead_letter_messages=1,
    )
    monkeypatch.setattr(exercise, "_get_message", lambda *_args, **_kwargs: message)

    with pytest.raises(RuntimeError, match="dead-letter JSON body is invalid") as error:
        exercise._observe_exhaustion(
            connection=object(),
            queue_name="ragflow_queue",
            task_name="ragflow.create_upload_task",
            probe_run_id=probe_run_id,
            expected_target_id=target_id,
            expected_retries=3,
        )

    assert "secret-marker" not in str(error.value)
    assert message.requeues == [True]


def test_replay_verification_accepts_bounded_utf8_text_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exercise = _load_exercise()
    probe_run_id = uuid.uuid4()
    task_id = uuid.uuid4()
    target_id = uuid.uuid4()
    message = _IdentityMessage(
        task_id=task_id,
        target_id=target_id,
        probe_run_id=probe_run_id,
    )
    _patch_queue_counts(
        exercise,
        monkeypatch,
        main_messages=1,
        dead_letter_messages=0,
    )
    monkeypatch.setattr(exercise, "_get_message", lambda *_args, **_kwargs: message)

    result = exercise._verify_replay(
        connection=object(),
        queue_name="ragflow_queue",
        task_name="ragflow.create_upload_task",
        probe_run_id=probe_run_id,
        expected_target_id=target_id,
        expected_task_id=task_id,
    )

    assert result["task_id"] == str(task_id)
    assert result["result"] == "passed"
    assert message.requeues == [True]


def test_replay_verification_requeues_malicious_utf8_text_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exercise = _load_exercise()
    probe_run_id = uuid.uuid4()
    task_id = uuid.uuid4()
    target_id = uuid.uuid4()
    message = _IdentityMessage(
        task_id=task_id,
        target_id=target_id,
        probe_run_id=probe_run_id,
        body="secret-marker-\ud800",
    )
    _patch_queue_counts(
        exercise,
        monkeypatch,
        main_messages=1,
        dead_letter_messages=0,
    )
    monkeypatch.setattr(exercise, "_get_message", lambda *_args, **_kwargs: message)

    with pytest.raises(RuntimeError, match="dead-letter JSON body is invalid") as error:
        exercise._verify_replay(
            connection=object(),
            queue_name="ragflow_queue",
            task_name="ragflow.create_upload_task",
            probe_run_id=probe_run_id,
            expected_target_id=target_id,
            expected_task_id=task_id,
        )

    assert "secret-marker" not in str(error.value)
    assert message.requeues == [True]


def test_exhaustion_observation_requires_worker_retry_and_rabbit_reject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exercise = _load_exercise()
    message = _Message(retries=3)
    task_id, target_id = _patch_observation(exercise, monkeypatch, message)

    result = exercise._observe_exhaustion(
        connection=object(),
        queue_name="ragflow_queue",
        task_name="ragflow.create_upload_task",
        probe_run_id=uuid.uuid4(),
        expected_target_id=target_id,
        expected_retries=3,
    )

    assert result["task_id"] == str(task_id)
    assert result["attempts"] == 4
    assert result["retry_count"] == 3
    assert result["dead_letter_reason"] == "rejected"
    assert result["delivery_path"] == "celery_worker_retry_exhaustion"
    assert message.requeues == [True]


@pytest.mark.parametrize(
    "message",
    (
        _Message(retries=2),
        _Message(retries=3, include_death=False),
    ),
)
def test_exhaustion_observation_requeues_unproven_dead_letter(
    monkeypatch: pytest.MonkeyPatch,
    message: _Message,
) -> None:
    exercise = _load_exercise()
    _task_id, target_id = _patch_observation(exercise, monkeypatch, message)

    with pytest.raises(RuntimeError):
        exercise._observe_exhaustion(
            connection=object(),
            queue_name="ragflow_queue",
            task_name="ragflow.create_upload_task",
            probe_run_id=uuid.uuid4(),
            expected_target_id=target_id,
            expected_retries=3,
        )

    assert message.requeues == [True]


def test_retry_observation_requeues_for_external_queue_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exercise = _load_exercise()
    message = _Message(retries=1, include_death=False)
    task_id = uuid.uuid4()
    target_id = uuid.uuid4()

    count_calls: list[str] = []

    def counts(_connection: object, queue_name: str) -> object:
        count_calls.append(queue_name)
        if len(count_calls) > 2:
            raise AssertionError("post-reject state must use a separate connection")
        messages = 1 if queue_name == "ragflow_queue" else 0
        return exercise.QueueCounts(messages=messages, consumers=0)

    monkeypatch.setattr(exercise, "_queue_counts", counts)
    monkeypatch.setattr(exercise, "_get_message", lambda *_args, **_kwargs: message)
    monkeypatch.setattr(
        exercise,
        "_validated_retry_identity",
        lambda *_args, **_kwargs: (task_id, target_id, str(task_id)),
    )

    result = exercise._observe_retry(
        connection=object(),
        queue_name="ragflow_queue",
        task_name="ragflow.create_upload_task",
        probe_run_id=uuid.uuid4(),
        expected_target_id=target_id,
        expected_retries=1,
    )

    assert result["task_id"] == str(task_id)
    assert result["target_id"] == str(target_id)
    assert result["retry_count"] == 1
    assert result["persistent_message"] is True
    assert result["result"] == "retry_requeued"
    assert message.requeues == [True]
    assert count_calls == ["ragflow_queue", "ragflow_queue.dlq"]


def test_retry_observation_requeues_message_when_retry_count_is_unproven(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exercise = _load_exercise()
    message = _Message(retries=0, include_death=False)
    target_id = uuid.uuid4()

    def counts(_connection: object, queue_name: str) -> object:
        messages = 1 if queue_name == "ragflow_queue" else 0
        return exercise.QueueCounts(messages=messages, consumers=0)

    monkeypatch.setattr(exercise, "_queue_counts", counts)
    monkeypatch.setattr(exercise, "_get_message", lambda *_args, **_kwargs: message)
    monkeypatch.setattr(
        exercise,
        "_validated_retry_identity",
        lambda *_args, **_kwargs: (uuid.uuid4(), target_id, str(uuid.uuid4())),
    )

    with pytest.raises(RuntimeError, match="retry count"):
        exercise._observe_retry(
            connection=object(),
            queue_name="ragflow_queue",
            task_name="ragflow.create_upload_task",
            probe_run_id=uuid.uuid4(),
            expected_target_id=target_id,
            expected_retries=1,
        )

    assert message.requeues == [True]
