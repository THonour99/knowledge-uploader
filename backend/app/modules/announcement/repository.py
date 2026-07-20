from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, MetaData, String, Table, and_, exists, func, or_, select
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.elements import ColumnElement

from app.modules.user.schemas import AuthUserRecord

from .models import Announcement, AnnouncementDepartment, AnnouncementRead, AnnouncementRole

USERS = Table(
    "users",
    MetaData(),
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("department_id", UUID(as_uuid=True), nullable=False),
    Column("role", String(40), nullable=False),
    Column("status", String(40), nullable=False),
    Column("email_verified", Boolean, nullable=False),
)
DEPARTMENTS = Table(
    "departments",
    MetaData(),
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("status", String(20), nullable=False),
)
USER_MANAGED_DEPARTMENTS = Table(
    "user_managed_departments",
    MetaData(),
    Column("user_id", UUID(as_uuid=True), primary_key=True),
    Column("department_id", UUID(as_uuid=True), primary_key=True),
)


class AnnouncementRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        title: str,
        body_markdown: str,
        audience_type: str,
        department_ids: list[uuid.UUID],
        roles: list[str],
        visible_from: datetime | None,
        expires_at: datetime | None,
        is_pinned: bool,
        actor_id: uuid.UUID,
    ) -> Announcement:
        item = Announcement(
            title=title,
            body_markdown=body_markdown,
            audience_type=audience_type,
            visible_from=visible_from,
            expires_at=expires_at,
            is_pinned=is_pinned,
            created_by=actor_id,
            updated_by=actor_id,
            departments=[AnnouncementDepartment(department_id=value) for value in department_ids],
            roles=[AnnouncementRole(role=value) for value in roles],
        )
        self._session.add(item)
        await self._session.flush()
        return item

    async def get_admin(
        self, announcement_id: uuid.UUID, *, for_update: bool = False
    ) -> Announcement | None:
        statement = (
            select(Announcement)
            .options(selectinload(Announcement.departments), selectinload(Announcement.roles))
            .where(Announcement.id == announcement_id)
        )
        if for_update:
            statement = statement.with_for_update(of=Announcement)
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def list_admin(
        self,
        *,
        state: str,
        search: str | None,
        now: datetime,
        limit: int,
        offset: int,
    ) -> tuple[list[Announcement], int]:
        filters = self._admin_filters(state=state, search=search, now=now)
        statement = (
            select(Announcement)
            .options(selectinload(Announcement.departments), selectinload(Announcement.roles))
            .where(*filters)
            .order_by(Announcement.updated_at.desc(), Announcement.id.desc())
            .offset(offset)
            .limit(limit)
        )
        items = list((await self._session.execute(statement)).scalars())
        total = int(
            (
                await self._session.execute(
                    select(func.count()).select_from(Announcement).where(*filters)
                )
            ).scalar_one()
        )
        return items, total

    async def get_public(
        self,
        *,
        announcement_id: uuid.UUID,
        current_user: AuthUserRecord,
        now: datetime,
    ) -> tuple[Announcement, bool] | None:
        audience = self._audience_filter(current_user)
        statement = (
            select(Announcement, AnnouncementRead.user_id.is_not(None))
            .outerjoin(
                AnnouncementRead,
                and_(
                    AnnouncementRead.announcement_id == Announcement.id,
                    AnnouncementRead.user_id == current_user.id,
                ),
            )
            .options(selectinload(Announcement.departments), selectinload(Announcement.roles))
            .where(
                Announcement.id == announcement_id,
                Announcement.lifecycle_state == "released",
                Announcement.visible_from <= now,
                audience,
            )
        )
        row = (await self._session.execute(statement)).one_or_none()
        return None if row is None else (row[0], bool(row[1]))

    async def list_public(
        self,
        *,
        current_user: AuthUserRecord,
        state: str,
        unread_only: bool,
        now: datetime,
        limit: int,
        offset: int,
    ) -> tuple[list[tuple[Announcement, bool]], int, int]:
        base_filters = [
            Announcement.lifecycle_state == "released",
            Announcement.visible_from <= now,
            self._audience_filter(current_user),
        ]
        if state == "active":
            base_filters.append(
                or_(Announcement.expires_at.is_(None), Announcement.expires_at > now)
            )
        elif state == "expired":
            base_filters.append(Announcement.expires_at <= now)
        read_join = and_(
            AnnouncementRead.announcement_id == Announcement.id,
            AnnouncementRead.user_id == current_user.id,
        )
        list_filters = list(base_filters)
        if unread_only:
            list_filters.append(AnnouncementRead.user_id.is_(None))
        statement = (
            select(Announcement, AnnouncementRead.user_id.is_not(None))
            .outerjoin(AnnouncementRead, read_join)
            .options(selectinload(Announcement.departments), selectinload(Announcement.roles))
            .where(*list_filters)
            .order_by(
                Announcement.is_pinned.desc(),
                Announcement.visible_from.desc(),
                Announcement.id.desc(),
            )
            .offset(offset)
            .limit(limit)
        )
        items = [(row[0], bool(row[1])) for row in (await self._session.execute(statement)).all()]
        count_statement = (
            select(func.count())
            .select_from(Announcement)
            .outerjoin(AnnouncementRead, read_join)
            .where(*list_filters)
        )
        total = int((await self._session.execute(count_statement)).scalar_one())
        unread_count = int(
            (
                await self._session.execute(
                    select(func.count())
                    .select_from(Announcement)
                    .outerjoin(AnnouncementRead, read_join)
                    .where(
                        Announcement.lifecycle_state == "released",
                        Announcement.visible_from <= now,
                        or_(Announcement.expires_at.is_(None), Announcement.expires_at > now),
                        self._audience_filter(current_user),
                        AnnouncementRead.user_id.is_(None),
                    )
                )
            ).scalar_one()
        )
        return items, total, unread_count

    async def mark_read(
        self, *, announcement_id: uuid.UUID, user_id: uuid.UUID, now: datetime
    ) -> datetime:
        statement = (
            pg_insert(AnnouncementRead)
            .values(announcement_id=announcement_id, user_id=user_id, read_at=now)
            .on_conflict_do_nothing(index_elements=["announcement_id", "user_id"])
            .returning(AnnouncementRead.read_at)
        )
        read_at = (await self._session.execute(statement)).scalar_one_or_none()
        if read_at is not None:
            return read_at
        return (
            await self._session.execute(
                select(AnnouncementRead.read_at).where(
                    AnnouncementRead.announcement_id == announcement_id,
                    AnnouncementRead.user_id == user_id,
                )
            )
        ).scalar_one()

    async def validate_departments(self, department_ids: list[uuid.UUID]) -> bool:
        if not department_ids:
            return True
        count = int(
            (
                await self._session.execute(
                    select(func.count())
                    .select_from(DEPARTMENTS)
                    .where(DEPARTMENTS.c.id.in_(department_ids), DEPARTMENTS.c.status == "active")
                )
            ).scalar_one()
        )
        return count == len(set(department_ids))

    async def stats(self, item: Announcement) -> tuple[int, int]:
        target_filter = self._target_user_filter(item)
        target_count = int(
            (
                await self._session.execute(
                    select(func.count(func.distinct(USERS.c.id))).where(
                        USERS.c.status == "active", target_filter
                    )
                )
            ).scalar_one()
        )
        read_count = int(
            (
                await self._session.execute(
                    select(func.count(func.distinct(USERS.c.id)))
                    .select_from(USERS)
                    .join(
                        AnnouncementRead,
                        and_(
                            AnnouncementRead.user_id == USERS.c.id,
                            AnnouncementRead.announcement_id == item.id,
                        ),
                    )
                    .where(USERS.c.status == "active", target_filter)
                )
            ).scalar_one()
        )
        return target_count, read_count

    @staticmethod
    def replace_targets(
        item: Announcement, *, department_ids: list[uuid.UUID], roles: list[str]
    ) -> None:
        item.departments = [AnnouncementDepartment(department_id=value) for value in department_ids]
        item.roles = [AnnouncementRole(role=value) for value in roles]

    @staticmethod
    def _admin_filters(
        *, state: str, search: str | None, now: datetime
    ) -> list[ColumnElement[bool]]:
        filters: list[ColumnElement[bool]] = []
        if search:
            filters.append(Announcement.title.ilike(f"%{search.strip()}%"))
        if state == "draft":
            filters.append(Announcement.lifecycle_state == "draft")
        elif state == "withdrawn":
            filters.append(Announcement.lifecycle_state == "withdrawn")
        elif state == "scheduled":
            filters.extend(
                [Announcement.lifecycle_state == "released", Announcement.visible_from > now]
            )
        elif state == "expired":
            filters.extend(
                [Announcement.lifecycle_state == "released", Announcement.expires_at <= now]
            )
        elif state == "published":
            filters.extend(
                [
                    Announcement.lifecycle_state == "released",
                    Announcement.visible_from <= now,
                    or_(Announcement.expires_at.is_(None), Announcement.expires_at > now),
                ]
            )
        return filters

    @staticmethod
    def _audience_filter(current_user: AuthUserRecord) -> ColumnElement[bool]:
        managed_ids = (
            list(current_user.managed_department_ids) if current_user.role == "dept_admin" else []
        )
        department_ids = [current_user.department_id, *managed_ids]
        return or_(
            Announcement.audience_type == "all",
            and_(
                Announcement.audience_type == "roles",
                exists(
                    select(AnnouncementRole.announcement_id).where(
                        AnnouncementRole.announcement_id == Announcement.id,
                        AnnouncementRole.role == current_user.role,
                    )
                ),
            ),
            and_(
                Announcement.audience_type == "departments",
                exists(
                    select(AnnouncementDepartment.announcement_id).where(
                        AnnouncementDepartment.announcement_id == Announcement.id,
                        AnnouncementDepartment.department_id.in_(department_ids),
                    )
                ),
            ),
        )

    @staticmethod
    def _target_user_filter(item: Announcement) -> ColumnElement[bool]:
        if item.audience_type == "all":
            return USERS.c.id.is_not(None)
        if item.audience_type == "roles":
            return USERS.c.role.in_([target.role for target in item.roles])
        target_departments = [target.department_id for target in item.departments]
        managed_match = exists(
            select(USER_MANAGED_DEPARTMENTS.c.user_id).where(
                USER_MANAGED_DEPARTMENTS.c.user_id == USERS.c.id,
                USER_MANAGED_DEPARTMENTS.c.department_id.in_(target_departments),
            )
        )
        return or_(
            USERS.c.department_id.in_(target_departments),
            and_(USERS.c.role == "dept_admin", managed_match),
        )
