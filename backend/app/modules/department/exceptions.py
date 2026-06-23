from __future__ import annotations

from dataclasses import dataclass

from starlette import status

from app.core.exceptions import ErrorCode


@dataclass(frozen=True)
class DepartmentError(Exception):
    error_code: ErrorCode
    message: str
    status_code: int


def department_not_found() -> DepartmentError:
    return DepartmentError(
        ErrorCode.DEPARTMENT_NOT_FOUND, "department not found", status.HTTP_404_NOT_FOUND
    )


def name_conflict() -> DepartmentError:
    return DepartmentError(
        ErrorCode.DEPARTMENT_NAME_CONFLICT,
        "department name already exists",
        status.HTTP_409_CONFLICT,
    )


def code_conflict() -> DepartmentError:
    return DepartmentError(
        ErrorCode.DEPARTMENT_CODE_CONFLICT,
        "department code already exists",
        status.HTTP_409_CONFLICT,
    )


def department_disabled() -> DepartmentError:
    return DepartmentError(
        ErrorCode.DEPARTMENT_DISABLED, "department is disabled", status.HTTP_409_CONFLICT
    )


def unassigned_immutable() -> DepartmentError:
    return DepartmentError(
        ErrorCode.UNASSIGNED_DEPARTMENT_IMMUTABLE,
        "unassigned department cannot be changed",
        status.HTTP_409_CONFLICT,
    )


def managed_departments_require_dept_admin() -> DepartmentError:
    return DepartmentError(
        ErrorCode.MANAGED_DEPARTMENTS_REQUIRE_DEPT_ADMIN,
        "managed departments require dept_admin role",
        status.HTTP_409_CONFLICT,
    )


def user_not_found() -> DepartmentError:
    return DepartmentError(ErrorCode.VALIDATION_ERROR, "user not found", status.HTTP_404_NOT_FOUND)
