from __future__ import annotations

from pydantic import BaseModel


class RagflowModuleStatus(BaseModel):
    name: str = "ragflow"
