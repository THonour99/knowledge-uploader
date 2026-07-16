from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, NoReturn

import pytest
from celery.exceptions import MaxRetriesExceededError, Reject, Retry

from app.core.events import EventDispatchContext
from app.modules.ragflow import service as ragflow_service  # noqa: TID251
from app.modules.ragflow import tasks
from app.modules.ragflow.exceptions import RagflowTaskAlreadyRunningError
from app.modules.ragflow.handlers import queue_sync_task_execution

SYNC_TASK_ID = "0c8b3a4e-9f2d-4f1a-8c5b-2e7d6a1b3c4d"


@pytest.mark.parametrize(
    ("configured_value", "expected_retries"),
    [(None, 120), (True, 120), (59, 120), (86_401, 120), (600, 20)],
)
def test_parse_poll_budget_is_independent_of_new_settings_field(
    monkeypatch: pytest.MonkeyPatch,
    configured_value: object | None,
    expected_retries: int,
) -> None:
    async def get_runtime_config(_key: str) -> object | None:
        return configured_value

    monkeypatch.setattr(ragflow_service, "get_config", get_runtime_config)
    monkeypatch.setattr(ragflow_service, "get_settings", lambda: SimpleNamespace())

    assert asyncio.run(ragflow_service.resolve_parse_poll_max_retries()) == expected_retries


@pytest.mark.parametrize(
    "task",
    [
        tasks.ragflow_create_upload_task,
        tasks.ragflow_create_delete_task,
        tasks.ragflow_upload_task,
        tasks.ragflow_delete_task,
    ],
)
def test_ragflow_worker_loss_policy_is_task_scoped(task: Any) -> None:
    assert task.acks_late is True
    assert task.acks_on_failure_or_timeout is False
    assert task.reject_on_worker_lost is True


@pytest.mark.parametrize(
    "task",
    [tasks.ragflow_create_upload_task, tasks.ragflow_create_delete_task],
)
def test_ragflow_creation_failure_is_dead_lettered_after_bounded_retry(
    task: Any,
) -> None:
    assert task.acks_on_failure_or_timeout is False
    assert task.max_retries == tasks.RAGFLOW_CREATION_MAX_RETRIES


class _Request:
    def __init__(self, retries: int) -> None:
        self.retries = retries


class _Task:
    def __init__(
        self,
        *,
        retries: int,
        exhausted: bool = False,
        max_retries: int = tasks.RAGFLOW_REDELIVERY_MAX_RETRIES,
    ) -> None:
        self.request = _Request(retries)
        self.exhausted = exhausted
        self.max_retries = max_retries
        self.retry_calls: list[dict[str, object]] = []

    def retry(
        self,
        *,
        exc: BaseException | None = None,
        countdown: int,
    ) -> NoReturn:
        self.retry_calls.append({"exc": exc, "countdown": countdown})
        if self.exhausted:
            raise MaxRetriesExceededError
        raise Retry(exc=exc, when=countdown)


def _running(_sync_task_id: str) -> str:
    raise RagflowTaskAlreadyRunningError


def _database_unavailable(_task_id: str) -> str:
    raise ConnectionError


@pytest.mark.parametrize(
    "runner",
    [tasks._run_ragflow_creation_with_retry, tasks._run_ragflow_with_retry],
)
def test_ragflow_persistence_failure_uses_bounded_retry(
    runner: Any,
) -> None:
    task = _Task(retries=0)

    with pytest.raises(Retry):
        runner(
            task,
            SYNC_TASK_ID,
            run_task=_database_unavailable,
        )

    assert task.retry_calls[0]["countdown"] == 30
    assert task.retry_calls[0]["exc"] is None


@pytest.mark.parametrize(
    "runner",
    [tasks._run_ragflow_creation_with_retry, tasks._run_ragflow_with_retry],
)
def test_ragflow_persistence_failure_exhaustion_rejects_to_dlq(
    runner: Any,
) -> None:
    task = _Task(retries=tasks.RAGFLOW_REDELIVERY_MAX_RETRIES)

    with pytest.raises(Reject) as rejected:
        runner(
            task,
            SYNC_TASK_ID,
            run_task=_database_unavailable,
        )

    assert rejected.value.requeue is False
    assert rejected.value.reason == "ConnectionError"


