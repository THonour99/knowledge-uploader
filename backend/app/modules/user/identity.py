from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Column, MetaData, String, Table, select
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.identity import NullableDatetimeUpdate, NullableStringUpdate
from app.modules.user.models import User
from app.modules.user.schemas import AuthUserRecord

UNASSIGNED_DEPARTMENT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
UNASSIGNED_DEPARTMENT_NAME = "未分配"

_DEPARTMENTS = Table(
    "departments",
    MetaData(),
    Column("id", PG_UUID(as_uuid=True), primary_key=True),
    Column("name", String(100), nullable=False),
    Column("code", String(50), nullable=False),
)

_USER_MANAGED_DEPARTMENTS = Table(
    "user_managed_departments",
    MetaData(),
    Column("user_id", PG_UUID(as_uuid=True), primary_key=True),
    Column("department_id", PG_UUID(as_uuid=True), primary_key=True),
)


def _record(
    user: User,
    *,
    department_name: str | None,
    department_code: str | None,
    managed_department_ids: list[uuid.UUID],
) -> AuthUserRecord:
    return AuthUserRecord(
        id=user.id,
        name=user.name,
        email=user.email,
        email_domain=user.email_domain,
        password_hash=user.password_hash,
        department_id=user.department_id,
        department_name=department_name,
        department_code=department_code,
        department=user.department,
        phone=user.phone,
        role=user.role,
        status=user.status,
        email_verified=user.email_verified,
        failed_login_count=user.failed_login_count,
        locked_until=user.locked_until,
        session_version=user.session_version,
        managed_department_ids=managed_department_ids,
    )


class SqlUserIdentityStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_email(self, email: str) -> AuthUserRecord | None:
        return await self._get_record(User.email == email)

    async def get_by_id(self, user_id: uuid.UUID) -> AuthUserRecord | None:
        return await self._get_record(User.id == user_id)

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
            department_id=UNASSIGNED_DEPARTMENT_ID,
            department=UNASSIGNED_DEPARTMENT_NAME,
            phone=phone,
            role="employee",
            status=status,
            email_verified=email_verified,
            failed_login_count=0,
            session_version=0,
        )
        self._session.add(user)
        await self._session.flush()
        return _record(
            user,
            department_name=UNASSIGNED_DEPARTMENT_NAME,
            department_code="unassigned",
            managed_department_ids=[],
        )

    async def mark_email_verified(self, user_id: uuid.UUID) -> AuthUserRecord:
        user = await self._required_by_id(user_id)
        user.email_verified = True
        user.status = "active"
        await self._session.flush()
        record = await self.get_by_id(user_id)
        if record is None:
            msg = "user was not found"
            raise RuntimeError(msg)
        return record

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
        record = await self.get_by_id(user_id)
        if record is None:
            msg = "user was not found"
            raise RuntimeError(msg)
        return record

    async def _get_record(self, criterion: ColumnElement[bool]) -> AuthUserRecord | None:
        result = await self._session.execute(
            select(
                User,
                _DEPARTMENTS.c.name.label("department_name"),
                _DEPARTMENTS.c.code.label("department_code"),
            )
            .outerjoin(_DEPARTMENTS, User.department_id == _DEPARTMENTS.c.id)
            .where(criterion)
        )
        row = result.one_or_none()
        if row is None:
            return None
        managed_department_ids = await self._managed_department_ids(row[0].id)
        return _record(
            row[0],
            department_name=row.department_name,
            department_code=row.department_code,
            managed_department_ids=managed_department_ids,
        )

    async def _managed_department_ids(self, user_id: uuid.UUID) -> list[uuid.UUID]:
        result = await self._session.execute(
            select(_USER_MANAGED_DEPARTMENTS.c.department_id)
            .where(_USER_MANAGED_DEPARTMENTS.c.user_id == user_id)
            .order_by(_USER_MANAGED_DEPARTMENTS.c.department_id.asc())
        )
        return list(result.scalars())

    async def _required_by_id(self, user_id: uuid.UUID) -> User:
        user = await self._session.get(User, user_id)
        if user is None:
            msg = "user was not found"
            raise RuntimeError(msg)
        return user
