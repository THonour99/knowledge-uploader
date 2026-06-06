from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import cast

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    MetaData,
    String,
    Table,
    Text,
    and_,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement


@dataclass(frozen=True)
class StatisticsFilters:
    start_at: datetime | None = None
    end_before: datetime | None = None
    department: str | None = None
    user_id: uuid.UUID | None = None
    category_id: uuid.UUID | None = None
    status: str | None = None
    review_status: str | None = None
    sync_status: str | None = None


@dataclass(frozen=True)
class StatisticsFileRow:
    id: uuid.UUID
    uploader_id: uuid.UUID
    user_name: str
    user_department: str | None
    department: str | None
    category_id: uuid.UUID | None
    category_name: str | None
    status: str
    review_status: str
    ragflow_document_id: str | None
    ragflow_parse_status: str | None
    size: int
    uploaded_at: datetime
    last_sync_at: datetime | None
    sensitive_risk_level: str | None


@dataclass(frozen=True)
class StatisticsFailedTaskRow:
    file_id: uuid.UUID
    reason: str


Meta = MetaData()

FILES = Table(
    "files",
    Meta,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("size", BigInteger, nullable=False),
    Column("uploader_id", UUID(as_uuid=True), nullable=False),
    Column("department", String(100)),
    Column("category_id", UUID(as_uuid=True)),
    Column("status", String(40), nullable=False),
    Column("review_status", String(40), nullable=False),
    Column("ragflow_document_id", String(120)),
    Column("ragflow_parse_status", String(40)),
    Column("uploaded_at", DateTime(timezone=True), nullable=False),
    Column("last_sync_at", DateTime(timezone=True)),
)

USERS = Table(
    "users",
    Meta,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("name", String(100), nullable=False),
    Column("department", String(100)),
)

CATEGORIES = Table(
    "categories",
    Meta,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("name", String(120), nullable=False),
)

DOCUMENT_ANALYSIS = Table(
    "document_analysis",
    Meta,
    Column("file_id", UUID(as_uuid=True), primary_key=True),
    Column("sensitive_risk_level", String(20), nullable=False),
)

