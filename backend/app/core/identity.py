from __future__ import annotations

import uuid
from datetime import datetime
from typing import Protocol

from app.modules.user.schemas import AuthUserRecord


class _NullValue:
    pass


NULL_VALUE = _NullValue()
NullableDatetimeUpdate = datetime | None | _NullValue
NullableStringUpdate = str | None | _NullValue


class UserIdentityStore(Protocol):
    async def get_by_email(self, email: str) -> AuthUserRecord | None:
        ...

    async def get_by_id(self, user_id: uuid.UUID) -> AuthUserRecord | None:
        ...

    async def create_user(
        self,
        *,
        name: str,
        email: str,
        email_domain: str,
        password_hash: str,
        department: str | None,
        phone: str | None,
        status: str,
        email_verified: bool,
    ) -> AuthUserRecord:
        ...

    async def mark_email_verified(self, user_id: uuid.UUID) -> AuthUserRecord:
        ...

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
    ) -> AuthUserRecord:
        ...
