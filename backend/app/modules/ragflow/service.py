from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.ragflow.base import RagflowClient, RagflowDocumentStatus
from app.core.audit import record_admin_audit_log
from app.core.config import get_settings
from app.core.document_state import DocumentStateError, DocumentStateMachine
from app.core.outbox import OutboxRepository
from app.modules.user.schemas import AuthUserRecord

from . import events, exceptions
from .models import SyncTask, SyncTaskLog
from .records import RagflowSyncFileRecord
from .repository import RagflowTaskRepository  # noqa: TID251 - same-module repository dependency
from .sync_locks import acquire_sync_lock, release_sync_lock, release_sync_lock_after_transaction

ADMIN_ROLES = {"knowledge_admin", "system_admin"}
RAGFLOW_UPLOAD_TASK = "ragflow_upload"
MAX_ERROR_MESSAGE_LENGTH = 2000
RAGFLOW_SUCCESS_RUNS = {"3", "DONE"}
RAGFLOW_FAILED_RUNS = {"4", "FAIL", "FAILED", "ERROR"}
RAGFLOW_UNSTART_RUNS = {"0", "UNSTART"}
SYNC_READY_STATUSES = {
    "queued",
    "syncing",
    "uploaded_to_ragflow",
    "parsing",
    "parsed",
    "failed",
}


class RagflowObjectStorage(Protocol):
    async def get_object(self, *, bucket: str, object_key: str) -> bytes: ...


class RagflowSyncPreconditionError(Exception):
    pass


class RagflowParseFailedError(Exception):
    pass


