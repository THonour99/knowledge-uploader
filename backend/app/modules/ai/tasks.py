from __future__ import annotations

import asyncio
import uuid
from importlib import import_module
from typing import Literal, NoReturn

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
    provider_failure_message,
)

import_module("app.db.models")

logger = structlog.get_logger(__name__)

RetryCounterKey = Literal[
    "storage_retries", "provider_retries", "lease_retries", "infrastructure_retries"
]
STORAGE_RETRY_MAX_RETRIES = 3
STORAGE_RETRY_BASE_COUNTDOWN_SECONDS = 30
ANALYSIS_REDELIVERY_MAX_RETRIES = 10
ANALYSIS_REDELIVERY_MAX_COUNTDOWN_SECONDS = 120
STORAGE_RETRY_EXHAUSTED_MESSAGE = f"存储暂不可用。已重试 {STORAGE_RETRY_MAX_RETRIES} 次"
PROVIDER_RETRY_MAX_RETRIES = 10
INFRASTRUCTURE_RETRY_MAX_RETRIES = 10
ANALYSIS_TOTAL_MAX_RETRIES = (
    STORAGE_RETRY_MAX_RETRIES
    + ANALYSIS_REDELIVERY_MAX_RETRIES
    + PROVIDER_RETRY_MAX_RETRIES
    + INFRASTRUCTURE_RETRY_MAX_RETRIES
)
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
    budget_key: RetryCounterKey,
    budget_retries: int,
    budget_limit: int,
    retry_state: dict[RetryCounterKey, int],
    max_countdown: int = ANALYSIS_REDELIVERY_MAX_COUNTDOWN_SECONDS,
) -> NoReturn:
    """Retry one failure class without consuming another class's retry budget."""
    safe_budget_retries = max(0, int(budget_retries))
    total_retries = max(0, int(task.request.retries or 0))
    countdown = min(
        (2 ** min(safe_budget_retries, 10)) * STORAGE_RETRY_BASE_COUNTDOWN_SECONDS,
        max_countdown,
    )
    error_type = type(error).__name__
    if safe_budget_retries >= budget_limit or total_retries >= ANALYSIS_TOTAL_MAX_RETRIES:
        raise Reject(reason=error_type, requeue=False) from None
    next_retry_state = dict(retry_state)
    next_retry_state[budget_key] = safe_budget_retries + 1
    try:
        # Retry metadata contains only sanitized exception type and bounded counters.
        raise task.retry(
            exc=RuntimeError(error_type),
            countdown=countdown,
            kwargs=next_retry_state,
        )
    except MaxRetriesExceededError:
        raise Reject(reason=error_type, requeue=False) from None


def _mark_analysis_failed_or_retry(
    task: Task,
    file_id: str,
    *,
    error_message: str,
    error_code: events.AiAnalysisFailureCode,
    delivery_token: str,
    retry_state: dict[RetryCounterKey, int],
    infrastructure_retries: int,
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
            infrastructure_retries=infrastructure_retries,
            total_retries=int(task.request.retries or 0),
        )
        _retry_or_dead_letter(
            task,
            exc,
            budget_key="infrastructure_retries",
            budget_retries=infrastructure_retries,
            budget_limit=INFRASTRUCTURE_RETRY_MAX_RETRIES,
            retry_state=retry_state,
        )


def _analyze_with_retry(
    task: Task,
    file_id: str,
    *,
    storage_retries: int = 0,
    provider_retries: int = 0,
    lease_retries: int = 0,
    infrastructure_retries: int = 0,
) -> str:
    """编排独立的存储、模型、租约和基础设施重试预算。"""
    retry_state: dict[RetryCounterKey, int] = {
        "storage_retries": max(0, int(storage_retries)),
        "provider_retries": max(0, int(provider_retries)),
        "lease_retries": max(0, int(lease_retries)),
        "infrastructure_retries": max(0, int(infrastructure_retries)),
    }
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
            retry_state=retry_state,
            infrastructure_retries=retry_state["infrastructure_retries"],
        )
    except AiAnalysisAlreadyRunningError as exc:
        retries = retry_state["lease_retries"]
        logger.info(
            "ai_analysis_redelivery_waiting_for_active_lease",
            file_id=file_id,
            lease_retries=retries,
            total_retries=int(task.request.retries or 0),
        )
        _retry_or_dead_letter(
            task,
            exc,
            budget_key="lease_retries",
            budget_retries=retries,
            budget_limit=ANALYSIS_REDELIVERY_MAX_RETRIES,
            retry_state=retry_state,
        )
    except AiAnalysisTransientError as exc:
        is_storage_failure = exc.retry_budget == "storage"
        budget_key: RetryCounterKey = (
            "storage_retries" if is_storage_failure else "provider_retries"
        )
        retries = retry_state[budget_key]
        budget_ceiling = (
            STORAGE_RETRY_MAX_RETRIES if is_storage_failure else PROVIDER_RETRY_MAX_RETRIES
        )
        retry_limit = min(exc.max_retries, budget_ceiling)
        if retries >= retry_limit:
            failure_code = (
                events.AiAnalysisFailureCode.PROVIDER_UNAVAILABLE
                if is_storage_failure
                else events.normalize_analysis_failure_code(exc.failure_category)
            )
            logger.error(
                "ai_analysis_transient_retry_exhausted",
                file_id=file_id,
                retry_budget=exc.retry_budget,
                budget_retries=retries,
                total_retries=int(task.request.retries or 0),
                failure_category=exc.failure_category,
            )
            return _mark_analysis_failed_or_retry(
                task,
                file_id,
                error_message=(
                    STORAGE_RETRY_EXHAUSTED_MESSAGE
                    if is_storage_failure
                    else provider_failure_message(exc.failure_category)
                ),
                error_code=failure_code,
                delivery_token=delivery_token,
                retry_state=retry_state,
                infrastructure_retries=retry_state["infrastructure_retries"],
                require_retry_wait=True,
            )
        _retry_or_dead_letter(
            task,
            exc,
            budget_key=budget_key,
            budget_retries=retries,
            budget_limit=retry_limit,
            retry_state=retry_state,
        )
    except Exception as exc:
        retries = retry_state["infrastructure_retries"]
        logger.error(
            "ai_analysis_infrastructure_failure",
            file_id=file_id,
            error_type=type(exc).__name__,
            infrastructure_retries=retries,
            total_retries=int(task.request.retries or 0),
        )
        _retry_or_dead_letter(
            task,
            exc,
            budget_key="infrastructure_retries",
            budget_retries=retries,
            budget_limit=INFRASTRUCTURE_RETRY_MAX_RETRIES,
            retry_state=retry_state,
        )


@celery_app.task(  # type: ignore[misc]
    name="ai.analyze_file",
    bind=True,
    acks_late=True,
    acks_on_failure_or_timeout=False,
    reject_on_worker_lost=True,
    max_retries=ANALYSIS_TOTAL_MAX_RETRIES,
    soft_time_limit=ANALYSIS_SOFT_TIME_LIMIT_SECONDS,
    time_limit=ANALYSIS_TIME_LIMIT_SECONDS,
)
def ai_analyze_file_task(
    self: Task,
    file_id: str,
    *,
    storage_retries: int = 0,
    provider_retries: int = 0,
    lease_retries: int = 0,
    infrastructure_retries: int = 0,
) -> str:
    return _analyze_with_retry(
        self,
        file_id,
        storage_retries=storage_retries,
        provider_retries=provider_retries,
        lease_retries=lease_retries,
        infrastructure_retries=infrastructure_retries,
    )


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
