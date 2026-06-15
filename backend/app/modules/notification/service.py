from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from .models import Notification
from .repository import NotificationRepository  # noqa: TID251 - same-module repository dependency
from .schemas import NotificationItem, NotificationListResponse


@dataclass(frozen=True)
class NotificationPage:
    page: int
    page_size: int
    unread_only: bool = False

    @property
    def limit(self) -> int:
        return self.page_size

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


@dataclass(frozen=True)
class NotificationCreateResult:
    notification: Notification
    created: bool


class NotificationService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        repository: NotificationRepository,
    ) -> None:
        self._session = session
        self._repository = repository

    async def create_in_app(
        self,
        *,
        user_id: uuid.UUID,
        type: str,
        title: str,
        body: str,
        metadata: dict[str, object] | None = None,
        commit: bool = True,
    ) -> Notification:
        notification = await self._repository.create(
            user_id=user_id,
            type=type,
            title=title.strip(),
            body=body.strip(),
            metadata_json=metadata or {},
        )
        if commit:
            await self._session.commit()
            await self._session.refresh(notification)
        return notification

    async def create_in_app_once(
        self,
        *,
        user_id: uuid.UUID,
        type: str,
        title: str,
        body: str,
        idempotency_key: str,
        metadata: dict[str, object] | None = None,
        commit: bool = True,
    ) -> NotificationCreateResult:
        key = idempotency_key.strip()
        existing = await self._repository.get_by_idempotency_key(
            user_id=user_id,
            type=type,
            idempotency_key=key,
        )
        if existing is not None:
            return NotificationCreateResult(notification=existing, created=False)

        next_metadata = dict(metadata or {})
        next_metadata["idempotency_key"] = key
        notification = await self.create_in_app(
            user_id=user_id,
            type=type,
            title=title,
            body=body,
            metadata=next_metadata,
            commit=commit,
        )
        return NotificationCreateResult(notification=notification, created=True)

    async def list_user_notifications(
        self,
        *,
        user_id: uuid.UUID,
        page: NotificationPage,
    ) -> NotificationListResponse:
        notifications = await self._repository.list_for_user(
            user_id=user_id,
            unread_only=page.unread_only,
            limit=page.limit,
            offset=page.offset,
        )
        total = await self._repository.count_for_user(
            user_id=user_id,
            unread_only=page.unread_only,
        )
        unread_count = await self._repository.count_unread(user_id=user_id)
        return NotificationListResponse(
            items=[NotificationItem.from_model(item) for item in notifications],
            total=total,
            unread_count=unread_count,
            page=page.page,
            page_size=page.page_size,
        )

    async def mark_read(
        self,
        *,
        notification_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> Notification | None:
        notification = await self._repository.mark_read(
            notification_id=notification_id,
            user_id=user_id,
        )
        if notification is not None:
            await self._session.commit()
            await self._session.refresh(notification)
        return notification
