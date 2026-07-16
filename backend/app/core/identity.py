from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.user.schemas import AuthUserRecord


class _NullValue:
    pass


NULL_VALUE = _NullValue()
NullableDatetimeUpdate = datetime | None | _NullValue
NullableStringUpdate = str | None | _NullValue
UNASSIGNED_DEPARTMENT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


@dataclass(frozen=True)
class RegistrationDepartment:
    """A public, active department that may be selected during registration."""

    id: uuid.UUID
    name: str
    code: str


def has_assigned_department(user: AuthUserRecord) -> bool:
    """Return whether a user belongs to a real department rather than the sentinel."""

    return user.department_id != UNASSIGNED_DEPARTMENT_ID and user.department_code is not None


class UserIdentityStore(Protocol):
    async def get_by_email(self, email: str) -> AuthUserRecord | None: ...

    async def get_by_id(self, user_id: uuid.UUID) -> AuthUserRecord | None: ...

    async def get_registration_department(
        self,
        department_id: uuid.UUID,
    ) -> RegistrationDepartment | None: ...

    async def list_registration_departments(self) -> list[RegistrationDepartment]: ...

    async def create_user(
        self,
        *,
        name: str,
        email: str,
        email_domain: str,
        password_hash: str,
        department: RegistrationDepartment | None,
        phone: str | None,
        status: str,
        email_verified: bool,
    ) -> AuthUserRecord: ...

    async def mark_email_verified(self, user_id: uuid.UUID) -> AuthUserRecord: ...

    async def record_verification_state(
        self,
        *,
        user_id: uuid.UUID,
        password_hash: str | None = None,
        failed_login_count: int | None = None,
        locked_until: NullableDatetimeUpdate = None,
        status: str | None = None,
        last_login_at: NullableDatetimeUpdate = None,
        last_login_ip: NullableStringUpdate = None,
        increment_session_version: bool = False,
    ) -> AuthUserRecord: ...


def get_user_identity_store(session: AsyncSession) -> UserIdentityStore:
    """Return the configured user identity store through the core protocol boundary."""
    from app.modules.user.identity import SqlUserIdentityStore

    return SqlUserIdentityStore(session)
