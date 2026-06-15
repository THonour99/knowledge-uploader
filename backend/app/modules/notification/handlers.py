from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import EventDispatchContext, EventEnvelope, event_handler
from app.core.outbox import EventOutbox
from app.modules.ragflow.events import RagflowSyncTaskFailed
from app.modules.review.events import ReviewFileApproved, ReviewFileRejected

from . import events
from .repository import NotificationRepository  # noqa: TID251 - same-module repository dependency
from .service import NotificationService  # noqa: TID251 - same-module service dependency
from .tasks import enqueue_email


@dataclass(frozen=True)
class NotificationRecipient:
    user_id: uuid.UUID
    email: str | None
    file_name: str


@event_handler(ReviewFileApproved)
def queue_review_approved_notification(
    event: EventEnvelope,
    context: EventDispatchContext,
) -> None:
    file_id = _required_payload_string(event, "file_id", "review approved event missing file_id")
    context.sender.send_task(
        "notification.review_approved",
        args=[file_id],
        queue="notification_queue",
    )


@event_handler(ReviewFileRejected)
def queue_review_rejected_notification(
    event: EventEnvelope,
    context: EventDispatchContext,
) -> None:
    file_id = _required_payload_string(event, "file_id", "review rejected event missing file_id")
    reason = _string_or_none(event.payload.get("reason")) or ""
    context.sender.send_task(
        "notification.review_rejected",
        args=[file_id, reason],
        queue="notification_queue",
    )


@event_handler(RagflowSyncTaskFailed)
def queue_ragflow_sync_failed_notification(
    event: EventEnvelope,
    context: EventDispatchContext,
) -> None:
    sync_task_id = _required_payload_string(
        event,
        "sync_task_id",
        "ragflow sync failed event missing sync_task_id",
    )
    error_message = _string_or_none(event.payload.get("error_message")) or ""
    context.sender.send_task(
        "notification.ragflow_sync_failed",
        args=[sync_task_id, error_message],
        queue="notification_queue",
    )


async def handle_review_file_approved(
    event: EventOutbox | Mapping[str, object],
    *,
    session: AsyncSession,
) -> None:
    await _handle_review_result(
        event,
        session=session,
        approved=True,
        reason=None,
    )


async def handle_review_file_rejected(
    event: EventOutbox | Mapping[str, object],
    *,
    session: AsyncSession,
) -> None:
    payload = _payload_from(event)
    reason = payload.get("reason")
    await _handle_review_result(
        event,
        session=session,
        approved=False,
        reason=reason if isinstance(reason, str) else None,
    )


async def handle_ragflow_sync_failed(
    event: EventOutbox | Mapping[str, object],
    *,
    session: AsyncSession,
) -> None:
    payload = _payload_from(event)
    recipient = await _recipient_from_payload(payload, session=session)
    if recipient is None:
        return
    error_message = payload.get("error_message")
    body = f"文件 {recipient.file_name} 同步到 RAGFlow 失败, 请稍后重试或联系管理员。"
    if isinstance(error_message, str) and error_message.strip():
        body = f"{body}\n失败原因: {error_message.strip()[:500]}"
    await _create_and_send(
        session=session,
        recipient=recipient,
        type=events.NOTIFICATION_RAGFLOW_SYNC_FAILED,
        title="RAGFlow 同步失败",
        body=body,
        metadata=_safe_metadata(payload),
    )


async def handle_document_expiry_reminder(
    event: EventOutbox | Mapping[str, object],
    *,
    session: AsyncSession,
) -> None:
    payload = _payload_from(event)
    recipient = await _recipient_from_payload(payload, session=session)
    if recipient is None:
        return

    file_id = _string_or_none(payload.get("file_id"))
    expires_at = _string_or_none(payload.get("expires_at"))
    expiry_status = _normalized_expiry_status(payload.get("expiry_status"))
    if file_id is None or expires_at is None or expiry_status is None:
        return

    notification_type = (
        events.NOTIFICATION_DOCUMENT_EXPIRED
        if expiry_status == "expired"
        else events.NOTIFICATION_DOCUMENT_EXPIRING
    )
    title = "文件已过期" if expiry_status == "expired" else "文件即将过期"
    display_date = _display_date(expires_at)
    if expiry_status == "expired":
        body = f"文件 {recipient.file_name} 已于 {display_date} 过期, 请及时复核或更新。"
    else:
        body = f"文件 {recipient.file_name} 将于 {display_date} 过期, 请及时复核或更新。"

    await _create_and_send(
        session=session,
        recipient=recipient,
        type=notification_type,
        title=title,
        body=body,
        metadata=_safe_metadata(payload),
        idempotency_key=_document_expiry_idempotency_key(
            file_id=file_id,
            expiry_status=expiry_status,
            expires_at=expires_at,
        ),
    )


