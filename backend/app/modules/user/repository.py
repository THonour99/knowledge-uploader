from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import Column, DateTime, MetaData, String, Table, delete, func, select
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.user.models import User

# ---------------------------------------------------------------------------
# Shadow tables for read-only cross-module data. Do not import repositories or
# services from other modules.
# ---------------------------------------------------------------------------

_FILES = Table(
    "files",
    MetaData(),
    Column("id", PG_UUID(as_uuid=True), primary_key=True),
    Column("uploader_id", PG_UUID(as_uuid=True), nullable=False),
    Column("uploaded_at", DateTime(timezone=True), nullable=False),
)

_DEPARTMENTS = Table(
    "departments",
    MetaData(),
    Column("id", PG_UUID(as_uuid=True), primary_key=True),
    Column("name", String(100), nullable=False),
    Column("code", String(50), nullable=False),
    Column("status", String(20), nullable=False),
)

_USER_MANAGED_DEPARTMENTS = Table(
    "user_managed_departments",
    MetaData(),
    Column("user_id", PG_UUID(as_uuid=True), primary_key=True),
    Column("department_id", PG_UUID(as_uuid=True), primary_key=True),
)


@dataclass(frozen=True)
class DepartmentInfo:
    id: uuid.UUID
    name: str
    code: str
    status: str


@dataclass(frozen=True)
class UserWithStats:
    """User ORM instance together with upload statistics."""

    user: User
    department_name: str | None
    department_code: str | None
    upload_count: int
    last_upload_at: datetime | None


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        result = await self._session.execute(
            select(
                User,
                _DEPARTMENTS.c.name.label("department_name"),
                _DEPARTMENTS.c.code.label("department_code"),
            )
            .outerjoin(_DEPARTMENTS, User.department_id == _DEPARTMENTS.c.id)
            .where(User.id == user_id)
        )
        row = result.one_or_none()
        if row is None:
            return None
        user = cast(User, row[0])
        _attach_department(user, row.department_name, row.department_code)
        return user

    async def list_users(self) -> list[User]:
        result = await self._session.execute(
            select(
                User,
                _DEPARTMENTS.c.name.label("department_name"),
                _DEPARTMENTS.c.code.label("department_code"),
            )
            .outerjoin(_DEPARTMENTS, User.department_id == _DEPARTMENTS.c.id)
            .order_by(User.created_at.desc())
        )
        users: list[User] = []
        for row in result:
            user = cast(User, row[0])
            _attach_department(user, row.department_name, row.department_code)
            users.append(user)
        return users

    async def count_active_system_admins(self) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(User)
            .where(
                User.role == "system_admin",
                User.status == "active",
            )
        )
        return int(result.scalar_one())

    async def get_active_department_info(self, department_id: uuid.UUID) -> DepartmentInfo | None:
        result = await self._session.execute(
            select(
                _DEPARTMENTS.c.id,
                _DEPARTMENTS.c.name,
                _DEPARTMENTS.c.code,
                _DEPARTMENTS.c.status,
            ).where(
                _DEPARTMENTS.c.id == department_id,
                _DEPARTMENTS.c.status == "active",
            )
        )
        row = result.mappings().one_or_none()
        if row is None:
            return None
        return DepartmentInfo(
            id=row["id"],
            name=row["name"],
            code=row["code"],
            status=row["status"],
        )

    async def set_user_department(self, user: User, department: DepartmentInfo) -> None:
        user.department_id = department.id
        user.department = department.name
        _attach_department(user, department.name, department.code)
        await self._session.flush()

    async def clear_managed_departments(self, user_id: uuid.UUID) -> list[uuid.UUID]:
        result = await self._session.execute(
            select(_USER_MANAGED_DEPARTMENTS.c.department_id).where(
                _USER_MANAGED_DEPARTMENTS.c.user_id == user_id
            )
        )
        existing = list(result.scalars())
        if existing:
            await self._session.execute(
                delete(_USER_MANAGED_DEPARTMENTS).where(
                    _USER_MANAGED_DEPARTMENTS.c.user_id == user_id
                )
            )
            await self._session.flush()
        return existing

    # ------------------------------------------------------------------
    # Paginated + filtered list with upload statistics
    # ------------------------------------------------------------------

    async def list_users_with_stats(
        self,
        *,
        page: int,
        page_size: int,
        search: str | None = None,
        role: str | None = None,
        status: str | None = None,
    ) -> tuple[list[UserWithStats], int]:
        """Return (rows, total_count) with per-user upload statistics."""
        users_table = User.__table__

        stats_sq = (
            select(
                _FILES.c.uploader_id.label("uploader_id"),
                func.count(_FILES.c.id).label("upload_count"),
                func.max(_FILES.c.uploaded_at).label("last_upload_at"),
            )
            .group_by(_FILES.c.uploader_id)
            .subquery("file_stats")
        )

        base = select(
            users_table,
            _DEPARTMENTS.c.name.label("department_name"),
            _DEPARTMENTS.c.code.label("department_code"),
            func.coalesce(stats_sq.c.upload_count, 0).label("upload_count"),
            stats_sq.c.last_upload_at.label("last_upload_at"),
        ).select_from(
            users_table.outerjoin(stats_sq, users_table.c.id == stats_sq.c.uploader_id).outerjoin(
                _DEPARTMENTS, users_table.c.department_id == _DEPARTMENTS.c.id
            )
        )

        if search:
            pattern = f"%{search}%"
            base = base.where(User.name.ilike(pattern) | User.email.ilike(pattern))
        if role is not None:
            base = base.where(User.role == role)
        if status is not None:
            base = base.where(User.status == status)

        count_q = select(func.count()).select_from(base.subquery())
        total = int((await self._session.execute(count_q)).scalar_one())

        offset = (page - 1) * page_size
        data_q = base.order_by(User.created_at.desc()).offset(offset).limit(page_size)
        rows = (await self._session.execute(data_q)).mappings().all()

        result: list[UserWithStats] = []
        for row in rows:
            user = User(
                id=row["id"],
                name=row["name"],
                email=row["email"],
                email_domain=row["email_domain"],
                password_hash=row["password_hash"],
                department_id=row["department_id"],
                department=row["department"],
                phone=row["phone"],
                role=row["role"],
                status=row["status"],
                email_verified=row["email_verified"],
                auth_provider=row["auth_provider"],
                external_user_id=row["external_user_id"],
                ding_user_id=row["ding_user_id"],
                employee_no=row["employee_no"],
                failed_login_count=row["failed_login_count"],
                session_version=row["session_version"],
                locked_until=row["locked_until"],
                last_login_at=row["last_login_at"],
                last_login_ip=row["last_login_ip"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            _attach_department(user, row["department_name"], row["department_code"])
            result.append(
                UserWithStats(
                    user=user,
                    department_name=row["department_name"],
                    department_code=row["department_code"],
                    upload_count=int(row["upload_count"]),
                    last_upload_at=row["last_upload_at"],
                )
            )

        return result, total


def _attach_department(
    user: User,
    department_name: str | None,
    department_code: str | None,
) -> None:
    dynamic_user = cast(Any, user)
    dynamic_user.department_name = department_name
    dynamic_user.department_code = department_code
