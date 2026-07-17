from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import NoReturn

import structlog
from celery import Task
from celery.exceptions import MaxRetriesExceededError, Reject

from app.core.database import AsyncSessionFactory, engine
from app.core.outbox import OutboxRepository
from app.workers.celery_app import celery_app

from .repository import DocumentRepository, ExpiryScanCandidate  # noqa: TID251

logger = structlog.get_logger(__name__)

DEFAULT_EXPIRY_LOOKAHEAD_DAYS = 30
DEFAULT_EXPIRY_SCAN_BATCH_SIZE = 500
DEFAULT_EXPIRY_SCAN_MAX_BATCHES = 20
MAX_EXPIRY_SCAN_BATCH_SIZE = 10_000
EXPIRY_SCAN_MAX_RETRIES = 3
EXPIRY_SCAN_BASE_COUNTDOWN_SECONDS = 30
EXPIRY_SCAN_MAX_COUNTDOWN_SECONDS = 300
MAX_EXPIRY_SCAN_BATCHES = 100
DOCUMENT_FILE_EXPIRING_EVENT = "document.file.expiring"
DOCUMENT_FILE_EXPIRED_EVENT = "document.file.expired"


@celery_app.task(  # type: ignore[misc]
    name="document.scan_expiring_files",
    bind=True,
    acks_late=True,
    acks_on_failure_or_timeout=False,
    reject_on_worker_lost=True,
    max_retries=EXPIRY_SCAN_MAX_RETRIES,
)
def scan_expiring_files_task(
    self: Task,
    lookahead_days: int = DEFAULT_EXPIRY_LOOKAHEAD_DAYS,
    batch_size: int = DEFAULT_EXPIRY_SCAN_BATCH_SIZE,
    max_batches: int = DEFAULT_EXPIRY_SCAN_MAX_BATCHES,
) -> int:
    try:
        return run_scan_expiring_files_task(
            lookahead_days=lookahead_days,
            batch_size=batch_size,
            max_batches=max_batches,
        )
    except Exception as error:
        logger.error(
            "document.expiry_scan.failed",
            error_type=type(error).__name__,
            retries=int(self.request.retries or 0),
        )
        _retry_or_reject(self, error)


def _retry_or_reject(task: Task, error: BaseException) -> NoReturn:
    retries = int(task.request.retries or 0)
    error_type = type(error).__name__
    max_retries = task.max_retries
    if max_retries is not None and retries >= max_retries:
        raise Reject(reason=error_type, requeue=False) from None
    countdown = min(
        (2**retries) * EXPIRY_SCAN_BASE_COUNTDOWN_SECONDS,
        EXPIRY_SCAN_MAX_COUNTDOWN_SECONDS,
    )
    try:
        raise task.retry(exc=RuntimeError(error_type), countdown=countdown)
    except MaxRetriesExceededError:
        raise Reject(reason=error_type, requeue=False) from None


def run_scan_expiring_files_task(
    *,
    lookahead_days: int = DEFAULT_EXPIRY_LOOKAHEAD_DAYS,
    batch_size: int = DEFAULT_EXPIRY_SCAN_BATCH_SIZE,
    max_batches: int = DEFAULT_EXPIRY_SCAN_MAX_BATCHES,
) -> int:
    return asyncio.run(
        run_scan_expiring_files_task_async(
            lookahead_days=lookahead_days,
            batch_size=batch_size,
            max_batches=max_batches,
        )
    )


async def run_scan_expiring_files_task_async(
    *,
    lookahead_days: int = DEFAULT_EXPIRY_LOOKAHEAD_DAYS,
    batch_size: int = DEFAULT_EXPIRY_SCAN_BATCH_SIZE,
    max_batches: int = DEFAULT_EXPIRY_SCAN_MAX_BATCHES,
) -> int:
    now = datetime.now(UTC)
    effective_batch_size = max(1, min(batch_size, MAX_EXPIRY_SCAN_BATCH_SIZE))
    effective_max_batches = max(1, min(max_batches, MAX_EXPIRY_SCAN_BATCHES))
    max_total_candidates = effective_batch_size * effective_max_batches
    queued = 0
    batches_processed = 0
    loop_limit_reached = False
    try:
        async with AsyncSessionFactory() as session:
            repository = DocumentRepository(session)
            outbox = OutboxRepository(session)
            warning_deadline = now + timedelta(days=max(0, lookahead_days))
            await repository.refresh_expiry_statuses(
                now=now,
                warning_deadline=warning_deadline,
            )
            await session.commit()
            for _batch_index in range(effective_max_batches):
                candidates = await repository.list_expiry_scan_candidates(
                    now=now,
                    warning_deadline=warning_deadline,
                    limit=effective_batch_size,
                )
                if not candidates:
                    break
                batch_queued = 0
                try:
                    for candidate in candidates:
                        accepted = await repository.mark_expiry_notification_sent(
                            file_id=candidate.file_id,
                            notification_kind=candidate.notification_kind,
                            expected_expires_at=candidate.expires_at,
                            now=now,
                            warning_deadline=warning_deadline,
                            sent_at=now,
                        )
                        if not accepted:
                            continue
                        await outbox.append(
                            event_type=_expiry_event_type(candidate),
                            aggregate_type="file",
                            aggregate_id=str(candidate.file_id),
                            # Consumers still reload canonical data and compare this CAS snapshot.
                            payload={
                                "expected_expires_at": candidate.expires_at.isoformat(),
                                "notification_kind": candidate.notification_kind,
                            },
                        )
                        batch_queued += 1
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise
                queued += batch_queued
                batches_processed += 1
                if len(candidates) < effective_batch_size:
                    break
            else:
                loop_limit_reached = True
        logger.info(
            "document.expiry_scan.completed",
            queued=queued,
            batches_processed=batches_processed,
            batch_size=effective_batch_size,
            max_batches=effective_max_batches,
            max_total_candidates=max_total_candidates,
            loop_limit_reached=loop_limit_reached,
        )
        return queued
    finally:
        await engine.dispose()


def _expiry_event_type(candidate: ExpiryScanCandidate) -> str:
    if candidate.notification_kind == "expired":
        return DOCUMENT_FILE_EXPIRED_EVENT
    if candidate.notification_kind == "warning":
        return DOCUMENT_FILE_EXPIRING_EVENT
    raise ValueError("invalid expiry notification kind")
