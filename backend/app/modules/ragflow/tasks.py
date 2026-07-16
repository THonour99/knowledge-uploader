from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from importlib import import_module
from typing import NoReturn

from celery import Task
from celery.exceptions import MaxRetriesExceededError, Reject
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.minio_client import MinioDocumentStorage
from app.adapters.ragflow.base import RagflowClient
from app.adapters.ragflow.http import HttpRagflowClient
from app.core.config import Settings, get_settings
from app.core.database import AsyncSessionFactory, engine
from app.core.ragflow_runtime import resolve_ragflow_runtime_settings
from app.workers.celery_app import celery_app

from .exceptions import RagflowTaskAlreadyRunningError, RagflowTaskLeaseLostError
from .repository import RagflowTaskRepository  # noqa: TID251 - same-module repository dependency
from .service import (  # noqa: TID251 - same-module service dependency
    RagflowObjectStorage,
    RagflowTaskService,
)
from .sync_locks import (
    RagflowSyncLockBusy,
    acquire_sync_lock,
    release_sync_lock,
    release_sync_lock_after_transaction,
    wait_for_active_ragflow_upload_task,
)

import_module("app.db.models")

# 40 attempts at the capped 120 s delay outlive the maximum 3,900 s execution lease.
RAGFLOW_REDELIVERY_MAX_RETRIES = 40
RAGFLOW_CREATION_MAX_RETRIES = 3
RAGFLOW_REDELIVERY_BASE_COUNTDOWN_SECONDS = 30
RAGFLOW_REDELIVERY_MAX_COUNTDOWN_SECONDS = 120
RAGFLOW_HEARTBEAT_INTERVAL_SECONDS = 60.0


class _ClaimedRagflowExecutionError(Exception):
    def __init__(self, error_type: str) -> None:
        super().__init__(error_type)
        self.error_type = error_type


def build_document_storage(settings: Settings) -> RagflowObjectStorage:
    return MinioDocumentStorage(settings)


async def build_ragflow_client_from_runtime_config() -> RagflowClient:
    """Build a RAGFlow HTTP client using runtime config (DB-first, env fallback)."""
    runtime_settings = await resolve_ragflow_runtime_settings()
    return HttpRagflowClient(
        base_url=runtime_settings.base_url,
        api_key=runtime_settings.api_key,
        timeout_seconds=runtime_settings.timeout_seconds,
    )


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


async def create_ragflow_delete_sync_task(
    *,
    session: AsyncSession,
    file_id: uuid.UUID,
) -> uuid.UUID:
    repository = RagflowTaskRepository(session)
    active_task = await repository.get_active_task(file_id=file_id, task_type="ragflow_delete")
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
        ).create_ragflow_delete_task(file_id)
        return task.id
    except Exception:
        await release_sync_lock(
            redis_url=settings.cache_redis_url,
            file_id=file_id,
            token=lock_token,
        )
        raise


def _retry_or_dead_letter(
    task: Task,
    error: BaseException,
    *,
    max_countdown: int = RAGFLOW_REDELIVERY_MAX_COUNTDOWN_SECONDS,
) -> NoReturn:
    retries = int(task.request.retries or 0)
    countdown = min(
        (2**retries) * RAGFLOW_REDELIVERY_BASE_COUNTDOWN_SECONDS,
        max_countdown,
    )
    error_type = type(error).__name__
    configured_max_retries = task.max_retries
    if configured_max_retries is not None and retries >= configured_max_retries:
        raise Reject(reason=error_type, requeue=False) from None
    try:
        # Do not serialize the infrastructure exception into broker metadata.
        # Celery raises MaxRetriesExceededError only when retry() receives no exc.
        raise task.retry(countdown=countdown)
    except MaxRetriesExceededError:
        # Reject without requeue is deliberate: RabbitMQ routes the original,
        # sanitized Celery message through the queue's DLX instead of hot-looping.
        raise Reject(reason=error_type, requeue=False) from None


def _run_ragflow_creation_with_retry(
    task: Task,
    file_id: str,
    *,
    run_task: Callable[[str], str],
) -> str:
    try:
        return run_task(file_id)
    except Exception as exc:
        _retry_or_dead_letter(task, exc)


