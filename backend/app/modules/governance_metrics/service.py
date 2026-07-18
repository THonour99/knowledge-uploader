from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import ceil
from typing import Literal, cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_admin_audit_log
from app.core.ragflow_metrics_contract import RAGFLOW_PERSISTED_RESULTS
from app.modules.user.schemas import AuthUserRecord

from . import exceptions
from .permissions import SYSTEM_ADMIN_ROLE
from .repository import (
    CapacityAggregate,
    GovernanceMetricsRepository,
    LlmUsageAggregate,
    MetricsRange,
    PhysicalSnapshotRow,
    RagflowUsageAggregate,
)
from .schemas import (
    CapacityGroupBy,
    CapacityResponse,
    CapacityRow,
    KnownCurrencyCost,
    LlmUsageResponse,
    LlmUsageRow,
    MetricsPagination,
    MetricsWindow,
    PhysicalCapacity,
    PhysicalDimension,
    RagflowGroupBy,
    RagflowUsageResponse,
    RagflowUsageRow,
    UnknownCostBucket,
    UsageGroupBy,
)

MAX_METRICS_WINDOW = timedelta(days=366)
DEFAULT_METRICS_WINDOW = timedelta(days=30)
PHYSICAL_SNAPSHOT_FRESHNESS = timedelta(minutes=15)
PHYSICAL_SNAPSHOT_MAX_CLOCK_SKEW = timedelta(minutes=1)
_KNOWN_COST_STATUS = "known"
_UNKNOWN_COST_STATUSES = ("unknown_pricing", "unknown_usage", "legacy_unverifiable")
_CAPACITY_DIMENSION_LABELS: dict[str, dict[str, str]] = {
    "processing_stage": {
        "draft": "草稿",
        "ai": "AI 处理",
        "review": "审核",
        "sync": "RAGFlow 同步",
        "available": "已可用",
        "failed": "失败",
        "archived": "已归档",
        "unknown": "未知阶段",
    }
}
_RAGFLOW_DIMENSION_LABELS: dict[str, dict[str, str]] = {
    "operation": {
        "ping": "连接检查",
        "upload_document": "上传文档",
        "find_document_by_name": "查找同名文档",
        "update_document_metadata": "更新文档元数据",
        "start_parse": "启动解析",
        "get_document_status": "查询文档状态",
        "delete_document": "删除文档",
        "other": "其他操作",
    },
    "result": {
        "started": "进行中",
        "success": "成功",
        "failure": "失败",
    },
    "failure_category": {
        "none": "无失败",
        "authentication": "认证失败",
        "authorization": "授权失败",
        "configuration": "配置错误",
        "conflict": "冲突",
        "network": "网络错误",
        "not_found": "未找到",
        "protocol": "协议错误",
        "rate_limited": "触发限流",
        "timeout": "超时",
        "unknown": "未知失败",
        "upstream_5xx": "上游服务错误",
    },
}


@dataclass(frozen=True)
class RequestContext:
    ip_address: str
    user_agent: str


@dataclass(frozen=True)
class MetricsQuery:
    start_at: datetime | None = None
    end_before: datetime | None = None
    page: int = 1
    page_size: int = 20


