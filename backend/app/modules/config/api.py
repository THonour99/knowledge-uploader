from __future__ import annotations

from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.responses import success_response

from .exceptions import ConfigError
from .permissions import SystemAdminDep
from .repository import ConfigRepository  # noqa: TID251 - same-module repository dependency
from .schemas import ConfigUpdateRequest
from .service import (  # noqa: TID251 - same-module service dependency
    ConfigService,
    RequestContext,
)

router = APIRouter(tags=["config"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _service(session: AsyncSession) -> ConfigService:
    return ConfigService(session=session, repository=ConfigRepository(session))


def _raise_config_error(error: ConfigError) -> NoReturn:
    raise HTTPException(
        status_code=error.status_code,
        detail={"error_code": error.error_code, "message": error.message},
    )


def _context_from(request: Request) -> RequestContext:
    client_host = request.client.host if request.client is not None else ""
    ip_address = client_host.strip()[:45] or "unknown"
    user_agent = request.headers.get("user-agent", "").strip()[:512] or "unknown"
    return RequestContext(
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.get("/api/admin/configs")
async def get_configs(
    request: Request,
    current_user: SystemAdminDep,
    session: SessionDep,
    group: Annotated[str, Query(min_length=1)],
) -> dict[str, object]:
    try:
        response = await _service(session).get_group(
            group=group,
            current_user=current_user,
            context=_context_from(request),
        )
    except ConfigError as error:
        _raise_config_error(error)
    return success_response(response.model_dump(mode="json"), request)


@router.put("/api/admin/configs/{group}")
async def update_configs(
    group: str,
    payload: ConfigUpdateRequest,
    request: Request,
    current_user: SystemAdminDep,
    session: SessionDep,
) -> dict[str, object]:
    try:
        response = await _service(session).update_group(
            group=group,
            items=payload.items,
            current_user=current_user,
            context=_context_from(request),
        )
    except ConfigError as error:
        _raise_config_error(error)
    return success_response(response.model_dump(mode="json"), request)
