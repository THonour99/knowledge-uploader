from __future__ import annotations

from pydantic import BaseModel


class ReviewModuleStatus(BaseModel):
    name: str = "review"