def test_celery_retry_without_exception_has_bounded_exhaustion_semantics() -> None:
    task = tasks.ragflow_create_upload_task
    task.push_request(retries=3, called_directly=False)
    try:
        with pytest.raises(MaxRetriesExceededError):
            task.retry(countdown=30)
    finally:
        task.pop_request()


@pytest.mark.parametrize(
    ("retries", "countdown"),
    [
        (0, 30),
        (1, 60),
        (2, 120),
        (10, 120),
    ],
)
def test_active_ragflow_lease_retries_without_mutating_shared_state(
    retries: int,
    countdown: int,
) -> None:
    task = _Task(retries=retries)

    with pytest.raises(Retry):
        tasks._run_ragflow_with_retry(
            task,
            SYNC_TASK_ID,
            run_task=_running,
        )

    assert task.retry_calls[0]["countdown"] == countdown
    assert isinstance(task.retry_calls[0]["exc"], RagflowTaskAlreadyRunningError)


def test_active_ragflow_lease_retry_exhaustion_schedules_fresh_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduled: list[str] = []
    monkeypatch.setattr(tasks, "schedule_ragflow_execution_probe", scheduled.append)
    task = _Task(retries=tasks.RAGFLOW_REDELIVERY_MAX_RETRIES, exhausted=True)

    result = tasks._run_ragflow_with_retry(
        task,
        SYNC_TASK_ID,
        run_task=_running,
    )

    assert result == SYNC_TASK_ID
    assert task.retry_calls[0]["countdown"] == tasks.RAGFLOW_REDELIVERY_MAX_COUNTDOWN_SECONDS
    assert scheduled == [SYNC_TASK_ID]


def test_status_check_dispatch_preserves_polling_delay() -> None:
    sent: list[dict[str, object]] = []

    class _Sender:
        def send_task(
            self,
            name: str,
            args: list[str],
            queue: str,
            *,
            countdown: int | None = None,
        ) -> object:
            sent.append(
                {
                    "name": name,
                    "args": args,
                    "queue": queue,
                    "countdown": countdown,
                }
            )
            return object()

    event = SimpleNamespace(
        payload={
            "sync_task_id": SYNC_TASK_ID,
            "task_type": "ragflow_status_check",
            "countdown_seconds": 30,
        }
    )
    queue_sync_task_execution(
        event,
        EventDispatchContext(sender=_Sender()),
    )

    assert sent == [
        {
            "name": "ragflow.upload",
            "args": [SYNC_TASK_ID],
            "queue": "ragflow_queue",
            "countdown": 30,
        }
    ]


def test_upload_reconciliation_dispatch_preserves_persisted_backoff() -> None:
    sent: list[dict[str, object]] = []

    class _Sender:
        def send_task(
            self,
            name: str,
            args: list[str],
            queue: str,
            *,
            countdown: int | None = None,
        ) -> object:
            sent.append(
                {
                    "name": name,
                    "args": args,
                    "queue": queue,
                    "countdown": countdown,
                }
            )
            return object()

    event = SimpleNamespace(
        payload={
            "sync_task_id": SYNC_TASK_ID,
            "task_type": "ragflow_upload",
            "countdown_seconds": 30,
        }
    )
    queue_sync_task_execution(
        event,
        EventDispatchContext(sender=_Sender()),
    )

    assert sent == [
        {
            "name": "ragflow.upload",
            "args": [SYNC_TASK_ID],
            "queue": "ragflow_queue",
            "countdown": 30,
        }
    ]


@pytest.mark.parametrize("task_type", [None, "ragflow_parse", "legacy_unknown", True])
def test_sync_task_dispatch_rejects_unsupported_task_types_without_misrouting(
    task_type: object,
) -> None:
    sent: list[dict[str, object]] = []

    class _Sender:
        def send_task(
            self,
            name: str,
            args: list[str],
            queue: str,
            *,
            countdown: int | None = None,
        ) -> object:
            sent.append(
                {
                    "name": name,
                    "args": args,
                    "queue": queue,
                    "countdown": countdown,
                }
            )
            return object()

    event = SimpleNamespace(
        event_type="ragflow.sync_task.queued",
        payload={"sync_task_id": SYNC_TASK_ID, "task_type": task_type},
    )

    with pytest.raises(RuntimeError, match="unsupported task_type"):
        queue_sync_task_execution(
            event,
            EventDispatchContext(sender=_Sender()),
        )

    assert sent == []
