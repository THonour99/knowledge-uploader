from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import EventDispatchContext, EventEnvelope, event_handler
from app.core.outbox import EventOutbox
from app.modules.ai.events import AiFileAnalysisFailed
from app.modules.ragflow.events import RagflowSyncTaskFailed, RagflowSyncTaskSucceeded
from app.modules.review.events import (
    ReviewFileApproved,
    ReviewFileRejected,
    ReviewFileSubmitted,
)

from . import events, exceptions
from .repository import (  # noqa: TID251 - same-module repository dependency
    NotificationFileContext,
    NotificationRecipientRecord,
    NotificationRepository,
)
from .schemas import NotificationMetadata
from .service import (  # noqa: TID251 - same-module service dependency
    NotificationMessage,
    NotificationService,
)


@event_handler(ReviewFileSubmitted)
@event_handler(ReviewFileApproved)
@event_handler(ReviewFileRejected)
@event_handler(RagflowSyncTaskFailed)
@event_handler(RagflowSyncTaskSucceeded)
@event_handler(events.AiFileAnalyzed)
@event_handler(AiFileAnalysisFailed)
@event_handler(events.DocumentFileExpiring)
@event_handler(events.DocumentFileExpired)
def queue_domain_notification(
    event: EventEnvelope,
    context: EventDispatchContext,
) -> None:
    """Queue only the stable outbox ID; task workers reload canonical event data from DB."""
    event_id = _required_event_id(event)
    context.sender.send_task(
        "notification.process_domain_event",
        args=[str(event_id)],
        queue="notification_queue",
    )


@event_handler(events.NotificationEmailRequested)
def queue_persisted_email(
    event: EventEnvelope,
    context: EventDispatchContext,
) -> None:
    if set(event.payload) != {"notification_id"}:
        raise RuntimeError("email request event must contain only notification_id")
    notification_id = _required_uuid(event.payload.get("notification_id"), "notification_id")
    context.sender.send_task(
        "notification.send_persisted_email",
        args=[str(notification_id)],
        queue="notification_queue",
    )


async def handle_source_event_id(
    event_id: int,
    *,
    session: AsyncSession,
) -> int:
    repository = NotificationRepository(session)
    event = await repository.get_source_event(event_id)
    if event is None:
        raise exceptions.NotificationSourceEventNotFoundError("notification source event not found")

    if event.event_type == ReviewFileSubmitted.ROUTING_KEY:
        created = await _handle_review_submitted(event, repository=repository, session=session)
    elif event.event_type == ReviewFileApproved.ROUTING_KEY:
        created = await _handle_review_result(
            event,
            repository=repository,
            session=session,
            approved=True,
        )
    elif event.event_type == ReviewFileRejected.ROUTING_KEY:
        created = await _handle_review_result(
            event,
            repository=repository,
            session=session,
            approved=False,
        )
    elif event.event_type == RagflowSyncTaskSucceeded.ROUTING_KEY:
        created = await _handle_ragflow_result(
            event,
            repository=repository,
            session=session,
            succeeded=True,
        )
    elif event.event_type == RagflowSyncTaskFailed.ROUTING_KEY:
        created = await _handle_ragflow_result(
            event,
            repository=repository,
            session=session,
            succeeded=False,
        )
    elif event.event_type == events.AI_FILE_ANALYZED:
        created = await _handle_ai_result(
            event,
            repository=repository,
            session=session,
            succeeded=True,
        )
    elif event.event_type == AiFileAnalysisFailed.ROUTING_KEY:
        created = await _handle_ai_result(
            event,
            repository=repository,
            session=session,
            succeeded=False,
        )
    elif event.event_type in {events.DOCUMENT_FILE_EXPIRING, events.DOCUMENT_FILE_EXPIRED}:
        created = await _handle_document_expiry(event, repository=repository, session=session)
    else:
        raise exceptions.UnsupportedNotificationSourceEventError(
            "unsupported notification source event"
        )

    await session.commit()
    return created


async def _handle_review_submitted(
    event: EventOutbox,
    *,
    repository: NotificationRepository,
    session: AsyncSession,
) -> int:
    file = await _file_from_event(event, repository=repository)
    recipients = await repository.list_active_department_admins(file.department_id)
    return await _create_for_recipients(
        event=event,
        session=session,
        repository=repository,
        recipients=recipients,
        message=NotificationMessage(
            type=events.NOTIFICATION_REVIEW_SUBMITTED,
            title="有新的文件待审核",
            body=f"文件《{file.original_name}》已提交审核, 请在 SLA 内领取并处理。",
            metadata=NotificationMetadata(
                resource_type="file",
                resource_id=file.id,
                status="pending_review",
            ),
        ),
    )


