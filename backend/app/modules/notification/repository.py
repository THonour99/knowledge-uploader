from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import (
    Column,
    DateTime,
    MetaData,
    Select,
    String,
    Table,
    exists,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.outbox import EventOutbox

from .models import Notification

USERS = Table(
    "users",
    MetaData(),
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("email", String(255), nullable=False),
    Column("department_id", UUID(as_uuid=True), nullable=False),
    Column("role", String(40), nullable=False),
    Column("status", String(40), nullable=False),
)
FILES = Table(
    "files",
    MetaData(),
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("uploader_id", UUID(as_uuid=True), nullable=False),
    Column("department_id", UUID(as_uuid=True), nullable=False),
    Column("original_name", String(255), nullable=False),
    Column("expires_at", DateTime(timezone=True)),
    Column("expiry_status", String(20), nullable=False),
)
SYNC_TASKS = Table(
    "sync_tasks",
    MetaData(),
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("file_id", UUID(as_uuid=True), nullable=False),
)
USER_MANAGED_DEPARTMENTS = Table(
    "user_managed_departments",
    MetaData(),
    Column("user_id", UUID(as_uuid=True), primary_key=True),
    Column("department_id", UUID(as_uuid=True), primary_key=True),
)


@dataclass(frozen=True, slots=True)
class NotificationFileContext:
    id: uuid.UUID
    uploader_id: uuid.UUID
    department_id: uuid.UUID
    original_name: str
    expires_at: datetime | None
    expiry_status: str


@dataclass(frozen=True, slots=True)
class NotificationRecipientRecord:
    user_id: uuid.UUID
    email: str


@dataclass(frozen=True, slots=True)
class EmailDeliveryRecord:
    notification: Notification
    recipient_email: str


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
            delivery_status="pending" if channel == "email" else "not_applicable",
        )
        self._session.add(notification)
        await self._session.flush()
        return notification

    async def create_for_source(
        self,
        *,
        source_event_id: int,
        user_id: uuid.UUID,
        type: str,
        channel: str,
        title: str,
        body: str,
        metadata_json: dict[str, object],
    ) -> Notification | None:
        """Insert once under the DB unique key; never use a check-then-insert race."""
        notification_id = uuid.uuid4()
        statement = (
            pg_insert(Notification)
            .values(
                id=notification_id,
                source_event_id=source_event_id,
                user_id=user_id,
                type=type,
                channel=channel,
                title=title,
                body=body,
                metadata_json=metadata_json,
                delivery_status="pending" if channel == "email" else "not_applicable",
                delivery_attempts=0,
            )
            .on_conflict_do_nothing(constraint="uq_notifications_source_recipient_channel")
            .returning(Notification.id)
        )
        created_id = (await self._session.execute(statement)).scalar_one_or_none()
        if created_id is None:
            return None
        result = await self._session.execute(
            select(Notification).where(Notification.id == created_id)
        )
        return result.scalar_one()

    async def get_source_event(self, event_id: int) -> EventOutbox | None:
        result = await self._session.execute(select(EventOutbox).where(EventOutbox.id == event_id))
        return result.scalar_one_or_none()

    async def get_file_context(self, file_id: uuid.UUID) -> NotificationFileContext | None:
        result = await self._session.execute(select(FILES).where(FILES.c.id == file_id))
        row = result.mappings().one_or_none()
        if row is None:
            return None
        return NotificationFileContext(
            id=row["id"],
            uploader_id=row["uploader_id"],
            department_id=row["department_id"],
            original_name=str(row["original_name"]),
            expires_at=row["expires_at"],
            expiry_status=str(row["expiry_status"]),
        )

    async def get_file_context_for_sync_task(
        self,
        sync_task_id: uuid.UUID,
    ) -> NotificationFileContext | None:
        result = await self._session.execute(
            select(FILES)
            .join(SYNC_TASKS, SYNC_TASKS.c.file_id == FILES.c.id)
            .where(SYNC_TASKS.c.id == sync_task_id)
        )
        row = result.mappings().one_or_none()
        if row is None:
            return None
        return NotificationFileContext(
            id=row["id"],
            uploader_id=row["uploader_id"],
            department_id=row["department_id"],
            original_name=str(row["original_name"]),
            expires_at=row["expires_at"],
            expiry_status=str(row["expiry_status"]),
        )

    async def get_active_recipient(
        self,
        user_id: uuid.UUID,
    ) -> NotificationRecipientRecord | None:
        result = await self._session.execute(
            select(USERS.c.id, USERS.c.email).where(
                USERS.c.id == user_id,
                USERS.c.status == "active",
            )
        )
        row = result.one_or_none()
        if row is None:
            return None
        return NotificationRecipientRecord(user_id=row.id, email=str(row.email))

    async def list_active_department_admins(
        self,
        department_id: uuid.UUID,
    ) -> list[NotificationRecipientRecord]:
        managed_department = exists(
            select(USER_MANAGED_DEPARTMENTS.c.user_id).where(
                USER_MANAGED_DEPARTMENTS.c.user_id == USERS.c.id,
                USER_MANAGED_DEPARTMENTS.c.department_id == department_id,
            )
        )
        result = await self._session.execute(
            select(USERS.c.id, USERS.c.email)
            .where(
                USERS.c.role == "dept_admin",
                USERS.c.status == "active",
                or_(
                    USERS.c.department_id == department_id,
                    managed_department,
                ),
            )
            .order_by(USERS.c.id)
        )
        return [NotificationRecipientRecord(user_id=row.id, email=str(row.email)) for row in result]

    async def get_email_for_delivery(
        self,
        notification_id: uuid.UUID,
    ) -> EmailDeliveryRecord | None:
        result = await self._session.execute(
            select(Notification, USERS.c.email)
            .join(USERS, USERS.c.id == Notification.user_id)
            .where(
                Notification.id == notification_id,
                Notification.channel == "email",
                USERS.c.status == "active",
            )
            .with_for_update(of=Notification)
        )
        row = result.one_or_none()
        if row is None:
            return None
        return EmailDeliveryRecord(
            notification=row[0],
            recipient_email=str(row[1]),
        )

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
            .where(
                Notification.user_id == user_id,
                Notification.channel == "in_app",
                Notification.read_at.is_(None),
            )
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
                Notification.channel == "in_app",
            )
        )
        notification = result.scalar_one_or_none()
        if notification is None:
            return None
        if notification.read_at is None:
            notification.read_at = datetime.now(UTC)
            await self._session.flush()
        return notification

    async def mark_all_read(self, *, user_id: uuid.UUID) -> int:
        result = await self._session.execute(
            update(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.channel == "in_app",
                Notification.read_at.is_(None),
            )
            .values(read_at=datetime.now(UTC))
            .returning(Notification.id)
        )
        updated_ids = result.scalars().all()
        await self._session.flush()
        return len(updated_ids)

    def _base_user_query(
        self,
        *,
        user_id: uuid.UUID,
        unread_only: bool,
    ) -> Select[tuple[Notification]]:
        statement = select(Notification).where(
            Notification.user_id == user_id,
            Notification.channel == "in_app",
        )
        if unread_only:
            statement = statement.where(Notification.read_at.is_(None))
        return statement
