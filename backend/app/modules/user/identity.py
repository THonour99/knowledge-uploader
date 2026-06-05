from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.identity import NullableDatetimeUpdate, NullableStringUpdate
from app.modules.user.models import User
from app.modules.user.schemas import AuthUserRecord


def _record(user: User) -> AuthUserRecord:
    return AuthUserRecord(
        id=user.id,
        name=user.name,
        email=user.email,
        email_domain=user.email_domain,
        password_hash=user.password_hash,
        department=user.department,
        phone=user.phone,
        role=user.role,
        status=user.status,
        email_verified=user.email_verified,
        failed_login_count=user.failed_login_count,
        locked_until=user.locked_until,
        session_version=user.session_version,
    )


class SqlUserIdentityStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_email(self, email: str) -> AuthUserRecord | None:
        result = await self._session.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        return _record(user) if user is not None else None

    async def get_by_id(self, user_id: uuid.UUID) -> AuthUserRecord | None:
        user = await self._session.get(User, user_id)
        return _record(user) if user is not None else None

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
        user = User(
            name=name,
            email=email,
            email_domain=email_domain,
            password_hash=password_hash,
            department=department,
            phone=phone,
            role="employee",
            status=status,
            email_verified=email_verified,
            failed_login_count=0,
            session_version=0,
        )
        self._session.add(user)
        await self._session.flush()
        return _record(user)

    async def mark_email_verified(self, user_id: uuid.UUID) -> AuthUserRecord:
        user = await self._required_by_id(user_id)
        user.email_verified = True
        user.status = "active"
        await self._session.flush()
        return _record(user)

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
        user = await self._required_by_id(user_id)
        if password_hash is not None:
            user.password_hash = password_hash
        if failed_login_count is not None:
            user.failed_login_count = failed_login_count
        if locked_until is not None:
            user.locked_until = locked_until if isinstance(locked_until, datetime) else None
        if status is not None:
            user.status = status
        if last_login_at is not None:
            user.last_login_at = last_login_at if isinstance(last_login_at, datetime) else None
        if last_login_ip is not None:
            user.last_login_ip = last_login_ip if isinstance(last_login_ip, str) else None
        if increment_session_version:
            user.session_version += 1
        await self._session.flush()
        return _record(user)

    async def _required_by_id(self, user_id: uuid.UUID) -> User:
        user = await self._session.get(User, user_id)
        if user is None:
            msg = "user was not found"
            raise RuntimeError(msg)
        return user