SYNC_TASKS = Table(
    "sync_tasks",
    Meta,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("file_id", UUID(as_uuid=True), nullable=False),
    Column("task_type", String(40), nullable=False),
    Column("status", String(40), nullable=False),
    Column("error_message", Text),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

AUDIT_LOGS = Table(
    "audit_logs",
    Meta,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("actor_id", UUID(as_uuid=True), nullable=False),
    Column("action", String(120), nullable=False),
    Column("target_type", String(80), nullable=False),
    Column("target_id", UUID(as_uuid=True), nullable=False),
    Column("ip_address", String(45), nullable=False),
    Column("user_agent", String(512), nullable=False),
    Column("metadata_json", JSONB, nullable=False),
    Column("reason", Text),
    Column("created_at", DateTime(timezone=True), nullable=False),
)


class StatisticsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_files(self, filters: StatisticsFilters) -> list[StatisticsFileRow]:
        statement = (
            select(
                FILES.c.id,
                FILES.c.uploader_id,
                USERS.c.name.label("user_name"),
                USERS.c.department.label("user_department"),
                FILES.c.department,
                FILES.c.category_id,
                CATEGORIES.c.name.label("category_name"),
                FILES.c.status,
                FILES.c.review_status,
                FILES.c.ragflow_document_id,
                FILES.c.ragflow_parse_status,
                FILES.c.size,
                FILES.c.uploaded_at,
                FILES.c.last_sync_at,
                DOCUMENT_ANALYSIS.c.sensitive_risk_level,
            )
            .select_from(
                FILES.join(USERS, USERS.c.id == FILES.c.uploader_id)
                .outerjoin(CATEGORIES, CATEGORIES.c.id == FILES.c.category_id)
                .outerjoin(DOCUMENT_ANALYSIS, DOCUMENT_ANALYSIS.c.file_id == FILES.c.id)
            )
            .where(*self._file_predicates(filters))
        )
        result = await self._session.execute(statement)
        return [file_row_from_mapping(row) for row in result.mappings()]

    async def list_failed_tasks(
        self,
        filters: StatisticsFilters,
    ) -> list[StatisticsFailedTaskRow]:
        statement = (
            select(
                SYNC_TASKS.c.file_id,
                SYNC_TASKS.c.error_message,
            )
            .select_from(SYNC_TASKS.join(FILES, FILES.c.id == SYNC_TASKS.c.file_id))
            .where(
                SYNC_TASKS.c.status == "failed",
                *self._file_predicates(filters),
            )
        )
        result = await self._session.execute(statement)
        return [
            StatisticsFailedTaskRow(
                file_id=cast(uuid.UUID, row["file_id"]),
                reason=cast(str | None, row["error_message"]) or "unknown",
            )
            for row in result.mappings()
        ]

    async def user_exists(self, user_id: uuid.UUID) -> bool:
        result = await self._session.execute(select(USERS.c.id).where(USERS.c.id == user_id))
        return result.scalar_one_or_none() is not None

    def _file_predicates(self, filters: StatisticsFilters) -> list[ColumnElement[bool]]:
        predicates: list[ColumnElement[bool]] = []
        if filters.start_at is not None:
            predicates.append(FILES.c.uploaded_at >= filters.start_at)
        if filters.end_before is not None:
            predicates.append(FILES.c.uploaded_at < filters.end_before)
        if filters.department is not None:
            predicates.append(FILES.c.department == filters.department)
        if filters.user_id is not None:
            predicates.append(FILES.c.uploader_id == filters.user_id)
        if filters.category_id is not None:
            predicates.append(FILES.c.category_id == filters.category_id)
        if filters.status is not None:
            predicates.append(FILES.c.status == filters.status)
        if filters.review_status is not None:
            predicates.append(FILES.c.review_status == filters.review_status)
        if filters.sync_status is not None:
            predicates.append(sync_status_predicate(filters.sync_status))
        return predicates


def sync_status_predicate(sync_status: str) -> ColumnElement[bool]:
    if sync_status == "synced":
        return FILES.c.status == "parsed"
    if sync_status == "failed":
        return cast(
            ColumnElement[bool],
            (FILES.c.status == "failed")
            | (FILES.c.ragflow_parse_status.in_(("FAIL", "FAILED", "ERROR"))),
        )
    if sync_status == "syncing":
        return cast(
            ColumnElement[bool],
            FILES.c.status.in_(("queued", "syncing", "uploaded_to_ragflow", "parsing")),
        )
    if sync_status == "not_synced":
        return and_(
            FILES.c.ragflow_document_id.is_(None),
            FILES.c.status.not_in(
                ("queued", "syncing", "uploaded_to_ragflow", "parsing", "parsed")
            ),
        )
    msg = f"invalid sync_status: {sync_status}"
    raise ValueError(msg)


def file_row_from_mapping(row: RowMapping) -> StatisticsFileRow:
    return StatisticsFileRow(
        id=cast(uuid.UUID, row["id"]),
        uploader_id=cast(uuid.UUID, row["uploader_id"]),
        user_name=cast(str, row["user_name"]),
        user_department=cast(str | None, row["user_department"]),
        department=cast(str | None, row["department"]),
        category_id=cast(uuid.UUID | None, row["category_id"]),
        category_name=cast(str | None, row["category_name"]),
        status=cast(str, row["status"]),
        review_status=cast(str, row["review_status"]),
        ragflow_document_id=cast(str | None, row["ragflow_document_id"]),
        ragflow_parse_status=cast(str | None, row["ragflow_parse_status"]),
        size=cast(int, row["size"]),
        uploaded_at=cast(datetime, row["uploaded_at"]),
        last_sync_at=cast(datetime | None, row["last_sync_at"]),
        sensitive_risk_level=cast(str | None, row["sensitive_risk_level"]),
    )