async def _handle_review_result(
    event: EventOutbox | Mapping[str, object],
    *,
    session: AsyncSession,
    approved: bool,
    reason: str | None,
) -> None:
    payload = _payload_from(event)
    recipient = await _recipient_from_payload(payload, session=session)
    if recipient is None:
        return
    type = events.NOTIFICATION_REVIEW_APPROVED if approved else events.NOTIFICATION_REVIEW_REJECTED
    title = "文件审核通过" if approved else "文件审核被拒绝"
    if approved:
        body = f"文件 {recipient.file_name} 已审核通过。"
    else:
        body = f"文件 {recipient.file_name} 未通过审核。"
    if reason is not None and reason.strip():
        body = f"{body}\n原因: {reason.strip()[:500]}"
    await _create_and_send(
        session=session,
        recipient=recipient,
        type=type,
        title=title,
        body=body,
        metadata=_safe_metadata(payload),
    )


async def _create_and_send(
    *,
    session: AsyncSession,
    recipient: NotificationRecipient,
    type: str,
    title: str,
    body: str,
    metadata: dict[str, object],
    idempotency_key: str | None = None,
) -> None:
    service = NotificationService(
        session=session,
        repository=NotificationRepository(session),
    )
    created = True
    if idempotency_key is None:
        await service.create_in_app(
            user_id=recipient.user_id,
            type=type,
            title=title,
            body=body,
            metadata=metadata,
            commit=False,
        )
    else:
        result = await service.create_in_app_once(
            user_id=recipient.user_id,
            type=type,
            title=title,
            body=body,
            metadata=metadata,
            idempotency_key=idempotency_key,
            commit=False,
        )
        created = result.created
    if not created:
        return
    await session.commit()
    if recipient.email:
        enqueue_email(recipient=recipient.email, subject=title, body=body)


async def _recipient_from_payload(
    payload: Mapping[str, object],
    *,
    session: AsyncSession,
) -> NotificationRecipient | None:
    explicit_recipient = _explicit_recipient(payload)
    if explicit_recipient is not None:
        return explicit_recipient

    file_id = _string_or_none(payload.get("file_id"))
    if file_id:
        return await _recipient_from_file_id(file_id, session=session)

    sync_task_id = _string_or_none(payload.get("sync_task_id"))
    if sync_task_id:
        return await _recipient_from_sync_task_id(sync_task_id, session=session)
    return None


def _explicit_recipient(payload: Mapping[str, object]) -> NotificationRecipient | None:
    raw_user_id = _string_or_none(payload.get("recipient_user_id")) or _string_or_none(
        payload.get("user_id")
    )
    if raw_user_id is None:
        return None
    try:
        user_id = uuid.UUID(raw_user_id)
    except ValueError:
        return None
    email = _string_or_none(payload.get("recipient_email")) or _string_or_none(payload.get("email"))
    file_name = _string_or_none(payload.get("file_name")) or "相关文件"
    return NotificationRecipient(user_id=user_id, email=email, file_name=file_name)


async def _recipient_from_file_id(
    file_id: str,
    *,
    session: AsyncSession,
) -> NotificationRecipient | None:
    result = await session.execute(
        text(
            """
            SELECT f.uploader_id, f.original_name, u.email
            FROM files AS f
            JOIN users AS u ON u.id = f.uploader_id
            WHERE f.id = :file_id
            """
        ),
        {"file_id": file_id},
    )
    row = result.mappings().first()
    if row is None:
        return None
    return NotificationRecipient(
        user_id=uuid.UUID(str(row["uploader_id"])),
        email=str(row["email"]),
        file_name=str(row["original_name"]),
    )


async def _recipient_from_sync_task_id(
    sync_task_id: str,
    *,
    session: AsyncSession,
) -> NotificationRecipient | None:
    result = await session.execute(
        text(
            """
            SELECT f.uploader_id, f.original_name, u.email
            FROM sync_tasks AS st
            JOIN files AS f ON f.id = st.file_id
            JOIN users AS u ON u.id = f.uploader_id
            WHERE st.id = :sync_task_id
            """
        ),
        {"sync_task_id": sync_task_id},
    )
    row = result.mappings().first()
    if row is None:
        return None
    return NotificationRecipient(
        user_id=uuid.UUID(str(row["uploader_id"])),
        email=str(row["email"]),
        file_name=str(row["original_name"]),
    )


def _payload_from(event: EventOutbox | Mapping[str, object]) -> Mapping[str, object]:
    if isinstance(event, EventOutbox):
        return event.payload
    return event


def _safe_metadata(payload: Mapping[str, object]) -> dict[str, object]:
    metadata: dict[str, object] = {}
    for key in (
        "file_id",
        "sync_task_id",
        "status",
        "review_status",
        "ragflow_dataset_id",
        "expires_at",
        "expiry_status",
    ):
        value = payload.get(key)
        if isinstance(value, str | int | float | bool) or value is None:
            metadata[key] = value
    return metadata


def _normalized_expiry_status(value: object) -> str | None:
    status = _string_or_none(value)
    if status in {"expiring", "expired"}:
        return status
    return None


def _display_date(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return parsed.date().isoformat()


def _document_expiry_idempotency_key(
    *,
    file_id: str,
    expiry_status: str,
    expires_at: str,
) -> str:
    return f"document_expiry:{file_id}:{expiry_status}:{_display_date(expires_at)}"


def _string_or_none(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _required_payload_string(event: EventEnvelope, key: str, message: str) -> str:
    value = event.payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(message)
    return value.strip()
