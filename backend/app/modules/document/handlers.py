from __future__ import annotations

from app.core.config import get_settings
from app.core.events import EventDispatchContext, EventEnvelope, event_handler

from .events import DocumentFileReanalyzeRequested, DocumentFileUploaded


def _require_file_id(event: EventEnvelope, *, message: str) -> str:
    file_id = event.payload.get("file_id")
    if not isinstance(file_id, str) or not file_id:
        raise RuntimeError(message)
    return file_id


@event_handler(DocumentFileReanalyzeRequested)
def queue_file_reanalysis(event: EventEnvelope, context: EventDispatchContext) -> None:
    file_id = _require_file_id(event, message="file reanalyze event missing file_id")
    # 管理员显式触发: 不在此处再校验 AI 开关, 投递后由任务前置条件兜底。
    context.sender.send_task("ai.analyze_file", args=[file_id], queue="ai_queue")


@event_handler(DocumentFileUploaded)
def queue_initial_file_analysis(event: EventEnvelope, context: EventDispatchContext) -> None:
    ai_enabled = event.payload.get("ai_analysis_enabled_at_upload")
    if ai_enabled is not True or not get_settings().ai_analysis_enabled:
        return
    file_id = _require_file_id(event, message="file uploaded event missing file_id")
    context.sender.send_task("ai.analyze_file", args=[file_id], queue="ai_queue")
