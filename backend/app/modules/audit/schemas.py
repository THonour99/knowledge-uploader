from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, model_validator

# Keys whose values must be redacted in metadata before returning to clients.
_SENSITIVE_KEY_FRAGMENTS = ("secret", "password", "token", "api_key")


def _redact_metadata(raw: dict[str, object] | None) -> dict[str, object] | None:
    """Return a copy of *raw* with sensitive key values replaced by '***'.

    A key is considered sensitive if any fragment in _SENSITIVE_KEY_FRAGMENTS
    appears as a substring of the lower-cased key name.
    """
    if raw is None:
        return None
    result: dict[str, object] = {}
    for key, value in raw.items():
        lower_key = key.lower()
        if any(frag in lower_key for frag in _SENSITIVE_KEY_FRAGMENTS):
            result[key] = "***"
        else:
            result[key] = value
    return result


class AuditModuleStatus(BaseModel):
    name: str = "audit"


class AuditLogItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    actor_id: uuid.UUID
    actor_name: str | None
    actor_email: str | None
    action: str
    target_type: str
    target_id: uuid.UUID
    ip_address: str | None
    user_agent: str | None
    reason: str | None
    metadata: dict[str, object] | None
    created_at: datetime

    @model_validator(mode="before")
    @classmethod
    def _map_fields(cls, data: object) -> object:
        """Accept both ORM-like objects and raw dicts from repository rows."""
        if isinstance(data, dict):
            raw_meta = data.get("metadata_json")
            if raw_meta is not None:
                data = dict(data)
                data["metadata"] = _redact_metadata(raw_meta)
            elif "metadata" not in data:
                data = dict(data)
                data["metadata"] = None
        return data


class AuditLogListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    items: list[AuditLogItemResponse]
    total: int
    page: int
    page_size: int
