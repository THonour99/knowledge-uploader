from __future__ import annotations

import uuid
from datetime import datetime
from typing import Self

from pydantic import BaseModel

from .models import Notification


class NotificationModuleStatus(BaseModel):
    name: str = "notification"


class NotificationItem(BaseModel):
    id: uuid.UUID
    type: str
    title: str
    body: str
    metadata: dict[str, object]
    read_at: datetime | None
    created_at: datetime

    @classmethod
    def from_model(cls, notification: Notification) -> Self:
        return cls(
            id=notification.id,
            type=notification.type,
            title=notification.title,
            body=notification.body,
            metadata=notification.metadata_json,
            read_at=notification.read_at,
            created_at=notification.created_at,
        )


class NotificationListResponse(BaseModel):
    items: list[NotificationItem]
    total: int
    unread_count: int
    page: int
    page_size: int
