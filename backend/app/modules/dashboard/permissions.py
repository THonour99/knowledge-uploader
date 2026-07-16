from __future__ import annotations

from typing import Final

from app.core.identity import has_assigned_department
from app.core.permissions import Role
from app.modules.user.schemas import AuthUserRecord

from . import exceptions

SUPPORTED_ROLES: Final = frozenset(role.value for role in Role)


def ensure_supported_role(current_user: AuthUserRecord) -> None:
    if current_user.role not in SUPPORTED_ROLES:
        raise exceptions.permission_denied()


def employee_department_is_ready(current_user: AuthUserRecord) -> bool:
    return has_assigned_department(current_user)
