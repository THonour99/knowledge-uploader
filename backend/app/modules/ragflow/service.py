from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.ragflow.base import (
    RagflowClient,
    RagflowDocumentNotFoundError,
    RagflowDocumentStatus,
    RagflowSubmissionOutcomeUnknownError,
    RagflowUploadResult,
)
from app.adapters.ragflow.instrumented import InstrumentedRagflowClient
from app.core.access_scope import DepartmentAccessScope
from app.core.audit import record_admin_audit_log
from app.core.config import get_settings
from app.core.document_state import DocumentStateError, DocumentStateMachine
from app.core.outbox import OutboxRepository
from app.core.ragflow_runtime import (
    is_ragflow_dataset_allowed,
    resolve_ragflow_runtime_settings,
)
from app.core.runtime_config import get_config
from app.modules.user.schemas import AuthUserRecord
from app.utils.filename import sanitize_filename

from . import events, exceptions
from .models import SyncTask, SyncTaskLog
from .records import RagflowSyncFileRecord
from .repository import RagflowTaskRepository  # noqa: TID251 - same-module repository dependency
from .sync_locks import acquire_sync_lock, release_sync_lock, release_sync_lock_after_transaction

ADMIN_ROLES = {"dept_admin", "system_admin"}
RAGFLOW_UPLOAD_TASK = "ragflow_upload"
RAGFLOW_STATUS_CHECK_TASK = "ragflow_status_check"
RAGFLOW_DELETE_TASK = "ragflow_delete"
VERSION_SWITCH_RECONCILABLE_TASKS = frozenset({RAGFLOW_UPLOAD_TASK, RAGFLOW_STATUS_CHECK_TASK})
MANUAL_SYNC_SOURCE_STATUSES = {"approved", "failed", "parsed"}
MAX_ERROR_MESSAGE_LENGTH = 2000
DELETE_CLEANUP_FAILURE_SOURCE_STATUSES = {"deleted"}
RAGFLOW_SUCCESS_RUNS = {"3", "DONE"}
RAGFLOW_FAILED_RUNS = {"4", "FAIL", "FAILED", "ERROR"}
RAGFLOW_UNSTART_RUNS = {"0", "UNSTART"}
RAGFLOW_STATUS_CHECK_INTERVAL_SECONDS = 30
DEFAULT_RAGFLOW_PARSE_POLL_TIMEOUT_SECONDS = 3600
RAGFLOW_PARSE_POLL_EXHAUSTED_ERROR = "RagflowParsePollingExhausted"
RAGFLOW_EXECUTION_LEASE_SECONDS = 1800
RAGFLOW_EXECUTION_LEASE_BUFFER_SECONDS = 300
RAGFLOW_UPLOAD_RECONCILE_MAX_ATTEMPTS = 3
RAGFLOW_UPLOAD_RECONCILE_DELAYS_SECONDS = (5, 30)
RAGFLOW_RECOVERY_PROBE_COUNTDOWN_SECONDS = 300
ABANDONED_VERSION_STATUSES = {"deleted", "disabled", "ragflow_cleanup_failed"}
SYNC_READY_STATUSES = {
    "queued",
    "syncing",
    "uploaded_to_ragflow",
    "parsing",
    "parsed",
    "failed",
}


def _instrument_ragflow_client(
    client: RagflowClient,
    *,
    department_id: uuid.UUID | None,
) -> RagflowClient:
    if isinstance(client, InstrumentedRagflowClient):
        return client
    return InstrumentedRagflowClient(client, department_id=department_id)


class RagflowObjectStorage(Protocol):
    async def get_object(self, *, bucket: str, object_key: str) -> bytes: ...


class RagflowSyncPreconditionError(Exception):
    pass


class RagflowParseFailedError(Exception):
    pass


class RagflowParsePendingError(Exception):
    pass


