from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Protocol

import structlog

from app.core.database import AsyncSessionFactory, engine
from app.workers.celery_app import celery_app

from .repository import DocumentRepository, ExpiryScanCandidate  # noqa: TID251

logger = structlog.get_logger(__name__)

DEFAULT_EXPIRY_LOOKAHEAD_DAYS = 30
DEFAULT_EXPIRY_SCAN_BATCH_SIZE = 500


class TaskSender(Protocol):
    def send_task(self, name: str, args: list[str], queue: str) -> object:
        pass


@celery_app.task(name="document.scan_expiring_files")  # type: ignore[misc]
def scan_expiring_files_task(
    lookahead_days: int = DEFAULT_EXPIRY_LOOKAHEAD_DAYS,
    batch_size: int = DEFAULT_EXPIRY_SCAN_BATCH_SIZE,
) -> int:
    return run_scan_expiring_files_task(
        lookahead_days=lookahead_days,
        batch_size=batch_size,
    )


def run_scan_expiring_files_task(
    *,
    lookahead_days: int = DEFAULT_EXPIRY_LOOKAHEAD_DAYS,
    batch_size: int = DEFAULT_EXPIRY_SCAN_BATCH_SIZE,
) -> int:
    return asyncio.run(
        run_scan_expiring_files_task_async(
            lookahead_days=lookahead_days,
            batch_size=batch_size,
        )
    )


async def run_scan_expiring_files_task_async(
    *,
    lookahead_days: int = DEFAULT_EXPIRY_LOOKAHEAD_DAYS,
    batch_size: int = DEFAULT_EXPIRY_SCAN_BATCH_SIZE,
) -> int:
    now = datetime.now(UTC)
    try:
        async with AsyncSessionFactory() as session:
            repository = DocumentRepository(session)
            warning_deadline = now + timedelta(days=max(0, lookahead_days))
            await repository.refresh_expiry_statuses(
                now=now,
                warning_deadline=warning_deadline,
            )
            candidates = await repository.list_expiry_scan_candidates(
                now=now,
                warning_deadline=warning_deadline,
                limit=max(1, min(batch_size, 10_000)),
            )
            queued = enqueue_expiry_notification_tasks(candidates)
            for candidate in candidates:
                await repository.mark_expiry_notification_sent(
                    file_id=candidate.file_id,
                    notification_kind=candidate.notification_kind,
                    sent_at=now,
                )
            await session.commit()
        logger.info("document.expiry_scan.completed", queued=queued)
        return queued
    finally:
        await engine.dispose()


def enqueue_expiry_notification_tasks(
    candidates: Sequence[ExpiryScanCandidate],
    *,
    sender: TaskSender = celery_app,
) -> int:
    for candidate in candidates:
        sender.send_task(
            "notification.document_expiry",
            args=[
                str(candidate.file_id),
                "",
                "",
                candidate.original_name,
                candidate.expires_at.isoformat(),
                "expired" if candidate.notification_kind == "expired" else "expiring",
            ],
            queue="notification_queue",
        )
    return len(candidates)
