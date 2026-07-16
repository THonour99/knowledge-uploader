from __future__ import annotations

from dataclasses import dataclass

from starlette import status


@dataclass(frozen=True)
class DashboardError(Exception):
    status_code: int
    error_code: str
    message: str


def permission_denied() -> DashboardError:
    return DashboardError(
        status_code=status.HTTP_403_FORBIDDEN,
        error_code="DASHBOARD_PERMISSION_DENIED",
        message="dashboard access is not permitted for this role",
    )


def unavailable() -> DashboardError:
    return DashboardError(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        error_code="DASHBOARD_UNAVAILABLE",
        message="dashboard data is temporarily unavailable",
    )
