from __future__ import annotations

import uuid

from sqlalchemy import Column, MetaData, String, Table, select
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Department, UserManagedDepartment

_USERS = Table(
    "users",
    MetaData(),
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("role", String(40), nullable=False),
    Column("status", String(40), nullable=False),
)


class SqlDepartmentScopeStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_managed_department_ids(self, user_id: uuid.UUID) -> frozenset[uuid.UUID]:
        result = await self._session.execute(
            select(UserManagedDepartment.department_id)
            .join(Department, UserManagedDepartment.department_id == Department.id)
            .where(
                UserManagedDepartment.user_id == user_id,
                Department.status == "active",
            )
        )
        return frozenset(result.scalars())

    async def has_non_self_reviewer(
        self,
        *,
        file_department_id: uuid.UUID,
        uploader_id: uuid.UUID,
    ) -> bool:
        system_admin = await self._session.execute(
            select(_USERS.c.id)
            .where(
                _USERS.c.id != uploader_id,
                _USERS.c.role == "system_admin",
                _USERS.c.status == "active",
            )
            .limit(1)
        )
        if system_admin.scalar_one_or_none() is not None:
            return True

        dept_admin = await self._session.execute(
            select(_USERS.c.id)
            .join(UserManagedDepartment, UserManagedDepartment.user_id == _USERS.c.id)
            .join(Department, Department.id == UserManagedDepartment.department_id)
            .where(
                _USERS.c.id != uploader_id,
                _USERS.c.role == "dept_admin",
                _USERS.c.status == "active",
                UserManagedDepartment.department_id == file_department_id,
                Department.status == "active",
            )
            .limit(1)
        )
        return dept_admin.scalar_one_or_none() is not None
