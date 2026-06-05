from __future__ import annotations

import asyncio
import uuid
from importlib import import_module

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionFactory
from app.workers.celery_app import celery_app

from .repository import RagflowTaskRepository  # noqa: TID251 - same-module repository dependency
from .service import RagflowTaskService  # noqa: TID251 - same-module service dependency

import_module("app.db.models")


async def create_ragflow_upload_sync_task(
    *,
    session: AsyncSession,
    file_id: uuid.UUID,
) -> uuid.UUID:
    task = await RagflowTaskService(
        session=session,
        repository=RagflowTaskRepository(session),
    ).create_ragflow_upload_task(file_id)
    return task.id


@celery_app.task(name="ragflow.upload", bind=True, max_retries=3)  # type: ignore[misc]
def ragflow_upload_task(_self: object, sync_task_id: str) -> str:
    return run_ragflow_upload_task(sync_task_id)


def run_ragflow_upload_task(sync_task_id: str) -> str:
    asyncio.run(run_ragflow_upload_task_async(sync_task_id))
    return sync_task_id


async def run_ragflow_upload_task_async(sync_task_id: str) -> None:
    task_id = uuid.UUID(sync_task_id)
    await _run_ragflow_upload_task(task_id)


async def _run_ragflow_upload_task(sync_task_id: uuid.UUID) -> None:
    async with AsyncSessionFactory() as session:
        service = RagflowTaskService(
            session=session,
            repository=RagflowTaskRepository(session),
        )
        await service.mark_running(sync_task_id)

    async with AsyncSessionFactory() as session:
        service = RagflowTaskService(
            session=session,
            repository=RagflowTaskRepository(session),
        )
        await service.mark_succeeded(sync_task_id)
