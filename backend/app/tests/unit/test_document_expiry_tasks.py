from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest


def test_enqueue_expiry_notification_tasks_dispatches_notification_queue() -> None:
    from app.modules.document.repository import ExpiryScanCandidate  # noqa: TID251
    from app.modules.document.tasks import enqueue_expiry_notification_tasks

    file_id = uuid.uuid4()
    uploader_id = uuid.uuid4()
    expires_at = datetime(2026, 6, 20, 0, 0, tzinfo=UTC)
    sent: list[dict[str, object]] = []

    class FakeSender:
        def send_task(self, name: str, args: list[str], queue: str) -> object:
            sent.append({"name": name, "args": args, "queue": queue})
            return object()

    queued = enqueue_expiry_notification_tasks(
        [
            ExpiryScanCandidate(
                file_id=file_id,
                uploader_id=uploader_id,
                original_name="policy.pdf",
                expires_at=expires_at,
                expiry_status="expiring",
                notification_kind="warning",
            ),
        ],
        sender=FakeSender(),
    )

    assert queued == 1
    assert sent == [
        {
            "name": "notification.document_expiry",
            "args": [
                str(file_id),
                "",
                "",
                "policy.pdf",
                expires_at.isoformat(),
                "expiring",
            ],
            "queue": "notification_queue",
        }
    ]


def test_document_expiry_notification_task_invokes_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.notification import tasks

    called: dict[str, str] = {}

    async def fake_handle_document_expiry(
        *,
        file_id: str,
        recipient_user_id: str,
        recipient_email: str,
        file_name: str,
        expires_at: str,
        expiry_status: str,
    ) -> None:
        called.update(
            {
                "file_id": file_id,
                "recipient_user_id": recipient_user_id,
                "recipient_email": recipient_email,
                "file_name": file_name,
                "expires_at": expires_at,
                "expiry_status": expiry_status,
            }
        )

    monkeypatch.setattr(tasks, "_handle_document_expiry", fake_handle_document_expiry)

    result = tasks.document_expiry_notification_task.run(
        "file-1",
        "user-1",
        "owner@company.test",
        "policy.pdf",
        "2026-06-20T00:00:00+00:00",
        "expiring",
    )

    assert result == "file-1"
    assert called == {
        "file_id": "file-1",
        "recipient_user_id": "user-1",
        "recipient_email": "owner@company.test",
        "file_name": "policy.pdf",
        "expires_at": "2026-06-20T00:00:00+00:00",
        "expiry_status": "expiring",
    }
