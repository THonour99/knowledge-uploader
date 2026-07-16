from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.outbox import OutboxRepository

from . import events
from .models import Notification
from .repository import NotificationRepository  # noqa: TID251 - same-module repository dependency
from .schemas import NotificationItem, NotificationListResponse, NotificationMetadata


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


@dataclass(frozen=True, slots=True)
class NotificationMessage:
    type: str
    title: str
    body: str
    metadata: NotificationMetadata


@dataclass(frozen=True, slots=True)
class SourceNotificationResult:
    in_app_notification_id: uuid.UUID | None
    email_notification_id: uuid.UUID | None


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
        cleaned_type, cleaned_title, cleaned_body = _clean_message_fields(
            type=type,
            title=title,
            body=body,
        )
        safe_metadata = NotificationMetadata.model_validate(metadata or {}).as_storage_dict()
        notification = await self._repository.create(
            user_id=user_id,
            type=cleaned_type,
            title=cleaned_title,
            body=cleaned_body,
            metadata_json=safe_metadata,
        )
        if commit:
            await self._session.commit()
            await self._session.refresh(notification)
        return notification

    async def create_from_source(
        self,
        *,
        source_event_id: int,
        user_id: uuid.UUID,
        message: NotificationMessage,
    ) -> SourceNotificationResult:
        if source_event_id < 1:
            raise ValueError("source_event_id must be positive")
        cleaned_type, cleaned_title, cleaned_body = _clean_message_fields(
            type=message.type,
            title=message.title,
            body=message.body,
        )
        metadata = message.metadata.as_storage_dict()
        in_app = await self._repository.create_for_source(
            source_event_id=source_event_id,
            user_id=user_id,
            type=cleaned_type,
            channel="in_app",
            title=cleaned_title,
            body=cleaned_body,
            metadata_json=metadata,
        )
        email = await self._repository.create_for_source(
            source_event_id=source_event_id,
            user_id=user_id,
            type=cleaned_type,
            channel="email",
            title=cleaned_title,
            body=cleaned_body,
            metadata_json=metadata,
        )
        if email is not None:
            await OutboxRepository(self._session).append(
                event_type=events.NOTIFICATION_EMAIL_REQUESTED,
                aggregate_type="notification",
                aggregate_id=str(email.id),
                payload={"notification_id": str(email.id)},
            )
        return SourceNotificationResult(
            in_app_notification_id=in_app.id if in_app is not None else None,
            email_notification_id=email.id if email is not None else None,
        )

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

    async def mark_all_read(self, *, user_id: uuid.UUID) -> int:
        updated_count = await self._repository.mark_all_read(user_id=user_id)
        await self._session.commit()
        return updated_count


def _clean_message_fields(*, type: str, title: str, body: str) -> tuple[str, str, str]:
    cleaned_type = type.strip()
    cleaned_title = title.strip()
    cleaned_body = body.strip()
    if not cleaned_type or len(cleaned_type) > 80:
        raise ValueError("notification type must contain at most 80 characters")
    if not cleaned_title or len(cleaned_title) > 200:
        raise ValueError("notification title must contain at most 200 characters")
    if not cleaned_body:
        raise ValueError("notification body is required")
    return cleaned_type, cleaned_title, cleaned_body[:2000]
