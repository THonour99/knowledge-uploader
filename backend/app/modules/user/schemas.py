from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


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


# ---------------------------------------------------------------------------
# Admin user management schemas
# ---------------------------------------------------------------------------

UserRole = Literal["employee", "knowledge_admin", "system_admin"]


class AdminUserItem(BaseModel):
    """User item returned in the admin user list."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    email: str
    role: str
    status: str
    department: str | None
    email_verified: bool
    created_at: datetime
    upload_count: int
    last_upload_at: datetime | None


class AdminUserListResponse(BaseModel):
    """Paginated response for GET /api/users."""

    items: list[AdminUserItem]
    total: int
    page: int
    page_size: int


class ChangeUserRoleRequest(BaseModel):
    """Body for PATCH /api/users/{id}/role."""

    role: UserRole


class UserListFilter(BaseModel):
    """Query parameters for GET /api/users."""

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    search: str | None = None
    role: UserRole | None = None
    status: str | None = None
