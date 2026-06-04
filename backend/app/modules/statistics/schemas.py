from __future__ import annotations

from pydantic import BaseModel


class StatisticsModuleStatus(BaseModel):
    name: str = "statistics"
