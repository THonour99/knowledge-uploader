from __future__ import annotations

import structlog

from app.core.outbox import EventOutbox

logger = structlog.get_logger(__name__)

# document 模块发布的文件生命周期事件 (routing keys), ragflow 模块订阅并联动远端删除。
# 决策位 (delete_remote / keep_remote) 由 document 侧写入 payload, 本模块只执行。
DOCUMENT_FILE_DELETED = "document.file.deleted"
DOCUMENT_FILE_ARCHIVED = "document.file.archived"
SUBSCRIBED_DOCUMENT_LIFECYCLE_EVENTS = (DOCUMENT_FILE_DELETED, DOCUMENT_FILE_ARCHIVED)


def resolve_remote_delete_file_id(event: EventOutbox) -> str | None:
    """决策文件删除/归档事件是否需要删除 RAGFlow 远端文档。

    需要删除时返回 file_id; 跳过时返回 None 并记录决策日志。
    payload 缺 file_id 但决策为删除时抛 RuntimeError (事件不完整, 交由 outbox 标记失败)。
    """
    payload = event.payload
    document_id = payload.get("ragflow_document_id")
    if event.event_type == DOCUMENT_FILE_DELETED:
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
