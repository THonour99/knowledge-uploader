from __future__ import annotations

import asyncio
import uuid
from importlib import import_module

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import AsyncSessionFactory, engine
from app.workers.celery_app import celery_app

from .repository import RagflowTaskRepository  # noqa: TID251 - same-module repository dependency
from .service import RagflowTaskService  # noqa: TID251 - same-module service dependency
from .sync_locks import (
    RagflowSyncLockBusy,
    acquire_sync_lock,
    release_sync_lock,
    release_sync_lock_after_transaction,
    wait_for_active_ragflow_upload_task,
)

import_module("app.db.models")


async def create_ragflow_upload_sync_task(
    *,
    session: AsyncSession,
    file_id: uuid.UUID,
) -> uuid.UUID:
    repository = RagflowTaskRepository(session)
    active_task = await repository.get_active_task(file_id=file_id, task_type="ragflow_upload")
    if active_task is not None:
        return active_task.id

    settings = get_settings()
    lock_token = uuid.uuid4().hex
    lock_acquired = await acquire_sync_lock(
        redis_url=settings.cache_redis_url,
        file_id=file_id,
        token=lock_token,
    )
    if not lock_acquired:
        active_task = await wait_for_active_ragflow_upload_task(
            repository=repository,
            file_id=file_id,
        )
        if active_task is not None:
            return active_task.id
        lock_token = uuid.uuid4().hex
        lock_acquired = await acquire_sync_lock(
            redis_url=settings.cache_redis_url,
            file_id=file_id,
            token=lock_token,
        )
        if not lock_acquired:
            msg = "ragflow sync lock is busy"
            raise RagflowSyncLockBusy(msg)

    release_sync_lock_after_transaction(
        session=session,
        redis_url=settings.cache_redis_url,
        file_id=file_id,
        token=lock_token,
    )
    try:
        task = await RagflowTaskService(
            session=session,
            repository=repository,
        ).create_ragflow_upload_task(file_id)
        return task.id
    except Exception:
        await release_sync_lock(
            redis_url=settings.cache_redis_url,
            file_id=file_id,
            token=lock_token,
        )
        raise


@celery_app.task(  # type: ignore[misc]
    name="ragflow.create_upload_task",
    autoretry_for=(RuntimeError,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def ragflow_create_upload_task(file_id: str) -> str:
    return run_create_ragflow_upload_task(file_id)


def run_create_ragflow_upload_task(file_id: str) -> str:
    return asyncio.run(run_create_ragflow_upload_task_async(file_id))


async def run_create_ragflow_upload_task_async(file_id: str) -> str:
    try:
        file_uuid = uuid.UUID(file_id)
        async with AsyncSessionFactory() as session:
            task_id = await create_ragflow_upload_sync_task(session=session, file_id=file_uuid)
            await session.commit()
            return str(task_id)
    except Exception as exc:
        error_type = type(exc).__name__
        raise RuntimeError(error_type) from None
    finally:
        await engine.dispose()


@celery_app.task(name="ragflow.upload")  # type: ignore[misc]
def ragflow_upload_task(sync_task_id: str) -> str:
    return run_ragflow_upload_task(sync_task_id)


def run_ragflow_upload_task(sync_task_id: str) -> str:
    asyncio.run(run_ragflow_upload_task_async(sync_task_id))
    return sync_task_id


async def run_ragflow_upload_task_async(sync_task_id: str) -> None:
    task_id = uuid.UUID(sync_task_id)
    try:
        await _run_ragflow_upload_task(task_id)
    except Exception as exc:
        error_type = type(exc).__name__
        await _mark_ragflow_upload_task_failed(task_id, error_type)
        raise RuntimeError(error_type) from None
    finally:
        await engine.dispose()


async def _run_ragflow_upload_task(sync_task_id: uuid.UUID) -> None:
    async with AsyncSessionFactory() as session:
        service = RagflowTaskService(
            session=session,
            repository=RagflowTaskRepository(session),
        )
        claimed = await service.claim_running(sync_task_id)
        if not claimed:
            return

    async with AsyncSessionFactory() as session:
        service = RagflowTaskService(
            session=session,
            repository=RagflowTaskRepository(session),
        )
        await service.mark_succeeded(sync_task_id)


async def _mark_ragflow_upload_task_failed(sync_task_id: uuid.UUID, error_type: str) -> None:
    async with AsyncSessionFactory() as session:
        service = RagflowTaskService(
            session=session,
            repository=RagflowTaskRepository(session),
        )
        await service.mark_failed(sync_task_id, error_type)
