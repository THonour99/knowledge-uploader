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


@pytest.mark.asyncio
async def test_expiry_scan_persists_id_only_outbox_in_same_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.document import tasks

    accepted = _candidate(notification_kind="warning")
    raced = _candidate(notification_kind="expired")
    appended: list[dict[str, object]] = []
    committed = False

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
            assert warning_deadline >= now
            assert limit == 500
            return [accepted, raced]

        async def mark_expiry_notification_sent(
            self,
            *,
            file_id: uuid.UUID,
            notification_kind: str,
            sent_at: datetime,
        ) -> bool:
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
            "payload": {},
        }
    ]
