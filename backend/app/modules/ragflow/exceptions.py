from __future__ import annotations

from dataclasses import dataclass

from starlette import status

from app.core.exceptions import ErrorCode


@dataclass(frozen=True)
class RagflowTaskError(Exception):
    error_code: ErrorCode
    message: str
    status_code: int


def permission_denied() -> RagflowTaskError:
    return RagflowTaskError(
        ErrorCode.PERMISSION_DENIED,
        "permission denied",
        status.HTTP_403_FORBIDDEN,
    )


def task_not_found() -> RagflowTaskError:
    return RagflowTaskError(
        ErrorCode.VALIDATION_ERROR,
        "task not found",
        status.HTTP_404_NOT_FOUND,
    )


def task_not_retryable() -> RagflowTaskError:
    return RagflowTaskError(
        ErrorCode.VALIDATION_ERROR,
        "task cannot be retried",
        status.HTTP_400_BAD_REQUEST,
    )


def task_not_cancelable() -> RagflowTaskError:
    return RagflowTaskError(
        ErrorCode.VALIDATION_ERROR,
        "task cannot be canceled",
        status.HTTP_400_BAD_REQUEST,
    )
