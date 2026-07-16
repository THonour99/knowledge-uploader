from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import Notification


class NotificationModuleStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "notification"


class NotificationMetadata(BaseModel):
    """Strict, non-executable metadata used only to build allowlisted UI deep links."""

    model_config = ConfigDict(extra="forbid")

    resource_type: Literal["file", "sync_task"] | None = None
    resource_id: uuid.UUID | None = None
    status: str | None = Field(default=None, max_length=80)
    expiry_status: Literal["expiring", "expired"] | None = None
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def validate_resource_pair(self) -> Self:
        if (self.resource_type is None) != (self.resource_id is None):
            raise ValueError("resource_type and resource_id must be provided together")
        return self

    def as_storage_dict(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude_none=True)


class NotificationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    type: str
    title: str
    body: str
    metadata: dict[str, object]
    read_at: datetime | None
    created_at: datetime

    @classmethod
    def from_model(cls, notification: Notification) -> Self:
        metadata = normalize_stored_metadata(notification.metadata_json)
        return cls(
            id=notification.id,
            type=notification.type,
            title=notification.title,
            body=notification.body,
            metadata=metadata.as_storage_dict(),
            read_at=notification.read_at,
            created_at=notification.created_at,
        )


class NotificationListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[NotificationItem]
    total: int
    unread_count: int
    page: int
    page_size: int


class NotificationReadAllResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    updated_count: int


def normalize_stored_metadata(value: object) -> NotificationMetadata:
    """Fail-safe legacy rows without ever forwarding arbitrary URL/path metadata."""
    if not isinstance(value, dict):
        return NotificationMetadata()

    resource_type: Literal["file", "sync_task"] | None = None
    resource_id: uuid.UUID | None = None
    has_structured_resource = "resource_type" in value or "resource_id" in value
    raw_resource_type = value.get("resource_type")
    raw_resource_id = value.get("resource_id")
    if (
        raw_resource_type in {"file", "sync_task"}
        and (parsed_resource_id := _uuid_or_none(raw_resource_id)) is not None
    ):
        resource_type = raw_resource_type
        resource_id = parsed_resource_id
    elif not has_structured_resource:
        if (legacy_file_id := _uuid_or_none(value.get("file_id"))) is not None:
            resource_type = "file"
            resource_id = legacy_file_id
        elif (legacy_task_id := _uuid_or_none(value.get("sync_task_id"))) is not None:
            resource_type = "sync_task"
            resource_id = legacy_task_id

    raw_status = value.get("status")
    if not isinstance(raw_status, str):
        raw_status = value.get("review_status")
    status = raw_status.strip()[:80] if isinstance(raw_status, str) and raw_status.strip() else None

    raw_expiry_status = value.get("expiry_status")
    expiry_status: Literal["expiring", "expired"] | None = (
        raw_expiry_status if raw_expiry_status in {"expiring", "expired"} else None
    )
    expires_at: datetime | None = None
    raw_expires_at = value.get("expires_at")
    if isinstance(raw_expires_at, datetime):
        expires_at = raw_expires_at
    elif isinstance(raw_expires_at, str):
        try:
            parsed_expires_at = datetime.fromisoformat(raw_expires_at.replace("Z", "+00:00"))
        except ValueError:
            pass
        else:
            if parsed_expires_at.tzinfo is not None:
                expires_at = parsed_expires_at

    return NotificationMetadata(
        resource_type=resource_type,
        resource_id=resource_id,
        status=status,
        expiry_status=expiry_status,
        expires_at=expires_at,
    )


def _uuid_or_none(value: object) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    if not isinstance(value, str):
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None
