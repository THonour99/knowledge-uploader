"""AI 分析任务重试编排与存储异常瞬态分类纯单元测试。

不依赖 conftest / DB / Celery worker、可 --noconftest 运行。
"""

from __future__ import annotations

from typing import NoReturn
from unittest.mock import Mock

import pytest
from celery.exceptions import MaxRetriesExceededError, Reject, Retry, SoftTimeLimitExceeded
from minio.error import S3Error

from app.adapters.minio_client import PERMANENT_S3_ERROR_CODES, is_transient_storage_error
from app.modules.ai import tasks as ai_tasks
from app.modules.ai.exceptions import AiAnalysisAlreadyRunningError, AiAnalysisTransientError

FILE_ID = "0c8b3a4e-9f2d-4f1a-8c5b-2e7d6a1b3c4d"


def test_ai_analysis_worker_loss_policy_is_task_scoped() -> None:
    task = ai_tasks.ai_analyze_file_task

    assert task.acks_late is True
    assert task.acks_on_failure_or_timeout is False
    assert task.reject_on_worker_lost is True
    assert task.max_retries == ai_tasks.ANALYSIS_TOTAL_MAX_RETRIES


def _make_s3_error(code: str) -> S3Error:
    return S3Error(
        code=code,
        message="test error",
        resource="/bucket/key",
        request_id="req-1",
        host_id="host-1",
        response=Mock(),
    )


class _FakeRequest:
    def __init__(self, retries: int) -> None:
        self.retries = retries
        self.id = "ai-task-delivery-1"


class _FakeTask:
    """模拟 Celery bind task 的最小接口: request.retries / max_retries / retry()。"""

    max_retries = ai_tasks.ANALYSIS_TOTAL_MAX_RETRIES

    def __init__(self, *, retries: int = 0, exhausted: bool = False) -> None:
        self.request = _FakeRequest(retries)
        self._exhausted = exhausted
        self.retry_calls: list[dict[str, object]] = []

    def retry(
        self,
        *,
        exc: BaseException,
        countdown: int,
        kwargs: dict[str, int],
    ) -> NoReturn:
        self.retry_calls.append({"exc": exc, "countdown": countdown, "kwargs": kwargs})
        if self._exhausted:
            raise MaxRetriesExceededError
        raise Retry(exc=exc, when=countdown)


def _raise_transient(file_id: str, *, delivery_token: str | None = None) -> str:
    _ = (file_id, delivery_token)
    raise AiAnalysisTransientError(
        "storage message may change without changing retry semantics",
        failure_category="storage_unavailable",
        max_retries=ai_tasks.STORAGE_RETRY_MAX_RETRIES,
        retry_budget="storage",
    )

def _raise_provider_transient(
    file_id: str,
    *,
    delivery_token: str | None = None,
) -> str:
    _ = (file_id, delivery_token)
    raise AiAnalysisTransientError(
        "provider unavailable",
        failure_category="provider_unavailable",
        max_retries=ai_tasks.PROVIDER_RETRY_MAX_RETRIES,
        retry_budget="provider",
    )



def _raise_running(file_id: str, *, delivery_token: str | None = None) -> str:
    _ = (file_id, delivery_token)
    raise AiAnalysisAlreadyRunningError("analysis delivery is already running")


def _raise_soft_time_limit(file_id: str, *, delivery_token: str | None = None) -> str:
    _ = (file_id, delivery_token)
    raise SoftTimeLimitExceeded


def _raise_infrastructure_error(
    file_id: str,
    *,
    delivery_token: str | None = None,
) -> str:
    _ = (file_id, delivery_token)
    raise ConnectionError("database connection contains no safe task metadata")


@pytest.mark.parametrize("code", sorted(PERMANENT_S3_ERROR_CODES))
def test_permanent_s3_error_codes_are_not_transient(code: str) -> None:
    assert is_transient_storage_error(_make_s3_error(code)) is False


@pytest.mark.parametrize("code", ["SlowDown", "InternalError", "RequestTimeout"])
def test_non_permanent_s3_error_codes_are_transient(code: str) -> None:
    assert is_transient_storage_error(_make_s3_error(code)) is True


def test_os_error_is_transient() -> None:
    assert is_transient_storage_error(OSError("connection reset")) is True


def test_unrelated_error_is_not_transient() -> None:
    assert is_transient_storage_error(ValueError("not storage related")) is False


@pytest.mark.parametrize(("retries", "expected_countdown"), [(0, 30), (1, 60), (2, 120)])
def test_transient_error_retries_with_exponential_backoff(
    monkeypatch: pytest.MonkeyPatch,
    retries: int,
    expected_countdown: int,
) -> None:
    monkeypatch.setattr(ai_tasks, "run_ai_analyze_file_task", _raise_transient)
    task = _FakeTask(retries=retries)

    with pytest.raises(Retry):
        ai_tasks._analyze_with_retry(task, FILE_ID, storage_retries=retries)

    assert len(task.retry_calls) == 1
    assert task.retry_calls[0]["countdown"] == expected_countdown
    assert task.retry_calls[0]["kwargs"] == {
        "storage_retries": retries + 1,
        "provider_retries": 0,
        "lease_retries": 0,
        "infrastructure_retries": 0,
    }
    retry_error = task.retry_calls[0]["exc"]
    assert isinstance(retry_error, RuntimeError)
    assert str(retry_error) == "AiAnalysisTransientError"


