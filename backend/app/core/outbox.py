from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class OutboxMessage:
    id: UUID
    routing_key: str
    payload: dict[str, object]
    created_at: datetime
