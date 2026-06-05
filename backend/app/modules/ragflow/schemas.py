from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class RagflowModuleStatus(BaseModel):
    name: str = "ragflow"


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
    logs: list[SyncTaskLogResponse] = []


class SyncTaskListResponse(BaseModel):
    items: list[SyncTaskResponse]
    total: int
