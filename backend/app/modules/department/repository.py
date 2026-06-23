from __future__ import annotations

import uuid

from sqlalchemy import Column, MetaData, String, Table, delete, func, select
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Department, UserManagedDepartment

USERS = Table(
    "users",
    MetaData(),
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("email", String(255), nullable=False),
    Column("role", String(40), nullable=False),
    Column("status", String(40), nullable=False),
    Column("department_id", UUID(as_uuid=True), nullable=False),
)


class DepartmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_departments(
        self,
        *,
        page: int,
        page_size: int,
        search: str | None,
        status: str | None,
    ) -> tuple[list[Department], int]:
        stmt = select(Department)
        count_stmt = select(func.count()).select_from(Department)
        if search:
            pattern = f"%{search}%"
            stmt = stmt.where(Department.name.ilike(pattern) | Department.code.ilike(pattern))
            count_stmt = count_stmt.where(
                Department.name.ilike(pattern) | Department.code.ilike(pattern)
            )
        if status:
            stmt = stmt.where(Department.status == status)
            count_stmt = count_stmt.where(Department.status == status)
        total = int((await self._session.execute(count_stmt)).scalar_one())
        result = await self._session.execute(
            stmt.order_by(Department.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars()), total

    async def get_department(self, department_id: uuid.UUID) -> Department | None:
        return await self._session.get(Department, department_id)

    async def get_by_name(self, name: str) -> Department | None:
        result = await self._session.execute(select(Department).where(Department.name == name))
        return result.scalar_one_or_none()

    async def get_by_code(self, code: str) -> Department | None:
        result = await self._session.execute(select(Department).where(Department.code == code))
        return result.scalar_one_or_none()

    async def add_department(self, department: Department) -> Department:
        self._session.add(department)
        await self._session.flush()
        await self._session.refresh(department)
        return department

    async def get_user_role(self, user_id: uuid.UUID) -> str | None:
        result = await self._session.execute(select(USERS.c.role).where(USERS.c.id == user_id))
        return result.scalar_one_or_none()

    async def list_managed_departments(self, user_id: uuid.UUID) -> list[Department]:
        result = await self._session.execute(
            select(Department)
            .join(UserManagedDepartment, UserManagedDepartment.department_id == Department.id)
            .where(UserManagedDepartment.user_id == user_id)
            .order_by(Department.name.asc(), Department.id.asc())
        )
        return list(result.scalars())

    async def replace_managed_departments(
        self,
        *,
        user_id: uuid.UUID,
        department_ids: set[uuid.UUID],
    ) -> None:
        await self._session.execute(
            delete(UserManagedDepartment).where(UserManagedDepartment.user_id == user_id)
        )
        for department_id in sorted(department_ids):
            self._session.add(UserManagedDepartment(user_id=user_id, department_id=department_id))
        await self._session.flush()

    async def clear_managed_departments(self, user_id: uuid.UUID) -> list[uuid.UUID]:
        existing = await self.list_managed_department_ids(user_id)
        await self._session.execute(
            delete(UserManagedDepartment).where(UserManagedDepartment.user_id == user_id)
        )
        await self._session.flush()
        return list(existing)

    async def list_managed_department_ids(self, user_id: uuid.UUID) -> frozenset[uuid.UUID]:
        result = await self._session.execute(
            select(UserManagedDepartment.department_id).where(
                UserManagedDepartment.user_id == user_id
            )
        )
        return frozenset(result.scalars())

    async def get_active_departments_by_ids(
        self,
        department_ids: set[uuid.UUID],
    ) -> list[Department]:
        if not department_ids:
            return []
        result = await self._session.execute(
            select(Department).where(
                Department.id.in_(department_ids),
                Department.status == "active",
            )
        )
        return list(result.scalars())