class GovernanceMetricsService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        repository: GovernanceMetricsRepository,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._repository = repository
        self._now_provider = now_provider or (lambda: datetime.now(UTC))

    async def capacity(
        self,
        *,
        current_user: AuthUserRecord,
        query: MetricsQuery,
        group_by: CapacityGroupBy,
        physical_dimension: PhysicalDimension,
        context: RequestContext,
    ) -> CapacityResponse:
        self._require_system_admin(current_user)
        metrics_range = self._validated_range(query)
        rows, total = await self._repository.capacity(
            metrics_range=metrics_range,
            group_by=group_by,
            page=query.page,
            page_size=query.page_size,
        )
        snapshot = await self._repository.latest_physical_snapshot()
        response = CapacityResponse(
            group_by=group_by,
            window=_window(metrics_range),
            physical=_physical_capacity(
                snapshot,
                physical_dimension=physical_dimension,
                now=self._now_provider(),
            ),
            items=[_capacity_row(row, group_by=group_by) for row in rows],
            pagination=_pagination(
                total=total,
                page=query.page,
                page_size=query.page_size,
            ),
        )
        await self._audit(
            current_user=current_user,
            action="statistics.capacity.view",
            context=context,
            metadata={
                **_audit_metadata(metrics_range, group_by, query),
                "physical_dimension": physical_dimension,
            },
        )
        return response

    async def llm_usage(
        self,
        *,
        current_user: AuthUserRecord,
        query: MetricsQuery,
        group_by: UsageGroupBy,
        context: RequestContext,
    ) -> LlmUsageResponse:
        self._require_system_admin(current_user)
        metrics_range = self._validated_range(query)
        aggregates, total = await self._repository.llm_usage(
            metrics_range=metrics_range,
            group_by=group_by,
            page=query.page,
            page_size=query.page_size,
        )
        response = LlmUsageResponse(
            group_by=group_by,
            window=_window(metrics_range),
            items=_build_llm_rows(aggregates),
            pagination=_pagination(
                total=total,
                page=query.page,
                page_size=query.page_size,
            ),
        )
        await self._audit(
            current_user=current_user,
            action="statistics.llm_usage.view",
            context=context,
            metadata=_audit_metadata(metrics_range, group_by, query),
        )
        return response

    async def ragflow_usage(
        self,
        *,
        current_user: AuthUserRecord,
        query: MetricsQuery,
        group_by: RagflowGroupBy,
        context: RequestContext,
    ) -> RagflowUsageResponse:
        self._require_system_admin(current_user)
        metrics_range = self._validated_range(query)
        rows, total = await self._repository.ragflow_usage(
            metrics_range=metrics_range,
            group_by=group_by,
            page=query.page,
            page_size=query.page_size,
        )
        response = RagflowUsageResponse(
            group_by=group_by,
            window=_window(metrics_range),
            items=[_ragflow_row(row, group_by=group_by) for row in rows],
            pagination=_pagination(
                total=total,
                page=query.page,
                page_size=query.page_size,
            ),
        )
        await self._audit(
            current_user=current_user,
            action="statistics.ragflow_usage.view",
            context=context,
            metadata=_audit_metadata(metrics_range, group_by, query),
        )
        return response

    def _validated_range(self, query: MetricsQuery) -> MetricsRange:
        now = _as_utc(self._now_provider(), field_name="current time")
        end_before = _as_utc(query.end_before, field_name="end_before") if query.end_before else now
        start_at = (
            _as_utc(query.start_at, field_name="start_at")
            if query.start_at
            else end_before - DEFAULT_METRICS_WINDOW
        )
        if start_at >= end_before:
            raise exceptions.invalid_query("start_at must be before end_before")
        if end_before - start_at > MAX_METRICS_WINDOW:
            raise exceptions.invalid_query("metrics time range cannot exceed 366 days")
        if query.page < 1 or query.page_size < 1 or query.page_size > 100:
            raise exceptions.invalid_query("invalid pagination")
        return MetricsRange(start_at=start_at, end_before=end_before)

    def _require_system_admin(self, current_user: AuthUserRecord) -> None:
        if current_user.role != SYSTEM_ADMIN_ROLE:
            raise exceptions.permission_denied()

    async def _audit(
        self,
        *,
        current_user: AuthUserRecord,
        action: str,
        context: RequestContext,
        metadata: dict[str, object],
    ) -> None:
        try:
            await record_admin_audit_log(
                self._session,
                actor_id=current_user.id,
                action=action,
                target_type="statistics",
                target_id=current_user.id,
                ip_address=context.ip_address,
                user_agent=context.user_agent,
                metadata_json=metadata,
            )
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            raise


def _as_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise exceptions.invalid_query(f"{field_name} must include a timezone")
    return value.astimezone(UTC)


