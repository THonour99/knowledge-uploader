from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PageKey = Literal["my_files", "review_files", "task_logs", "statistics"]
SavedViewScope = Literal["private", "department"]
Compatibility = Literal["current", "migrated", "unsupported"]
SortOrder = Literal["asc", "desc"]


class SavedViewCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_key: PageKey
    name: str = Field(min_length=1, max_length=80)
    scope: SavedViewScope = "private"
    department_id: UUID | None = None
    definition_schema_version: int = Field(ge=1, le=32767)
    query_definition: dict[str, object] = Field(default_factory=dict)
    column_preferences: dict[str, object] = Field(default_factory=dict)


class SavedViewUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=80)
    definition_schema_version: int | None = Field(default=None, ge=1, le=32767)
    query_definition: dict[str, object] | None = None
    column_preferences: dict[str, object] | None = None

    @model_validator(mode="after")
    def require_change(self) -> SavedViewUpdateRequest:
        if self.model_fields_set == {"row_version"}:
            raise ValueError("at least one editable field is required")
        return self


class EffectiveDefinition(BaseModel):
    query_definition: dict[str, object]
    column_preferences: dict[str, object]


class SavedViewItem(BaseModel):
    id: UUID
    owner_id: UUID
    scope: SavedViewScope
    department_id: UUID | None
    page_key: PageKey
    name: str
    stored_schema_version: int
    effective_schema_version: int | None
    compatibility: Compatibility
    effective_definition: EffectiveDefinition | None
    row_version: int
    created_at: datetime
    updated_at: datetime


class SavedViewQuotaPolicy(BaseModel):
    private_per_owner_page: int = Field(ge=1)
    department_per_department_page: int = Field(ge=1)


class SavedViewListResponse(BaseModel):
    items: list[SavedViewItem]
    total: int
    page: int
    page_size: int
    total_pages: int
    quota: SavedViewQuotaPolicy


class ColumnPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    visible: list[str] = Field(default_factory=list, max_length=32)
    order: list[str] = Field(default_factory=list, max_length=32)
    widths: dict[str, Annotated[int, Field(ge=80, le=800)]] = Field(default_factory=dict)

    @field_validator("visible", "order")
    @classmethod
    def reject_duplicate_columns(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("column identifiers must be unique")
        return value


class MyFilesQueryDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    q: str | None = Field(default=None, max_length=200)
    status: (
        Literal[
            "uploaded",
            "extracting_text",
            "analysis_queued",
            "analyzing",
            "analysis_failed",
            "analyzed",
            "pending_review",
            "sensitive_review_required",
            "approved",
            "rejected",
            "queued",
            "syncing",
            "uploaded_to_ragflow",
            "parsing",
            "parsed",
            "failed",
            "disabled",
        ]
        | None
    ) = None
    extension: str | None = Field(
        default=None, min_length=1, max_length=20, pattern=r"^[A-Za-z0-9]+$"
    )
    relationship: Literal["uploaded", "responsible"] = "uploaded"
    tag_id: UUID | None = None
    expiry_status: Literal["never", "active", "expiring", "expired"] | None = None
    sort: Literal["uploaded_at", "updated_at", "original_name", "title", "size", "status"] = (
        "uploaded_at"
    )
    order: SortOrder = "desc"
    page_size: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def reject_unsupported_responsible_filters(self) -> MyFilesQueryDefinition:
        if self.relationship == "responsible" and self.tag_id is not None:
            raise ValueError("tag_id is not supported for responsible documents")
        return self


class ReviewFilesQueryDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    q: str | None = Field(default=None, max_length=200)
    queue: Literal["unclaimed", "mine", "due_soon", "overdue"] | None = None
    extension: str | None = Field(
        default=None, min_length=1, max_length=20, pattern=r"^[A-Za-z0-9]+$"
    )
    tag_id: UUID | None = None
    department_id: UUID | None = None
    sensitive_risk_level: Literal["none", "low", "medium", "high", "critical"] | None = None
    sort: (
        Literal["submitted_at", "review_due_at", "uploaded_at", "original_name", "risk"] | None
    ) = None
    order: SortOrder = "asc"
    page_size: int = Field(default=20, ge=1, le=100)


class TaskLogsQueryDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_type: (
        Literal[
            "ragflow_upload",
            "ragflow_parse",
            "ragflow_status_check",
            "ragflow_delete",
        ]
        | None
    ) = None
    status: Literal["queued", "running", "succeeded", "failed", "canceled"] | None = None
    file_id: UUID | None = None
    department_id: UUID | None = None
    sort: Literal["created_at", "updated_at", "started_at", "finished_at"] = "created_at"
    order: SortOrder = "desc"
    page_size: int = Field(default=20, ge=1, le=100)


class StatisticsQueryDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_date: date | None = None
    end_date: date | None = None
    department: str | None = Field(default=None, max_length=100)
    user_q: str | None = Field(default=None, max_length=100)
    user_id: UUID | None = None
    category_id: UUID | None = None
    status: str | None = Field(default=None, max_length=40)
    review_status: Literal["pending", "in_review", "approved", "rejected"] | None = None
    sync_status: Literal["synced", "failed", "syncing", "not_synced"] | None = None
    group_by: Literal["day", "week", "month"] = "day"
    page_size: int = Field(default=20, ge=1, le=100)
    sort_by: Literal[
        "total_files",
        "synced_files",
        "failed_files",
        "pending_review_files",
        "total_file_size",
        "last_upload_at",
    ] = "total_files"
    sort_order: SortOrder = "desc"

    @model_validator(mode="after")
    def dates_in_order(self) -> StatisticsQueryDefinition:
        if self.start_date is not None and self.end_date is not None:
            if self.start_date > self.end_date:
                raise ValueError("start_date must not be after end_date")
        return self
