from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

CapacityGroupBy = Literal["none", "department", "file_type", "processing_stage", "day"]
UsageGroupBy = Literal["none", "department", "provider", "model", "day"]
RagflowGroupBy = Literal["none", "department", "operation", "result", "failure_category", "day"]
PhysicalDimension = Literal["cluster", "department", "file_type"]
PhysicalCapacityStatus = Literal["available", "stale", "unavailable", "unsupported_dimension"]
CostStatus = Literal["known", "unknown_pricing", "unknown_usage", "legacy_unverifiable"]


class MetricsWindow(BaseModel):
    start_at: datetime
    end_before: datetime
    timezone: Literal["UTC"] = "UTC"


class MetricsPagination(BaseModel):
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)
    total_pages: int = Field(ge=0)


class CapacityRow(BaseModel):
    dimension_key: str
    dimension_label: str
    file_count: str
    active_logical_bytes: str
    retained_inactive_bytes: str
    total_referenced_bytes: str


class PhysicalCapacity(BaseModel):
    status: PhysicalCapacityStatus
    requested_dimension: PhysicalDimension = "cluster"
    scope: Literal["cluster"] = "cluster"
    measurement_basis: Literal["minio_raw_cluster_capacity"] | None = None
    source_kind: Literal["minio_cluster_metrics"] | None = None
    total_bytes: str | None = None
    used_bytes: str | None = None
    free_bytes: str | None = None
    captured_at: datetime | None = None
    collected_at: datetime | None = None


class CapacityResponse(BaseModel):
    basis: Literal["database_file_rows_uploaded_in_window"] = (
        "database_file_rows_uploaded_in_window"
    )
    group_by: CapacityGroupBy
    window: MetricsWindow
    physical: PhysicalCapacity
    items: list[CapacityRow]
    pagination: MetricsPagination


class KnownCurrencyCost(BaseModel):
    currency: str
    calls: str
    prompt_tokens: str
    completion_tokens: str
    estimated_cost_microunits: str


class UnknownCostBucket(BaseModel):
    status: Literal["unknown_pricing", "unknown_usage", "legacy_unverifiable"]
    calls: str
    known_prompt_tokens: str
    known_completion_tokens: str
    calls_with_unknown_tokens: str


class LlmUsageRow(BaseModel):
    dimension_key: str
    dimension_label: str
    total_calls: str
    known_costs: list[KnownCurrencyCost]
    unknown_costs: list[UnknownCostBucket]


class LlmUsageResponse(BaseModel):
    basis: Literal["ai_usage_logs_created_in_window"] = "ai_usage_logs_created_in_window"
    group_by: UsageGroupBy
    window: MetricsWindow
    items: list[LlmUsageRow]
    pagination: MetricsPagination


class RagflowUsageRow(BaseModel):
    dimension_key: str
    dimension_label: str
    calls: str
    completed_calls: str
    failure_calls: str
    in_progress_calls: str
    total_latency_ms: str


class RagflowUsageResponse(BaseModel):
    basis: Literal["ragflow_api_calls_started_in_window"] = "ragflow_api_calls_started_in_window"
    group_by: RagflowGroupBy
    window: MetricsWindow
    items: list[RagflowUsageRow]
    pagination: MetricsPagination