def _window(metrics_range: MetricsRange) -> MetricsWindow:
    return MetricsWindow(start_at=metrics_range.start_at, end_before=metrics_range.end_before)


def _pagination(*, total: int, page: int, page_size: int) -> MetricsPagination:
    return MetricsPagination(
        page=page,
        page_size=page_size,
        total=total,
        total_pages=ceil(total / page_size) if total else 0,
    )


def _capacity_row(
    row: CapacityAggregate,
    *,
    group_by: CapacityGroupBy = "none",
) -> CapacityRow:
    return CapacityRow(
        dimension_key=row.dimension_key,
        dimension_label=_localized_dimension_label(
            group_by=group_by,
            dimension_key=row.dimension_key,
            fallback=row.dimension_label,
            mappings=_CAPACITY_DIMENSION_LABELS,
        ),
        file_count=str(row.file_count),
        active_logical_bytes=str(row.active_logical_bytes),
        retained_inactive_bytes=str(row.retained_inactive_bytes),
        total_referenced_bytes=str(row.total_referenced_bytes),
    )


def _physical_capacity(
    snapshot: PhysicalSnapshotRow | None,
    *,
    physical_dimension: PhysicalDimension,
    now: datetime,
) -> PhysicalCapacity:
    if physical_dimension != "cluster":
        return PhysicalCapacity(
            status="unsupported_dimension",
            requested_dimension=physical_dimension,
        )
    if snapshot is None:
        return PhysicalCapacity(
            status="unavailable",
            requested_dimension=physical_dimension,
        )
    captured_at = _as_utc(snapshot.captured_at, field_name="snapshot captured_at")
    collected_at = _as_utc(snapshot.collected_at, field_name="snapshot collected_at")
    current_time = _as_utc(now, field_name="current time")
    if (
        captured_at > current_time + PHYSICAL_SNAPSHOT_MAX_CLOCK_SKEW
        or collected_at > current_time + PHYSICAL_SNAPSHOT_MAX_CLOCK_SKEW
    ):
        return PhysicalCapacity(
            status="unavailable",
            requested_dimension=physical_dimension,
        )
    status = "stale" if current_time - captured_at > PHYSICAL_SNAPSHOT_FRESHNESS else "available"
    return PhysicalCapacity(
        status=status,
        requested_dimension=physical_dimension,
        measurement_basis="minio_raw_cluster_capacity",
        source_kind="minio_cluster_metrics",
        total_bytes=str(snapshot.total_bytes),
        used_bytes=str(snapshot.used_bytes),
        free_bytes=str(snapshot.free_bytes),
        captured_at=captured_at,
        collected_at=collected_at,
    )


def _build_llm_rows(aggregates: list[LlmUsageAggregate]) -> list[LlmUsageRow]:
    grouped: dict[tuple[str, str], list[LlmUsageAggregate]] = defaultdict(list)
    for aggregate in aggregates:
        grouped[(aggregate.dimension_key, aggregate.dimension_label)].append(aggregate)

    rows: list[LlmUsageRow] = []
    for (dimension_key, dimension_label), values in sorted(grouped.items()):
        known_by_currency: dict[str, list[LlmUsageAggregate]] = defaultdict(list)
        unknown_by_status: dict[str, list[LlmUsageAggregate]] = defaultdict(list)
        for value in values:
            if value.cost_status == _KNOWN_COST_STATUS:
                if value.estimated_cost_microunits is None:
                    raise RuntimeError("known LLM cost cannot be null")
                known_by_currency[value.cost_currency].append(value)
            elif value.cost_status in _UNKNOWN_COST_STATUSES:
                if value.estimated_cost_microunits is not None:
                    raise RuntimeError("unknown LLM cost must be null")
                unknown_by_status[value.cost_status].append(value)
            else:
                raise RuntimeError("unsupported persisted LLM cost status")
        known_costs = [
            KnownCurrencyCost(
                currency=currency,
                calls=str(sum(item.calls for item in currency_rows)),
                prompt_tokens=str(sum(item.prompt_tokens for item in currency_rows)),
                completion_tokens=str(sum(item.completion_tokens for item in currency_rows)),
                estimated_cost_microunits=str(
                    sum(item.estimated_cost_microunits or 0 for item in currency_rows)
                ),
            )
            for currency, currency_rows in sorted(known_by_currency.items())
        ]
        unknown_costs = [
            UnknownCostBucket(
                status=cast(
                    Literal["unknown_pricing", "unknown_usage", "legacy_unverifiable"],
                    cost_status,
                ),
                calls=str(sum(item.calls for item in status_rows)),
                known_prompt_tokens=str(sum(item.prompt_tokens for item in status_rows)),
                known_completion_tokens=str(sum(item.completion_tokens for item in status_rows)),
                calls_with_unknown_tokens=str(
                    sum(item.calls_with_unknown_tokens for item in status_rows)
                ),
            )
            for cost_status, status_rows in sorted(unknown_by_status.items())
        ]
        rows.append(
            LlmUsageRow(
                dimension_key=dimension_key,
                dimension_label=dimension_label,
                total_calls=str(sum(value.calls for value in values)),
                known_costs=known_costs,
                unknown_costs=unknown_costs,
            )
        )
    return rows


