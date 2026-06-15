from __future__ import annotations

import structlog

from app.core.events import EventDispatchContext, EventEnvelope, event_handler
from app.modules.document.events import DocumentFileArchived, DocumentFileDeleted
from app.modules.review.events import ReviewFileApproved

from .events import RagflowSyncTaskQueued

logger = structlog.get_logger(__name__)

# document 模块发布的文件生命周期事件 (routing keys), ragflow 模块订阅并联动远端删除。
# 决策位 (delete_remote / keep_remote) 由 document 侧写入 payload, 本模块只执行。
SUBSCRIBED_DOCUMENT_LIFECYCLE_EVENTS = (
    DocumentFileDeleted.ROUTING_KEY,
    DocumentFileArchived.ROUTING_KEY,
)


def resolve_remote_delete_file_id(event: EventEnvelope) -> str | None:
    """决策文件删除/归档事件是否需要删除 RAGFlow 远端文档。

    需要删除时返回 file_id; 跳过时返回 None 并记录决策日志。
    payload 缺 file_id 但决策为删除时抛 RuntimeError (事件不完整, 交由 outbox 标记失败)。
    """
    payload = event.payload
    document_id = payload.get("ragflow_document_id")
    if event.event_type == DocumentFileDeleted.ROUTING_KEY:
        should_delete = payload.get("delete_remote") is True
        skip_reason = "delete_remote_disabled"
    else:
        should_delete = payload.get("keep_remote") is False
        skip_reason = "keep_remote_enabled"

    if not should_delete:
        logger.info(
            "ragflow_remote_delete_skipped",
            event_type=event.event_type,
            file_id=payload.get("file_id"),
            reason=skip_reason,
        )
        return None
    if not isinstance(document_id, str) or not document_id:
        logger.info(
            "ragflow_remote_delete_skipped",
            event_type=event.event_type,
            file_id=payload.get("file_id"),
            reason="missing_ragflow_document_id",
        )
        return None

    file_id = payload.get("file_id")
    if not isinstance(file_id, str) or not file_id:
        msg = "document lifecycle event missing file_id"
        raise RuntimeError(msg)
    logger.info(
        "ragflow_remote_delete_dispatched",
        event_type=event.event_type,
        file_id=file_id,
    )
    return file_id


@event_handler(DocumentFileDeleted)
@event_handler(DocumentFileArchived)
def queue_remote_delete_task(event: EventEnvelope, context: EventDispatchContext) -> None:
    delete_file_id = resolve_remote_delete_file_id(event)
    if delete_file_id is None:
        return
    context.sender.send_task(
        "ragflow.create_delete_task",
        args=[delete_file_id],
        queue="ragflow_queue",
    )


@event_handler(ReviewFileApproved)
def queue_remote_upload_task_creation(
    event: EventEnvelope,
    context: EventDispatchContext,
) -> None:
    ragflow_dataset_id = event.payload.get("ragflow_dataset_id")
    if not isinstance(ragflow_dataset_id, str) or not ragflow_dataset_id:
        return
    file_id = event.payload.get("file_id")
    if not isinstance(file_id, str) or not file_id:
        msg = "file approved event missing file_id"
        raise RuntimeError(msg)
    context.sender.send_task("ragflow.create_upload_task", args=[file_id], queue="ragflow_queue")


@event_handler(RagflowSyncTaskQueued)
def queue_sync_task_execution(event: EventEnvelope, context: EventDispatchContext) -> None:
    sync_task_id = event.payload.get("sync_task_id")
    if not isinstance(sync_task_id, str) or not sync_task_id:
        msg = "sync task event missing sync_task_id"
        raise RuntimeError(msg)
    if event.payload.get("task_type") == "ragflow_delete":
        context.sender.send_task("ragflow.delete", args=[sync_task_id], queue="ragflow_queue")
        return
    context.sender.send_task("ragflow.upload", args=[sync_task_id], queue="ragflow_queue")
