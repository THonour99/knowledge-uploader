from __future__ import annotations

from datetime import date
from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.permissions import AdminUserDep
from app.core.responses import success_response

from . import exceptions
from .repository import StatisticsRepository  # noqa: TID251 - same-module repository dependency
from .service import (  # noqa: TID251 - same-module service dependency
    RequestContext,
    StatisticsQuery,
    StatisticsService,
)

router = APIRouter(prefix="/api/admin/statistics", tags=["statistics"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _service(session: AsyncSession) -> StatisticsService:
    return StatisticsService(
        session=session,
        repository=StatisticsRepository(session),
    )


def _raise_statistics_error(error: exceptions.StatisticsError) -> NoReturn:
    raise HTTPException(
        status_code=error.status_code,
        detail={"error_code": error.error_code, "message": error.message},
    )


def _context_from(request: Request) -> RequestContext:
    client_host = request.client.host if request.client is not None else ""
    ip_address = client_host.strip()[:45] or "unknown"
    user_agent = request.headers.get("user-agent", "").strip()[:512] or "unknown"
    return RequestContext(ip_address=ip_address, user_agent=user_agent)


def _query_from(
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    department: str | None = None,
    user_id: UUID | None = None,
    category_id: UUID | None = None,
    status: str | None = None,
    review_status: str | None = None,
    sync_status: str | None = None,
    group_by: str = "day",
    page: int = 1,
    page_size: int = 20,
    sort_by: str = "total_files",
    sort_order: str = "desc",
) -> StatisticsQuery:
    return StatisticsQuery(
        start_date=start_date,
        end_date=end_date,
        department=department,
        user_id=user_id,
        category_id=category_id,
        status=status,
        review_status=review_status,
        sync_status=sync_status,
        group_by=group_by,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_order=sort_order,
    )


@router.get("/overview")
async def get_statistics_overview(
    request: Request,
    current_user: AdminUserDep,
    session: SessionDep,
    start_date: date | None = None,
    end_date: date | None = None,
    department: str | None = None,
    user_id: UUID | None = None,
    category_id: UUID | None = None,
    status: str | None = None,
    review_status: str | None = None,
    sync_status: str | None = None,
) -> dict[str, object]:
    try:
        response = await _service(session).overview(
            current_user=current_user,
            query=_query_from(
                start_date=start_date,
                end_date=end_date,
                department=department,
                user_id=user_id,
                category_id=category_id,
                status=status,
                review_status=review_status,
                sync_status=sync_status,
            ),
            context=_context_from(request),
        )
    except exceptions.StatisticsError as error:
        _raise_statistics_error(error)
    return success_response(response.model_dump(mode="json"), request)


@router.get("/users")
async def list_statistics_users(
    request: Request,
    current_user: AdminUserDep,
    session: SessionDep,
    start_date: date | None = None,
    end_date: date | None = None,
    department: str | None = None,
    user_id: UUID | None = None,
    category_id: UUID | None = None,
    status: str | None = None,
    review_status: str | None = None,
    sync_status: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    sort_by: str = "total_files",
    sort_order: str = "desc",
) -> dict[str, object]:
    try:
        response = await _service(session).users(
            current_user=current_user,
            query=_query_from(
                start_date=start_date,
                end_date=end_date,
                department=department,
                user_id=user_id,
                category_id=category_id,
                status=status,
                review_status=review_status,
                sync_status=sync_status,
                page=page,
                page_size=page_size,
                sort_by=sort_by,
                sort_order=sort_order,
            ),
            context=_context_from(request),
        )
    except exceptions.StatisticsError as error:
        _raise_statistics_error(error)
    return success_response(response.model_dump(mode="json"), request)


@router.get("/users/{user_id}")
async def get_statistics_user(
    user_id: UUID,
    request: Request,
    current_user: AdminUserDep,
    session: SessionDep,
    start_date: date | None = None,
    end_date: date | None = None,
    department: str | None = None,
    category_id: UUID | None = None,
    status: str | None = None,
    review_status: str | None = None,
    sync_status: str | None = None,
) -> dict[str, object]:
    try:
        response = await _service(session).user_detail(
            current_user=current_user,
            user_id=user_id,
            query=_query_from(
                start_date=start_date,
                end_date=end_date,
                department=department,
                category_id=category_id,
                status=status,
                review_status=review_status,
                sync_status=sync_status,
            ),
            context=_context_from(request),
        )
    except exceptions.StatisticsError as error:
        _raise_statistics_error(error)
    return success_response(response.model_dump(mode="json"), request)


@router.get("/departments")
async def list_statistics_departments(
    request: Request,
    current_user: AdminUserDep,
    session: SessionDep,
    start_date: date | None = None,
    end_date: date | None = None,
    department: str | None = None,
    user_id: UUID | None = None,
    category_id: UUID | None = None,
    status: str | None = None,
    review_status: str | None = None,
    sync_status: str | None = None,
) -> dict[str, object]:
    try:
        response = await _service(session).departments(
            current_user=current_user,
            query=_query_from(
                start_date=start_date,
                end_date=end_date,
                department=department,
                user_id=user_id,
                category_id=category_id,
                status=status,
                review_status=review_status,
                sync_status=sync_status,
            ),
            context=_context_from(request),
        )
    except exceptions.StatisticsError as error:
        _raise_statistics_error(error)
    return success_response(response.model_dump(mode="json"), request)


@router.get("/categories")
async def list_statistics_categories(
    request: Request,
    current_user: AdminUserDep,
    session: SessionDep,
    start_date: date | None = None,
    end_date: date | None = None,
    department: str | None = None,
    user_id: UUID | None = None,
    category_id: UUID | None = None,
    status: str | None = None,
    review_status: str | None = None,
    sync_status: str | None = None,
) -> dict[str, object]:
    try:
        response = await _service(session).categories(
            current_user=current_user,
            query=_query_from(
                start_date=start_date,
                end_date=end_date,
                department=department,
                user_id=user_id,
                category_id=category_id,
                status=status,
                review_status=review_status,
                sync_status=sync_status,
            ),
            context=_context_from(request),
        )
    except exceptions.StatisticsError as error:
        _raise_statistics_error(error)
    return success_response(response.model_dump(mode="json"), request)


@router.get("/trends")
async def list_statistics_trends(
    request: Request,
    current_user: AdminUserDep,
    session: SessionDep,
    start_date: date | None = None,
    end_date: date | None = None,
    department: str | None = None,
    user_id: UUID | None = None,
    category_id: UUID | None = None,
    status: str | None = None,
    review_status: str | None = None,
    sync_status: str | None = None,
    group_by: str = "day",
) -> dict[str, object]:
    try:
        response = await _service(session).trends(
            current_user=current_user,
            query=_query_from(
                start_date=start_date,
                end_date=end_date,
                department=department,
                user_id=user_id,
                category_id=category_id,
                status=status,
                review_status=review_status,
                sync_status=sync_status,
                group_by=group_by,
            ),
            context=_context_from(request),
        )
    except exceptions.StatisticsError as error:
        _raise_statistics_error(error)
    return success_response(response.model_dump(mode="json"), request)


@router.get("/failures")
async def list_statistics_failures(
    request: Request,
    current_user: AdminUserDep,
    session: SessionDep,
    start_date: date | None = None,
    end_date: date | None = None,
    department: str | None = None,
    user_id: UUID | None = None,
    category_id: UUID | None = None,
    status: str | None = None,
    review_status: str | None = None,
    sync_status: str | None = None,
) -> dict[str, object]:
    try:
        response = await _service(session).failures(
            current_user=current_user,
            query=_query_from(
                start_date=start_date,
                end_date=end_date,
                department=department,
                user_id=user_id,
                category_id=category_id,
                status=status,
                review_status=review_status,
                sync_status=sync_status,
            ),
            context=_context_from(request),
        )
    except exceptions.StatisticsError as error:
        _raise_statistics_error(error)
    return success_response(response.model_dump(mode="json"), request)


@router.get("/export")
async def export_statistics(
    request: Request,
    current_user: AdminUserDep,
    session: SessionDep,
    start_date: date | None = None,
    end_date: date | None = None,
    department: str | None = None,
    user_id: UUID | None = None,
    category_id: UUID | None = None,
    status: str | None = None,
    review_status: str | None = None,
    sync_status: str | None = None,
    sort_by: str = "total_files",
    sort_order: str = "desc",
) -> Response:
    try:
        csv_text = await _service(session).export_users_csv(
            current_user=current_user,
            query=_query_from(
                start_date=start_date,
                end_date=end_date,
                department=department,
                user_id=user_id,
                category_id=category_id,
                status=status,
                review_status=review_status,
                sync_status=sync_status,
                sort_by=sort_by,
                sort_order=sort_order,
            ),
            context=_context_from(request),
        )
    except exceptions.StatisticsError as error:
        _raise_statistics_error(error)
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="statistics.csv"'},
    )
