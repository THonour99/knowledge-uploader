from __future__ import annotations

import asyncio
import uuid
from importlib import import_module

from app.adapters.minio_client import MinioDocumentStorage
from app.core.config import Settings, get_settings
from app.core.database import AsyncSessionFactory, engine
from app.workers.celery_app import celery_app

from .exceptions import AiAnalysisPreconditionError
from .repository import AiRepository  # noqa: TID251 - same-module repository dependency
from .service import (  # noqa: TID251 - same-module service dependency
    AiAnalysisService,
    AiObjectStorage,
)

import_module("app.db.models")


def build_ai_storage(settings: Settings) -> AiObjectStorage:
    return MinioDocumentStorage(settings)


@celery_app.task(name="ai.analyze_file")  # type: ignore[misc]
def ai_analyze_file_task(file_id: str) -> str:
    return run_ai_analyze_file_task(file_id)


def run_ai_analyze_file_task(file_id: str) -> str:
    asyncio.run(run_ai_analyze_file_task_async(file_id))
    return file_id


async def run_ai_analyze_file_task_async(file_id: str) -> None:
    file_uuid = uuid.UUID(file_id)
    try:
        await _run_ai_analyze_file(file_uuid)
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
        except AiAnalysisPreconditionError:
            return
