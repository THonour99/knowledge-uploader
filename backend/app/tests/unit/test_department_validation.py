from __future__ import annotations

import uuid
from typing import Any, cast

import pytest
from pydantic import ValidationError

from app.core.exceptions import ErrorCode
from app.modules.department.exceptions import DepartmentError
from app.modules.department.models import UNASSIGNED_DEPARTMENT_ID, Department
from app.modules.department.schemas import DepartmentCreateRequest, DepartmentUpdateRequest
from app.modules.department.service import DepartmentService, RequestContext
from app.modules.user.schemas import AuthUserRecord


class _FakeDepartmentRepository:
    def __init__(self, department: Department) -> None:
        self._department = department

    async def get_department(self, department_id: uuid.UUID) -> Department | None:
        if department_id == self._department.id:
            return self._department
        return None


def _actor() -> AuthUserRecord:
    return AuthUserRecord(
        id=uuid.uuid4(),
        name="Department Validator",
        email="department-validator@company.com",
        role="system_admin",
        status="active",
        email_verified=True,
        department_id=UNASSIGNED_DEPARTMENT_ID,
        department_name="Unassigned",
        department_code="unassigned",
        department="Unassigned",
        phone=None,
        email_domain="company.com",
        password_hash="x",
        failed_login_count=0,
        locked_until=None,
        session_version=0,
    )


def test_department_create_request_strips_valid_name_and_code() -> None:
    payload = DepartmentCreateRequest(name="  Legal Validation  ", code="  legal-team  ")

    assert payload.name == "Legal Validation"
    assert payload.code == "legal-team"


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "   ", "code": "blank-name"},
        {"name": "Blank Code", "code": "   "},
    ],
)
def test_department_create_request_rejects_blank_required_text(
    payload: dict[str, str],
) -> None:
    with pytest.raises(ValidationError):
        DepartmentCreateRequest(**payload)


def test_department_update_request_strips_valid_name() -> None:
    payload = DepartmentUpdateRequest(name="  Legal Validation Updated  ")

    assert payload.name == "Legal Validation Updated"


@pytest.mark.parametrize("name", ["   ", "\t\n"])
def test_department_update_request_rejects_blank_name(name: str) -> None:
    with pytest.raises(ValidationError):
        DepartmentUpdateRequest(name=name)


@pytest.mark.asyncio
async def test_department_service_rejects_blank_direct_inputs() -> None:
    department = Department(
        id=uuid.uuid4(),
        name="Existing Department",
        code="existing-department",
        status="active",
    )
    service = DepartmentService(
        session=cast(Any, object()),
        repository=cast(Any, _FakeDepartmentRepository(department)),
    )
    context = RequestContext(ip_address="127.0.0.1", user_agent="pytest")

    with pytest.raises(DepartmentError) as blank_name_error:
        await service.create_department(
            actor=_actor(),
            name="   ",
            code="service-valid-code",
            context=context,
        )
    with pytest.raises(DepartmentError) as blank_code_error:
        await service.create_department(
            actor=_actor(),
            name="Service Valid Name",
            code="   ",
            context=context,
        )
    with pytest.raises(DepartmentError) as blank_update_error:
        await service.update_department(
            actor=_actor(),
            department_id=department.id,
            name="   ",
            status=None,
            context=context,
        )

    assert blank_name_error.value.error_code == ErrorCode.VALIDATION_ERROR
    assert blank_code_error.value.error_code == ErrorCode.VALIDATION_ERROR
    assert blank_update_error.value.error_code == ErrorCode.VALIDATION_ERROR