async def _handle_review_result(
    event: EventOutbox,
    *,
    repository: NotificationRepository,
    session: AsyncSession,
    approved: bool,
) -> int:
    file = await _file_from_event(event, repository=repository)
    recipient = await repository.get_active_recipient(file.uploader_id)
    if recipient is None:
        return 0

    if approved:
        message = NotificationMessage(
            type=events.NOTIFICATION_REVIEW_APPROVED,
            title="文件审核通过",
            body=f"文件《{file.original_name}》已审核通过。",
            metadata=NotificationMetadata(
                resource_type="file",
                resource_id=file.id,
                status="approved",
            ),
        )
    else:
        reason = _safe_reason(event.payload.get("reason"))
        body = f"文件《{file.original_name}》未通过审核。"
        if reason is not None:
            body = f"{body}\n原因: {reason}"
        message = NotificationMessage(
            type=events.NOTIFICATION_REVIEW_REJECTED,
            title="文件审核被拒绝",
            body=body,
            metadata=NotificationMetadata(
                resource_type="file",
                resource_id=file.id,
                status="rejected",
            ),
        )
    return await _create_for_recipients(
        event=event,
        session=session,
        repository=repository,
        recipients=[recipient],
        message=message,
    )


async def _handle_ragflow_result(
    event: EventOutbox,
    *,
    repository: NotificationRepository,
    session: AsyncSession,
    succeeded: bool,
) -> int:
    sync_task_id = _aggregate_uuid(event, expected_type="sync_task")
    file = await repository.get_file_context_for_sync_task(sync_task_id)
    if file is None:
        raise exceptions.NotificationSourceEventNotFoundError(
            "notification sync task no longer resolves to a file"
        )
    recipient = await repository.get_active_recipient(file.uploader_id)
    if recipient is None:
        return 0
    if succeeded:
        type = events.NOTIFICATION_RAGFLOW_SYNC_SUCCEEDED
        title = "RAGFlow 同步完成"
        body = f"文件《{file.original_name}》已同步到知识库并完成解析。"
        status = "succeeded"
    else:
        type = events.NOTIFICATION_RAGFLOW_SYNC_FAILED
        title = "RAGFlow 同步失败"
        body = f"文件《{file.original_name}》同步失败, 请在任务详情查看状态或联系管理员。"
        status = "failed"
    return await _create_for_recipients(
        event=event,
        session=session,
        repository=repository,
        recipients=[recipient],
        message=NotificationMessage(
            type=type,
            title=title,
            body=body,
            metadata=NotificationMetadata(
                resource_type="sync_task",
                resource_id=sync_task_id,
                status=status,
            ),
        ),
    )


async def _handle_ai_result(
    event: EventOutbox,
    *,
    repository: NotificationRepository,
    session: AsyncSession,
    succeeded: bool,
) -> int:
    file = await _file_from_event(event, repository=repository)
    recipient = await repository.get_active_recipient(file.uploader_id)
    if recipient is None:
        return 0
    if succeeded:
        type = events.NOTIFICATION_AI_ANALYSIS_SUCCEEDED
        title = "AI 分析完成"
        body = f"文件《{file.original_name}》的 AI 分析已完成, 可查看分析结果。"
        status = "succeeded"
    else:
        type = events.NOTIFICATION_AI_ANALYSIS_FAILED
        title = "AI 分析失败"
        body = f"文件《{file.original_name}》的 AI 分析未完成, 请稍后重试或联系管理员。"
        status = "failed"
    return await _create_for_recipients(
        event=event,
        session=session,
        repository=repository,
        recipients=[recipient],
        message=NotificationMessage(
            type=type,
            title=title,
            body=body,
            metadata=NotificationMetadata(
                resource_type="file",
                resource_id=file.id,
                status=status,
            ),
        ),
    )


