from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from types import ModuleType

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
    return task_id, target_id


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
