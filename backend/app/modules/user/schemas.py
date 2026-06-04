from __future__ import annotations

from pydantic import BaseModel


class UserModuleStatus(BaseModel):
    name: str = "user"
