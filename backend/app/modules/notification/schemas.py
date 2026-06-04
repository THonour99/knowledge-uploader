from __future__ import annotations

from pydantic import BaseModel


class NotificationModuleStatus(BaseModel):
    name: str = "notification"
