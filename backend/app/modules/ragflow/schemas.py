from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class RagflowModuleStatus(BaseModel):
    name: str = "ragflow"


class ManualSyncRequest(BaseModel):
    dataset_mapping_id: UUID
    reason: str | None = Field(default=None, max_length=1000)


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


class SyncTaskListResponse(BaseModel):
    items: list[SyncTaskResponse]
    total: int
