from __future__ import annotations

from dataclasses import dataclass

from starlette import status

from app.core.exceptions import ErrorCode


@dataclass(frozen=True)
class AiError(Exception):
    error_code: ErrorCode
    message: str
    status_code: int


def permission_denied() -> AiError:
    return AiError(
        ErrorCode.PERMISSION_DENIED,
        "permission denied",
        status.HTTP_403_FORBIDDEN,
    )


def provider_not_found() -> AiError:
    return AiError(
        ErrorCode.VALIDATION_ERROR,
        "ai provider not found",
        status.HTTP_404_NOT_FOUND,
    )


def feature_not_found() -> AiError:
    return AiError(
        ErrorCode.VALIDATION_ERROR,
        "ai feature not found",
        status.HTTP_404_NOT_FOUND,
    )


def invalid_provider_config(message: str = "invalid ai provider config") -> AiError:
    return AiError(
        ErrorCode.VALIDATION_ERROR,
        message,
        status.HTTP_400_BAD_REQUEST,
    )


class AiAnalysisPreconditionError(Exception):
    pass


class AiAnalysisTransientError(Exception):
    pass


class DocumentParseError(Exception):
    def __init__(self, *, format: str, reason: str) -> None:
        self.format = format
        self.reason = reason
        super().__init__(f"{format} 解析失败: {reason}")
