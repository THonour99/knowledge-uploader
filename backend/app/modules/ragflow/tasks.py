from __future__ import annotations

import asyncio
import uuid
from importlib import import_module

from redis.asyncio import from_url
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from sqlalchemy.orm.session import SessionTransaction

from app.core.config import get_settings
from app.core.database import AsyncSessionFactory
from app.workers.celery_app import celery_app

from .models import SyncTask
from .repository import RagflowTaskRepository  # noqa: TID251 - same-module repository dependency
from .service import RagflowTaskService  # noqa: TID251 - same-module service dependency

import_module("app.db.models")

SYNC_LOCK_TTL_SECONDS = 30
SYNC_LOCK_WAIT_SECONDS = 2.0
SYNC_LOCK_POLL_SECONDS = 0.05
RELEASE_SYNC_LOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
end
return 0
"""
_PENDING_LOCK_RELEASE_TASKS: set[asyncio.Task[None]] = set()


class RagflowSyncLockBusy(Exception):
    pass


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
    lock_acquired = await _acquire_sync_lock(
        redis_url=settings.cache_redis_url,
        file_id=file_id,
        token=lock_token,
    )
    if not lock_acquired:
        active_task = await _wait_for_active_ragflow_upload_task(
            repository=repository,
            file_id=file_id,
        )
        if active_task is not None:
            return active_task.id
        lock_token = uuid.uuid4().hex
        lock_acquired = await _acquire_sync_lock(
            redis_url=settings.cache_redis_url,
            file_id=file_id,
            token=lock_token,
        )
        if not lock_acquired:
            msg = "ragflow sync lock is busy"
            raise RagflowSyncLockBusy(msg)

    _release_sync_lock_after_transaction(
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
        await _release_sync_lock(
            redis_url=settings.cache_redis_url,
            file_id=file_id,
            token=lock_token,
        )
        raise


async def _wait_for_active_ragflow_upload_task(
    *,
    repository: RagflowTaskRepository,
    file_id: uuid.UUID,
) -> SyncTask | None:
    deadline = asyncio.get_running_loop().time() + SYNC_LOCK_WAIT_SECONDS
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(SYNC_LOCK_POLL_SECONDS)
        active_task = await repository.get_active_task(file_id=file_id, task_type="ragflow_upload")
        if active_task is not None:
            return active_task
    return None


async def _acquire_sync_lock(*, redis_url: str, file_id: uuid.UUID, token: str) -> bool:
    client = from_url(  # type: ignore[no-untyped-call]
        redis_url,
        encoding="utf-8",
        decode_responses=True,
    )
    try:
        return bool(
            await client.set(
                _sync_lock_key(file_id),
                token,
                nx=True,
                ex=SYNC_LOCK_TTL_SECONDS,
            )
        )
    finally:
        await client.aclose()


async def _release_sync_lock(*, redis_url: str, file_id: uuid.UUID, token: str) -> None:
    client = from_url(  # type: ignore[no-untyped-call]
        redis_url,
        encoding="utf-8",
        decode_responses=True,
    )
    try:
        await client.eval(RELEASE_SYNC_LOCK_SCRIPT, 1, _sync_lock_key(file_id), token)
    finally:
        await client.aclose()


def _release_sync_lock_after_transaction(
    *,
    session: AsyncSession,
    redis_url: str,
    file_id: uuid.UUID,
    token: str,
) -> None:
    sync_session = session.sync_session
    released = False

    def release_after_end(
        ended_session: Session,
        transaction: SessionTransaction,
    ) -> None:
        nonlocal released
        del ended_session
        if transaction.parent is not None:
            return
        if released:
            return
        released = True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(_release_sync_lock(redis_url=redis_url, file_id=file_id, token=token))
            return
        release_task = loop.create_task(
            _release_sync_lock(redis_url=redis_url, file_id=file_id, token=token)
        )
        _PENDING_LOCK_RELEASE_TASKS.add(release_task)
        release_task.add_done_callback(_PENDING_LOCK_RELEASE_TASKS.discard)

    event.listen(sync_session, "after_transaction_end", release_after_end)


def _sync_lock_key(file_id: uuid.UUID) -> str:
    return f"lock:sync:{file_id}"


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