@celery_app.task(  # type: ignore[misc]
    name="ragflow.create_upload_task",
    bind=True,
    acks_late=True,
    acks_on_failure_or_timeout=False,
    reject_on_worker_lost=True,
    max_retries=RAGFLOW_CREATION_MAX_RETRIES,
)
def ragflow_create_upload_task(self: Task, file_id: str) -> str:
    return _run_ragflow_creation_with_retry(
        self,
        file_id,
        run_task=run_create_ragflow_upload_task,
    )


@celery_app.task(  # type: ignore[misc]
    name="ragflow.create_delete_task",
    bind=True,
    acks_late=True,
    acks_on_failure_or_timeout=False,
    reject_on_worker_lost=True,
    max_retries=RAGFLOW_CREATION_MAX_RETRIES,
)
def ragflow_create_delete_task(self: Task, file_id: str) -> str:
    return _run_ragflow_creation_with_retry(
        self,
        file_id,
        run_task=run_create_ragflow_delete_task,
    )


def run_create_ragflow_delete_task(file_id: str) -> str:
    return asyncio.run(run_create_ragflow_delete_task_async(file_id))


async def run_create_ragflow_delete_task_async(file_id: str) -> str:
    try:
        file_uuid = uuid.UUID(file_id)
        async with AsyncSessionFactory() as session:
            task_id = await create_ragflow_delete_sync_task(session=session, file_id=file_uuid)
            await session.commit()
            return str(task_id)
    except Exception as exc:
        error_type = type(exc).__name__
        raise RuntimeError(error_type) from None
    finally:
        await engine.dispose()


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


def _run_ragflow_with_retry(
    task: Task,
    sync_task_id: str,
    *,
    run_task: Callable[[str], str],
) -> str:
    try:
        return run_task(sync_task_id)
    except RagflowTaskAlreadyRunningError as exc:
        retries = int(task.request.retries or 0)
        countdown = min(
            (2**retries) * RAGFLOW_REDELIVERY_BASE_COUNTDOWN_SECONDS,
            RAGFLOW_REDELIVERY_MAX_COUNTDOWN_SECONDS,
        )
        try:
            raise task.retry(exc=exc, countdown=countdown)
        except MaxRetriesExceededError:
            schedule_ragflow_execution_probe(sync_task_id)
            return sync_task_id
    except Exception as exc:
        _retry_or_dead_letter(task, exc)


def schedule_ragflow_execution_probe(sync_task_id: str) -> None:
    asyncio.run(schedule_ragflow_execution_probe_async(sync_task_id))


async def schedule_ragflow_execution_probe_async(sync_task_id: str) -> None:
    try:
        task_id = uuid.UUID(sync_task_id)
        async with AsyncSessionFactory() as session:
            service = RagflowTaskService(
                session=session,
                repository=RagflowTaskRepository(session),
            )
            await service.schedule_execution_recovery_probe(task_id)
    finally:
        await engine.dispose()


async def _maintain_ragflow_execution_lease(
    *,
    task_id: uuid.UUID,
    execution_token: str,
    stop: asyncio.Event,
) -> None:
    while True:
        try:
            await asyncio.wait_for(stop.wait(), timeout=RAGFLOW_HEARTBEAT_INTERVAL_SECONDS)
            return
        except TimeoutError:
            async with AsyncSessionFactory() as session:
                service = RagflowTaskService(
                    session=session,
                    repository=RagflowTaskRepository(session),
                )
                await service.heartbeat_execution_lease(
                    task_id=task_id,
                    execution_token=execution_token,
                )


