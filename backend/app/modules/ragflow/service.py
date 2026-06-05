from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_admin_audit_log
from app.core.config import get_settings
from app.core.outbox import OutboxRepository
from app.modules.user.schemas import AuthUserRecord

from . import events, exceptions
from .models import SyncTask, SyncTaskLog
from .repository import RagflowTaskRepository  # noqa: TID251 - same-module repository dependency
from .sync_locks import acquire_sync_lock, release_sync_lock, release_sync_lock_after_transaction

ADMIN_ROLES = {"knowledge_admin", "system_admin"}
RAGFLOW_UPLOAD_TASK = "ragflow_upload"
DEFAULT_MAX_RETRY_COUNT = 3
MAX_ERROR_MESSAGE_LENGTH = 2000


@dataclass(frozen=True)
class RequestContext:
    ip_address: str
    user_agent: str


@dataclass(frozen=True)
class SyncTaskBundle:
    task: SyncTask
    logs: list[SyncTaskLog]


class RagflowTaskService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        repository: RagflowTaskRepository,
    ) -> None:
        self._session = session
        self._repository = repository

    async def create_ragflow_upload_task(self, file_id: uuid.UUID) -> SyncTask:
        existing = await self._repository.get_active_task(
            file_id=file_id,
            task_type=RAGFLOW_UPLOAD_TASK,
        )
        if existing is not None:
            return existing

        task = SyncTask(
            file_id=file_id,
            task_type=RAGFLOW_UPLOAD_TASK,
            status="queued",
            retry_count=0,
            max_retry_count=DEFAULT_MAX_RETRY_COUNT,
        )
        task = await self._repository.add_task(task)
        await self._repository.add_log(
            task_id=task.id,
            status=task.status,
            message="ragflow upload task queued",
        )
        await self._append_task_queued_event(task)
        return task

    async def list_tasks(
        self,
        *,
        current_user: AuthUserRecord,
        context: RequestContext,
    ) -> list[SyncTaskBundle]:
        self._require_admin(current_user)
        tasks = await self._repository.list_tasks()
        bundles = [await self._bundle(task) for task in tasks]
        await self._record_admin_audit(
            current_user=current_user,
            action="task.list",
            target_type="task_collection",
            target_id=current_user.id,
            context=context,
            metadata_json={"result_count": len(tasks)},
        )
        await self._session.commit()
        return bundles

    async def get_task(
        self,
        *,
        current_user: AuthUserRecord,
        task_id: uuid.UUID,
        context: RequestContext,
    ) -> SyncTaskBundle:
        self._require_admin(current_user)
        task = await self._get_task_or_raise(task_id)
        bundle = await self._bundle(task)
        await self._record_admin_audit(
            current_user=current_user,
            action="task.get",
            target_type="task",
            target_id=task.id,
            context=context,
        )
        await self._session.commit()
        return bundle

    async def retry_task(
        self,
        *,
        current_user: AuthUserRecord,
        task_id: uuid.UUID,
        context: RequestContext,
    ) -> SyncTaskBundle:
        self._require_admin(current_user)
        task = await self._get_task_for_update_or_raise(task_id)
        if task.status != "failed" or task.retry_count >= task.max_retry_count:
            raise exceptions.task_not_retryable()

        settings = get_settings()
        lock_token = uuid.uuid4().hex
        lock_acquired = await acquire_sync_lock(
            redis_url=settings.cache_redis_url,
            file_id=task.file_id,
            token=lock_token,
        )
        if not lock_acquired:
            raise exceptions.task_lock_busy()

        release_sync_lock_after_transaction(
            session=self._session,
            redis_url=settings.cache_redis_url,
            file_id=task.file_id,
            token=lock_token,
        )
        try:
            active_task = await self._repository.get_active_task(
                file_id=task.file_id,
                task_type=RAGFLOW_UPLOAD_TASK,
            )
            if active_task is not None and active_task.id != task.id:
                raise exceptions.task_conflict()

            task.status = "queued"
            task.retry_count += 1
            task.error_message = None
            task.started_at = None
            task.finished_at = None
            await self._repository.add_log(
                task_id=task.id,
                status=task.status,
                message=f"task manually retried, attempt {task.retry_count}",
            )
            await self._append_task_queued_event(task)
            await self._record_admin_audit(
                current_user=current_user,
                action="task.retry",
                target_type="task",
                target_id=task.id,
                context=context,
                metadata_json={"retry_count": task.retry_count},
            )
            await self._session.commit()
            await self._session.refresh(task)
            return await self._bundle(task)
        except Exception:
            await release_sync_lock(
                redis_url=settings.cache_redis_url,
                file_id=task.file_id,
                token=lock_token,
            )
            raise

    async def cancel_task(
        self,
        *,
        current_user: AuthUserRecord,
        task_id: uuid.UUID,
        context: RequestContext,
    ) -> SyncTaskBundle:
        self._require_admin(current_user)
        task = await self._get_task_for_update_or_raise(task_id)
        if task.status != "queued":
            raise exceptions.task_not_cancelable()

        task.status = "canceled"
        task.finished_at = datetime.now(UTC)
        await self._repository.add_log(
            task_id=task.id,
            status=task.status,
            message="task canceled by administrator",
        )
        await self._record_admin_audit(
            current_user=current_user,
            action="task.cancel",
            target_type="task",
            target_id=task.id,
            context=context,
        )
        await self._session.commit()
        await self._session.refresh(task)
        return await self._bundle(task)

    async def claim_running(self, task_id: uuid.UUID) -> bool:
        task = await self._get_task_for_update_or_raise(task_id)
        if task.status != "queued":
            return False
        task.status = "running"
        task.started_at = datetime.now(UTC)
        task.finished_at = None
        task.error_message = None
        await self._repository.add_log(
            task_id=task.id,
            status=task.status,
            message="ragflow upload task started",
        )
        await self._session.commit()
        return True

    async def mark_succeeded(self, task_id: uuid.UUID) -> SyncTask:
        task = await self._get_task_for_update_or_raise(task_id)
        if task.status in {"canceled", "failed", "succeeded"}:
            return task
        if task.status != "running":
            return task
        task.status = "succeeded"
        task.finished_at = datetime.now(UTC)
        task.error_message = None
        await self._repository.add_log(
            task_id=task.id,
            status=task.status,
            message="ragflow upload task completed",
        )
        await self._session.commit()
        return task

    async def mark_failed(self, task_id: uuid.UUID, error_message: str) -> SyncTask:
        task = await self._get_task_for_update_or_raise(task_id)
        if task.status in {"canceled", "succeeded"}:
            return task
        task.status = "failed"
        task.finished_at = datetime.now(UTC)
        task.error_message = error_message[:MAX_ERROR_MESSAGE_LENGTH]
        await self._repository.add_log(
            task_id=task.id,
            status=task.status,
            message="ragflow upload task failed",
        )
        await self._session.commit()
        return task

    async def _bundle(self, task: SyncTask) -> SyncTaskBundle:
        return SyncTaskBundle(task=task, logs=await self._repository.list_logs(task.id))

    async def _get_task_or_raise(self, task_id: uuid.UUID) -> SyncTask:
        task = await self._repository.get_task(task_id)
        if task is None:
            raise exceptions.task_not_found()
        return task

    async def _get_task_for_update_or_raise(self, task_id: uuid.UUID) -> SyncTask:
        task = await self._repository.get_task_for_update(task_id)
        if task is None:
            raise exceptions.task_not_found()
        return task

    async def _record_admin_audit(
        self,
        *,
        current_user: AuthUserRecord,
        action: str,
        target_type: str,
        target_id: uuid.UUID,
        context: RequestContext,
        metadata_json: dict[str, object] | None = None,
    ) -> None:
        await record_admin_audit_log(
            self._session,
            actor_id=current_user.id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            ip_address=context.ip_address,
            user_agent=context.user_agent,
            metadata_json=metadata_json,
        )

    def _require_admin(self, current_user: AuthUserRecord) -> None:
        if current_user.role not in ADMIN_ROLES:
            raise exceptions.permission_denied()

    async def _append_task_queued_event(self, task: SyncTask) -> None:
        await OutboxRepository(self._session).append(
            event_type=events.RAGFLOW_SYNC_TASK_QUEUED,
            aggregate_type="sync_task",
            aggregate_id=str(task.id),
            payload={
                "sync_task_id": str(task.id),
                "file_id": str(task.file_id),
                "task_type": task.task_type,
                "status": task.status,
            },
        )
