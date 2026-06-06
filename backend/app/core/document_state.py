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
        ("uploaded", "pending_review"),
        ("analyzed", "pending_review"),
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
    }

    @classmethod
    def transition(cls, from_status: str, to_status: str) -> str:
        if (from_status, to_status) not in cls._allowed_transitions:
            raise DocumentStateError(from_status=from_status, to_status=to_status)
        return to_status


def document_state_error_code() -> ErrorCode:
    return ErrorCode.VALIDATION_ERROR