async def _run_with_execution_heartbeat(
    *,
    task_id: uuid.UUID,
    execution_token: str,
    operation: Callable[[], Awaitable[None]],
) -> None:
    stop = asyncio.Event()
    operation_task: asyncio.Future[None] = asyncio.ensure_future(operation())
    heartbeat_task: asyncio.Task[None] = asyncio.create_task(
        _maintain_ragflow_execution_lease(
            task_id=task_id,
            execution_token=execution_token,
            stop=stop,
        )
    )
    try:
        done, _pending = await asyncio.wait(
            {operation_task, heartbeat_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if operation_task in done:
            await operation_task
            return
        await heartbeat_task
        msg = "ragflow execution heartbeat stopped unexpectedly"
        raise RuntimeError(msg)
    finally:
        stop.set()
        for running_task in (operation_task, heartbeat_task):
            if not running_task.done():
                running_task.cancel()
        await asyncio.gather(operation_task, heartbeat_task, return_exceptions=True)


@celery_app.task(  # type: ignore[misc]
    name="ragflow.upload",
    bind=True,
    acks_late=True,
    acks_on_failure_or_timeout=False,
    reject_on_worker_lost=True,
    max_retries=RAGFLOW_REDELIVERY_MAX_RETRIES,
)
def ragflow_upload_task(self: Task, sync_task_id: str) -> str:
    return _run_ragflow_with_retry(
        self,
        sync_task_id,
        run_task=run_ragflow_upload_task,
    )


def run_ragflow_upload_task(sync_task_id: str) -> str:
    asyncio.run(run_ragflow_upload_task_async(sync_task_id))
    return sync_task_id


async def run_ragflow_upload_task_async(sync_task_id: str) -> None:
    task_id = uuid.UUID(sync_task_id)
    execution_token = uuid.uuid4().hex
    try:
        await _run_ragflow_upload_task(task_id, execution_token=execution_token)
    except RagflowTaskAlreadyRunningError:
        raise
    except RagflowTaskLeaseLostError:
        return
    except _ClaimedRagflowExecutionError as exc:
        acknowledged = await _mark_ragflow_upload_task_failed(
            task_id,
            exc.error_type,
            expected_lease_token=execution_token,
            was_claimed=True,
        )
        if not acknowledged:
            raise RuntimeError(exc.error_type) from None
        return
    except Exception as exc:
        error_type = type(exc).__name__
        acknowledged = await _mark_ragflow_upload_task_failed(
            task_id,
            error_type,
            expected_lease_token=execution_token,
            was_claimed=False,
        )
        if not acknowledged:
            raise RuntimeError(error_type) from None
        # 领域失败已可靠持久化; 正常返回让 Celery ack, 避免低层任务进入无法安全重放的 DLQ。
        return
    finally:
        await engine.dispose()


async def _run_ragflow_upload_task(
    sync_task_id: uuid.UUID,
    *,
    execution_token: str,
) -> None:
    async with AsyncSessionFactory() as session:
        repository = RagflowTaskRepository(session)
        service = RagflowTaskService(
            session=session,
            repository=repository,
        )
        claimed = await service.claim_running(
            sync_task_id,
            expected_task_types={"ragflow_upload", "ragflow_status_check"},
            execution_token=execution_token,
        )
        if not claimed:
            task = await repository.get_task(sync_task_id)
            if task is not None and (
                task.task_type in {"ragflow_upload", "ragflow_status_check"}
                and (
                    task.status == "running"
                    or (task.status == "queued" and task.reconcile_not_before is not None)
                )
            ):
                raise RagflowTaskAlreadyRunningError
            return

    async def execute_claimed_upload() -> None:
        settings = get_settings()
        storage = build_document_storage(settings)
        ragflow_client = await build_ragflow_client_from_runtime_config()
        async with AsyncSessionFactory() as session:
            service = RagflowTaskService(
                session=session,
                repository=RagflowTaskRepository(session),
            )
            await service.run_upload_task(
                sync_task_id,
                storage=storage,
                ragflow_client=ragflow_client,
            )

    try:
        await _run_with_execution_heartbeat(
            task_id=sync_task_id,
            execution_token=execution_token,
            operation=execute_claimed_upload,
        )
    except RagflowTaskLeaseLostError:
        raise
    except Exception as exc:
        raise _ClaimedRagflowExecutionError(type(exc).__name__) from None


async def _mark_ragflow_upload_task_failed(
    sync_task_id: uuid.UUID,
    error_type: str,
    *,
    expected_lease_token: str | None = None,
    was_claimed: bool = False,
) -> bool:
    async with AsyncSessionFactory() as session:
        service = RagflowTaskService(
            session=session,
            repository=RagflowTaskRepository(session),
        )
        task = await service.mark_failed(
            sync_task_id,
            error_type,
            mark_file_failed=error_type != "RagflowParsePendingError",
            expected_lease_token=expected_lease_token,
        )
        return _failure_is_acknowledged(
            task_status=task.status,
            task_error=task.error_message,
            current_lease_token=task.lease_token,
            expected_lease_token=expected_lease_token,
            error_type=error_type,
            was_claimed=was_claimed,
        )


def run_mark_ragflow_upload_task_failed(sync_task_id: str, error_type: str) -> str:
    asyncio.run(_mark_ragflow_upload_task_failed(uuid.UUID(sync_task_id), error_type))
    return sync_task_id


@celery_app.task(  # type: ignore[misc]
    name="ragflow.delete",
    bind=True,
    acks_late=True,
    acks_on_failure_or_timeout=False,
    reject_on_worker_lost=True,
    max_retries=RAGFLOW_REDELIVERY_MAX_RETRIES,
)
def ragflow_delete_task(self: Task, sync_task_id: str) -> str:
    return _run_ragflow_with_retry(
        self,
        sync_task_id,
        run_task=run_ragflow_delete_task,
    )


def run_ragflow_delete_task(sync_task_id: str) -> str:
    asyncio.run(run_ragflow_delete_task_async(sync_task_id))
    return sync_task_id


async def run_ragflow_delete_task_async(sync_task_id: str) -> None:
    task_id = uuid.UUID(sync_task_id)
    execution_token = uuid.uuid4().hex
    try:
        await _run_ragflow_delete_task(task_id, execution_token=execution_token)
    except RagflowTaskAlreadyRunningError:
        raise
    except RagflowTaskLeaseLostError:
        return
    except _ClaimedRagflowExecutionError as exc:
        acknowledged = await _mark_ragflow_delete_task_failed(
            task_id,
            exc.error_type,
            expected_lease_token=execution_token,
            was_claimed=True,
        )
        if not acknowledged:
            raise RuntimeError(exc.error_type) from None
        return
    except Exception as exc:
        error_type = type(exc).__name__
        acknowledged = await _mark_ragflow_delete_task_failed(
            task_id,
            error_type,
            expected_lease_token=execution_token,
            was_claimed=False,
        )
        if not acknowledged:
            raise RuntimeError(error_type) from None
        # 领域失败已可靠持久化; 正常返回让 Celery ack, 恢复应通过 DB 任务重试入口。
        return
    finally:
        await engine.dispose()


async def _run_ragflow_delete_task(
    sync_task_id: uuid.UUID,
    *,
    execution_token: str,
) -> None:
    async with AsyncSessionFactory() as session:
        repository = RagflowTaskRepository(session)
        service = RagflowTaskService(
            session=session,
            repository=repository,
        )
        claimed = await service.claim_running(
            sync_task_id,
            expected_task_types={"ragflow_delete"},
            execution_token=execution_token,
        )
        if not claimed:
            task = await repository.get_task(sync_task_id)
            if (
                task is not None
                and task.task_type == "ragflow_delete"
                and task.status == "running"
            ):
                raise RagflowTaskAlreadyRunningError
            return

    async def execute_claimed_delete() -> None:
        ragflow_client = await build_ragflow_client_from_runtime_config()
        async with AsyncSessionFactory() as session:
            service = RagflowTaskService(
                session=session,
                repository=RagflowTaskRepository(session),
            )
            await service.run_delete_task(sync_task_id, ragflow_client=ragflow_client)

    try:
        await _run_with_execution_heartbeat(
            task_id=sync_task_id,
            execution_token=execution_token,
            operation=execute_claimed_delete,
        )
    except RagflowTaskLeaseLostError:
        raise
    except Exception as exc:
        raise _ClaimedRagflowExecutionError(type(exc).__name__) from None


async def _mark_ragflow_delete_task_failed(
    sync_task_id: uuid.UUID,
    error_type: str,
    *,
    expected_lease_token: str | None = None,
    was_claimed: bool = False,
) -> bool:
    async with AsyncSessionFactory() as session:
        service = RagflowTaskService(
            session=session,
            repository=RagflowTaskRepository(session),
        )
        task = await service.mark_failed(
            sync_task_id,
            error_type,
            expected_lease_token=expected_lease_token,
        )
        return _failure_is_acknowledged(
            task_status=task.status,
            task_error=task.error_message,
            current_lease_token=task.lease_token,
            expected_lease_token=expected_lease_token,
            error_type=error_type,
            was_claimed=was_claimed,
        )


def _failure_is_acknowledged(
    *,
    task_status: str,
    task_error: str | None,
    current_lease_token: str | None,
    expected_lease_token: str | None,
    error_type: str,
    was_claimed: bool,
) -> bool:
    if task_status == "failed" and task_error == error_type:
        return True
    if task_status in {"succeeded", "failed", "canceled"}:
        return True
    return (
        was_claimed
        and task_status in {"queued", "running"}
        and expected_lease_token is not None
        and current_lease_token != expected_lease_token
    )


def run_mark_ragflow_delete_task_failed(sync_task_id: str, error_type: str) -> str:
    asyncio.run(_mark_ragflow_delete_task_failed(uuid.UUID(sync_task_id), error_type))
    return sync_task_id
