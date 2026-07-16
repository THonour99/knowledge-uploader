from __future__ import annotations

from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.permissions import CurrentUserDep
from app.core.responses import success_response

from . import exceptions
from .repository import DashboardRepository
from .schemas import DashboardEnvelope
from .service import (
    DashboardQuery,
    DashboardService,
    RequestContext,
)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _service(session: AsyncSession) -> DashboardService:
    return DashboardService(
        session=session,
        repository=DashboardRepository(session),
    )


def _context_from(request: Request) -> RequestContext:
    client_host = request.client.host if request.client is not None else ""
    return RequestContext(
        ip_address=client_host.strip()[:45] or "unknown",
        user_agent=request.headers.get("user-agent", "").strip()[:512] or "unknown",
    )


def _raise_dashboard_error(error: exceptions.DashboardError) -> NoReturn:
    raise HTTPException(
        status_code=error.status_code,
        detail={"error_code": error.error_code, "message": error.message},
    )


@router.get("", response_model=DashboardEnvelope)
async def get_dashboard(
    request: Request,
    current_user: CurrentUserDep,
    session: SessionDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 10,
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> dict[str, object]:
    try:
        response = await _service(session).get_dashboard(
            current_user=current_user,
            query=DashboardQuery(page=page, page_size=page_size, q=q),
            context=_context_from(request),
        )
    except exceptions.DashboardError as error:
        _raise_dashboard_error(error)
    return success_response(response.model_dump(mode="json"), request)