def _ragflow_row(
    row: RagflowUsageAggregate,
    *,
    group_by: RagflowGroupBy = "none",
) -> RagflowUsageRow:
    _validate_ragflow_aggregate(row, group_by=group_by)
    return RagflowUsageRow(
        dimension_key=row.dimension_key,
        dimension_label=_localized_dimension_label(
            group_by=group_by,
            dimension_key=row.dimension_key,
            fallback=row.dimension_label,
            mappings=_RAGFLOW_DIMENSION_LABELS,
        ),
        calls=str(row.calls),
        completed_calls=str(row.completed_calls),
        failure_calls=str(row.failure_calls),
        in_progress_calls=str(row.in_progress_calls),
        total_latency_ms=str(row.total_latency_ms),
    )


def _validate_ragflow_aggregate(
    row: RagflowUsageAggregate,
    *,
    group_by: RagflowGroupBy,
) -> None:
    counters = (
        row.calls,
        row.completed_calls,
        row.failure_calls,
        row.in_progress_calls,
        row.total_latency_ms,
    )
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counters
    ):
        raise exceptions.aggregate_invariant_violation()
    if not isinstance(row.dimension_key, str) or not row.dimension_key:
        raise exceptions.aggregate_invariant_violation()
    if not isinstance(row.dimension_label, str) or not row.dimension_label:
        raise exceptions.aggregate_invariant_violation()
    if row.calls != row.completed_calls + row.in_progress_calls:
        raise exceptions.aggregate_invariant_violation()
    if row.failure_calls > row.completed_calls:
        raise exceptions.aggregate_invariant_violation()
    if group_by != "result":
        return
    if row.dimension_key not in RAGFLOW_PERSISTED_RESULTS:
        raise exceptions.aggregate_invariant_violation()
    expected = {
        "started": (0, 0, row.calls),
        "success": (row.calls, 0, 0),
        "failure": (row.calls, row.calls, 0),
    }[row.dimension_key]
    actual = (
        row.completed_calls,
        row.failure_calls,
        row.in_progress_calls,
    )
    if actual != expected:
        raise exceptions.aggregate_invariant_violation()


def _localized_dimension_label(
    *,
    group_by: str,
    dimension_key: str,
    fallback: str,
    mappings: dict[str, dict[str, str]],
) -> str:
    return mappings.get(group_by, {}).get(dimension_key, fallback)


def _audit_metadata(
    metrics_range: MetricsRange,
    group_by: str,
    query: MetricsQuery,
) -> dict[str, object]:
    return {
        "start_at": metrics_range.start_at.isoformat(),
        "end_before": metrics_range.end_before.isoformat(),
        "group_by": group_by,
        "page": query.page,
        "page_size": query.page_size,
    }
