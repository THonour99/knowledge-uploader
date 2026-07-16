from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class ConfigModuleStatus(BaseModel):
    name: str = "config"


class ConfigItemResponse(BaseModel):
    key: str
    value: object | None = None
    value_type: str
    is_secret: bool
    masked_value: str | None = None
    description: str
    immutable: bool = False
    updated_at: datetime | None = None


class ConfigGroupResponse(BaseModel):
    group: str
    items: list[ConfigItemResponse]
    total: int


class ConfigUpdateRequest(BaseModel):
    items: dict[str, object]


class DeadLetterItemResponse(BaseModel):
    id: UUID
    event_id: int
    event_type: str
    aggregate_type: str
    aggregate_id: str
    status: Literal["pending", "requeued", "resolved"] = Field(
        description="requeued 仅表示已重新入队 resolved 才表示发布成功",
    )
    first_failed_at: datetime
    last_failed_at: datetime
    attempts: int
    error_type: str
    correlation_id: str
    trace_id: str | None
    payload_summary: dict[str, object]
    replay_count: int
    last_replayed_at: datetime | None
    resolved_at: datetime | None


class DeadLetterListResponse(BaseModel):
    items: list[DeadLetterItemResponse]
    total: int
    page: int
    page_size: int


class DeadLetterReplayRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 3:
            raise ValueError("reason must contain at least 3 non-whitespace characters")
        return cleaned


class DeadLetterReplayResponse(BaseModel):
    item: DeadLetterItemResponse
    replay_queued: bool


class RabbitDeadLetterReplayRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 3:
            raise ValueError("reason must contain at least 3 non-whitespace characters")
        return cleaned


class RabbitDeadLetterReplayResponse(BaseModel):
    queue_name: Literal[
        "document_queue",
        "ai_queue",
        "ragflow_queue",
        "notification_queue",
    ]
    task_name: str
    original_task_id: UUID
    replay_task_id: UUID
    target_id: UUID
    audit_log_id: UUID
    replay_queued: bool
    raw_payload_copied: Literal[False] = False
    replay_policy: Literal["clean_room_allowlist_only"] = "clean_room_allowlist_only"