@pytest.mark.parametrize(
    ("runner", "storage_retries", "provider_retries", "lease_retries", "budget_key"),
    [
        (_raise_transient, 0, ai_tasks.PROVIDER_RETRY_MAX_RETRIES, 10, "storage_retries"),
        (_raise_provider_transient, 3, 0, 10, "provider_retries"),
        (_raise_running, 3, ai_tasks.PROVIDER_RETRY_MAX_RETRIES, 0, "lease_retries"),
    ],
)
def test_retry_budgets_do_not_consume_each_other(
    monkeypatch: pytest.MonkeyPatch,
    runner: object,
    storage_retries: int,
    provider_retries: int,
    lease_retries: int,
    budget_key: str,
) -> None:
    monkeypatch.setattr(ai_tasks, "run_ai_analyze_file_task", runner)
    task = _FakeTask(retries=storage_retries + provider_retries + lease_retries)

    with pytest.raises(Retry):
        ai_tasks._analyze_with_retry(
            task,
            FILE_ID,
            storage_retries=storage_retries,
            provider_retries=provider_retries,
            lease_retries=lease_retries,
        )

    assert task.retry_calls[0]["countdown"] == ai_tasks.STORAGE_RETRY_BASE_COUNTDOWN_SECONDS
    expected = {
        "storage_retries": storage_retries,
        "provider_retries": provider_retries,
        "lease_retries": lease_retries,
        "infrastructure_retries": 0,
    }
    expected[budget_key] += 1
    assert task.retry_calls[0]["kwargs"] == expected


def test_retry_exhausted_marks_analysis_failed_with_retry_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ai_tasks, "run_ai_analyze_file_task", _raise_transient)
    mark_failed_calls: list[tuple[str, str]] = []

    def _record_mark_failed(
        file_id: str,
        *,
        error_message: str,
        error_code: str,
        delivery_token: str | None = None,
        require_retry_wait: bool = False,
    ) -> str:
        assert delivery_token == "ai-task-delivery-1"
        assert require_retry_wait is True
        assert error_code == "provider_unavailable"
        mark_failed_calls.append((file_id, error_message))
        return file_id

    monkeypatch.setattr(ai_tasks, "run_mark_analysis_failed_task", _record_mark_failed)
    task = _FakeTask(retries=ai_tasks.STORAGE_RETRY_MAX_RETRIES)

    result = ai_tasks._analyze_with_retry(
        task,
        FILE_ID,
        storage_retries=ai_tasks.STORAGE_RETRY_MAX_RETRIES,
    )

    assert result == FILE_ID
    assert task.retry_calls == []
    assert mark_failed_calls == [(FILE_ID, ai_tasks.STORAGE_RETRY_EXHAUSTED_MESSAGE)]
    assert str(ai_tasks.STORAGE_RETRY_MAX_RETRIES) in mark_failed_calls[0][1]


def test_active_analysis_lease_retry_exhaustion_rejects_to_dlq_without_marking_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ai_tasks, "run_ai_analyze_file_task", _raise_running)
    mark_failed = Mock(side_effect=AssertionError("active lease must not be failed"))
    monkeypatch.setattr(ai_tasks, "run_mark_analysis_failed_task", mark_failed)
    task = _FakeTask(
        retries=ai_tasks.ANALYSIS_REDELIVERY_MAX_RETRIES,
        exhausted=True,
    )

    with pytest.raises(Reject) as rejected:
        ai_tasks._analyze_with_retry(
            task,
            FILE_ID,
            lease_retries=ai_tasks.ANALYSIS_REDELIVERY_MAX_RETRIES,
        )

    assert task.retry_calls == []
    assert rejected.value.requeue is False
    assert rejected.value.reason == "AiAnalysisAlreadyRunningError"
    mark_failed.assert_not_called()


def test_soft_time_limit_marks_analysis_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ai_tasks, "run_ai_analyze_file_task", _raise_soft_time_limit)
    mark_failed_calls: list[tuple[str, str]] = []

    def _record_mark_failed(
        file_id: str,
        *,
        error_message: str,
        error_code: str,
        delivery_token: str | None = None,
        require_retry_wait: bool = False,
    ) -> str:
        assert delivery_token == "ai-task-delivery-1"
        assert require_retry_wait is False
        assert error_code == "timeout"
        mark_failed_calls.append((file_id, error_message))
        return file_id

    monkeypatch.setattr(ai_tasks, "run_mark_analysis_failed_task", _record_mark_failed)
    task = _FakeTask()

    result = ai_tasks._analyze_with_retry(task, FILE_ID)

    assert result == FILE_ID
    assert task.retry_calls == []
    assert mark_failed_calls == [(FILE_ID, ai_tasks.ANALYSIS_TIMEOUT_MESSAGE)]


