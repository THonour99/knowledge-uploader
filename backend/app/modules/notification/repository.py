from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Notification


class NotificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        type: str,
        title: str,
        body: str,
        metadata_json: dict[str, object] | None = None,
        channel: str = "in_app",
    ) -> Notification:
        notification = Notification(
            user_id=user_id,
            type=type,
            title=title,
            body=body,
            metadata_json=metadata_json or {},
            channel=channel,
        )
        self._session.add(notification)
        await self._session.flush()
        return notification

    async def list_for_user(
        self,
        *,
        user_id: uuid.UUID,
        unread_only: bool,
        limit: int,
        offset: int,
    ) -> list[Notification]:
        statement = self._base_user_query(user_id=user_id, unread_only=unread_only)
        result = await self._session.execute(
            statement.order_by(Notification.created_at.desc(), Notification.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars())

    async def count_for_user(self, *, user_id: uuid.UUID, unread_only: bool) -> int:
        source = self._base_user_query(user_id=user_id, unread_only=unread_only).subquery()
        result = await self._session.execute(select(func.count()).select_from(source))
        return int(result.scalar_one())

    async def count_unread(self, *, user_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(Notification)
            .where(Notification.user_id == user_id, Notification.read_at.is_(None))
        )
        return int(result.scalar_one())

    async def mark_read(
        self,
        *,
        notification_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> Notification | None:
        result = await self._session.execute(
            select(Notification).where(
                Notification.id == notification_id,
                Notification.user_id == user_id,
            )
        )
        notification = result.scalar_one_or_none()
        if notification is None:
            return None
        if notification.read_at is None:
            notification.read_at = datetime.now(UTC)
            await self._session.flush()
        return notification

    def _base_user_query(
        self,
        *,
        user_id: uuid.UUID,
        unread_only: bool,
    ) -> Select[tuple[Notification]]:
        statement = select(Notification).where(Notification.user_id == user_id)
        if unread_only:
            statement = statement.where(Notification.read_at.is_(None))
        return statement
