from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class UserProfile(BaseModel):
    id: UUID
    name: str
    email: str
    role: str
    status: str
    email_verified: bool
    department: str | None
    phone: str | None


class AuthUserRecord(UserProfile):
    email_domain: str
    password_hash: str
    failed_login_count: int
    locked_until: datetime | None
    session_version: int


class UpdateUserRequest(BaseModel):
    name: str | None = None
    department: str | None = None
    phone: str | None = None
    role: str | None = None
