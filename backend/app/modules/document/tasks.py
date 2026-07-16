from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import structlog

from app.core.database import AsyncSessionFactory, engine
from app.core.outbox import OutboxRepository
from app.workers.celery_app import celery_app

from .repository import DocumentRepository, ExpiryScanCandidate  # noqa: TID251

logger = structlog.get_logger(__name__)

DEFAULT_EXPIRY_LOOKAHEAD_DAYS = 30
DEFAULT_EXPIRY_SCAN_BATCH_SIZE = 500
DOCUMENT_FILE_EXPIRING_EVENT = "document.file.expiring"
DOCUMENT_FILE_EXPIRED_EVENT = "document.file.expired"


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
            queued = 0
            for candidate in candidates:
                accepted = await repository.mark_expiry_notification_sent(
                    file_id=candidate.file_id,
                    notification_kind=candidate.notification_kind,
                    sent_at=now,
                )
                if not accepted:
                    continue
                await OutboxRepository(session).append(
                    event_type=_expiry_event_type(candidate),
                    aggregate_type="file",
                    aggregate_id=str(candidate.file_id),
                    # Notification workers receive only EventOutbox.id and reload this
                    # canonical file. No email, filename, expiry text, or error reaches Celery.
                    payload={},
                )
                queued += 1
            await session.commit()
        logger.info("document.expiry_scan.completed", queued=queued)
        return queued
    finally:
        await engine.dispose()


def _expiry_event_type(candidate: ExpiryScanCandidate) -> str:
    if candidate.notification_kind == "expired":
        return DOCUMENT_FILE_EXPIRED_EVENT
    if candidate.notification_kind == "warning":
        return DOCUMENT_FILE_EXPIRING_EVENT
    raise ValueError("invalid expiry notification kind")