class RagflowParsePendingError(Exception):
    pass


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
            max_retry_count=max(0, get_settings().ragflow_max_retry_count),
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

    async def run_upload_task(
        self,
        task_id: uuid.UUID,
        *,
        storage: RagflowObjectStorage,
        ragflow_client: RagflowClient,
    ) -> SyncTask:
        task = await self._get_task_for_update_or_raise(task_id)
        if task.status != "running":
            return task

        file = await self._get_file_for_update_or_raise(task.file_id)
        dataset_id = await self._require_sync_target(file)
        document_id = file.ragflow_document_id
        if document_id:
            file = await self._ensure_file_parsing(task=task, file=file)
            parse_status = await ragflow_client.get_document_status(
                dataset_id=dataset_id,
                document_id=document_id,
            )
            if _is_unstarted_run(parse_status.run):
                await ragflow_client.update_document_metadata(
                    dataset_id=dataset_id,
                    document_id=document_id,
                    name=file.stored_name,
                    metadata=self._build_metadata(file),
                )
                await self._repository.add_log(
                    task_id=task.id,
                    status=task.status,
                    message="ragflow document metadata updated",
                )
                await self._session.commit()
                await ragflow_client.start_parse(dataset_id=dataset_id, document_id=document_id)
                file = await self._transition_sync_file(
                    task=task,
                    file=file,
                    to_status="parsing",
                    parse_status="RUNNING",
                    message="ragflow document parse started",
                )
                parse_status = await ragflow_client.get_document_status(
                    dataset_id=dataset_id,
                    document_id=document_id,
                )
            await self._apply_parse_status(task=task, file=file, parse_status=parse_status)
            _raise_if_parse_not_terminal(parse_status)
            return await self.mark_succeeded(task_id)

        file = await self._transition_sync_file(
            task=task,
            file=file,
            to_status="syncing",
            parse_status="UPLOADING",
            message="ragflow document upload started",
        )
        content = await storage.get_object(bucket=file.bucket, object_key=file.object_key)
        upload_result = await ragflow_client.upload_document(
            dataset_id=dataset_id,
            filename=file.stored_name,
            content=content,
            content_type=file.mime_type,
        )
        file.ragflow_document_id = upload_result.document_id
        file = await self._transition_sync_file(
            task=task,
            file=file,
            to_status="uploaded_to_ragflow",
            parse_status="UNSTART",
            message="ragflow document uploaded",
        )
        await ragflow_client.update_document_metadata(
            dataset_id=dataset_id,
            document_id=upload_result.document_id,
            name=file.stored_name,
            metadata=self._build_metadata(file),
        )
        await self._repository.add_log(
            task_id=task.id,
            status=task.status,
            message="ragflow document metadata updated",
        )
        await self._session.commit()
        await ragflow_client.start_parse(
            dataset_id=dataset_id,
            document_id=upload_result.document_id,
        )
        file = await self._transition_sync_file(
            task=task,
            file=file,
            to_status="parsing",
            parse_status="RUNNING",
            message="ragflow document parse started",
        )
        parse_status = await ragflow_client.get_document_status(
            dataset_id=dataset_id,
            document_id=upload_result.document_id,
        )
        await self._apply_parse_status(task=task, file=file, parse_status=parse_status)
        _raise_if_parse_not_terminal(parse_status)
        return await self.mark_succeeded(task_id)

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

    async def mark_failed(
        self,
        task_id: uuid.UUID,
        error_message: str,
        *,
        mark_file_failed: bool = True,
    ) -> SyncTask:
        task = await self._get_task_for_update_or_raise(task_id)
        if task.status in {"canceled", "succeeded"}:
            return task
        file = await self._repository.get_file_for_update(task.file_id)
        if file is not None and mark_file_failed:
            await self._try_mark_file_failed(file, error_message)
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

    async def _get_file_for_update_or_raise(self, file_id: uuid.UUID) -> RagflowSyncFileRecord:
        file = await self._repository.get_file_for_update(file_id)
        if file is None:
            raise RagflowSyncPreconditionError
        return file

    async def _require_sync_target(self, file: RagflowSyncFileRecord) -> str:
        dataset_id = self._require_dataset_id(file)
        if file.review_status != "approved" or file.status not in SYNC_READY_STATUSES:
            raise RagflowSyncPreconditionError
        if file.dataset_mapping_id is None:
            raise RagflowSyncPreconditionError
        mapping = await self._repository.get_dataset_mapping(file.dataset_mapping_id)
        if (
            mapping is None
            or not mapping.enabled
            or mapping.ragflow_dataset_id != dataset_id
        ):
            raise RagflowSyncPreconditionError
        settings = get_settings()
        allowed_dataset_ids = _normalized_dataset_ids(settings.ragflow_allowed_dataset_ids)
        if settings.ragflow_api_key.strip() and not allowed_dataset_ids:
            raise RagflowSyncPreconditionError
        if allowed_dataset_ids and dataset_id not in allowed_dataset_ids:
            raise RagflowSyncPreconditionError
        return dataset_id

    def _require_dataset_id(self, file: RagflowSyncFileRecord) -> str:
        if file.ragflow_dataset_id is None or not file.ragflow_dataset_id.strip():
            raise RagflowSyncPreconditionError
        return file.ragflow_dataset_id

    async def _ensure_file_parsing(
        self,
        *,
        task: SyncTask,
        file: RagflowSyncFileRecord,
    ) -> RagflowSyncFileRecord:
        if file.status in {"parsing", "parsed"}:
            return file
        return await self._transition_sync_file(
            task=task,
            file=file,
            to_status="parsing",
            parse_status=file.ragflow_parse_status or "RUNNING",
            message="ragflow existing document status check started",
        )

    async def _transition_sync_file(
        self,
        *,
        task: SyncTask,
        file: RagflowSyncFileRecord,
        to_status: str,
        parse_status: str,
        message: str,
    ) -> RagflowSyncFileRecord:
        try:
            if file.status != to_status:
                file.status = DocumentStateMachine.transition(file.status, to_status)
        except DocumentStateError as exc:
            raise RagflowSyncPreconditionError from exc
        file.ragflow_parse_status = parse_status
        file.ragflow_error_message = None
        file.last_sync_at = datetime.now(UTC)
        updated_file = await self._repository.update_file_sync_state(file)
        await self._repository.add_log(task_id=task.id, status=task.status, message=message)
        await self._session.commit()
        return updated_file

    async def _apply_parse_status(
        self,
        *,
        task: SyncTask,
        file: RagflowSyncFileRecord,
        parse_status: RagflowDocumentStatus,
    ) -> RagflowSyncFileRecord:
        run = parse_status.run.upper()
        to_status = "parsing"
        error_message = None
        if _is_success_run(run):
            to_status = "parsed"
        elif _is_failed_run(run):
            to_status = "failed"
            error_message = "RAGFlow parse failed"

        try:
            if file.status != to_status:
                file.status = DocumentStateMachine.transition(file.status, to_status)
        except DocumentStateError as exc:
            raise RagflowSyncPreconditionError from exc
        file.ragflow_parse_status = run
        file.ragflow_error_message = error_message
        file.last_sync_at = datetime.now(UTC)
        updated_file = await self._repository.update_file_sync_state(file)
        await self._repository.add_log(
            task_id=task.id,
            status=task.status,
            message=f"ragflow parse status {run}",
        )
        await self._session.commit()
        return updated_file

    async def _try_mark_file_failed(
        self,
        file: RagflowSyncFileRecord,
        error_message: str,
    ) -> None:
        try:
            if file.status != "failed":
                file.status = DocumentStateMachine.transition(file.status, "failed")
        except DocumentStateError:
            return
        file.ragflow_error_message = error_message[:MAX_ERROR_MESSAGE_LENGTH]
        file.last_sync_at = datetime.now(UTC)
        await self._repository.update_file_sync_state(file)

    def _build_metadata(self, file: RagflowSyncFileRecord) -> dict[str, object]:
        return {
            "source": "knowledge_uploader",
            "file_id": str(file.id),
            "uploader": str(file.uploader_id),
            "department": file.department,
            "category": str(file.category_id) if file.category_id is not None else None,
            "tags": file.tags,
            "visibility": file.visibility,
            "summary": None,
            "version": "1",
            "uploaded_at": file.uploaded_at.isoformat(),
        }

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


def _is_success_run(run: str) -> bool:
    return run.upper() in RAGFLOW_SUCCESS_RUNS


def _is_failed_run(run: str) -> bool:
    return run.upper() in RAGFLOW_FAILED_RUNS


def _is_unstarted_run(run: str) -> bool:
    return run.upper() in RAGFLOW_UNSTART_RUNS


def _raise_if_parse_not_terminal(parse_status: RagflowDocumentStatus) -> None:
    run = parse_status.run.upper()
    if _is_failed_run(run):
        raise RagflowParseFailedError
    if not _is_success_run(run):
        raise RagflowParsePendingError


def _normalized_dataset_ids(raw_value: str) -> set[str]:
    return {item.strip() for item in raw_value.split(",") if item.strip()}
