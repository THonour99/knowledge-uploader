from __future__ import annotations

from typing import ClassVar

from app.core.exceptions import ErrorCode


class DocumentStateError(Exception):
    def __init__(self, *, from_status: str, to_status: str) -> None:
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(f"invalid document transition: {from_status} -> {to_status}")


class DocumentStateMachine:
    _allowed_transitions: ClassVar[set[tuple[str, str]]] = {
        ("uploaded", "extracting_text"),
        ("extracting_text", "analysis_queued"),
        ("analysis_queued", "analyzing"),
        ("extracting_text", "analysis_failed"),
        ("analysis_queued", "analysis_failed"),
        ("analyzing", "analysis_failed"),
        ("analysis_failed", "extracting_text"),
        ("analysis_failed", "analysis_queued"),
        ("analyzed", "analysis_queued"),
        ("analyzing", "analysis_queued"),
        ("analyzing", "analyzed"),
        ("analyzing", "sensitive_review_required"),
        ("uploaded", "pending_review"),
        ("analyzed", "pending_review"),
        ("analysis_failed", "pending_review"),
        ("sensitive_review_required", "pending_review"),
        ("pending_review", "approved"),
        ("pending_review", "rejected"),
        ("approved", "queued"),
        ("queued", "syncing"),
        ("queued", "parsing"),
        ("syncing", "uploaded_to_ragflow"),
        ("uploaded_to_ragflow", "parsing"),
        ("parsing", "parsed"),
        ("syncing", "failed"),
        ("uploaded_to_ragflow", "failed"),
        ("parsing", "failed"),
        ("failed", "syncing"),
        ("failed", "parsing"),
        # 管理员重新分析: 把已完成分析的文件重置回可重试状态 (PRD 6.10.2)
        ("analyzed", "analysis_failed"),
        # 归档 (-> disabled): 仅允许稳定态进入 (PRD 6.4 / 6.10)
        ("approved", "disabled"),
        ("parsed", "disabled"),
        ("failed", "disabled"),
        ("rejected", "disabled"),
        ("analyzed", "disabled"),
        ("pending_review", "disabled"),
        # 软删 (-> deleted): 流水线中间态 (queued/syncing/parsing 等) 不允许直接删除
        ("uploaded", "deleted"),
        ("pending_review", "deleted"),
        ("approved", "deleted"),
        ("rejected", "deleted"),
        ("failed", "deleted"),
        ("parsed", "deleted"),
        ("analysis_failed", "deleted"),
        ("analyzed", "deleted"),
        ("sensitive_review_required", "deleted"),
        ("disabled", "deleted"),
        ("deleted", "ragflow_cleanup_failed"),
        ("ragflow_cleanup_failed", "deleted"),
    }

    @classmethod
    def transition(cls, from_status: str, to_status: str) -> str:
        if (from_status, to_status) not in cls._allowed_transitions:
            raise DocumentStateError(from_status=from_status, to_status=to_status)
        return to_status


def document_state_error_code() -> ErrorCode:
    return ErrorCode.VALIDATION_ERROR
