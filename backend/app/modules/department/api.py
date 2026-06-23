from __future__ import annotations

import uuid
from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.permissions import SystemAdminDep
from app.core.responses import success_response

from .exceptions import DepartmentError
from .repository import DepartmentRepository
from .schemas import (
    DepartmentCreateRequest,
    DepartmentResponse,
    DepartmentUpdateRequest,
    ManagedDepartmentsResponse,
    ReplaceManagedDepartmentsRequest,
)
from .service import DepartmentService, RequestContext

router = APIRouter(prefix="/api/admin/departments", tags=["departments"])
managed_router = APIRouter(prefix="/api/admin/users", tags=["departments"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _service(session: AsyncSession) -> DepartmentService:
    return DepartmentService(session=session, repository=DepartmentRepository(session))


def _context(request: Request) -> RequestContext:
    return RequestContext(
        ip_address=request.client.host if request.client is not None else "unknown",
        user_agent=request.headers.get("user-agent", "unknown")[:512],
    )


def _raise_department_error(error: DepartmentError) -> NoReturn:
    from fastapi import HTTPException

    raise HTTPException(
        status_code=error.status_code,
        detail={"error_code": error.error_code, "message": error.message},
    )


@router.get("")
async def list_departments(
    request: Request,
    current_user: SystemAdminDep,
    session: SessionDep,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    search: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> dict[str, object]:
    try:
        result = await _service(session).list_departments(
            actor=current_user,
            page=page,
            page_size=page_size,
            search=search,
            status=status,
            context=_context(request),
        )
    except DepartmentError as error:
        _raise_department_error(error)
    return success_response(result.model_dump(mode="json"), request)


@router.post("", status_code=201)
async def create_department(
    payload: DepartmentCreateRequest,
    request: Request,
    current_user: SystemAdminDep,
    session: SessionDep,
) -> dict[str, object]:
    try:
        department = await _service(session).create_department(
            actor=current_user,
            name=payload.name,
            code=payload.code,
            context=_context(request),
        )
    except DepartmentError as error:
        _raise_department_error(error)
    return success_response(
        DepartmentResponse.model_validate(department).model_dump(mode="json"), request
    )


@router.get("/{department_id}")
async def get_department(
    department_id: uuid.UUID,
    request: Request,
    current_user: SystemAdminDep,
    session: SessionDep,
) -> dict[str, object]:
    try:
        department = await _service(session).get_department(
            actor=current_user,
            department_id=department_id,
            context=_context(request),
        )
    except DepartmentError as error:
        _raise_department_error(error)
    return success_response(
        DepartmentResponse.model_validate(department).model_dump(mode="json"), request
    )


@router.patch("/{department_id}")
async def update_department(
    department_id: uuid.UUID,
    payload: DepartmentUpdateRequest,
    request: Request,
    current_user: SystemAdminDep,
    session: SessionDep,
) -> dict[str, object]:
    try:
        department = await _service(session).update_department(
            actor=current_user,
            department_id=department_id,
            name=payload.name,
            status=payload.status,
            context=_context(request),
        )
    except DepartmentError as error:
        _raise_department_error(error)
    return success_response(
        DepartmentResponse.model_validate(department).model_dump(mode="json"), request
    )


@router.delete("/{department_id}")
async def disable_department(
    department_id: uuid.UUID,
    request: Request,
    current_user: SystemAdminDep,
    session: SessionDep,
) -> dict[str, object]:
    try:
        await _service(session).disable_department(
            actor=current_user,
            department_id=department_id,
            context=_context(request),
        )
    except DepartmentError as error:
        _raise_department_error(error)
    return success_response({}, request)


@managed_router.get("/{user_id}/managed-departments")
async def get_managed_departments(
    user_id: uuid.UUID,
    request: Request,
    current_user: SystemAdminDep,
    session: SessionDep,
) -> dict[str, object]:
    try:
        departments = await _service(session).get_managed_departments(
            actor=current_user,
            user_id=user_id,
            context=_context(request),
        )
    except DepartmentError as error:
        _raise_department_error(error)
    result = ManagedDepartmentsResponse(
        user_id=user_id,
        departments=[DepartmentResponse.model_validate(item) for item in departments],
    )
    return success_response(result.model_dump(mode="json"), request)


@managed_router.put("/{user_id}/managed-departments")
async def replace_managed_departments(
    user_id: uuid.UUID,
    payload: ReplaceManagedDepartmentsRequest,
    request: Request,
    current_user: SystemAdminDep,
    session: SessionDep,
) -> dict[str, object]:
    try:
        departments = await _service(session).replace_managed_departments(
            actor=current_user,
            user_id=user_id,
            department_ids=payload.department_ids,
            context=_context(request),
        )
    except DepartmentError as error:
        _raise_department_error(error)
    result = ManagedDepartmentsResponse(
        user_id=user_id,
        departments=[DepartmentResponse.model_validate(item) for item in departments],
    )
    return success_response(result.model_dump(mode="json"), request)
