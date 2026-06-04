from __future__ import annotations

from pydantic import BaseModel


class AiModuleStatus(BaseModel):
    name: str = "ai"
