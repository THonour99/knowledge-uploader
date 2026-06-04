from __future__ import annotations

from pydantic import BaseModel


class AuthModuleStatus(BaseModel):
    name: str = "auth"
