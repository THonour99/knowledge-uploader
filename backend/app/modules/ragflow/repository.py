from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, cast

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
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

from .models import ACTIVE_SYNC_TASK_STATUSES, SyncTask, SyncTaskLog
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

    async def list_tasks(
        self,
        *,
        file_id: uuid.UUID | None = None,
        department_ids: frozenset[uuid.UUID] | None = None,
    ) -> list[SyncTask]:
        if department_ids is not None and not department_ids:
            return []
        query = select(SyncTask).order_by(SyncTask.created_at.desc(), SyncTask.id.desc())
        if department_ids is not None:
            query = query.join(FILES, FILES.c.id == SyncTask.file_id).where(
                FILES.c.department_id.in_(department_ids)
            )
        if file_id is not None:
            query = query.where(SyncTask.file_id == file_id)
        result = await self._session.execute(query)
        return list(result.scalars())

    async def get_task(self, task_id: uuid.UUID) -> SyncTask | None:
        result = await self._session.execute(select(SyncTask).where(SyncTask.id == task_id))
        return result.scalar_one_or_none()

    async def get_task_for_update(self, task_id: uuid.UUID) -> SyncTask | None:
        result = await self._session.execute(
            select(SyncTask).where(SyncTask.id == task_id).with_for_update()
        )
        return result.scalar_one_or_none()

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
            select(*FILE_COLUMNS, *self._department_lookup_columns())
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

    def _file_select(self) -> Select[tuple[Any, ...]]:
        return select(*FILE_COLUMNS, *self._department_lookup_columns())

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

    async def get_dataset_mapping(
        self,
        mapping_id: uuid.UUID,
    ) -> RagflowDatasetMappingRecord | None:
        result = await self._session.execute(
            select(
                DATASET_MAPPINGS.c.id,
                DATASET_MAPPINGS.c.ragflow_dataset_id,
                DATASET_MAPPINGS.c.enabled,
            ).where(DATASET_MAPPINGS.c.id == mapping_id)
        )
        row = result.mappings().one_or_none()
        if row is None:
            return None
        return RagflowDatasetMappingRecord(
            id=cast(uuid.UUID, row["id"]),
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
        ragflow_dataset_id=cast(str | None, row["ragflow_dataset_id"]),
        ragflow_document_id=cast(str | None, row["ragflow_document_id"]),
        ragflow_parse_status=cast(str | None, row["ragflow_parse_status"]),
        ragflow_error_message=cast(str | None, row["ragflow_error_message"]),
        uploaded_at=cast(datetime, row["uploaded_at"]),
        last_sync_at=cast(datetime | None, row["last_sync_at"]),
    )
