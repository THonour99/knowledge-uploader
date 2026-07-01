from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any

from fastapi import Depends, HTTPException, status

from app.core.deps import get_current_user
from app.core.exceptions import ErrorCode
from app.modules.user.schemas import AuthUserRecord


class Role(StrEnum):
    EMPLOYEE = "employee"
    DEPT_ADMIN = "dept_admin"
    SYSTEM_ADMIN = "system_admin"


CurrentUserDep = Annotated[AuthUserRecord, Depends(get_current_user)]


def permission_denied() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"error_code": ErrorCode.PERMISSION_DENIED, "message": "permission denied"},
    )


def ensure_role(current_role: Role, allowed_roles: set[Role]) -> None:
    if current_role not in allowed_roles:
        raise permission_denied()


def require_role(*allowed_roles: Role) -> Any:
    allowed_values = {role.value for role in allowed_roles}

    def dependency(current_user: CurrentUserDep) -> AuthUserRecord:
        if current_user.role not in allowed_values:
            raise permission_denied()
        return current_user

    return Depends(dependency)


AnyAdminDep = Annotated[
    AuthUserRecord,
    require_role(Role.DEPT_ADMIN, Role.SYSTEM_ADMIN),
]
# Deprecated name retained for existing imports; new code should choose
# SystemAdminDep for global settings or ScopedAdminDep for file-domain actions.
AdminUserDep = AnyAdminDep
SystemAdminDep = Annotated[AuthUserRecord, require_role(Role.SYSTEM_ADMIN)]
