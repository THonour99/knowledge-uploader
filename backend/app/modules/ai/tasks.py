from __future__ import annotations

import asyncio
import uuid
from importlib import import_module

import structlog
from celery import Task
from celery.exceptions import MaxRetriesExceededError, SoftTimeLimitExceeded

from app.adapters.minio_client import MinioDocumentStorage
from app.core.config import Settings, get_settings
from app.core.database import AsyncSessionFactory, engine
from app.workers.celery_app import celery_app

from .exceptions import AiAnalysisPreconditionError, AiAnalysisTransientError
from .repository import AiRepository  # noqa: TID251 - same-module repository dependency
from .service import (  # noqa: TID251 - same-module service dependency
    AiAnalysisService,
    AiObjectStorage,
)

import_module("app.db.models")

logger = structlog.get_logger(__name__)

STORAGE_RETRY_MAX_RETRIES = 3
STORAGE_RETRY_BASE_COUNTDOWN_SECONDS = 30
STORAGE_RETRY_EXHAUSTED_MESSAGE = f"存储暂不可用。已重试 {STORAGE_RETRY_MAX_RETRIES} 次"
ANALYSIS_SOFT_TIME_LIMIT_SECONDS = 600
ANALYSIS_TIME_LIMIT_SECONDS = 660
ANALYSIS_TIMEOUT_MESSAGE = f"分析超时({ANALYSIS_SOFT_TIME_LIMIT_SECONDS}s)"


def build_ai_storage(settings: Settings) -> AiObjectStorage:
    return MinioDocumentStorage(settings)


def _analyze_with_retry(task: Task, file_id: str) -> str:
    """任务壳层编排: 瞬态错误指数退避重试、软超时与重试耗尽落 analysis_failed。"""
    try:
        return run_ai_analyze_file_task(file_id)
    except SoftTimeLimitExceeded:
        logger.error("ai_analysis_soft_time_limit", file_id=file_id)
        run_mark_analysis_failed_task(file_id, error_message=ANALYSIS_TIMEOUT_MESSAGE)
        return file_id
    except AiAnalysisTransientError as exc:
        retries = int(task.request.retries or 0)
        countdown = (2**retries) * STORAGE_RETRY_BASE_COUNTDOWN_SECONDS
        try:
            raise task.retry(exc=exc, countdown=countdown)
        except MaxRetriesExceededError:
            logger.error(
                "ai_analysis_storage_retry_exhausted",
                file_id=file_id,
                retries=retries,
            )
            run_mark_analysis_failed_task(
                file_id,
                error_message=STORAGE_RETRY_EXHAUSTED_MESSAGE,
            )
            return file_id


@celery_app.task(  # type: ignore[misc]
    name="ai.analyze_file",
    bind=True,
    max_retries=STORAGE_RETRY_MAX_RETRIES,
    soft_time_limit=ANALYSIS_SOFT_TIME_LIMIT_SECONDS,
    time_limit=ANALYSIS_TIME_LIMIT_SECONDS,
)
def ai_analyze_file_task(self: Task, file_id: str) -> str:
    return _analyze_with_retry(self, file_id)


def run_ai_analyze_file_task(file_id: str) -> str:
    asyncio.run(run_ai_analyze_file_task_async(file_id))
    return file_id


async def run_ai_analyze_file_task_async(file_id: str) -> None:
    file_uuid = uuid.UUID(file_id)
    try:
        await _run_ai_analyze_file(file_uuid)
    finally:
        await engine.dispose()


def run_mark_analysis_failed_task(file_id: str, *, error_message: str) -> str:
    asyncio.run(run_mark_analysis_failed_task_async(file_id, error_message=error_message))
    return file_id


async def run_mark_analysis_failed_task_async(file_id: str, *, error_message: str) -> None:
    file_uuid = uuid.UUID(file_id)
    try:
        settings = get_settings()
        async with AsyncSessionFactory() as session:
            service = AiAnalysisService(
                session=session,
                repository=AiRepository(session),
                settings=settings,
            )
            await service.mark_analysis_failed(file_id=file_uuid, error_message=error_message)
    finally:
        await engine.dispose()


async def _run_ai_analyze_file(file_id: uuid.UUID) -> None:
    settings = get_settings()
    storage = build_ai_storage(settings)
    async with AsyncSessionFactory() as session:
        service = AiAnalysisService(
            session=session,
            repository=AiRepository(session),
            settings=settings,
        )
        try:
            await service.run_file_analysis(file_id, storage=storage)
        except AiAnalysisPreconditionError as exc:
            # 前置条件在投递与执行之间的窗口内变化(例如 AI 被关闭)属预期竞态、
            # 不算失败。记录 warning 便于排查、保持静默跳过的行为不变。
            logger.warning(
                "ai_analysis_precondition_failed",
                file_id=str(file_id),
                reason=str(exc),
            )
            return
