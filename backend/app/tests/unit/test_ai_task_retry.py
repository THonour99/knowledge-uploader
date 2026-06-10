"""AI 分析任务重试编排与存储异常瞬态分类纯单元测试。

不依赖 conftest / DB / Celery worker、可 --noconftest 运行。
"""

from __future__ import annotations

from typing import NoReturn
from unittest.mock import Mock

import pytest
from celery.exceptions import MaxRetriesExceededError, Retry, SoftTimeLimitExceeded
from minio.error import S3Error

from app.adapters.minio_client import PERMANENT_S3_ERROR_CODES, is_transient_storage_error
from app.modules.ai import tasks as ai_tasks
from app.modules.ai.exceptions import AiAnalysisTransientError

FILE_ID = "0c8b3a4e-9f2d-4f1a-8c5b-2e7d6a1b3c4d"


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


class _FakeTask:
    """模拟 Celery bind task 的最小接口: request.retries / max_retries / retry()。"""

    max_retries = ai_tasks.STORAGE_RETRY_MAX_RETRIES

    def __init__(self, *, retries: int = 0, exhausted: bool = False) -> None:
        self.request = _FakeRequest(retries)
        self._exhausted = exhausted
        self.retry_calls: list[dict[str, object]] = []

    def retry(self, *, exc: BaseException, countdown: int) -> NoReturn:
        self.retry_calls.append({"exc": exc, "countdown": countdown})
        if self._exhausted:
            raise MaxRetriesExceededError
        raise Retry(exc=exc, when=countdown)


def _raise_transient(file_id: str) -> str:
    _ = file_id
    raise AiAnalysisTransientError("object storage unavailable")


def _raise_soft_time_limit(file_id: str) -> str:
    _ = file_id
    raise SoftTimeLimitExceeded


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
        ai_tasks._analyze_with_retry(task, FILE_ID)

    assert len(task.retry_calls) == 1
    assert task.retry_calls[0]["countdown"] == expected_countdown
    assert isinstance(task.retry_calls[0]["exc"], AiAnalysisTransientError)


def test_retry_exhausted_marks_analysis_failed_with_retry_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ai_tasks, "run_ai_analyze_file_task", _raise_transient)
    mark_failed_calls: list[tuple[str, str]] = []

    def _record_mark_failed(file_id: str, *, error_message: str) -> str:
        mark_failed_calls.append((file_id, error_message))
        return file_id

    monkeypatch.setattr(ai_tasks, "run_mark_analysis_failed_task", _record_mark_failed)
    task = _FakeTask(retries=ai_tasks.STORAGE_RETRY_MAX_RETRIES, exhausted=True)

    result = ai_tasks._analyze_with_retry(task, FILE_ID)

    assert result == FILE_ID
    assert len(task.retry_calls) == 1
    assert mark_failed_calls == [(FILE_ID, ai_tasks.STORAGE_RETRY_EXHAUSTED_MESSAGE)]
    assert str(ai_tasks.STORAGE_RETRY_MAX_RETRIES) in mark_failed_calls[0][1]


def test_soft_time_limit_marks_analysis_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ai_tasks, "run_ai_analyze_file_task", _raise_soft_time_limit)
    mark_failed_calls: list[tuple[str, str]] = []

    def _record_mark_failed(file_id: str, *, error_message: str) -> str:
        mark_failed_calls.append((file_id, error_message))
        return file_id

    monkeypatch.setattr(ai_tasks, "run_mark_analysis_failed_task", _record_mark_failed)
    task = _FakeTask()

    result = ai_tasks._analyze_with_retry(task, FILE_ID)

    assert result == FILE_ID
    assert task.retry_calls == []
    assert mark_failed_calls == [(FILE_ID, ai_tasks.ANALYSIS_TIMEOUT_MESSAGE)]
