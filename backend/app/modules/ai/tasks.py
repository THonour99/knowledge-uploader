from __future__ import annotations

import asyncio
import uuid
from importlib import import_module
from typing import NoReturn

import structlog
from celery import Task
from celery.exceptions import MaxRetriesExceededError, Reject, SoftTimeLimitExceeded

from app.adapters.minio_client import MinioDocumentStorage
from app.core.config import Settings, get_settings
from app.core.database import AsyncSessionFactory, engine
from app.workers.celery_app import celery_app

from . import events
from .exceptions import (
    AiAnalysisAlreadyRunningError,
    AiAnalysisPreconditionError,
    AiAnalysisTransientError,
)
from .repository import AiRepository  # noqa: TID251 - same-module repository dependency
from .service import (  # noqa: TID251 - same-module service dependency
    AiAnalysisService,
    AiObjectStorage,
)

import_module("app.db.models")

logger = structlog.get_logger(__name__)

STORAGE_RETRY_MAX_RETRIES = 3
STORAGE_RETRY_BASE_COUNTDOWN_SECONDS = 30
ANALYSIS_REDELIVERY_MAX_RETRIES = 10
ANALYSIS_REDELIVERY_MAX_COUNTDOWN_SECONDS = 120
STORAGE_RETRY_EXHAUSTED_MESSAGE = f"存储暂不可用。已重试 {STORAGE_RETRY_MAX_RETRIES} 次"
ANALYSIS_SOFT_TIME_LIMIT_SECONDS = 600
ANALYSIS_TIME_LIMIT_SECONDS = 660
ANALYSIS_TIMEOUT_MESSAGE = f"分析超时({ANALYSIS_SOFT_TIME_LIMIT_SECONDS}s)"
# R1 遗留自愈: 前置条件在投递窗口内失效时, 处于这些中间态的文件必须补标失败,
# 否则会永远卡在 extracting_text/analyzing 而 analysis 停在 running。
STUCK_ANALYSIS_FILE_STATUSES = frozenset({"extracting_text", "analysis_queued", "analyzing"})
PRECONDITION_FAILED_MESSAGE_PREFIX = "前置条件失效"


def build_ai_storage(settings: Settings) -> AiObjectStorage:
    return MinioDocumentStorage(settings)


def _retry_or_dead_letter(
    task: Task,
    error: BaseException,
    *,
    max_countdown: int = ANALYSIS_REDELIVERY_MAX_COUNTDOWN_SECONDS,
) -> NoReturn:
    """Retry infrastructure failures, then route the original message to the AI DLQ."""
    retries = int(task.request.retries or 0)
    countdown = min(
        (2**retries) * STORAGE_RETRY_BASE_COUNTDOWN_SECONDS,
        max_countdown,
    )
    error_type = type(error).__name__
    max_retries = task.max_retries
    if max_retries is not None and retries >= max_retries:
        raise Reject(reason=error_type, requeue=False) from None
    try:
        # Only persist the exception type in Celery retry metadata. Database and
        # provider errors may contain credentials, object keys, or document text.
        raise task.retry(exc=RuntimeError(error_type), countdown=countdown)
    except MaxRetriesExceededError:
        # acks_late plus Reject(requeue=False) sends the original sanitized Celery
        # message through ai_queue's configured dead-letter exchange.
        raise Reject(reason=error_type, requeue=False) from None


def _mark_analysis_failed_or_retry(
    task: Task,
    file_id: str,
    *,
    error_message: str,
    error_code: events.AiAnalysisFailureCode,
    delivery_token: str,
    require_retry_wait: bool = False,
) -> str:
    """ACK only after the terminal analysis failure is durably committed."""
    try:
        return run_mark_analysis_failed_task(
            file_id,
            error_message=error_message,
            error_code=error_code.value,
            delivery_token=delivery_token,
            require_retry_wait=require_retry_wait,
        )
    except Exception as exc:
        logger.error(
            "ai_analysis_failure_persistence_failed",
            file_id=file_id,
            error_type=type(exc).__name__,
            retries=int(task.request.retries or 0),
        )
        _retry_or_dead_letter(task, exc)


def _analyze_with_retry(task: Task, file_id: str) -> str:
    """任务壳层编排: 瞬态错误指数退避重试、软超时与重试耗尽落 analysis_failed。"""
    request_id = getattr(task.request, "id", None)
    delivery_token = str(request_id or uuid.uuid4().hex)[:64]
    try:
        return run_ai_analyze_file_task(file_id, delivery_token=delivery_token)
    except SoftTimeLimitExceeded:
        logger.error("ai_analysis_soft_time_limit", file_id=file_id)
        return _mark_analysis_failed_or_retry(
            task,
            file_id,
            error_message=ANALYSIS_TIMEOUT_MESSAGE,
            error_code=events.AiAnalysisFailureCode.TIMEOUT,
            delivery_token=delivery_token,
        )
    except AiAnalysisAlreadyRunningError as exc:
        logger.info(
            "ai_analysis_redelivery_waiting_for_active_lease",
            file_id=file_id,
            retries=int(task.request.retries or 0),
        )
        _retry_or_dead_letter(task, exc)
    except AiAnalysisTransientError as exc:
        retries = int(task.request.retries or 0)
        if retries >= STORAGE_RETRY_MAX_RETRIES:
            logger.error(
                "ai_analysis_storage_retry_exhausted",
                file_id=file_id,
                retries=retries,
            )
            return _mark_analysis_failed_or_retry(
                task,
                file_id,
                error_message=STORAGE_RETRY_EXHAUSTED_MESSAGE,
                error_code=events.AiAnalysisFailureCode.PROVIDER_UNAVAILABLE,
                delivery_token=delivery_token,
                require_retry_wait=True,
            )
        _retry_or_dead_letter(task, exc)
    except Exception as exc:
        logger.error(
            "ai_analysis_infrastructure_failure",
            file_id=file_id,
            error_type=type(exc).__name__,
            retries=int(task.request.retries or 0),
        )
        _retry_or_dead_letter(task, exc)


