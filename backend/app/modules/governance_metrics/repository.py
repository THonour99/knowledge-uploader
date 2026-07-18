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
    case,
    func,
    literal,
    or_,
    select,
    tuple_,
)
from sqlalchemy import (
    cast as sa_cast,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from .schemas import CapacityGroupBy, RagflowGroupBy, UsageGroupBy


@dataclass(frozen=True)
class MetricsRange:
    start_at: datetime
    end_before: datetime


@dataclass(frozen=True)
class CapacityAggregate:
    dimension_key: str
    dimension_label: str
    file_count: int
    active_logical_bytes: int
    retained_inactive_bytes: int
    total_referenced_bytes: int


@dataclass(frozen=True)
class PhysicalSnapshotRow:
    source_kind: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    captured_at: datetime
    collected_at: datetime


@dataclass(frozen=True)
class LlmUsageAggregate:
    dimension_key: str
    dimension_label: str
    cost_status: str
    cost_currency: str
    calls: int
    prompt_tokens: int
    completion_tokens: int
    calls_with_unknown_tokens: int
    estimated_cost_microunits: int | None


@dataclass(frozen=True)
class RagflowUsageAggregate:
    dimension_key: str
    dimension_label: str
    calls: int
    completed_calls: int
    failure_calls: int
    in_progress_calls: int
    total_latency_ms: int


META = MetaData()

FILES = Table(
    "files",
    META,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("department_id", UUID(as_uuid=True), nullable=False),
    Column("extension", String(20), nullable=False),
    Column("size", BigInteger, nullable=False),
    Column("storage_type", String(20), nullable=False),
    Column("status", String(40), nullable=False),
    Column("uploaded_at", DateTime(timezone=True), nullable=False),
)

DEPARTMENTS = Table(
    "departments",
    META,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("name", String(120), nullable=False),
)

AI_USAGE_LOGS = Table(
    "ai_usage_logs",
    META,
    Column("id", BigInteger, primary_key=True),
    Column("file_id", UUID(as_uuid=True)),
    Column("provider_name", String(120)),
    Column("model_name", String(120)),
    Column("cost_status", String(40), nullable=False),
    Column("estimated_cost_microunits", BigInteger, nullable=False),
    Column("cost_currency", String(3), nullable=False),
    Column("prompt_tokens", BigInteger),
    Column("completion_tokens", BigInteger),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

RAGFLOW_API_CALLS = Table(
    "ragflow_api_calls",
    META,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("department_id", UUID(as_uuid=True)),
    Column("operation", String(40), nullable=False),
    Column("result", String(20), nullable=False),
    Column("failure_category", String(40)),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("latency_ms", BigInteger),
)

STORAGE_CAPACITY_SNAPSHOTS = Table(
    "storage_capacity_snapshots",
    META,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("source_kind", String(40), nullable=False),
    Column("total_bytes", BigInteger, nullable=False),
    Column("used_bytes", BigInteger, nullable=False),
    Column("free_bytes", BigInteger, nullable=False),
    Column("captured_at", DateTime(timezone=True), nullable=False),
    Column("collected_at", DateTime(timezone=True), nullable=False),
)

_RETAINED_INACTIVE_STATUSES = ("disabled", "deleted", "ragflow_cleanup_failed")
_PROCESSING_STAGE_STATUSES: dict[str, tuple[str, ...]] = {
    "draft": ("uploaded",),
    "ai": (
        "extracting_text",
        "analysis_queued",
        "analyzing",
        "analysis_failed",
        "analyzed",
    ),
    "review": (
        "sensitive_review_required",
        "pending_review",
        "approved",
        "rejected",
    ),
    "sync": ("queued", "syncing", "uploaded_to_ragflow", "parsing"),
    "available": ("parsed",),
    "failed": ("failed", "ragflow_cleanup_failed"),
    "archived": ("disabled", "deleted"),
}


class GovernanceMetricsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def capacity(
        self,
        *,
        metrics_range: MetricsRange,
        group_by: CapacityGroupBy,
        page: int,
        page_size: int,
    ) -> tuple[list[CapacityAggregate], int]:
        from_clause = FILES.outerjoin(DEPARTMENTS, DEPARTMENTS.c.id == FILES.c.department_id)
        key, label = _capacity_dimension(group_by)
        active_predicate = FILES.c.status.not_in(_RETAINED_INACTIVE_STATUSES)
        predicates = (
            FILES.c.storage_type == "minio",
            FILES.c.uploaded_at >= metrics_range.start_at,
            FILES.c.uploaded_at < metrics_range.end_before,
        )
        dimension_statement = (
            select(
                key.label("dimension_key"),
                label.label("dimension_label"),
            )
            .select_from(from_clause)
            .where(*predicates)
            .group_by(key, label)
        )
        count_result = await self._session.execute(
            select(func.count()).select_from(dimension_statement.subquery())
        )
        total = int(count_result.scalar_one())
        statement = (
            select(
                key.label("dimension_key"),
                label.label("dimension_label"),
                func.count(FILES.c.id).label("file_count"),
                func.coalesce(func.sum(FILES.c.size).filter(active_predicate), 0).label(
                    "active_logical_bytes"
                ),
                func.coalesce(
                    func.sum(FILES.c.size).filter(FILES.c.status.in_(_RETAINED_INACTIVE_STATUSES)),
                    0,
                ).label("retained_inactive_bytes"),
                func.coalesce(func.sum(FILES.c.size), 0).label("total_referenced_bytes"),
            )
            .select_from(from_clause)
            .where(*predicates)
            .group_by(key, label)
            .order_by(key.asc(), label.asc())
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
        result = await self._session.execute(statement)
        return [_capacity_from_row(row) for row in result.mappings()], total

    async def latest_physical_snapshot(self) -> PhysicalSnapshotRow | None:
        result = await self._session.execute(
            select(
                STORAGE_CAPACITY_SNAPSHOTS.c.source_kind,
                STORAGE_CAPACITY_SNAPSHOTS.c.total_bytes,
                STORAGE_CAPACITY_SNAPSHOTS.c.used_bytes,
                STORAGE_CAPACITY_SNAPSHOTS.c.free_bytes,
                STORAGE_CAPACITY_SNAPSHOTS.c.captured_at,
                STORAGE_CAPACITY_SNAPSHOTS.c.collected_at,
            )
            .where(STORAGE_CAPACITY_SNAPSHOTS.c.source_kind == "minio_cluster_metrics")
            .order_by(
                STORAGE_CAPACITY_SNAPSHOTS.c.captured_at.desc(),
                STORAGE_CAPACITY_SNAPSHOTS.c.id.desc(),
            )
            .limit(1)
        )
        row = result.mappings().one_or_none()
        return _physical_snapshot_from_row(row) if row is not None else None

    async def llm_usage(
        self,
        *,
        metrics_range: MetricsRange,
        group_by: UsageGroupBy,
        page: int,
        page_size: int,
    ) -> tuple[list[LlmUsageAggregate], int]:
        from_clause = AI_USAGE_LOGS.outerjoin(
            FILES, FILES.c.id == AI_USAGE_LOGS.c.file_id
        ).outerjoin(DEPARTMENTS, DEPARTMENTS.c.id == FILES.c.department_id)
        key, label = _llm_dimension(group_by)
        predicates = (
            AI_USAGE_LOGS.c.created_at >= metrics_range.start_at,
            AI_USAGE_LOGS.c.created_at < metrics_range.end_before,
        )
        dimension_statement = (
            select(
                key.label("dimension_key"),
                label.label("dimension_label"),
            )
            .select_from(from_clause)
            .where(*predicates)
            .group_by(key, label)
        )
        count_result = await self._session.execute(
            select(func.count()).select_from(dimension_statement.subquery())
        )
        total = int(count_result.scalar_one())
        dimension_result = await self._session.execute(
            dimension_statement.order_by(key.asc(), label.asc())
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
        dimensions = [
            (
                cast(str, row["dimension_key"]),
                cast(str, row["dimension_label"]),
            )
            for row in dimension_result.mappings()
        ]
        if not dimensions:
            return [], total
        statement = (
            select(
                key.label("dimension_key"),
                label.label("dimension_label"),
                AI_USAGE_LOGS.c.cost_status,
                AI_USAGE_LOGS.c.cost_currency,
                func.count(AI_USAGE_LOGS.c.id).label("calls"),
                func.coalesce(func.sum(AI_USAGE_LOGS.c.prompt_tokens), 0).label("prompt_tokens"),
                func.coalesce(func.sum(AI_USAGE_LOGS.c.completion_tokens), 0).label(
                    "completion_tokens"
                ),
                func.count(AI_USAGE_LOGS.c.id)
                .filter(
                    or_(
                        AI_USAGE_LOGS.c.prompt_tokens.is_(None),
                        AI_USAGE_LOGS.c.completion_tokens.is_(None),
                    )
                )
                .label("calls_with_unknown_tokens"),
                func.sum(AI_USAGE_LOGS.c.estimated_cost_microunits)
                .filter(AI_USAGE_LOGS.c.cost_status == "known")
                .label("estimated_cost_microunits"),
            )
            .select_from(from_clause)
            .where(*predicates, tuple_(key, label).in_(dimensions))
            .group_by(
                key,
                label,
                AI_USAGE_LOGS.c.cost_status,
                AI_USAGE_LOGS.c.cost_currency,
            )
            .order_by(
                key.asc(),
                label.asc(),
                AI_USAGE_LOGS.c.cost_status.asc(),
                AI_USAGE_LOGS.c.cost_currency,
            )
        )
        result = await self._session.execute(statement)
        return [_llm_usage_from_row(row) for row in result.mappings()], total

    async def ragflow_usage(
        self,
        *,
        metrics_range: MetricsRange,
        group_by: RagflowGroupBy,
        page: int,
        page_size: int,
    ) -> tuple[list[RagflowUsageAggregate], int]:
        from_clause = RAGFLOW_API_CALLS.outerjoin(
            DEPARTMENTS, DEPARTMENTS.c.id == RAGFLOW_API_CALLS.c.department_id
        )
        key, label = _ragflow_dimension(group_by)
        predicates = (
            RAGFLOW_API_CALLS.c.started_at >= metrics_range.start_at,
            RAGFLOW_API_CALLS.c.started_at < metrics_range.end_before,
        )
        dimension_statement = (
            select(
                key.label("dimension_key"),
                label.label("dimension_label"),
            )
            .select_from(from_clause)
            .where(*predicates)
            .group_by(key, label)
        )
        count_result = await self._session.execute(
            select(func.count()).select_from(dimension_statement.subquery())
        )
        total = int(count_result.scalar_one())
        statement = (
            select(
                key.label("dimension_key"),
                label.label("dimension_label"),
                func.count(RAGFLOW_API_CALLS.c.id).label("calls"),
                func.count(RAGFLOW_API_CALLS.c.id)
                .filter(RAGFLOW_API_CALLS.c.result.in_(("success", "failure")))
                .label("completed_calls"),
                func.count(RAGFLOW_API_CALLS.c.id)
                .filter(RAGFLOW_API_CALLS.c.result == "failure")
                .label("failure_calls"),
                func.count(RAGFLOW_API_CALLS.c.id)
                .filter(RAGFLOW_API_CALLS.c.result == "started")
                .label("in_progress_calls"),
                func.coalesce(func.sum(RAGFLOW_API_CALLS.c.latency_ms), 0).label(
                    "total_latency_ms"
                ),
            )
            .select_from(from_clause)
            .where(*predicates)
            .group_by(key, label)
            .order_by(key.asc(), label.asc())
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
        result = await self._session.execute(statement)
        return [_ragflow_usage_from_row(row) for row in result.mappings()], total


def _capacity_dimension(
    group_by: CapacityGroupBy,
) -> tuple[ColumnElement[str], ColumnElement[str]]:
    if group_by == "department":
        return _department_dimension()
    if group_by == "file_type":
        extension = func.lower(func.coalesce(FILES.c.extension, "unknown"))
        return extension, extension
    if group_by == "processing_stage":
        stage = case(
            *(
                (FILES.c.status.in_(statuses), stage_name)
                for stage_name, statuses in _PROCESSING_STAGE_STATUSES.items()
            ),
            else_="unknown",
        )
        return stage, stage
    if group_by == "day":
        utc_uploaded_at = func.timezone("UTC", FILES.c.uploaded_at)
        day = func.to_char(func.date_trunc("day", utc_uploaded_at), "YYYY-MM-DD")
        return day, day
    return literal("all"), literal("全部")


def _llm_dimension(group_by: UsageGroupBy) -> tuple[ColumnElement[str], ColumnElement[str]]:
    if group_by == "department":
        return _department_dimension()
    if group_by == "provider":
        value = func.coalesce(AI_USAGE_LOGS.c.provider_name, "unknown")
        return value, value
    if group_by == "model":
        value = func.coalesce(AI_USAGE_LOGS.c.model_name, "unknown")
        return value, value
    if group_by == "day":
        utc_created_at = func.timezone("UTC", AI_USAGE_LOGS.c.created_at)
        day = func.to_char(func.date_trunc("day", utc_created_at), "YYYY-MM-DD")
        return day, day
    return literal("all"), literal("全部")


def _ragflow_dimension(
    group_by: RagflowGroupBy,
) -> tuple[ColumnElement[str], ColumnElement[str]]:
    if group_by == "department":
        return _department_dimension(RAGFLOW_API_CALLS.c.department_id)
    if group_by == "operation":
        return RAGFLOW_API_CALLS.c.operation, RAGFLOW_API_CALLS.c.operation
    if group_by == "result":
        return RAGFLOW_API_CALLS.c.result, RAGFLOW_API_CALLS.c.result
    if group_by == "failure_category":
        value = func.coalesce(RAGFLOW_API_CALLS.c.failure_category, "none")
        return value, value
    if group_by == "day":
        utc_started_at = func.timezone("UTC", RAGFLOW_API_CALLS.c.started_at)
        day = func.to_char(func.date_trunc("day", utc_started_at), "YYYY-MM-DD")
        return day, day
    return literal("all"), literal("全部")


def _department_dimension(
    department_id: ColumnElement[uuid.UUID] | None = None,
) -> tuple[ColumnElement[str], ColumnElement[str]]:
    source_id = FILES.c.department_id if department_id is None else department_id
    key = func.coalesce(sa_cast(source_id, String), "unknown")
    label = func.coalesce(DEPARTMENTS.c.name, "未知部门")
    return key, label


def _capacity_from_row(row: RowMapping) -> CapacityAggregate:
    return CapacityAggregate(
        dimension_key=cast(str, row["dimension_key"]),
        dimension_label=cast(str, row["dimension_label"]),
        file_count=int(row["file_count"]),
        active_logical_bytes=int(row["active_logical_bytes"]),
        retained_inactive_bytes=int(row["retained_inactive_bytes"]),
        total_referenced_bytes=int(row["total_referenced_bytes"]),
    )


def _physical_snapshot_from_row(row: RowMapping) -> PhysicalSnapshotRow:
    return PhysicalSnapshotRow(
        source_kind=cast(str, row["source_kind"]),
        total_bytes=int(row["total_bytes"]),
        used_bytes=int(row["used_bytes"]),
        free_bytes=int(row["free_bytes"]),
        captured_at=cast(datetime, row["captured_at"]),
        collected_at=cast(datetime, row["collected_at"]),
    )


def _llm_usage_from_row(row: RowMapping) -> LlmUsageAggregate:
    cost = cast(int | None, row["estimated_cost_microunits"])
    return LlmUsageAggregate(
        dimension_key=cast(str, row["dimension_key"]),
        dimension_label=cast(str, row["dimension_label"]),
        cost_status=cast(str, row["cost_status"]),
        cost_currency=cast(str, row["cost_currency"]),
        calls=int(row["calls"]),
        prompt_tokens=int(row["prompt_tokens"]),
        completion_tokens=int(row["completion_tokens"]),
        calls_with_unknown_tokens=int(row["calls_with_unknown_tokens"]),
        estimated_cost_microunits=int(cost) if cost is not None else None,
    )


def _ragflow_usage_from_row(row: RowMapping) -> RagflowUsageAggregate:
    return RagflowUsageAggregate(
        dimension_key=cast(str, row["dimension_key"]),
        dimension_label=cast(str, row["dimension_label"]),
        calls=int(row["calls"]),
        completed_calls=int(row["completed_calls"]),
        failure_calls=int(row["failure_calls"]),
        in_progress_calls=int(row["in_progress_calls"]),
        total_latency_ms=int(row["total_latency_ms"]),
    )
