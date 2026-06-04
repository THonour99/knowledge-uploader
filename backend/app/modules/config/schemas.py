from __future__ import annotations

from pydantic import BaseModel


class ConfigModuleStatus(BaseModel):
    name: str = "config"
