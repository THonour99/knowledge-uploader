from __future__ import annotations

import uuid
from collections.abc import Collection
from datetime import datetime
from typing import Any, cast

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    func,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from .models import (
    ACTIVE_SYNC_TASK_STATUSES,
    SYNC_TASK_STATUSES,
    RagflowVersionOperation,
    SyncTask,
    SyncTaskLog,
)
from .records import RagflowDatasetMappingRecord, RagflowSyncFileRecord

FILES = Table(
    "files",
    MetaData(),
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("original_name", String(255), nullable=False),
    Column("stored_name", String(255), nullable=False),
    Column("extension", String(20), nullable=False),
    Column("mime_type", String(120), nullable=False),
    Column("size", BigInteger, nullable=False),
    Column("hash", String(64), nullable=False),
    Column("bucket", String(100), nullable=False),
    Column("object_key", String(512), nullable=False),
    Column("uploader_id", UUID(as_uuid=True), nullable=False),
    Column("department_id", UUID(as_uuid=True), nullable=False),
    Column("department", String(100)),
    Column("category_id", UUID(as_uuid=True)),
    Column("dataset_mapping_id", UUID(as_uuid=True)),
    Column("visibility", String(20), nullable=False),
    Column("description", Text),
    Column("tags", JSONB, nullable=False),
    Column("series_id", UUID(as_uuid=True), nullable=False),
    Column("version_number", Integer, nullable=False),
    Column("replaces_file_id", UUID(as_uuid=True)),
    Column("replacement_remote_action", String(20)),
    Column("is_current_version", Boolean, nullable=False),
    Column("remote_visibility", String(20), nullable=False),
    Column("version_switch_status", String(40), nullable=False),
    Column("version_switch_error", String(120)),
    Column("version_switch_attempt_count", Integer, nullable=False),
    Column("predecessor_remote_deactivated_at", DateTime(timezone=True)),
    Column("local_version_activated_at", DateTime(timezone=True)),
    Column("remote_version_activated_at", DateTime(timezone=True)),
    Column("status", String(40), nullable=False),
    Column("review_status", String(40), nullable=False),
    Column("ragflow_dataset_id", String(120)),
    Column("ragflow_document_id", String(120)),
    Column("ragflow_parse_status", String(40)),
    Column("ragflow_error_message", Text),
    Column("uploaded_at", DateTime(timezone=True), nullable=False),
    Column("last_sync_at", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

FILE_COLUMNS = tuple(FILES.c)

DEPARTMENTS = Table(
    "departments",
    MetaData(),
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("name", String(100), nullable=False),
    Column("code", String(50), nullable=False),
)

DATASET_MAPPINGS = Table(
    "dataset_mappings",
    MetaData(),
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("category_id", UUID(as_uuid=True), nullable=False),
    Column("ragflow_dataset_id", String(120), nullable=False),
    Column("enabled", Boolean, nullable=False),
)

DOCUMENT_ANALYSIS = Table(
    "document_analysis",
    MetaData(),
    Column("file_id", UUID(as_uuid=True), primary_key=True),
    Column("status", String(30), nullable=False),
    Column("sensitive_risk_level", String(20), nullable=False),
    Column("sensitive_hits", JSONB, nullable=False),
)

AUDIT_LOGS = Table(
    "audit_logs",
    MetaData(),
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("actor_id", UUID(as_uuid=True), nullable=False),
    Column("action", String(120), nullable=False),
    Column("target_type", String(80), nullable=False),
    Column("target_id", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

AI_FEATURE_CONFIGS = Table(
    "ai_feature_configs",
    MetaData(),
    Column("feature_name", String(80), nullable=False),
    Column("enabled", Boolean, nullable=False),
)


class RagflowTaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_task(self, task: SyncTask) -> SyncTask:
        self._session.add(task)
        await self._session.flush()
        await self._session.refresh(task)
        return task

    async def begin_version_operation(
        self,
        *,
        file_id: uuid.UUID,
        target_file_id: uuid.UUID,
        operation: str,
        started_at: datetime,
    ) -> RagflowVersionOperation:
        result = await self._session.execute(
            select(RagflowVersionOperation)
            .where(
                RagflowVersionOperation.file_id == file_id,
                RagflowVersionOperation.operation == operation,
            )
            .with_for_update()
        )
        record = result.scalar_one_or_none()
        if record is None:
            record = RagflowVersionOperation(
                file_id=file_id,
                target_file_id=target_file_id,
                operation=operation,
            )
            self._session.add(record)
            await self._session.flush()
        elif record.target_file_id != target_file_id:
            raise RuntimeError("version operation target is immutable")
        record.status = "running"
        record.attempt_count += 1
        record.last_error = None
        record.started_at = started_at
        record.finished_at = None
        await self._session.flush()
        return record

    async def finish_version_operation(
        self,
        *,
        file_id: uuid.UUID,
        operation: str,
        succeeded: bool,
        finished_at: datetime,
        error_type: str | None = None,
        outcome_unknown: bool = False,
    ) -> RagflowVersionOperation:
        result = await self._session.execute(
            select(RagflowVersionOperation)
            .where(
                RagflowVersionOperation.file_id == file_id,
                RagflowVersionOperation.operation == operation,
            )
            .with_for_update()
        )
        record = result.scalar_one_or_none()
        if record is None:
            raise RuntimeError("version operation record is missing")
        if succeeded:
            record.status = "succeeded"
        else:
            record.status = "unknown" if outcome_unknown else "failed"
        record.last_error = None if succeeded else (error_type or "RemoteMetadataError")[:120]
        record.finished_at = finished_at
        await self._session.flush()
        return record

    async def get_active_task(self, *, file_id: uuid.UUID, task_type: str) -> SyncTask | None:
        result = await self._session.execute(
            select(SyncTask)
            .where(
                SyncTask.file_id == file_id,
                SyncTask.task_type == task_type,
                SyncTask.status.in_(ACTIVE_SYNC_TASK_STATUSES),
            )
            .order_by(SyncTask.created_at.asc(), SyncTask.id.asc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_active_task_for_types(
        self,
        *,
        file_id: uuid.UUID,
        task_types: Collection[str],
        exclude_task_id: uuid.UUID,
    ) -> SyncTask | None:
        normalized_types = tuple(sorted(set(task_types)))
        if not normalized_types:
            return None
        result = await self._session.execute(
            select(SyncTask)
            .where(
                SyncTask.file_id == file_id,
                SyncTask.task_type.in_(normalized_types),
                SyncTask.status.in_(ACTIVE_SYNC_TASK_STATUSES),
                SyncTask.id != exclude_task_id,
            )
            .order_by(SyncTask.created_at.asc(), SyncTask.id.asc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_tasks(
        self,
        *,
        file_id: uuid.UUID | None = None,
        task_type: str | None = None,
        status: str | None = None,
        department_ids: frozenset[uuid.UUID] | None = None,
        sort: str = "created_at",
        order: str = "desc",
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[SyncTask], int, dict[str, int]]:
        status_counts = {task_status: 0 for task_status in SYNC_TASK_STATUSES}
        if department_ids is not None and not department_ids:
            return [], 0, status_counts
        query = select(SyncTask)
        status_counts_query = select(
            SyncTask.status,
            func.count(SyncTask.id),
        ).group_by(SyncTask.status)
        if department_ids is not None:
            query = query.join(FILES, FILES.c.id == SyncTask.file_id).where(
                FILES.c.department_id.in_(department_ids)
            )
            status_counts_query = status_counts_query.join(
                FILES, FILES.c.id == SyncTask.file_id
            ).where(FILES.c.department_id.in_(department_ids))
        if file_id is not None:
            query = query.where(SyncTask.file_id == file_id)
            status_counts_query = status_counts_query.where(SyncTask.file_id == file_id)
        if task_type is not None:
            query = query.where(SyncTask.task_type == task_type)
            status_counts_query = status_counts_query.where(SyncTask.task_type == task_type)
        if status is not None:
            query = query.where(SyncTask.status == status)
            status_counts_query = status_counts_query.where(SyncTask.status == status)
        sort_column = {
            "created_at": SyncTask.created_at,
            "updated_at": SyncTask.updated_at,
            "started_at": SyncTask.started_at,
            "finished_at": SyncTask.finished_at,
        }[sort]
        ordering = sort_column.asc() if order == "asc" else sort_column.desc()
        id_ordering = SyncTask.id.asc() if order == "asc" else SyncTask.id.desc()
        result = await self._session.execute(
            query.order_by(ordering.nulls_last(), id_ordering).offset(offset).limit(limit)
        )
        status_count_rows = (await self._session.execute(status_counts_query)).all()
        for task_status, count in status_count_rows:
            status_counts[task_status] = int(count)
        total = sum(status_counts.values())
        return list(result.scalars()), total, status_counts

    async def get_task(self, task_id: uuid.UUID) -> SyncTask | None:
        result = await self._session.execute(select(SyncTask).where(SyncTask.id == task_id))
        return result.scalar_one_or_none()

    async def get_task_for_update(self, task_id: uuid.UUID) -> SyncTask | None:
        result = await self._session.execute(
            select(SyncTask)
            .where(SyncTask.id == task_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def heartbeat_task(
        self,
        *,
        task_id: uuid.UUID,
        execution_token: str,
        heartbeat_at: datetime,
    ) -> bool:
        result = await self._session.execute(
            update(SyncTask)
            .where(
                SyncTask.id == task_id,
                SyncTask.status == "running",
                SyncTask.lease_token == execution_token,
            )
            .values(lease_heartbeat_at=heartbeat_at, updated_at=func.now())
            .returning(SyncTask.id)
        )
        return result.scalar_one_or_none() is not None

    async def add_log(self, *, task_id: uuid.UUID, status: str, message: str) -> SyncTaskLog:
        log = SyncTaskLog(task_id=task_id, status=status, message=message)
        self._session.add(log)
        await self._session.flush()
        await self._session.refresh(log)
        return log

    async def list_logs(self, task_id: uuid.UUID) -> list[SyncTaskLog]:
        result = await self._session.execute(
            select(SyncTaskLog)
            .where(SyncTaskLog.task_id == task_id)
            .order_by(SyncTaskLog.created_at.asc(), SyncTaskLog.id.asc())
        )
        return list(result.scalars())

    async def list_logs_for_tasks(
        self,
        task_ids: Collection[uuid.UUID],
    ) -> dict[uuid.UUID, list[SyncTaskLog]]:
        normalized_task_ids = tuple(dict.fromkeys(task_ids))
        logs_by_task: dict[uuid.UUID, list[SyncTaskLog]] = {
            task_id: [] for task_id in normalized_task_ids
        }
        if not normalized_task_ids:
            return logs_by_task
        result = await self._session.execute(
            select(SyncTaskLog)
            .where(SyncTaskLog.task_id.in_(normalized_task_ids))
            .order_by(
                SyncTaskLog.task_id.asc(),
                SyncTaskLog.created_at.asc(),
                SyncTaskLog.id.asc(),
            )
        )
        for log in result.scalars():
            logs_by_task[log.task_id].append(log)
        return logs_by_task

    async def get_file(
        self,
        file_id: uuid.UUID,
    ) -> RagflowSyncFileRecord | None:
        result = await self._session.execute(self._file_select().where(FILES.c.id == file_id))
        row = result.mappings().one_or_none()
        return file_record_from_row(row) if row is not None else None

    async def get_file_for_update(
        self,
        file_id: uuid.UUID,
    ) -> RagflowSyncFileRecord | None:
        result = await self._session.execute(
            select(
                *FILE_COLUMNS,
                *self._department_lookup_columns(),
                *self._metadata_lookup_columns(),
            )
            .where(FILES.c.id == file_id)
            .with_for_update()
        )
        row = result.mappings().one_or_none()
        return file_record_from_row(row) if row is not None else None

    async def update_file_sync_state(
        self,
        file: RagflowSyncFileRecord,
    ) -> RagflowSyncFileRecord:
        await self._session.execute(
            update(FILES)
            .where(FILES.c.id == file.id)
            .values(
                status=file.status,
                category_id=file.category_id,
                dataset_mapping_id=file.dataset_mapping_id,
                is_current_version=file.is_current_version,
                remote_visibility=file.remote_visibility,
                version_switch_status=file.version_switch_status,
                version_switch_error=file.version_switch_error,
                version_switch_attempt_count=file.version_switch_attempt_count,
                predecessor_remote_deactivated_at=file.predecessor_remote_deactivated_at,
                local_version_activated_at=file.local_version_activated_at,
                remote_version_activated_at=file.remote_version_activated_at,
                ragflow_dataset_id=file.ragflow_dataset_id,
                ragflow_document_id=file.ragflow_document_id,
                ragflow_parse_status=file.ragflow_parse_status,
                ragflow_error_message=file.ragflow_error_message,
                last_sync_at=file.last_sync_at,
                updated_at=func.now(),
            )
        )
        updated_file = await self.get_file(file.id)
        if updated_file is None:
            raise RuntimeError("updated ragflow file record disappeared")
        return updated_file

    async def get_version_files_for_update(
        self,
        file_ids: set[uuid.UUID],
    ) -> list[RagflowSyncFileRecord]:
        if not file_ids:
            return []
        result = await self._session.execute(
            select(
                *FILE_COLUMNS,
                *self._department_lookup_columns(),
                *self._metadata_lookup_columns(),
            )
            .where(FILES.c.id.in_(file_ids))
            .order_by(FILES.c.id.asc())
            .with_for_update(of=FILES)
        )
        return [file_record_from_row(row) for row in result.mappings()]

    async def get_version_series_for_update(
        self,
        series_id: uuid.UUID,
    ) -> list[RagflowSyncFileRecord]:
        result = await self._session.execute(
            select(
                *FILE_COLUMNS,
                *self._department_lookup_columns(),
                *self._metadata_lookup_columns(),
            )
            .where(FILES.c.series_id == series_id)
            .order_by(FILES.c.version_number.asc(), FILES.c.id.asc())
            .with_for_update(of=FILES)
            .execution_options(populate_existing=True)
        )
        return [file_record_from_row(row) for row in result.mappings()]

    def _file_select(self) -> Select[tuple[Any, ...]]:
        return select(
            *FILE_COLUMNS,
            *self._department_lookup_columns(),
            *self._metadata_lookup_columns(),
        )

    def _department_lookup_columns(self) -> tuple[Any, Any]:
        return (
            select(DEPARTMENTS.c.name)
            .where(DEPARTMENTS.c.id == FILES.c.department_id)
            .scalar_subquery()
            .label("department_name"),
            select(DEPARTMENTS.c.code)
            .where(DEPARTMENTS.c.id == FILES.c.department_id)
            .scalar_subquery()
            .label("department_code"),
        )

    def _metadata_lookup_columns(self) -> tuple[Any, Any, Any]:
        approval_predicates = (
            AUDIT_LOGS.c.target_type == "file",
            AUDIT_LOGS.c.target_id == FILES.c.id,
            AUDIT_LOGS.c.action == "file.approve",
        )
        approval_order = (AUDIT_LOGS.c.created_at.desc(), AUDIT_LOGS.c.id.desc())
        return (
            select(AUDIT_LOGS.c.actor_id)
            .where(*approval_predicates)
            .order_by(*approval_order)
            .limit(1)
            .scalar_subquery()
            .label("reviewer_id"),
            select(AUDIT_LOGS.c.created_at)
            .where(*approval_predicates)
            .order_by(*approval_order)
            .limit(1)
            .scalar_subquery()
            .label("reviewed_at"),
            select(DOCUMENT_ANALYSIS.c.sensitive_risk_level)
            .where(DOCUMENT_ANALYSIS.c.file_id == FILES.c.id)
            .limit(1)
            .scalar_subquery()
            .label("metadata_sensitive_risk_level"),
        )

    async def get_dataset_mapping(
        self,
        mapping_id: uuid.UUID,
    ) -> RagflowDatasetMappingRecord | None:
        result = await self._session.execute(
            select(
                DATASET_MAPPINGS.c.id,
                DATASET_MAPPINGS.c.category_id,
                DATASET_MAPPINGS.c.ragflow_dataset_id,
                DATASET_MAPPINGS.c.enabled,
            ).where(DATASET_MAPPINGS.c.id == mapping_id)
        )
        row = result.mappings().one_or_none()
        if row is None:
            return None
        return RagflowDatasetMappingRecord(
            id=cast(uuid.UUID, row["id"]),
            category_id=cast(uuid.UUID, row["category_id"]),
            ragflow_dataset_id=cast(str, row["ragflow_dataset_id"]),
            enabled=cast(bool, row["enabled"]),
        )

    async def get_dataset_mapping_for_update(
        self,
        mapping_id: uuid.UUID,
    ) -> RagflowDatasetMappingRecord | None:
        result = await self._session.execute(
            select(
                DATASET_MAPPINGS.c.id,
                DATASET_MAPPINGS.c.category_id,
                DATASET_MAPPINGS.c.ragflow_dataset_id,
                DATASET_MAPPINGS.c.enabled,
            )
            .where(DATASET_MAPPINGS.c.id == mapping_id)
            .with_for_update(of=DATASET_MAPPINGS)
        )
        row = result.mappings().one_or_none()
        if row is None:
            return None
        return RagflowDatasetMappingRecord(
            id=cast(uuid.UUID, row["id"]),
            category_id=cast(uuid.UUID, row["category_id"]),
            ragflow_dataset_id=cast(str, row["ragflow_dataset_id"]),
            enabled=cast(bool, row["enabled"]),
        )

    async def get_file_sensitive_risk_level(self, file_id: uuid.UUID) -> str | None:
        result = await self._session.execute(
            select(DOCUMENT_ANALYSIS.c.sensitive_risk_level).where(
                DOCUMENT_ANALYSIS.c.file_id == file_id
            )
        )
        return cast(str | None, result.scalar_one_or_none())

    async def has_block_sync_sensitive_hit(self, file_id: uuid.UUID) -> bool:
        result = await self._session.execute(
            select(DOCUMENT_ANALYSIS.c.sensitive_hits).where(DOCUMENT_ANALYSIS.c.file_id == file_id)
        )
        hits = result.scalar_one_or_none()
        if not isinstance(hits, list):
            return False
        return any(isinstance(hit, dict) and hit.get("action") == "block_sync" for hit in hits)

    async def get_file_analysis_status(self, file_id: uuid.UUID) -> str | None:
        result = await self._session.execute(
            select(DOCUMENT_ANALYSIS.c.status).where(DOCUMENT_ANALYSIS.c.file_id == file_id)
        )
        return cast(str | None, result.scalar_one_or_none())

    async def get_ai_feature_enabled(self, feature_name: str) -> bool | None:
        result = await self._session.execute(
            select(AI_FEATURE_CONFIGS.c.enabled).where(
                AI_FEATURE_CONFIGS.c.feature_name == feature_name
            )
        )
        return cast(bool | None, result.scalar_one_or_none())


def file_record_from_row(row: RowMapping) -> RagflowSyncFileRecord:
    return RagflowSyncFileRecord(
        id=cast(uuid.UUID, row["id"]),
        original_name=cast(str, row["original_name"]),
        stored_name=cast(str, row["stored_name"]),
        extension=cast(str, row["extension"]),
        mime_type=cast(str, row["mime_type"]),
        size=cast(int, row["size"]),
        content_hash=cast(str, row["hash"]),
        bucket=cast(str, row["bucket"]),
        object_key=cast(str, row["object_key"]),
        uploader_id=cast(uuid.UUID, row["uploader_id"]),
        department_id=cast(uuid.UUID, row["department_id"]),
        department_name=cast(str | None, row.get("department_name")),
        department_code=cast(str | None, row.get("department_code")),
        department=cast(str | None, row["department"]),
        category_id=cast(uuid.UUID | None, row["category_id"]),
        dataset_mapping_id=cast(uuid.UUID | None, row["dataset_mapping_id"]),
        visibility=cast(str, row["visibility"]),
        description=cast(str | None, row["description"]),
        tags=cast(list[str], row["tags"]),
        status=cast(str, row["status"]),
        review_status=cast(str, row["review_status"]),
        reviewer_id=cast(uuid.UUID | None, row.get("reviewer_id")),
        reviewed_at=cast(datetime | None, row.get("reviewed_at")),
        sensitive_risk_level=cast(
            str | None,
            row.get("metadata_sensitive_risk_level"),
        )
        or "none",
        series_id=cast(uuid.UUID, row["series_id"]),
        version_number=cast(int, row["version_number"]),
        replaces_file_id=cast(uuid.UUID | None, row["replaces_file_id"]),
        replacement_remote_action=cast(str | None, row["replacement_remote_action"]),
        is_current_version=cast(bool, row["is_current_version"]),
        remote_visibility=cast(str, row["remote_visibility"]),
        version_switch_status=cast(str, row["version_switch_status"]),
        version_switch_error=cast(str | None, row["version_switch_error"]),
        version_switch_attempt_count=cast(int, row["version_switch_attempt_count"]),
        predecessor_remote_deactivated_at=cast(
            datetime | None, row["predecessor_remote_deactivated_at"]
        ),
        local_version_activated_at=cast(datetime | None, row["local_version_activated_at"]),
        remote_version_activated_at=cast(datetime | None, row["remote_version_activated_at"]),
        ragflow_dataset_id=cast(str | None, row["ragflow_dataset_id"]),
        ragflow_document_id=cast(str | None, row["ragflow_document_id"]),
        ragflow_parse_status=cast(str | None, row["ragflow_parse_status"]),
        ragflow_error_message=cast(str | None, row["ragflow_error_message"]),
        uploaded_at=cast(datetime, row["uploaded_at"]),
        last_sync_at=cast(datetime | None, row["last_sync_at"]),
    )
