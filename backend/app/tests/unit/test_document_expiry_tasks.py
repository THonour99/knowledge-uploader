from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import TracebackType
from typing import Self

import pytest

from app.modules.document.repository import ExpiryScanCandidate  # noqa: TID251


def _candidate(*, notification_kind: str) -> ExpiryScanCandidate:
    return ExpiryScanCandidate(
        file_id=uuid.uuid4(),
        uploader_id=uuid.uuid4(),
        original_name="policy.pdf",
        expires_at=datetime(2026, 6, 20, 0, 0, tzinfo=UTC),
        expiry_status="expired" if notification_kind == "expired" else "expiring",
        notification_kind=notification_kind,
    )


def test_expiry_event_type_has_distinct_terminal_contract() -> None:
    from app.modules.document import tasks

    assert tasks._expiry_event_type(_candidate(notification_kind="warning")) == (
        "document.file.expiring"
    )
    assert tasks._expiry_event_type(_candidate(notification_kind="expired")) == (
        "document.file.expired"
    )
    with pytest.raises(ValueError, match="invalid expiry notification kind"):
        tasks._expiry_event_type(_candidate(notification_kind="unexpected"))


def test_expiry_scan_worker_has_bounded_dlq_delivery_contract() -> None:
    from celery.exceptions import Reject

    from app.modules.document import tasks

    task = tasks.scan_expiring_files_task
    assert task.acks_late is True
    assert task.acks_on_failure_or_timeout is False
    assert task.reject_on_worker_lost is True
    assert task.max_retries == tasks.EXPIRY_SCAN_MAX_RETRIES

    task.push_request(retries=tasks.EXPIRY_SCAN_MAX_RETRIES, called_directly=False)
    try:
        with pytest.raises(Reject) as rejected:
            tasks._retry_or_reject(task, RuntimeError("database unavailable"))
    finally:
        task.pop_request()
    assert rejected.value.requeue is False
    assert rejected.value.reason == "RuntimeError"


@pytest.mark.asyncio
async def test_expiry_scan_persists_id_only_outbox_in_same_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.document import tasks

    accepted = _candidate(notification_kind="warning")
    raced = _candidate(notification_kind="expired")
    appended: list[dict[str, object]] = []
    committed = False
    listed = False

    class FakeSession:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(
            self,
            _exc_type: type[BaseException] | None,
            _exc: BaseException | None,
            _traceback: TracebackType | None,
        ) -> None:
            return None

        async def commit(self) -> None:
            nonlocal committed
            committed = True

    class FakeDocumentRepository:
        def __init__(self, _session: object) -> None:
            pass

        async def refresh_expiry_statuses(
            self,
            *,
            now: datetime,
            warning_deadline: datetime,
        ) -> int:
            assert warning_deadline >= now
            return 2

        async def list_expiry_scan_candidates(
            self,
            *,
            now: datetime,
            warning_deadline: datetime,
            limit: int,
        ) -> list[ExpiryScanCandidate]:
            nonlocal listed
            if listed:
                return []
            listed = True
            assert warning_deadline >= now
            assert limit == 500
            return [accepted, raced]

        async def mark_expiry_notification_sent(
            self,
            *,
            file_id: uuid.UUID,
            notification_kind: str,
            expected_expires_at: datetime,
            now: datetime,
            warning_deadline: datetime,
            sent_at: datetime,
        ) -> bool:
            assert expected_expires_at.tzinfo is not None
            assert warning_deadline >= now
            assert sent_at.tzinfo is not None
            assert notification_kind in {"warning", "expired"}
            return file_id == accepted.file_id

    class FakeOutboxRepository:
        def __init__(self, _session: object) -> None:
            pass

        async def append(self, **values: object) -> object:
            appended.append(values)
            return object()

    class FakeEngine:
        async def dispose(self) -> None:
            return None

    fake_session = FakeSession()
    monkeypatch.setattr(tasks, "AsyncSessionFactory", lambda: fake_session)
    monkeypatch.setattr(tasks, "DocumentRepository", FakeDocumentRepository)
    monkeypatch.setattr(tasks, "OutboxRepository", FakeOutboxRepository)
    monkeypatch.setattr(tasks, "engine", FakeEngine())

    queued = await tasks.run_scan_expiring_files_task_async()

    assert queued == 1
    assert committed is True
    assert appended == [
        {
            "event_type": "document.file.expiring",
            "aggregate_type": "file",
            "aggregate_id": str(accepted.file_id),
            "payload": {
                "expected_expires_at": accepted.expires_at.isoformat(),
                "notification_kind": "warning",
            },
        }
    ]


