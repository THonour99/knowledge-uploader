from __future__ import annotations

from datetime import datetime
from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.permissions import SystemAdminDep
from app.core.responses import success_response

from . import exceptions
from .repository import GovernanceMetricsRepository
from .schemas import CapacityGroupBy, PhysicalDimension, RagflowGroupBy, UsageGroupBy
from .service import GovernanceMetricsService, MetricsQuery, RequestContext

router = APIRouter(prefix="/api/admin/statistics", tags=["governance-metrics"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _service(session: AsyncSession) -> GovernanceMetricsService:
    return GovernanceMetricsService(
        session=session,
        repository=GovernanceMetricsRepository(session),
    )


def _raise_error(error: exceptions.GovernanceMetricsError) -> NoReturn:
    raise HTTPException(
        status_code=error.status_code,
        detail={"error_code": error.error_code, "message": error.message},
    )


def _context(request: Request) -> RequestContext:
    client_host = request.client.host if request.client is not None else ""
    return RequestContext(
        ip_address=client_host.strip()[:45] or "unknown",
        user_agent=request.headers.get("user-agent", "").strip()[:512] or "unknown",
    )


def _query(
    *,
    start_at: datetime | None,
    end_before: datetime | None,
    page: int,
    page_size: int,
) -> MetricsQuery:
    return MetricsQuery(
        start_at=start_at,
        end_before=end_before,
        page=page,
        page_size=page_size,
    )


@router.get("/capacity")
async def get_capacity(
    request: Request,
    current_user: SystemAdminDep,
    session: SessionDep,
    start_at: datetime | None = None,
    end_before: datetime | None = None,
    group_by: CapacityGroupBy = "none",
    physical_dimension: PhysicalDimension = "cluster",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, object]:
    try:
        response = await _service(session).capacity(
            current_user=current_user,
            query=_query(
                start_at=start_at,
                end_before=end_before,
                page=page,
                page_size=page_size,
            ),
            group_by=group_by,
            physical_dimension=physical_dimension,
            context=_context(request),
        )
    except exceptions.GovernanceMetricsError as error:
        _raise_error(error)
    return success_response(response.model_dump(mode="json"), request)


@router.get("/llm-usage")
async def get_llm_usage(
    request: Request,
    current_user: SystemAdminDep,
    session: SessionDep,
    start_at: datetime | None = None,
    end_before: datetime | None = None,
    group_by: UsageGroupBy = "none",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, object]:
    try:
        response = await _service(session).llm_usage(
            current_user=current_user,
            query=_query(
                start_at=start_at,
                end_before=end_before,
                page=page,
                page_size=page_size,
            ),
            group_by=group_by,
            context=_context(request),
        )
    except exceptions.GovernanceMetricsError as error:
        _raise_error(error)
    return success_response(response.model_dump(mode="json"), request)


@router.get("/ragflow-usage")
async def get_ragflow_usage(
    request: Request,
    current_user: SystemAdminDep,
    session: SessionDep,
    start_at: datetime | None = None,
    end_before: datetime | None = None,
    group_by: RagflowGroupBy = "none",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, object]:
    try:
        response = await _service(session).ragflow_usage(
            current_user=current_user,
            query=_query(
                start_at=start_at,
                end_before=end_before,
                page=page,
                page_size=page_size,
            ),
            group_by=group_by,
            context=_context(request),
        )
    except exceptions.GovernanceMetricsError as error:
        _raise_error(error)
    return success_response(response.model_dump(mode="json"), request)
