from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class ConfigModuleStatus(BaseModel):
    name: str = "config"


class ConfigItemResponse(BaseModel):
    key: str
    value: object | None = None
    value_type: str
    is_secret: bool
    masked_value: str | None = None
    description: str
    updated_at: datetime | None = None


class ConfigGroupResponse(BaseModel):
    group: str
    items: list[ConfigItemResponse]
    total: int


class ConfigUpdateRequest(BaseModel):
    items: dict[str, object]
