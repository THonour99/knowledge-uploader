from __future__ import annotations

from dataclasses import dataclass

from starlette import status

from app.core.exceptions import ErrorCode


@dataclass(frozen=True)
class StatisticsError(Exception):
    error_code: ErrorCode
    message: str
    status_code: int


def permission_denied() -> StatisticsError:
    return StatisticsError(
        ErrorCode.PERMISSION_DENIED,
        "permission denied",
        status.HTTP_403_FORBIDDEN,
    )


def invalid_filter(message: str = "invalid statistics filter") -> StatisticsError:
    return StatisticsError(
        ErrorCode.VALIDATION_ERROR,
        message,
        status.HTTP_400_BAD_REQUEST,
    )


def user_not_found() -> StatisticsError:
    return StatisticsError(
        ErrorCode.VALIDATION_ERROR,
        "user not found",
        status.HTTP_404_NOT_FOUND,
    )