class RagflowVersionSwitchError(Exception):
    """A recoverable two-stage RAGFlow version switch did not complete."""


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
            max_retry_count=await resolve_sync_max_retries(),
        )
        task = await self._repository.add_task(task)
        await self._repository.add_log(
            task_id=task.id,
            status=task.status,
            message="ragflow upload task queued",
        )
        await self._append_task_queued_event(task)
        return task

    async def create_ragflow_delete_task(self, file_id: uuid.UUID) -> SyncTask:
        existing = await self._repository.get_active_task(
            file_id=file_id,
            task_type=RAGFLOW_DELETE_TASK,
        )
        if existing is not None:
            return existing

        task = SyncTask(
            file_id=file_id,
            task_type=RAGFLOW_DELETE_TASK,
            status="queued",
            retry_count=0,
            max_retry_count=await resolve_sync_max_retries(),
        )
        task = await self._repository.add_task(task)
        await self._repository.add_log(
            task_id=task.id,
            status=task.status,
            message="ragflow delete task queued",
        )
        await self._append_task_queued_event(task)
        return task

    async def create_ragflow_status_check_task(
        self,
        file_id: uuid.UUID,
        *,
        retry_count: int = 0,
        max_retry_count: int | None = None,
    ) -> SyncTask:
        existing = await self._repository.get_active_task(
            file_id=file_id,
            task_type=RAGFLOW_STATUS_CHECK_TASK,
        )
        if existing is not None:
            return existing

        task = SyncTask(
            file_id=file_id,
            task_type=RAGFLOW_STATUS_CHECK_TASK,
            status="queued",
            retry_count=retry_count,
            max_retry_count=(
                await resolve_parse_poll_max_retries()
                if max_retry_count is None
                else max_retry_count
            ),
        )
        task = await self._repository.add_task(task)
        await self._repository.add_log(
            task_id=task.id,
            status=task.status,
            message="ragflow status check task queued",
        )
        await self._append_task_queued_event(task)
        return task

    async def list_tasks(
        self,
        *,
        current_user: AuthUserRecord,
        scope: DepartmentAccessScope,
        context: RequestContext,
        file_id: uuid.UUID | None = None,
    ) -> list[SyncTaskBundle]:
        self._require_admin(current_user)
        tasks = await self._repository.list_tasks(
            file_id=file_id,
            department_ids=scope.query_department_ids(),
        )
        bundles = [await self._bundle(task) for task in tasks]
        await self._record_admin_audit(
            current_user=current_user,
            action="task.list",
            target_type="task_collection",
            target_id=current_user.id,
            context=context,
            metadata_json={
                "result_count": len(tasks),
                "file_id": str(file_id) if file_id is not None else None,
                **scope.audit_metadata(),
            },
        )
        await self._session.commit()
        return bundles

    async def get_task(
        self,
        *,
        current_user: AuthUserRecord,
        scope: DepartmentAccessScope,
        task_id: uuid.UUID,
        context: RequestContext,
    ) -> SyncTaskBundle:
        self._require_admin(current_user)
        task = await self._get_task_or_raise(task_id)
        file = await self._get_task_file_or_raise(task)
        self._require_scope_for_file(scope=scope, file=file)
        bundle = await self._bundle(task)
        await self._record_admin_audit(
            current_user=current_user,
            action="task.get",
            target_type="task",
            target_id=task.id,
            context=context,
            metadata_json=scope.audit_metadata(file_department_id=file.department_id),
        )
        await self._session.commit()
        return bundle

    async def retry_task(
        self,
        *,
        current_user: AuthUserRecord,
        scope: DepartmentAccessScope,
        task_id: uuid.UUID,
        context: RequestContext,
    ) -> SyncTaskBundle:
        self._require_admin(current_user)
        task = await self._get_task_for_update_or_raise(task_id)
        file = await self._get_task_file_or_raise(task)
        self._require_scope_for_file(scope=scope, file=file)
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
                task_type=task.task_type,
            )
            if active_task is not None and active_task.id != task.id:
                raise exceptions.task_conflict()

            task.status = "queued"
            task.retry_count += 1
            task.error_message = None
            task.lease_token = None
            task.lease_heartbeat_at = None
            task.reconcile_not_before = None
            task.recovery_probe_due_at = None
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
                metadata_json={
                    "retry_count": task.retry_count,
                    **scope.audit_metadata(file_department_id=file.department_id),
                },
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

    async def reconcile_version_switch_task(
        self,
        *,
        current_user: AuthUserRecord,
        scope: DepartmentAccessScope,
        task_id: uuid.UUID,
        reason: str,
        context: RequestContext,
    ) -> SyncTaskBundle:
        self._require_admin(current_user)
        cleaned_reason = reason.strip()
        if not cleaned_reason:
            raise exceptions.version_switch_reconcile_reason_required()
        task = await self._get_task_for_update_or_raise(task_id)
        file = await self._get_task_file_or_raise(task)
        self._require_scope_for_file(scope=scope, file=file)
        if not self._is_incomplete_version_switch_task(task=task, file=file):
            raise exceptions.task_not_version_switch_reconcilable()
        if task.status == "failed" and task.retry_count < task.max_retry_count:
            raise exceptions.task_not_version_switch_reconcilable()

        if task.status in {"queued", "running"}:
            active_sibling = await self._repository.get_active_task_for_types(
                file_id=task.file_id,
                task_types=VERSION_SWITCH_RECONCILABLE_TASKS,
                exclude_task_id=task.id,
            )
            if active_sibling is not None:
                raise exceptions.task_conflict()
            await self._record_admin_audit(
                current_user=current_user,
                action="task.version_switch_reconcile",
                target_type="task",
                target_id=task.id,
                context=context,
                reason=cleaned_reason,
                metadata_json={
                    "previous_status": task.status,
                    "idempotent": True,
                    "retry_count": task.retry_count,
                    "max_retry_count": task.max_retry_count,
                    "version_switch_status": file.version_switch_status,
                    **scope.audit_metadata(file_department_id=file.department_id),
                },
            )
            await self._session.commit()
            return await self._bundle(task)
        if task.status not in {"failed", "canceled"}:
            raise exceptions.task_not_version_switch_reconcilable()

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
            active_sibling = await self._repository.get_active_task_for_types(
                file_id=task.file_id,
                task_types=VERSION_SWITCH_RECONCILABLE_TASKS,
                exclude_task_id=task.id,
            )
            if active_sibling is not None:
                raise exceptions.task_conflict()

            previous_status = task.status
            previous_reconcile_attempt_count = task.reconcile_attempt_count
            task.status = "queued"
            task.error_message = None
            task.lease_token = None
            task.lease_heartbeat_at = None
            task.reconcile_attempt_count = 0
            task.reconcile_not_before = None
            task.recovery_probe_due_at = None
            task.started_at = None
            task.finished_at = None
            await self._repository.add_log(
                task_id=task.id,
                status=task.status,
                message="version switch reconciliation requested by administrator",
            )
            await self._append_task_queued_event(task)
            await self._record_admin_audit(
                current_user=current_user,
                action="task.version_switch_reconcile",
                target_type="task",
                target_id=task.id,
                context=context,
                reason=cleaned_reason,
                metadata_json={
                    "previous_status": previous_status,
                    "idempotent": False,
                    "retry_count": task.retry_count,
                    "max_retry_count": task.max_retry_count,
                    "previous_reconcile_attempt_count": previous_reconcile_attempt_count,
                    "version_switch_status": file.version_switch_status,
                    **scope.audit_metadata(file_department_id=file.department_id),
                },
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
        scope: DepartmentAccessScope,
        task_id: uuid.UUID,
        context: RequestContext,
    ) -> SyncTaskBundle:
        self._require_admin(current_user)
        task = await self._get_task_for_update_or_raise(task_id)
        file = await self._get_task_file_or_raise(task)
        self._require_scope_for_file(scope=scope, file=file)
        if self._is_incomplete_version_switch_task(task=task, file=file):
            raise exceptions.incomplete_version_switch_task_not_cancelable()
        if task.status != "queued":
            raise exceptions.task_not_cancelable()

        task.status = "canceled"
        task.lease_token = None
        task.lease_heartbeat_at = None
        task.reconcile_not_before = None
        task.recovery_probe_due_at = None
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
            metadata_json=scope.audit_metadata(file_department_id=file.department_id),
        )
        await self._session.commit()
        await self._session.refresh(task)
        return await self._bundle(task)

    async def manual_sync_file(
        self,
        *,
        current_user: AuthUserRecord,
        scope: DepartmentAccessScope,
        file_id: uuid.UUID,
        dataset_mapping_id: uuid.UUID,
        reason: str | None,
        context: RequestContext,
    ) -> SyncTaskBundle:
        self._require_admin(current_user)
        file = await self._repository.get_file_for_update(file_id)
        if file is None:
            raise exceptions.file_not_found()
        self._require_scope_for_file(
            scope=scope, file=file, on_out_of_scope=exceptions.file_not_found
        )
        if file.status not in MANUAL_SYNC_SOURCE_STATUSES or file.review_status != "approved":
            raise exceptions.file_not_syncable()
        cleaned_reason = reason.strip() if reason is not None else ""
        reconcile_existing_remote = file.status == "parsed"
        if reconcile_existing_remote:
            if not cleaned_reason:
                raise exceptions.parsed_reconciliation_reason_required()
            if file.ragflow_document_id is None or not file.ragflow_document_id.strip():
                raise exceptions.parsed_remote_identity_missing()
            expected_version_switch_status = (
                "completed" if file.replaces_file_id is not None else "not_required"
            )
            if (
                not file.is_current_version
                or file.remote_visibility != "current"
                or file.version_switch_status != expected_version_switch_status
            ):
                raise exceptions.parsed_version_not_current()
        if await self._sensitive_policy_blocks_sync(file):
            raise exceptions.sync_blocked_by_sensitive_policy()
        risk_level = await self._repository.get_file_sensitive_risk_level(file.id)
        if risk_level == "high":
            if await get_config("ragflow.allow_high_risk_sync") is not True:
                raise exceptions.high_risk_sync_not_allowed()
            if not cleaned_reason:
                raise exceptions.high_risk_reason_required()
        mapping = await self._repository.get_dataset_mapping_for_update(dataset_mapping_id)
        if mapping is None or not mapping.enabled:
            raise exceptions.dataset_mapping_not_found()
        if reconcile_existing_remote and file.category_id is None:
            raise exceptions.dataset_mapping_category_mismatch()
        if file.category_id is not None and mapping.category_id != file.category_id:
            raise exceptions.dataset_mapping_category_mismatch()
        dataset_id = mapping.ragflow_dataset_id
        if file.ragflow_document_id is not None and (
            file.dataset_mapping_id != mapping.id or file.ragflow_dataset_id != dataset_id
        ):
            raise exceptions.remote_document_dataset_change_not_allowed()
        if not await self._is_dataset_id_allowed(dataset_id):
            raise exceptions.dataset_not_allowed()
        active_task_types = (
            (RAGFLOW_UPLOAD_TASK, RAGFLOW_STATUS_CHECK_TASK)
            if reconcile_existing_remote
            else (RAGFLOW_UPLOAD_TASK,)
        )
        for active_task_type in active_task_types:
            active_task = await self._repository.get_active_task(
                file_id=file_id,
                task_type=active_task_type,
            )
            if active_task is not None:
                raise exceptions.task_conflict()

        settings = get_settings()
        lock_token = uuid.uuid4().hex
        lock_acquired = await acquire_sync_lock(
            redis_url=settings.cache_redis_url,
            file_id=file_id,
            token=lock_token,
        )
        if not lock_acquired:
            raise exceptions.task_lock_busy()

        release_sync_lock_after_transaction(
            session=self._session,
            redis_url=settings.cache_redis_url,
            file_id=file_id,
            token=lock_token,
        )
        try:
            from_status = file.status
            previous_dataset_mapping_id = file.dataset_mapping_id
            if not reconcile_existing_remote:
                file.category_id = file.category_id or mapping.category_id
            file.dataset_mapping_id = mapping.id
            file.ragflow_dataset_id = dataset_id
            if file.status == "approved":
                file.status = DocumentStateMachine.transition(file.status, "queued")
            file = await self._repository.update_file_sync_state(file)
            if reconcile_existing_remote:
                task = await self.create_ragflow_status_check_task(file_id)
                await self._repository.add_log(
                    task_id=task.id,
                    status=task.status,
                    message="existing ragflow document reconciliation queued",
                )
            else:
                task = await self.create_ragflow_upload_task(file_id)
            await self._record_admin_audit(
                current_user=current_user,
                action="file.manual_sync",
                target_type="file",
                target_id=file_id,
                context=context,
                metadata_json={
                    "task_id": str(task.id),
                    "from_status": from_status,
                    "sync_mode": (
                        "reconcile_existing_remote" if reconcile_existing_remote else "manual_sync"
                    ),
                    "dataset_mapping_id": str(mapping.id),
                    "ragflow_dataset_id": dataset_id,
                    "previous_dataset_mapping_id": (
                        str(previous_dataset_mapping_id)
                        if previous_dataset_mapping_id is not None
                        else None
                    ),
                    **scope.audit_metadata(file_department_id=file.department_id),
                },
                reason=cleaned_reason or None,
            )
            await self._session.commit()
            await self._session.refresh(task)
            return await self._bundle(task)
        except Exception:
            await release_sync_lock(
                redis_url=settings.cache_redis_url,
                file_id=file_id,
                token=lock_token,
            )
            raise

    async def claim_running(
        self,
        task_id: uuid.UUID,
        *,
        expected_task_types: set[str] | None = None,
        execution_token: str | None = None,
    ) -> bool:
        lease_seconds = await resolve_execution_lease_seconds()
        task = await self._get_task_for_update_or_raise(task_id)
        now = datetime.now(UTC)
        next_execution_token = (execution_token or uuid.uuid4().hex)[:64]
        if expected_task_types is not None and task.task_type not in expected_task_types:
            return False
        if task.status == "running":
            stale_before = now - timedelta(seconds=lease_seconds)
            lease_freshness = task.lease_heartbeat_at or task.started_at
            if lease_freshness is not None and lease_freshness > stale_before:
                return False
            task.lease_token = next_execution_token
            task.started_at = now
            task.lease_heartbeat_at = now
            task.finished_at = None
            task.error_message = None
            task.reconcile_not_before = None
            task.recovery_probe_due_at = None
            await self._repository.add_log(
                task_id=task.id,
                status=task.status,
                message="stale ragflow execution lease reclaimed",
            )
            await self._session.commit()
            return True
        if task.status != "queued":
            return False
        if task.reconcile_not_before is not None and task.reconcile_not_before > now:
            return False
        task.status = "running"
        task.lease_token = next_execution_token
        task.started_at = task.started_at or now
        task.lease_heartbeat_at = now
        task.finished_at = None
        task.error_message = None
        task.reconcile_not_before = None
        task.recovery_probe_due_at = None
        await self._repository.add_log(
            task_id=task.id,
            status=task.status,
            message=f"{_task_label(task.task_type)} task started",
        )
        await self._session.commit()
        return True

    async def schedule_execution_recovery_probe(
        self,
        task_id: uuid.UUID,
        *,
        countdown_seconds: int = RAGFLOW_RECOVERY_PROBE_COUNTDOWN_SECONDS,
    ) -> bool:
        task = await self._get_task_for_update_or_raise(task_id)
        if task.status != "running":
            return False
        now = datetime.now(UTC)
        if task.recovery_probe_due_at is not None and task.recovery_probe_due_at > now:
            return False
        task.recovery_probe_due_at = now + timedelta(seconds=countdown_seconds)
        await self._repository.add_log(
            task_id=task.id,
            status=task.status,
            message=f"ragflow execution recovery probe scheduled in {countdown_seconds} seconds",
        )
        await self._append_task_queued_event(
            task,
            countdown_seconds=countdown_seconds,
        )
        await self._session.commit()
        return True

    async def run_upload_task(
        self,
        task_id: uuid.UUID,
        *,
        storage: RagflowObjectStorage | None,
        ragflow_client: RagflowClient,
    ) -> SyncTask:
        task = await self._get_task_or_raise(task_id)
        if task.status != "running":
            return task
        if task.task_type == RAGFLOW_STATUS_CHECK_TASK:
            return await self._run_status_check_task(
                task=task,
                ragflow_client=ragflow_client,
            )
        if task.task_type != RAGFLOW_UPLOAD_TASK:
            return task

        execution_token = task.lease_token
        if execution_token is None:
            raise RagflowSyncPreconditionError
        file = await self._repository.get_file(task.file_id)
        if file is None:
            raise RagflowSyncPreconditionError
        ragflow_client = _instrument_ragflow_client(
            ragflow_client,
            department_id=file.department_id,
        )
        dataset_id = await self._require_sync_target(file)
        document_id = file.ragflow_document_id
        if document_id:
            restart_failed_parse = (
                task.retry_count > 0 or file.ragflow_parse_status in RAGFLOW_FAILED_RUNS
            )
            file = await self._ensure_file_parsing(task=task, file=file)
            return await self._poll_existing_document_without_lock(
                task=task,
                file=file,
                dataset_id=dataset_id,
                document_id=document_id,
                ragflow_client=ragflow_client,
                start_if_unstarted=True,
                restart_if_failed=restart_failed_parse,
            )

        upload_outcome_unknown = file.ragflow_parse_status == "UPLOADING"
        if not upload_outcome_unknown:
            file = await self._transition_sync_file(
                task=task,
                file=file,
                to_status="syncing",
                parse_status="RECONCILING",
                message="ragflow document reconciliation started",
            )
        await self._heartbeat_execution_lease(
            task_id=task.id,
            execution_token=execution_token,
        )
        document_name = self._ragflow_document_name(file)
        upload_result = await self._reconcile_upload_without_lock(
            task=task,
            dataset_id=dataset_id,
            document_name=document_name,
            ragflow_client=ragflow_client,
        )
        reconciled = upload_result is not None
        if upload_result is None and upload_outcome_unknown:
            return await self._defer_unknown_upload_reconciliation(task)
        if upload_result is not None and upload_outcome_unknown and file.status == "failed":
            file = await self._transition_sync_file(
                task=task,
                file=file,
                to_status="syncing",
                parse_status="UPLOADING",
                message="ragflow unknown upload outcome reconciled",
            )
        if upload_result is None:
            if storage is None:
                raise RagflowSyncPreconditionError
            content = await storage.get_object(
                bucket=file.bucket,
                object_key=file.object_key,
            )
            await self._heartbeat_execution_lease(
                task_id=task.id,
                execution_token=execution_token,
            )
            file = await self._transition_sync_file(
                task=task,
                file=file,
                to_status="syncing",
                parse_status="UPLOADING",
                message="ragflow document remote upload requested",
            )
            try:
                upload_result = await ragflow_client.upload_document(
                    dataset_id=dataset_id,
                    filename=document_name,
                    content=content,
                    content_type=file.mime_type,
                )
            except RagflowSubmissionOutcomeUnknownError:
                # The remote may have committed the multipart request before the client
                # observed a timeout/disconnect. Keep UPLOADING as the durable uncertainty
                # marker and reconcile by the unique remote name before any further POST.
                return await self._defer_unknown_upload_reconciliation(task)
            await self._heartbeat_execution_lease(
                task_id=task.id,
                execution_token=execution_token,
            )

        task = await self._assert_execution_lease(task)
        locked_file = await self._repository.get_file_for_update(file.id)
        if locked_file is None:
            raise RagflowSyncPreconditionError
        locked_dataset_id = await self._require_sync_target(locked_file)
        if locked_dataset_id != dataset_id:
            raise RagflowSyncPreconditionError
        locked_file.ragflow_document_id = upload_result.document_id
        file = await self._transition_sync_file(
            task=task,
            file=locked_file,
            to_status="uploaded_to_ragflow",
            parse_status="UNSTART",
            message=(
                "ragflow document reconciled after interrupted upload"
                if reconciled
                else "ragflow document uploaded"
            ),
        )
        await ragflow_client.update_document_metadata(
            dataset_id=dataset_id,
            document_id=upload_result.document_id,
            name=document_name,
            metadata=self._build_metadata(file),
        )
        await self._heartbeat_execution_lease(
            task_id=task.id,
            execution_token=execution_token,
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
        await self._heartbeat_execution_lease(
            task_id=task.id,
            execution_token=execution_token,
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
        await self._heartbeat_execution_lease(
            task_id=task.id,
            execution_token=execution_token,
        )
        file = await self._apply_parse_status(task=task, file=file, parse_status=parse_status)
        return await self._complete_after_parse_status(
            task=task,
            file=file,
            parse_status=parse_status,
            ragflow_client=ragflow_client,
        )

    async def _reconcile_upload_without_lock(
        self,
        *,
        task: SyncTask,
        dataset_id: str,
        document_name: str,
        ragflow_client: RagflowClient,
    ) -> RagflowUploadResult | None:
        execution_token = task.lease_token
        if execution_token is None:
            raise RagflowSyncPreconditionError
        result = await ragflow_client.find_document_by_name(
            dataset_id=dataset_id,
            name=document_name,
        )
        await self._heartbeat_execution_lease(
            task_id=task.id,
            execution_token=execution_token,
        )
        return result

    async def _defer_unknown_upload_reconciliation(self, task: SyncTask) -> SyncTask:
        task = await self._assert_execution_lease(task)
        attempt = task.reconcile_attempt_count + 1
        task.reconcile_attempt_count = attempt
        if attempt >= RAGFLOW_UPLOAD_RECONCILE_MAX_ATTEMPTS:
            await self._repository.add_log(
                task_id=task.id,
                status=task.status,
                message="ragflow unknown upload outcome reconciliation exhausted",
            )
            await self._session.commit()
            raise exceptions.RagflowUploadOutcomeUnknownError
        countdown = RAGFLOW_UPLOAD_RECONCILE_DELAYS_SECONDS[attempt - 1]
        task.status = "queued"
        task.lease_token = None
        task.lease_heartbeat_at = None
        task.finished_at = None
        task.error_message = None
        task.reconcile_not_before = datetime.now(UTC) + timedelta(seconds=countdown)
        task.recovery_probe_due_at = None
        await self._repository.add_log(
            task_id=task.id,
            status=task.status,
            message=(
                "ragflow unknown upload outcome reconciliation deferred "
                f"for {countdown} seconds (attempt {attempt})"
            ),
        )
        await self._append_task_queued_event(task, countdown_seconds=countdown)
        await self._session.commit()
        return task

    async def _run_status_check_task(
        self,
        *,
        task: SyncTask,
        ragflow_client: RagflowClient,
    ) -> SyncTask:
        file = await self._repository.get_file(task.file_id)
        if file is None:
            raise RagflowSyncPreconditionError
        ragflow_client = _instrument_ragflow_client(
            ragflow_client,
            department_id=file.department_id,
        )
        dataset_id = await self._require_sync_target(file)
        document_id = file.ragflow_document_id
        if document_id is None or not document_id.strip():
            raise RagflowSyncPreconditionError
        file = await self._ensure_file_parsing(task=task, file=file)

        return await self._poll_existing_document_without_lock(
            task=task,
            file=file,
            dataset_id=dataset_id,
            document_id=document_id,
            ragflow_client=ragflow_client,
            start_if_unstarted=False,
            restart_if_failed=False,
        )

    async def _poll_existing_document_without_lock(
        self,
        *,
        task: SyncTask,
        file: RagflowSyncFileRecord,
        dataset_id: str,
        document_id: str,
        ragflow_client: RagflowClient,
        start_if_unstarted: bool,
        restart_if_failed: bool,
    ) -> SyncTask:
        execution_token = task.lease_token
        if execution_token is None:
            raise RagflowSyncPreconditionError
        task_id = task.id
        file_id = file.id
        metadata = self._build_metadata(file)
        document_name = self._ragflow_document_name(file)
        await self._session.rollback()

        parse_status = await ragflow_client.get_document_status(
            dataset_id=dataset_id,
            document_id=document_id,
        )
        await self._heartbeat_execution_lease(
            task_id=task_id,
            execution_token=execution_token,
        )
        parse_started = False
        should_start_parse = (start_if_unstarted and _is_unstarted_run(parse_status.run)) or (
            restart_if_failed and _is_failed_run(parse_status.run)
        )
        if should_start_parse:
            await ragflow_client.update_document_metadata(
                dataset_id=dataset_id,
                document_id=document_id,
                name=document_name,
                metadata=metadata,
            )
            await self._heartbeat_execution_lease(
                task_id=task_id,
                execution_token=execution_token,
            )
            await ragflow_client.start_parse(dataset_id=dataset_id, document_id=document_id)
            await self._heartbeat_execution_lease(
                task_id=task_id,
                execution_token=execution_token,
            )
            parse_started = True
            parse_status = await ragflow_client.get_document_status(
                dataset_id=dataset_id,
                document_id=document_id,
            )
            await self._heartbeat_execution_lease(
                task_id=task_id,
                execution_token=execution_token,
            )

        locked_task = await self._get_task_for_update_or_raise(task_id)
        if locked_task.status != "running" or locked_task.lease_token != execution_token:
            await self._session.rollback()
            return locked_task
        locked_file = await self._repository.get_file_for_update(file_id)
        if (
            locked_file is None
            or locked_file.ragflow_dataset_id != dataset_id
            or locked_file.ragflow_document_id != document_id
        ):
            await self._session.rollback()
            return locked_task
        if parse_started:
            await self._repository.add_log(
                task_id=locked_task.id,
                status=locked_task.status,
                message="ragflow document metadata updated",
            )
            await self._repository.add_log(
                task_id=locked_task.id,
                status=locked_task.status,
                message="ragflow document parse started",
            )
        locked_file = await self._apply_parse_status(
            task=locked_task,
            file=locked_file,
            parse_status=parse_status,
        )
        return await self._complete_after_parse_status(
            task=locked_task,
            file=locked_file,
            parse_status=parse_status,
            ragflow_client=ragflow_client,
        )

    async def run_delete_task(
        self,
        task_id: uuid.UUID,
        *,
        ragflow_client: RagflowClient,
    ) -> SyncTask:
        task = await self._get_task_for_update_or_raise(task_id)
        if task.status != "running":
            return task
        if task.task_type != RAGFLOW_DELETE_TASK:
            return task
        execution_token = task.lease_token
        if execution_token is None:
            raise RagflowSyncPreconditionError

        file = await self._repository.get_file_for_update(task.file_id)
        if file is None:
            await self._repository.add_log(
                task_id=task.id,
                status=task.status,
                message="file record missing, nothing to delete remotely",
            )
            await self._session.commit()
            return await self.mark_succeeded(
                task_id,
                expected_lease_token=task.lease_token,
            )

        ragflow_client = _instrument_ragflow_client(
            ragflow_client,
            department_id=file.department_id,
        )
        document_id = file.ragflow_document_id
        if document_id is None or not document_id.strip():
            await self._repository.add_log(
                task_id=task.id,
                status=task.status,
                message="ragflow document pointer already cleared",
            )
            await self._session.commit()
            return await self.mark_succeeded(
                task_id,
                expected_lease_token=task.lease_token,
            )

        dataset_id = await self._require_dataset_id_allowed(file)
        await self._repository.add_log(
            task_id=task.id,
            status=task.status,
            message="ragflow document delete started",
        )
        await self._session.commit()

        try:
            await ragflow_client.delete_document(
                dataset_id=dataset_id,
                document_id=document_id,
            )
            delete_message = "ragflow document deleted"
        except RagflowDocumentNotFoundError:
            delete_message = "ragflow document already absent (404), treated as success"

        await self._heartbeat_execution_lease(
            task_id=task.id,
            execution_token=execution_token,
        )
        task = await self._assert_execution_lease(task)
        locked_file = await self._repository.get_file_for_update(task.file_id)
        if (
            locked_file is None
            or locked_file.ragflow_dataset_id != dataset_id
            or locked_file.ragflow_document_id != document_id
        ):
            await self._session.rollback()
            raise exceptions.RagflowTaskLeaseLostError
        file = locked_file
        file.ragflow_document_id = None
        if file.status == "ragflow_cleanup_failed":
            file.status = DocumentStateMachine.transition(file.status, "deleted")
        file.ragflow_error_message = None
        file.last_sync_at = datetime.now(UTC)
        await self._repository.update_file_sync_state(file)
        await self._repository.add_log(
            task_id=task.id,
            status=task.status,
            message=delete_message,
        )
        await self._session.commit()
        return await self.mark_succeeded(
            task_id,
            expected_lease_token=task.lease_token,
        )

    async def mark_succeeded(
        self,
        task_id: uuid.UUID,
        *,
        expected_lease_token: str | None = None,
        publish_sync_success: bool = False,
    ) -> SyncTask:
        task = await self._get_task_for_update_or_raise(task_id)
        if expected_lease_token is not None and task.lease_token != expected_lease_token:
            return task
        if task.status in {"canceled", "failed", "succeeded"}:
            return task
        if task.status != "running":
            return task
        task.status = "succeeded"
        task.lease_token = None
        task.lease_heartbeat_at = None
        task.reconcile_not_before = None
        task.recovery_probe_due_at = None
        task.finished_at = datetime.now(UTC)
        task.error_message = None
        await self._repository.add_log(
            task_id=task.id,
            status=task.status,
            message=f"{_task_label(task.task_type)} task completed",
        )
        if publish_sync_success:
            await OutboxRepository(self._session).append(
                event_type=events.RAGFLOW_SYNC_TASK_SUCCEEDED,
                aggregate_type="sync_task",
                aggregate_id=str(task.id),
                payload={
                    "sync_task_id": str(task.id),
                    "file_id": str(task.file_id),
                    "task_type": task.task_type,
                    "status": task.status,
                },
            )
        await self._session.commit()
        return task

    async def mark_failed(
        self,
        task_id: uuid.UUID,
        error_message: str,
        *,
        mark_file_failed: bool = True,
        expected_lease_token: str | None = None,
    ) -> SyncTask:
        task = await self._get_task_for_update_or_raise(task_id)
        if expected_lease_token is not None and task.lease_token != expected_lease_token:
            return task
        if task.status in {"canceled", "succeeded"}:
            return task
        file = await self._repository.get_file_for_update(task.file_id)
        if file is not None and task.task_type == RAGFLOW_DELETE_TASK:
            await self._try_mark_file_cleanup_failed(file, error_message)
        elif file is not None and mark_file_failed:
            await self._try_mark_file_failed(file, error_message)
        task.status = "failed"
        task.lease_token = None
        task.lease_heartbeat_at = None
        task.reconcile_not_before = None
        task.recovery_probe_due_at = None
        task.finished_at = datetime.now(UTC)
        task.error_message = error_message[:MAX_ERROR_MESSAGE_LENGTH]
        await self._repository.add_log(
            task_id=task.id,
            status=task.status,
            message=f"{_task_label(task.task_type)} task failed",
        )
        await OutboxRepository(self._session).append(
            event_type=events.RAGFLOW_SYNC_TASK_FAILED,
            aggregate_type="sync_task",
            aggregate_id=str(task.id),
            payload={
                "sync_task_id": str(task.id),
                "file_id": str(task.file_id),
                "task_type": task.task_type,
                "status": task.status,
                "error_message": task.error_message,
            },
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
        await self._ensure_ai_sync_policy_allows(file)
        if file.dataset_mapping_id is None:
            raise RagflowSyncPreconditionError
        mapping = await self._repository.get_dataset_mapping_for_update(file.dataset_mapping_id)
        if mapping is None or not mapping.enabled or mapping.ragflow_dataset_id != dataset_id:
            raise RagflowSyncPreconditionError
        if not await self._is_dataset_id_allowed(dataset_id):
            raise RagflowSyncPreconditionError
        return dataset_id

    def _require_dataset_id(self, file: RagflowSyncFileRecord) -> str:
        if file.ragflow_dataset_id is None or not file.ragflow_dataset_id.strip():
            raise RagflowSyncPreconditionError
        return file.ragflow_dataset_id

    async def _require_dataset_id_allowed(self, file: RagflowSyncFileRecord) -> str:
        dataset_id = self._require_dataset_id(file)
        if not await self._is_dataset_id_allowed(dataset_id):
            raise RagflowSyncPreconditionError
        return dataset_id

    async def _is_dataset_id_allowed(self, dataset_id: str) -> bool:
        runtime_settings = await resolve_ragflow_runtime_settings()
        return is_ragflow_dataset_allowed(dataset_id, runtime_settings)

    async def _sensitive_policy_blocks_sync(self, file: RagflowSyncFileRecord) -> bool:
        if await self._repository.has_block_sync_sensitive_hit(file.id):
            return True
        return await self._repository.get_file_sensitive_risk_level(file.id) == "critical"

    async def _ensure_ai_sync_policy_allows(self, file: RagflowSyncFileRecord) -> None:
        if await self._sensitive_policy_blocks_sync(file):
            raise RagflowSyncPreconditionError
        analysis_status = await self._repository.get_file_analysis_status(file.id)
        if analysis_status != "failed":
            return
        allow_sync = await self._repository.get_ai_feature_enabled(
            "allow_sync_when_analysis_failed"
        )
        if allow_sync is None:
            allow_sync = get_settings().ai_allow_sync_when_analysis_failed
        if not allow_sync:
            raise RagflowSyncPreconditionError

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
        await self._assert_execution_lease(task)
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
        await self._assert_execution_lease(task)
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

    async def _complete_after_parse_status(
        self,
        *,
        task: SyncTask,
        file: RagflowSyncFileRecord,
        parse_status: RagflowDocumentStatus,
        ragflow_client: RagflowClient,
    ) -> SyncTask:
        run = parse_status.run.upper()
        if _is_failed_run(run):
            raise RagflowParseFailedError
        if _is_success_run(run):
            file = await self._complete_version_switch(
                task=task,
                file=file,
                ragflow_client=ragflow_client,
            )
            return await self.mark_succeeded(
                task.id,
                expected_lease_token=task.lease_token,
                publish_sync_success=True,
            )
        return await self._complete_and_queue_status_check(task=task, file=file, run=run)

    async def _complete_version_switch(
        self,
        *,
        task: SyncTask,
        file: RagflowSyncFileRecord,
        ragflow_client: RagflowClient,
    ) -> RagflowSyncFileRecord:
        if file.status != "parsed":
            raise RagflowSyncPreconditionError
        if file.replaces_file_id is None:
            return await self._record_initial_version_active(task=task, file=file)
        if file.version_switch_status == "completed":
            if not file.is_current_version or file.remote_visibility != "current":
                raise RagflowSyncPreconditionError
            return file

        candidate = file
        if candidate.version_switch_status in {"pending", "failed_old_deactivate"}:
            candidate = await self._deactivate_predecessor_remote(
                task=task,
                candidate=candidate,
                ragflow_client=ragflow_client,
            )
        if candidate.version_switch_status == "old_remote_deactivated":
            candidate = await self._activate_local_version(task=task, candidate=candidate)
        if candidate.version_switch_status in {"local_switched", "failed_new_activate"}:
            candidate = await self._activate_candidate_remote(
                task=task,
                candidate=candidate,
                ragflow_client=ragflow_client,
            )
        if candidate.version_switch_status != "completed":
            raise RagflowSyncPreconditionError
        return candidate

    async def _record_initial_version_active(
        self,
        *,
        task: SyncTask,
        file: RagflowSyncFileRecord,
    ) -> RagflowSyncFileRecord:
        await self._assert_execution_lease(task)
        locked = await self._repository.get_file_for_update(file.id)
        if (
            locked is None
            or locked.replaces_file_id is not None
            or locked.series_id != locked.id
            or locked.version_number != 1
            or not locked.is_current_version
        ):
            raise RagflowSyncPreconditionError
        changed = (
            locked.remote_visibility != "current"
            or locked.version_switch_status != "not_required"
            or locked.version_switch_error is not None
        )
        if changed:
            now = datetime.now(UTC)
            locked.remote_visibility = "current"
            locked.version_switch_status = "not_required"
            locked.version_switch_error = None
            locked.local_version_activated_at = (
                locked.local_version_activated_at or locked.uploaded_at
            )
            locked.remote_version_activated_at = locked.remote_version_activated_at or now
            locked = await self._repository.update_file_sync_state(locked)
            await self._repository.add_log(
                task_id=task.id,
                status=task.status,
                message="initial ragflow document version activated",
            )
        await self._session.commit()
        return locked

    async def _deactivate_predecessor_remote(
        self,
        *,
        task: SyncTask,
        candidate: RagflowSyncFileRecord,
        ragflow_client: RagflowClient,
    ) -> RagflowSyncFileRecord:
        await self._assert_execution_lease(task)
        candidate, predecessor = await self._load_version_pair_for_update(candidate)
        if (
            candidate.version_switch_status not in {"pending", "failed_old_deactivate"}
            or candidate.is_current_version
            or not predecessor.is_current_version
            or candidate.remote_visibility != "candidate"
        ):
            raise RagflowSyncPreconditionError
        dataset_id, document_id = self._remote_identity(predecessor)
        remote_action = candidate.replacement_remote_action
        if remote_action not in {"delete", "archive"}:
            raise RagflowSyncPreconditionError
        await self._repository.begin_version_operation(
            file_id=candidate.id,
            target_file_id=predecessor.id,
            operation="deactivate_predecessor",
            started_at=datetime.now(UTC),
        )
        candidate.version_switch_status = "pending"
        candidate.version_switch_error = None
        candidate.version_switch_attempt_count += 1
        candidate = await self._repository.update_file_sync_state(candidate)
        await self._repository.add_log(
            task_id=task.id,
            status=task.status,
            message=f"ragflow predecessor remote {remote_action} started",
        )
        await self._session.commit()
        execution_token = self._require_execution_token(task)
        await self._heartbeat_execution_lease(
            task_id=task.id,
            execution_token=execution_token,
        )
        try:
            if remote_action == "delete":
                await ragflow_client.delete_document(
                    dataset_id=dataset_id,
                    document_id=document_id,
                )
            else:
                await ragflow_client.update_document_metadata(
                    dataset_id=dataset_id,
                    document_id=document_id,
                    name=self._ragflow_document_name(predecessor),
                    metadata=self._build_metadata(predecessor, is_current_version=False),
                )
        except RagflowDocumentNotFoundError as exc:
            if remote_action != "delete":
                await self._record_version_operation_failure(
                    task=task,
                    candidate_id=candidate.id,
                    operation="deactivate_predecessor",
                    failure_status="failed_old_deactivate",
                    error=exc,
                )
                raise RagflowVersionSwitchError from None
        except Exception as exc:
            await self._record_version_operation_failure(
                task=task,
                candidate_id=candidate.id,
                operation="deactivate_predecessor",
                failure_status="failed_old_deactivate",
                error=exc,
            )
            raise RagflowVersionSwitchError from None
        await self._heartbeat_execution_lease(
            task_id=task.id,
            execution_token=execution_token,
        )

        await self._assert_execution_lease(task)
        candidate, predecessor = await self._load_version_pair_for_update(candidate)
        if candidate.is_current_version or not predecessor.is_current_version:
            raise RagflowSyncPreconditionError
        now = datetime.now(UTC)
        predecessor.remote_visibility = "not_current"
        candidate.predecessor_remote_deactivated_at = now
        candidate.version_switch_status = "old_remote_deactivated"
        candidate.version_switch_error = None
        await self._repository.finish_version_operation(
            file_id=candidate.id,
            operation="deactivate_predecessor",
            succeeded=True,
            finished_at=now,
        )
        await self._repository.update_file_sync_state(predecessor)
        candidate = await self._repository.update_file_sync_state(candidate)
        await self._repository.add_log(
            task_id=task.id,
            status=task.status,
            message=(
                "ragflow predecessor remote document deleted or already absent"
                if remote_action == "delete"
                else "ragflow predecessor remote document preserved and marked non-current"
            ),
        )
        await self._session.commit()
        return candidate

    async def _activate_local_version(
        self,
        *,
        task: SyncTask,
        candidate: RagflowSyncFileRecord,
    ) -> RagflowSyncFileRecord:
        await self._assert_execution_lease(task)
        candidate, predecessor = await self._load_version_pair_for_update(candidate)
        if (
            candidate.version_switch_status != "old_remote_deactivated"
            or candidate.is_current_version
            or not predecessor.is_current_version
            or predecessor.remote_visibility != "not_current"
        ):
            raise RagflowSyncPreconditionError
        now = datetime.now(UTC)
        predecessor.is_current_version = False
        candidate.is_current_version = True
        candidate.version_switch_status = "local_switched"
        candidate.version_switch_error = None
        candidate.local_version_activated_at = now
        await self._repository.update_file_sync_state(predecessor)
        candidate = await self._repository.update_file_sync_state(candidate)
        await self._repository.add_log(
            task_id=task.id,
            status=task.status,
            message="local current document version switched",
        )
        await self._session.commit()
        return candidate

    async def _activate_candidate_remote(
        self,
        *,
        task: SyncTask,
        candidate: RagflowSyncFileRecord,
        ragflow_client: RagflowClient,
    ) -> RagflowSyncFileRecord:
        await self._assert_execution_lease(task)
        candidate, predecessor = await self._load_version_pair_for_update(candidate)
        if (
            candidate.version_switch_status not in {"local_switched", "failed_new_activate"}
            or not candidate.is_current_version
            or predecessor.is_current_version
            or predecessor.remote_visibility != "not_current"
        ):
            raise RagflowSyncPreconditionError
        dataset_id, document_id = self._remote_identity(candidate)
        await self._repository.begin_version_operation(
            file_id=candidate.id,
            target_file_id=candidate.id,
            operation="activate_candidate",
            started_at=datetime.now(UTC),
        )
        candidate.version_switch_status = "local_switched"
        candidate.version_switch_error = None
        candidate.version_switch_attempt_count += 1
        candidate = await self._repository.update_file_sync_state(candidate)
        await self._repository.add_log(
            task_id=task.id,
            status=task.status,
            message="ragflow candidate metadata activation started",
        )
        await self._session.commit()
        execution_token = self._require_execution_token(task)
        await self._heartbeat_execution_lease(
            task_id=task.id,
            execution_token=execution_token,
        )
        try:
            await ragflow_client.update_document_metadata(
                dataset_id=dataset_id,
                document_id=document_id,
                name=self._ragflow_document_name(candidate),
                metadata=self._build_metadata(candidate, is_current_version=True),
            )
        except Exception as exc:
            await self._record_version_operation_failure(
                task=task,
                candidate_id=candidate.id,
                operation="activate_candidate",
                failure_status="failed_new_activate",
                error=exc,
            )
            raise RagflowVersionSwitchError from None
        await self._heartbeat_execution_lease(
            task_id=task.id,
            execution_token=execution_token,
        )

        await self._assert_execution_lease(task)
        candidate, predecessor = await self._load_version_pair_for_update(candidate)
        if not candidate.is_current_version or predecessor.is_current_version:
            raise RagflowSyncPreconditionError
        now = datetime.now(UTC)
        candidate.remote_visibility = "current"
        candidate.version_switch_status = "completed"
        candidate.version_switch_error = None
        candidate.remote_version_activated_at = now
        await self._repository.finish_version_operation(
            file_id=candidate.id,
            operation="activate_candidate",
            succeeded=True,
            finished_at=now,
        )
        candidate = await self._repository.update_file_sync_state(candidate)
        await self._repository.add_log(
            task_id=task.id,
            status=task.status,
            message="ragflow candidate metadata activated",
        )
        await self._session.commit()
        return candidate

    async def _record_version_operation_failure(
        self,
        *,
        task: SyncTask,
        candidate_id: uuid.UUID,
        operation: str,
        failure_status: str,
        error: Exception,
    ) -> None:
        error_type = type(error).__name__[:120]
        candidate = await self._repository.get_file_for_update(candidate_id)
        if candidate is None:
            raise RagflowSyncPreconditionError
        candidate.version_switch_status = failure_status
        candidate.version_switch_error = error_type
        await self._repository.finish_version_operation(
            file_id=candidate.id,
            operation=operation,
            succeeded=False,
            finished_at=datetime.now(UTC),
            error_type=error_type,
            outcome_unknown=isinstance(error, RagflowSubmissionOutcomeUnknownError),
        )
        await self._repository.update_file_sync_state(candidate)
        await self._repository.add_log(
            task_id=task.id,
            status=task.status,
            message=f"ragflow version operation {operation} failed ({error_type})",
        )
        await self._session.commit()

    async def _load_version_pair_for_update(
        self,
        candidate: RagflowSyncFileRecord,
    ) -> tuple[RagflowSyncFileRecord, RagflowSyncFileRecord]:
        predecessor_id = candidate.replaces_file_id
        if predecessor_id is None:
            raise RagflowSyncPreconditionError
        records = await self._repository.get_version_series_for_update(candidate.series_id)
        by_id = {record.id: record for record in records}
        locked_candidate = by_id.get(candidate.id)
        predecessor = by_id.get(predecessor_id)
        if locked_candidate is None or predecessor is None:
            raise RagflowSyncPreconditionError
        self._validate_version_pair(
            candidate=locked_candidate,
            predecessor=predecessor,
            series=records,
        )
        return locked_candidate, predecessor

    @staticmethod
    def _validate_version_pair(
        *,
        candidate: RagflowSyncFileRecord,
        predecessor: RagflowSyncFileRecord,
        series: list[RagflowSyncFileRecord],
    ) -> None:
        if (
            candidate.replaces_file_id != predecessor.id
            or candidate.series_id != predecessor.series_id
            or candidate.version_number <= predecessor.version_number
            or candidate.department_id != predecessor.department_id
            or candidate.status != "parsed"
            or candidate.review_status != "approved"
            or predecessor.status != "parsed"
            or candidate.replacement_remote_action not in {"delete", "archive"}
        ):
            raise RagflowSyncPreconditionError
        ordered = sorted(series, key=lambda item: item.version_number)
        if not ordered:
            raise RagflowSyncPreconditionError
        root = ordered[0]
        if (
            root.id != root.series_id
            or root.version_number != 1
            or root.replaces_file_id is not None
            or root.replacement_remote_action is not None
            or candidate.version_number != ordered[-1].version_number
            or [item.version_number for item in ordered] != list(range(1, len(ordered) + 1))
        ):
            raise RagflowSyncPreconditionError
        by_id = {item.id: item for item in ordered}
        current = [item for item in ordered if item.is_current_version]
        if len(current) != 1 or current[0].id not in {candidate.id, predecessor.id}:
            raise RagflowSyncPreconditionError
        for item in ordered:
            if (
                item.series_id != root.id
                or item.department_id != root.department_id
                or item.uploader_id != root.uploader_id
                or (
                    item.id != root.id
                    and item.replacement_remote_action not in {"delete", "archive"}
                )
            ):
                raise RagflowSyncPreconditionError
            if item.id == root.id:
                continue
            if item.replaces_file_id is None:
                raise RagflowSyncPreconditionError
            item_predecessor = by_id.get(item.replaces_file_id)
            if item_predecessor is None or item_predecessor.version_number >= item.version_number:
                raise RagflowSyncPreconditionError
        skipped = [
            item
            for item in ordered
            if predecessor.version_number < item.version_number < candidate.version_number
        ]
        if any(
            item.status not in ABANDONED_VERSION_STATUSES
            or item.is_current_version
            or item.remote_visibility != "candidate"
            or item.ragflow_document_id is not None
            or item.version_switch_status not in {"pending", "failed_old_deactivate"}
            or item.predecessor_remote_deactivated_at is not None
            or item.local_version_activated_at is not None
            or item.remote_version_activated_at is not None
            for item in skipped
        ):
            raise RagflowSyncPreconditionError
        RagflowTaskService._remote_identity(candidate)
        RagflowTaskService._remote_identity(predecessor)

    @staticmethod
    def _remote_identity(file: RagflowSyncFileRecord) -> tuple[str, str]:
        dataset_id = file.ragflow_dataset_id
        document_id = file.ragflow_document_id
        if (
            dataset_id is None
            or not dataset_id.strip()
            or document_id is None
            or not document_id.strip()
        ):
            raise RagflowSyncPreconditionError
        return dataset_id, document_id

    @staticmethod
    def _require_execution_token(task: SyncTask) -> str:
        if task.lease_token is None:
            raise RagflowSyncPreconditionError
        return task.lease_token

    async def _complete_and_queue_status_check(
        self,
        *,
        task: SyncTask,
        file: RagflowSyncFileRecord,
        run: str,
    ) -> SyncTask:
        if task.status != "running":
            return task
        await self._assert_execution_lease(task)

        if task.task_type == RAGFLOW_STATUS_CHECK_TASK:
            poll_max_retry_count = task.max_retry_count
            next_retry_count = task.retry_count + 1
        else:
            # upload/delete 的 max_retry_count 是网络/人工重试预算, 不得复用为异步解析时限。
            poll_max_retry_count = await resolve_parse_poll_max_retries()
            next_retry_count = 1
        if next_retry_count > poll_max_retry_count:
            await self._repository.add_log(
                task_id=task.id,
                status=task.status,
                message=(
                    f"ragflow parse status {run} pending; polling budget exhausted "
                    f"after {task.retry_count} retries"
                ),
            )
            return await self.mark_failed(
                task.id,
                RAGFLOW_PARSE_POLL_EXHAUSTED_ERROR,
                expected_lease_token=task.lease_token,
            )

        task.status = "succeeded"
        task.lease_token = None
        task.lease_heartbeat_at = None
        task.reconcile_not_before = None
        task.recovery_probe_due_at = None
        task.finished_at = datetime.now(UTC)
        task.error_message = None
        await self._repository.add_log(
            task_id=task.id,
            status=task.status,
            message=f"{_task_label(task.task_type)} task completed",
        )
        status_check_task = await self.create_ragflow_status_check_task(
            file.id,
            retry_count=next_retry_count,
            max_retry_count=poll_max_retry_count,
        )
        await self._repository.add_log(
            task_id=task.id,
            status=task.status,
            message=(
                f"ragflow parse status {run} pending; "
                f"status check task {status_check_task.id} queued"
            ),
        )
        await self._session.commit()
        return task

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

    async def _try_mark_file_cleanup_failed(
        self,
        file: RagflowSyncFileRecord,
        error_message: str,
    ) -> None:
        if file.status in DELETE_CLEANUP_FAILURE_SOURCE_STATUSES:
            try:
                file.status = DocumentStateMachine.transition(
                    file.status,
                    "ragflow_cleanup_failed",
                )
            except DocumentStateError:
                pass
        file.ragflow_error_message = error_message[:MAX_ERROR_MESSAGE_LENGTH]
        file.last_sync_at = datetime.now(UTC)
        await self._repository.update_file_sync_state(file)

    def _build_metadata(
        self,
        file: RagflowSyncFileRecord,
        *,
        is_current_version: bool | None = None,
    ) -> dict[str, object]:
        effective_current = (
            file.is_current_version if is_current_version is None else is_current_version
        )
        return {
            "source": "knowledge_uploader",
            "file_id": str(file.id),
            "version_id": str(file.id),
            "series_id": str(file.series_id),
            "version_number": file.version_number,
            "replaces_file_id": str(file.replaces_file_id) if file.replaces_file_id else None,
            "is_current_version": effective_current,
            "uploader_id": str(file.uploader_id),
            "department_id": str(file.department_id),
            "department_name": file.department_name or file.department,
            "department_code": file.department_code,
            "category_id": str(file.category_id) if file.category_id is not None else None,
            "tags": file.tags,
            "visibility": file.visibility,
            "reviewer_id": str(file.reviewer_id) if file.reviewer_id is not None else None,
            "reviewed_at": file.reviewed_at.isoformat() if file.reviewed_at else None,
            "sensitive_risk_level": file.sensitive_risk_level,
            "content_hash": file.content_hash,
            "uploaded_at": file.uploaded_at.isoformat(),
        }

    @staticmethod
    def _ragflow_document_name(file: RagflowSyncFileRecord) -> str:
        """Return the stable remote identity without changing MinIO deduplication.

        Current uploads already include the local file id in ``stored_name``. Legacy and
        deduplicated rows can share a physical object name, so their RAGFlow identity must
        instead be derived from the current local row id.
        """
        if str(file.id) in file.stored_name:
            return file.stored_name
        original_name = sanitize_filename(file.original_name, max_length=200)
        return f"{file.id}-{original_name}"

    async def _get_task_file_or_raise(self, task: SyncTask) -> RagflowSyncFileRecord:
        file = await self._repository.get_file(task.file_id)
        if file is None:
            raise exceptions.task_not_found()
        return file

    def _require_scope_for_file(
        self,
        *,
        scope: DepartmentAccessScope,
        file: RagflowSyncFileRecord,
        on_out_of_scope: Callable[[], Exception] = exceptions.task_not_found,
    ) -> None:
        # 越权伪装成"资源不存在": task 入口抛 task_not_found, file 入口(manual_sync)抛
        # file_not_found, 使越权与不存在返回同一 404, 消除跨部门存在性枚举 oracle
        if not scope.covers_department(file.department_id):
            raise on_out_of_scope()

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

    async def _assert_execution_lease(self, task: SyncTask) -> SyncTask:
        expected_token = task.lease_token
        if expected_token is None:
            raise exceptions.RagflowTaskLeaseLostError
        current = await self._get_task_for_update_or_raise(task.id)
        if current.status != "running" or current.lease_token != expected_token:
            await self._session.rollback()
            raise exceptions.RagflowTaskLeaseLostError
        return current

    async def _heartbeat_execution_lease(
        self,
        *,
        task_id: uuid.UUID,
        execution_token: str,
    ) -> None:
        heartbeat_written = await self._repository.heartbeat_task(
            task_id=task_id,
            execution_token=execution_token,
            heartbeat_at=datetime.now(UTC),
        )
        if not heartbeat_written:
            await self._session.rollback()
            raise exceptions.RagflowTaskLeaseLostError
        await self._session.commit()

    async def heartbeat_execution_lease(
        self,
        *,
        task_id: uuid.UUID,
        execution_token: str,
    ) -> None:
        await self._heartbeat_execution_lease(
            task_id=task_id,
            execution_token=execution_token,
        )

    async def _record_admin_audit(
        self,
        *,
        current_user: AuthUserRecord,
        action: str,
        target_type: str,
        target_id: uuid.UUID,
        context: RequestContext,
        metadata_json: dict[str, object] | None = None,
        reason: str | None = None,
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
            reason=reason,
        )

    @staticmethod
    def _is_incomplete_version_switch_task(
        *,
        task: SyncTask,
        file: RagflowSyncFileRecord,
    ) -> bool:
        return (
            task.task_type in VERSION_SWITCH_RECONCILABLE_TASKS
            and file.replaces_file_id is not None
            and file.version_switch_status != "completed"
        )

    def _require_admin(self, current_user: AuthUserRecord) -> None:
        if current_user.role not in ADMIN_ROLES:
            raise exceptions.permission_denied()

    async def _append_task_queued_event(
        self,
        task: SyncTask,
        *,
        countdown_seconds: int | None = None,
    ) -> None:
        payload: dict[str, object] = {
            "sync_task_id": str(task.id),
            "file_id": str(task.file_id),
            "task_type": task.task_type,
            "status": task.status,
        }
        if task.task_type == RAGFLOW_STATUS_CHECK_TASK:
            payload.update(
                {
                    "countdown_seconds": (
                        countdown_seconds or RAGFLOW_STATUS_CHECK_INTERVAL_SECONDS
                    ),
                    "retry_count": task.retry_count,
                    "max_retry_count": task.max_retry_count,
                }
            )
        elif countdown_seconds is not None:
            payload["countdown_seconds"] = countdown_seconds
        await OutboxRepository(self._session).append(
            event_type=events.RAGFLOW_SYNC_TASK_QUEUED,
            aggregate_type="sync_task",
            aggregate_id=str(task.id),
            payload=payload,
        )


async def resolve_sync_max_retries() -> int:
    """解析 RAGFlow 同步最大重试次数 (ragflow.sync_max_retries), 非法值回退环境变量。"""
    value = await get_config("ragflow.sync_max_retries")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return max(0, get_settings().ragflow_max_retry_count)
    return value


async def resolve_execution_lease_seconds() -> int:
    """Keep a lease longer than one configured RAGFlow request, with heartbeat margin."""
    runtime_settings = await resolve_ragflow_runtime_settings()
    request_window = max(0, int(runtime_settings.timeout_seconds))
    return max(
        RAGFLOW_EXECUTION_LEASE_SECONDS,
        request_window + RAGFLOW_EXECUTION_LEASE_BUFFER_SECONDS,
    )


async def resolve_parse_poll_max_retries() -> int:
    """把独立解析超时配置换算为固定 30 秒间隔的有限轮询预算。"""
    value = await get_config("ragflow.parse_poll_timeout_seconds")
    if isinstance(value, bool) or not isinstance(value, int) or not 60 <= value <= 86_400:
        settings_value: object = getattr(
            get_settings(),
            "ragflow_parse_poll_timeout_seconds",
            DEFAULT_RAGFLOW_PARSE_POLL_TIMEOUT_SECONDS,
        )
        if (
            isinstance(settings_value, bool)
            or not isinstance(settings_value, int)
            or not 60 <= settings_value <= 86_400
        ):
            value = DEFAULT_RAGFLOW_PARSE_POLL_TIMEOUT_SECONDS
        else:
            value = settings_value
    timeout_seconds = min(86_400, max(60, value))
    return max(1, timeout_seconds // RAGFLOW_STATUS_CHECK_INTERVAL_SECONDS)


def _task_label(task_type: str) -> str:
    """sync 任务类型转日志用语 (ragflow_upload -> 'ragflow upload')。"""
    return task_type.replace("_", " ")


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