async def _handle_document_expiry(
    event: EventOutbox,
    *,
    repository: NotificationRepository,
    session: AsyncSession,
) -> int:
    snapshot = _expiry_snapshot_from_event(event)
    if snapshot is None:
        return 0
    expected_expires_at, expiry_status = snapshot
    file = await _file_from_event(event, repository=repository)
    now = datetime.now(UTC)
    if (
        file.expires_at is None
        or file.expires_at != expected_expires_at
        or not file.is_current_version
        or file.status in {"deleted", "ragflow_cleanup_failed", "disabled"}
    ):
        return 0
    if expiry_status == "expired":
        if file.expiry_status != "expired" or file.expires_at > now:
            return 0
    elif file.expiry_status != "expiring" or file.expires_at <= now:
        return 0
    recipients: list[NotificationRecipientRecord] = []
    owner = (
        await repository.get_active_department_recipient(
            file.owner_id,
            department_id=file.department_id,
        )
        if file.owner_id is not None
        else None
    )
    if owner is not None:
        recipients.append(owner)
    else:
        uploader = await repository.get_active_recipient(file.uploader_id)
        # The uploader keeps historical access to their own file even after a
        # department move, so fallback delivery is intentionally not department-scoped.
        if uploader is not None:
            recipients.append(uploader)
    recipients.extend(await repository.list_active_department_admins(file.department_id))

    display_date = _display_date(file.expires_at)
    if expiry_status == "expired":
        type = events.NOTIFICATION_DOCUMENT_EXPIRED
        title = "文件已过期"
        body = f"文件《{file.original_name}》已于 {display_date} 过期, 请及时复核或替代。"
    else:
        type = events.NOTIFICATION_DOCUMENT_EXPIRING
        title = "文件即将过期"
        body = f"文件《{file.original_name}》将于 {display_date} 过期, 请及时复核或更新。"
    return await _create_for_recipients(
        event=event,
        session=session,
        repository=repository,
        recipients=recipients,
        message=NotificationMessage(
            type=type,
            title=title,
            body=body,
            metadata=NotificationMetadata(
                resource_type="file",
                resource_id=file.id,
                status=expiry_status,
                expiry_status=expiry_status,
                expires_at=file.expires_at,
            ),
        ),
    )


async def _create_for_recipients(
    *,
    event: EventOutbox,
    session: AsyncSession,
    repository: NotificationRepository,
    recipients: Iterable[NotificationRecipientRecord],
    message: NotificationMessage,
) -> int:
    if event.id is None:
        raise RuntimeError("notification source event must be persisted")
    service = NotificationService(session=session, repository=repository)
    created = 0
    unique_recipients = {recipient.user_id: recipient for recipient in recipients}
    for recipient in sorted(unique_recipients.values(), key=lambda item: item.user_id.int):
        result = await service.create_from_source(
            source_event_id=event.id,
            user_id=recipient.user_id,
            message=message,
        )
        if result.in_app_notification_id is not None:
            created += 1
    return created


async def _file_from_event(
    event: EventOutbox,
    *,
    repository: NotificationRepository,
) -> NotificationFileContext:
    file_id = _aggregate_uuid(event, expected_type="file")
    file = await repository.get_file_context(file_id)
    if file is None:
        raise exceptions.NotificationSourceEventNotFoundError("notification source file not found")
    return file


def _required_event_id(event: EventEnvelope) -> int:
    value = getattr(event, "event_id", None)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise RuntimeError("outbox event envelope missing event_id")
    return value


def _aggregate_uuid(event: EventOutbox, *, expected_type: str) -> uuid.UUID:
    if event.aggregate_type != expected_type:
        raise exceptions.UnsupportedNotificationSourceEventError(
            "notification source aggregate type is invalid"
        )
    return _required_uuid(event.aggregate_id, "aggregate_id")


def _required_uuid(value: object, field: str) -> uuid.UUID:
    if not isinstance(value, str):
        raise RuntimeError(f"{field} must be a UUID")
    try:
        return uuid.UUID(value)
    except ValueError:
        raise RuntimeError(f"{field} must be a UUID") from None


def _safe_reason(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned[:500] or None


def _expiry_snapshot_from_event(event: EventOutbox) -> tuple[datetime, str] | None:
    if set(event.payload) != {"expected_expires_at", "notification_kind"}:
        return None
    notification_kind = event.payload.get("notification_kind")
    if not isinstance(notification_kind, str):
        return None
    expected_event_type = {
        "warning": events.DOCUMENT_FILE_EXPIRING,
        "expired": events.DOCUMENT_FILE_EXPIRED,
    }.get(notification_kind)
    if expected_event_type != event.event_type:
        return None
    value = event.payload.get("expected_expires_at")
    if not isinstance(value, str):
        return None
    try:
        expected_expires_at = datetime.fromisoformat(value)
    except ValueError:
        return None
    if expected_expires_at.tzinfo is None:
        return None
    expiry_status = "expired" if notification_kind == "expired" else "expiring"
    return expected_expires_at, expiry_status


def _display_date(value: datetime) -> str:
    return value.date().isoformat()