@celery_app.task(  # type: ignore[misc]
    name="ai.analyze_file",
    bind=True,
    acks_late=True,
    acks_on_failure_or_timeout=False,
    reject_on_worker_lost=True,
    max_retries=ANALYSIS_REDELIVERY_MAX_RETRIES,
    soft_time_limit=ANALYSIS_SOFT_TIME_LIMIT_SECONDS,
    time_limit=ANALYSIS_TIME_LIMIT_SECONDS,
)
def ai_analyze_file_task(self: Task, file_id: str) -> str:
    return _analyze_with_retry(self, file_id)


def run_ai_analyze_file_task(file_id: str, *, delivery_token: str | None = None) -> str:
    asyncio.run(run_ai_analyze_file_task_async(file_id, delivery_token=delivery_token))
    return file_id


async def run_ai_analyze_file_task_async(
    file_id: str,
    *,
    delivery_token: str | None = None,
) -> None:
    file_uuid = uuid.UUID(file_id)
    try:
        await _run_ai_analyze_file(file_uuid, delivery_token=delivery_token)
    finally:
        await engine.dispose()


def run_mark_analysis_failed_task(
    file_id: str,
    *,
    error_message: str,
    error_code: str = events.AiAnalysisFailureCode.INTERNAL.value,
    delivery_token: str | None = None,
    require_retry_wait: bool = False,
) -> str:
    asyncio.run(
        run_mark_analysis_failed_task_async(
            file_id,
            error_message=error_message,
            error_code=error_code,
            delivery_token=delivery_token,
            require_retry_wait=require_retry_wait,
        )
    )
    return file_id


async def run_mark_analysis_failed_task_async(
    file_id: str,
    *,
    error_message: str,
    error_code: str = events.AiAnalysisFailureCode.INTERNAL.value,
    delivery_token: str | None = None,
    require_retry_wait: bool = False,
) -> None:
    file_uuid = uuid.UUID(file_id)
    try:
        settings = get_settings()
        async with AsyncSessionFactory() as session:
            service = AiAnalysisService(
                session=session,
                repository=AiRepository(session),
                settings=settings,
            )
            await service.mark_analysis_failed(
                file_id=file_uuid,
                error_message=error_message,
                error_code=error_code,
                expected_delivery_token=delivery_token,
                require_retry_wait=require_retry_wait,
            )
    finally:
        await engine.dispose()


async def _run_ai_analyze_file(
    file_id: uuid.UUID,
    *,
    delivery_token: str | None = None,
) -> None:
    settings = get_settings()
    storage = build_ai_storage(settings)
    precondition_reason: str | None = None
    hard_disabled = not settings.ai_analysis_enabled
    async with AsyncSessionFactory() as session:
        service = AiAnalysisService(
            session=session,
            repository=AiRepository(session),
            settings=settings,
        )
        try:
            await service.run_file_analysis(
                file_id,
                storage=storage,
                delivery_token=delivery_token,
            )
        except AiAnalysisPreconditionError as exc:
            # 前置条件在投递与执行之间的窗口内变化(例如 AI 被关闭)属预期竞态、
            # 不算失败。记录 warning 便于排查; 若文件已进入分析中间态,
            # 环境硬关闭时恢复为草稿/待审核, 其他前置竞态才补标 analysis_failed。
            logger.warning(
                "ai_analysis_precondition_failed",
                file_id=str(file_id),
                reason=str(exc),
            )
            precondition_reason = str(exc)
    if precondition_reason is not None:
        if hard_disabled:
            await _recover_hard_disabled_intermediate_file(file_id)
        else:
            await _mark_stuck_intermediate_file_failed(file_id, reason=precondition_reason)


async def _recover_hard_disabled_intermediate_file(file_id: uuid.UUID) -> None:
    settings = get_settings()
    async with AsyncSessionFactory() as session:
        service = AiAnalysisService(
            session=session,
            repository=AiRepository(session),
            settings=settings,
        )
        recovered = await service.recover_hard_disabled_intermediate_file(file_id)
        if recovered:
            logger.warning(
                "ai_analysis_hard_disabled_file_recovered",
                file_id=str(file_id),
            )


async def _mark_stuck_intermediate_file_failed(file_id: uuid.UUID, *, reason: str) -> None:
    """把卡在分析中间态的文件补标 analysis_failed, 非中间态文件保持静默跳过。"""
    settings = get_settings()
    async with AsyncSessionFactory() as session:
        repository = AiRepository(session)
        file = await repository.get_file_for_update(file_id)
        if file is None or file.status not in STUCK_ANALYSIS_FILE_STATUSES:
            return
        service = AiAnalysisService(
            session=session,
            repository=repository,
            settings=settings,
        )
        await service.mark_analysis_failed(
            file_id=file_id,
            error_message=f"{PRECONDITION_FAILED_MESSAGE_PREFIX}: {reason}",
        )
        logger.warning(
            "ai_analysis_stuck_file_marked_failed",
            file_id=str(file_id),
            previous_status=file.status,
            reason=reason,
        )