@pytest.mark.parametrize(
    "analysis_runner",
    [_raise_transient, _raise_soft_time_limit],
)
def test_failure_state_persistence_error_uses_bounded_retry(
    monkeypatch: pytest.MonkeyPatch,
    analysis_runner: object,
) -> None:
    monkeypatch.setattr(ai_tasks, "run_ai_analyze_file_task", analysis_runner)

    def _raise_database_error(
        file_id: str,
        *,
        error_message: str,
        error_code: str,
        delivery_token: str | None = None,
        require_retry_wait: bool = False,
    ) -> str:
        _ = (file_id, error_message, error_code, delivery_token, require_retry_wait)
        raise ConnectionError("database unavailable")

    monkeypatch.setattr(ai_tasks, "run_mark_analysis_failed_task", _raise_database_error)
    retries = ai_tasks.STORAGE_RETRY_MAX_RETRIES if analysis_runner is _raise_transient else 0
    task = _FakeTask(retries=retries)

    with pytest.raises(Retry):
        ai_tasks._analyze_with_retry(task, FILE_ID, storage_retries=retries)

    assert task.retry_calls[0]["countdown"] == ai_tasks.STORAGE_RETRY_BASE_COUNTDOWN_SECONDS
    assert task.retry_calls[0]["kwargs"] == {
        "storage_retries": retries,
        "provider_retries": 0,
        "lease_retries": 0,
        "infrastructure_retries": 1,
    }
    retry_error = task.retry_calls[0]["exc"]
    assert isinstance(retry_error, RuntimeError)
    assert str(retry_error) == "ConnectionError"


def test_unknown_infrastructure_error_uses_bounded_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ai_tasks,
        "run_ai_analyze_file_task",
        _raise_infrastructure_error,
    )
    task = _FakeTask()

    with pytest.raises(Retry):
        ai_tasks._analyze_with_retry(task, FILE_ID)

    assert task.retry_calls[0]["countdown"] == ai_tasks.STORAGE_RETRY_BASE_COUNTDOWN_SECONDS
    retry_error = task.retry_calls[0]["exc"]
    assert isinstance(retry_error, RuntimeError)
    assert str(retry_error) == "ConnectionError"


def test_failure_state_persistence_retry_exhaustion_rejects_to_dlq(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ai_tasks, "run_ai_analyze_file_task", _raise_soft_time_limit)

    def _raise_database_error(
        file_id: str,
        *,
        error_message: str,
        error_code: str,
        delivery_token: str | None = None,
        require_retry_wait: bool = False,
    ) -> str:
        _ = (file_id, error_message, error_code, delivery_token, require_retry_wait)
        raise ConnectionError("database unavailable")

    monkeypatch.setattr(ai_tasks, "run_mark_analysis_failed_task", _raise_database_error)
    task = _FakeTask(
        retries=ai_tasks.ANALYSIS_REDELIVERY_MAX_RETRIES,
        exhausted=True,
    )

    with pytest.raises(Reject) as rejected:
        ai_tasks._analyze_with_retry(
            task,
            FILE_ID,
            infrastructure_retries=ai_tasks.INFRASTRUCTURE_RETRY_MAX_RETRIES,
        )

    assert task.retry_calls == []
    assert rejected.value.requeue is False
    assert rejected.value.reason == "ConnectionError"


def test_retry_exhaustion_precheck_handles_real_celery_retry_semantics() -> None:
    task = ai_tasks.ai_analyze_file_task
    task.push_request(
        id="real-ai-task-delivery",
        retries=ai_tasks.ANALYSIS_TOTAL_MAX_RETRIES,
        called_directly=False,
        is_eager=True,
        args=(FILE_ID,),
        kwargs={},
    )
    try:
        # Celery re-raises the supplied exception rather than
        # MaxRetriesExceededError when retry() receives exc at the limit.
        with pytest.raises(RuntimeError, match="ConnectionError"):
            task.retry(exc=RuntimeError("ConnectionError"), countdown=30)

        with pytest.raises(Reject) as rejected:
            ai_tasks._retry_or_dead_letter(
                task,
                ConnectionError("database password must not enter retry metadata"),
                budget_key="infrastructure_retries",
                budget_retries=0,
                budget_limit=ai_tasks.INFRASTRUCTURE_RETRY_MAX_RETRIES,
                retry_state={
                    "storage_retries": 0,
                    "provider_retries": 0,
                    "lease_retries": 0,
                    "infrastructure_retries": 0,
                },
            )
    finally:
        task.pop_request()

    assert rejected.value.requeue is False
    assert rejected.value.reason == "ConnectionError"


def test_successfully_committed_analysis_returns_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed: list[tuple[str, str | None]] = []

    def _complete(file_id: str, *, delivery_token: str | None = None) -> str:
        completed.append((file_id, delivery_token))
        return file_id

    monkeypatch.setattr(ai_tasks, "run_ai_analyze_file_task", _complete)
    task = _FakeTask()

    result = ai_tasks._analyze_with_retry(task, FILE_ID)

    assert result == FILE_ID
    assert completed == [(FILE_ID, "ai-task-delivery-1")]
    assert task.retry_calls == []
