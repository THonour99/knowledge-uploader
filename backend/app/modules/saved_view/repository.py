from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import (
    Column,
    MetaData,
    Select,
    String,
    Table,
    and_,
    delete,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncSession

from .models import SavedView

_METADATA = MetaData()
DEPARTMENTS = Table(
    "departments",
    _METADATA,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("status", String(20), nullable=False),
)


@dataclass(frozen=True, slots=True)
class SavedViewAccess:
    actor_id: uuid.UUID
    actor_role: str
    managed_department_ids: frozenset[uuid.UUID]


class SavedViewRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        owner_id: uuid.UUID,
        scope: str,
        department_id: uuid.UUID | None,
        page_key: str,
        name: str,
        definition_schema_version: int,
        query_definition: dict[str, object],
        column_preferences: dict[str, object],
    ) -> SavedView:
        saved_view = SavedView(
            owner_id=owner_id,
            scope=scope,
            department_id=department_id,
            page_key=page_key,
            name=name,
            definition_schema_version=definition_schema_version,
            query_definition=query_definition,
            column_preferences=column_preferences,
        )
        self._session.add(saved_view)
        await self._session.flush()
        return saved_view

    async def list_visible(
        self,
        *,
        access: SavedViewAccess,
        page_key: str,
        scope: str | None,
        limit: int,
        offset: int,
    ) -> list[SavedView]:
        statement = self._visible_query(access=access).where(SavedView.page_key == page_key)
        if scope is not None:
            statement = statement.where(SavedView.scope == scope)
        result = await self._session.execute(
            statement.order_by(SavedView.updated_at.desc(), SavedView.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars())

    async def count_visible(
        self,
        *,
        access: SavedViewAccess,
        page_key: str,
        scope: str | None,
    ) -> int:
        source = self._visible_query(access=access).where(SavedView.page_key == page_key)
        if scope is not None:
            source = source.where(SavedView.scope == scope)
        result = await self._session.execute(select(func.count()).select_from(source.subquery()))
        return int(result.scalar_one())

    async def get_visible(
        self,
        *,
        access: SavedViewAccess,
        saved_view_id: uuid.UUID,
    ) -> SavedView | None:
        result = await self._session.execute(
            self._visible_query(access=access).where(SavedView.id == saved_view_id)
        )
        return result.scalar_one_or_none()

    async def update_if_version(
        self,
        *,
        saved_view_id: uuid.UUID,
        expected_row_version: int,
        values: dict[str, object],
    ) -> SavedView | None:
        statement = (
            update(SavedView)
            .where(
                SavedView.id == saved_view_id,
                SavedView.row_version == expected_row_version,
            )
            .values(
                **values,
                row_version=SavedView.row_version + 1,
                updated_at=func.now(),
            )
            .returning(SavedView)
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def delete_by_id(self, *, saved_view_id: uuid.UUID) -> bool:
        result = await self._session.execute(
            delete(SavedView).where(SavedView.id == saved_view_id).returning(SavedView.id)
        )
        return result.scalar_one_or_none() is not None

    async def active_department_exists(self, department_id: uuid.UUID) -> bool:
        result = await self._session.execute(
            select(DEPARTMENTS.c.id).where(
                DEPARTMENTS.c.id == department_id,
                DEPARTMENTS.c.status == "active",
            )
        )
        return result.scalar_one_or_none() is not None

    @staticmethod
    def _visible_query(*, access: SavedViewAccess) -> Select[tuple[SavedView]]:
        private_predicate = and_(
            SavedView.scope == "private",
            SavedView.owner_id == access.actor_id,
        )
        department_predicate = and_(
            SavedView.scope == "department",
            SavedView.page_key.in_(("review_files", "task_logs")),
        )
        if access.actor_role == "system_admin":
            scope_predicate = or_(private_predicate, department_predicate)
        elif access.actor_role == "dept_admin" and access.managed_department_ids:
            scope_predicate = or_(
                private_predicate,
                and_(
                    department_predicate,
                    SavedView.department_id.in_(access.managed_department_ids),
                ),
            )
        else:
            scope_predicate = private_predicate
        return select(SavedView).where(scope_predicate)
