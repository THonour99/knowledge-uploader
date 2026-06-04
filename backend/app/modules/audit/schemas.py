from __future__ import annotations

from pydantic import BaseModel


class AuditModuleStatus(BaseModel):
    name: str = "audit"
