from __future__ import annotations

from dataclasses import dataclass

from starlette import status

from app.core.exceptions import ErrorCode


@dataclass(frozen=True)
class GovernanceMetricsError(Exception):
    error_code: ErrorCode
    message: str
    status_code: int


def permission_denied() -> GovernanceMetricsError:
    return GovernanceMetricsError(
        ErrorCode.PERMISSION_DENIED,
        "permission denied",
        status.HTTP_403_FORBIDDEN,
    )


def invalid_query(message: str) -> GovernanceMetricsError:
    return GovernanceMetricsError(
        ErrorCode.VALIDATION_ERROR,
        message,
        status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


def aggregate_invariant_violation() -> GovernanceMetricsError:
    return GovernanceMetricsError(
        ErrorCode.INTERNAL_ERROR,
        "governance metrics aggregate invariant violation",
        status.HTTP_500_INTERNAL_SERVER_ERROR,
    )
