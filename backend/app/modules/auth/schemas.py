from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class AuthModuleStatus(BaseModel):
    name: str = "auth"


class LoginRequest(BaseModel):
    email: str
    password: str
    remember_me: bool


class MockCurrentUser(BaseModel):
    id: str = "phase0-user"
    name: str = "Phase 0 Mock User"
    email: str
    role: Literal["employee", "knowledge_admin", "system_admin"] = "system_admin"


class LoginResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    user: MockCurrentUser
