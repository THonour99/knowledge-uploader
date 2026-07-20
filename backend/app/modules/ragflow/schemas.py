from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class RagflowModuleStatus(BaseModel):
    name: str = "ragflow"


class ManualSyncRequest(BaseModel):
    dataset_mapping_id: UUID
    reason: str | None = Field(default=None, max_length=1000)


class RagflowDatasetDiscoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str | None = Field(default=None, max_length=500)
    api_key: str | None = None


class RagflowDatasetOptionResponse(BaseModel):
    dataset_id: str
    name: str


class RagflowDatasetDiscoveryResponse(BaseModel):
    ok: bool
    items: list[RagflowDatasetOptionResponse] = Field(default_factory=list)
    error: str | None = None


class VersionSwitchReconcileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=1000),
    ]


class SyncTaskLogResponse(BaseModel):
    id: int
    task_id: UUID
    status: str
    message: str
    created_at: datetime


class SyncTaskResponse(BaseModel):
    id: UUID
    file_id: UUID
    task_type: str
    status: str
    retry_count: int
    max_retry_count: int
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime
    logs: list[SyncTaskLogResponse] = Field(default_factory=list)


class SyncTaskStatusCountsResponse(BaseModel):
    queued: int = Field(ge=0)
    running: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
    canceled: int = Field(ge=0)


class SyncTaskListResponse(BaseModel):
    items: list[SyncTaskResponse]
    total: int
    status_counts: SyncTaskStatusCountsResponse
    page: int
    page_size: int
    total_pages: int