@pytest.mark.parametrize(
    ("total_candidates", "batch_size", "max_batches", "expected_queued", "expected_remaining"),
    [
        (5, 2, 10, 5, 0),
        (7, 2, 3, 6, 1),
    ],
)
async def test_expiry_scan_drains_multiple_batches_without_unbounded_loop(
    monkeypatch: pytest.MonkeyPatch,
    total_candidates: int,
    batch_size: int,
    max_batches: int,
    expected_queued: int,
    expected_remaining: int,
) -> None:
    from app.modules.document import tasks

    candidates = [_candidate(notification_kind="warning") for _ in range(total_candidates)]
    pending = list(candidates)
    appended_file_ids: list[uuid.UUID] = []
    commit_count = 0
    list_count = 0

    class FakeSession:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(
            self,
            _exc_type: type[BaseException] | None,
            _exc: BaseException | None,
            _traceback: TracebackType | None,
        ) -> None:
            return None

        async def commit(self) -> None:
            nonlocal commit_count
            commit_count += 1

        async def rollback(self) -> None:
            return None

    class FakeDocumentRepository:
        def __init__(self, _session: object) -> None:
            pass

        async def refresh_expiry_statuses(
            self,
            *,
            now: datetime,
            warning_deadline: datetime,
        ) -> int:
            assert warning_deadline >= now
            return len(pending)

        async def list_expiry_scan_candidates(
            self,
            *,
            now: datetime,
            warning_deadline: datetime,
            limit: int,
        ) -> list[ExpiryScanCandidate]:
            nonlocal list_count
            assert warning_deadline >= now
            assert limit == batch_size
            list_count += 1
            return list(pending[:limit])

        async def mark_expiry_notification_sent(
            self,
            *,
            file_id: uuid.UUID,
            notification_kind: str,
            expected_expires_at: datetime,
            now: datetime,
            warning_deadline: datetime,
            sent_at: datetime,
        ) -> bool:
            assert notification_kind == "warning"
            assert expected_expires_at.tzinfo is not None
            assert warning_deadline >= now
            assert sent_at.tzinfo is not None
            index = next(
                (index for index, item in enumerate(pending) if item.file_id == file_id),
                None,
            )
            if index is None:
                return False
            pending.pop(index)
            return True

    class FakeOutboxRepository:
        def __init__(self, _session: object) -> None:
            pass

        async def append(self, **values: object) -> object:
            appended_file_ids.append(uuid.UUID(str(values["aggregate_id"])))
            return object()

    class FakeEngine:
        async def dispose(self) -> None:
            return None

    fake_session = FakeSession()
    monkeypatch.setattr(tasks, "AsyncSessionFactory", lambda: fake_session)
    monkeypatch.setattr(tasks, "DocumentRepository", FakeDocumentRepository)
    monkeypatch.setattr(tasks, "OutboxRepository", FakeOutboxRepository)
    monkeypatch.setattr(tasks, "engine", FakeEngine())

    queued = await tasks.run_scan_expiring_files_task_async(
        batch_size=batch_size,
        max_batches=max_batches,
    )

    assert queued == expected_queued
    assert len(pending) == expected_remaining
    assert appended_file_ids == [item.file_id for item in candidates[:expected_queued]]
    assert list_count == 3
    assert commit_count == 4
