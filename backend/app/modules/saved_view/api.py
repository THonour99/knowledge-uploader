from __future__ import annotations

import uuid
from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.permissions import CurrentUserDep
from app.core.responses import success_response

from .exceptions import SavedViewError
from .repository import SavedViewRepository
from .schemas import (
    PageKey,
    SavedViewCreateRequest,
    SavedViewScope,
    SavedViewUpdateRequest,
)
from .service import RequestContext, SavedViewService

router = APIRouter(prefix="/api/saved-views", tags=["saved-view"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _service(session: AsyncSession) -> SavedViewService:
    return SavedViewService(
        session=session,
        repository=SavedViewRepository(session),
    )


def _raise_saved_view_error(error: SavedViewError) -> NoReturn:
    raise HTTPException(
        status_code=error.status_code,
        detail={"error_code": error.error_code, "message": error.message},
    )


def _context_from(request: Request) -> RequestContext:
    client_host = request.client.host if request.client is not None else ""
    return RequestContext(
        ip_address=client_host.strip()[:45] or "unknown",
        user_agent=request.headers.get("user-agent", "").strip()[:512] or "unknown",
    )


@router.get("")
async def list_saved_views(
    request: Request,
    current_user: CurrentUserDep,
    session: SessionDep,
    page_key: Annotated[PageKey, Query()],
    scope: Annotated[SavedViewScope | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, object]:
    try:
        response = await _service(session).list_saved_views(
            current_user=current_user,
            page_key=page_key,
            scope=scope,
            q=q,
            page=page,
            page_size=page_size,
        )
    except SavedViewError as error:
        _raise_saved_view_error(error)
    return success_response(response.model_dump(mode="json"), request)


@router.get("/{saved_view_id}")
async def get_saved_view(
    saved_view_id: uuid.UUID,
    request: Request,
    current_user: CurrentUserDep,
    session: SessionDep,
) -> dict[str, object]:
    try:
        response = await _service(session).get_saved_view(
            current_user=current_user,
            saved_view_id=saved_view_id,
        )
    except SavedViewError as error:
        _raise_saved_view_error(error)
    return success_response(response.model_dump(mode="json"), request)


@router.post("", status_code=201)
async def create_saved_view(
    payload: SavedViewCreateRequest,
    request: Request,
    current_user: CurrentUserDep,
    session: SessionDep,
) -> dict[str, object]:
    try:
        response = await _service(session).create_saved_view(
            current_user=current_user,
            request=payload,
            context=_context_from(request),
        )
    except SavedViewError as error:
        _raise_saved_view_error(error)
    return success_response(response.model_dump(mode="json"), request)


@router.patch("/{saved_view_id}")
async def update_saved_view(
    saved_view_id: uuid.UUID,
    payload: SavedViewUpdateRequest,
    request: Request,
    current_user: CurrentUserDep,
    session: SessionDep,
) -> dict[str, object]:
    try:
        response = await _service(session).update_saved_view(
            current_user=current_user,
            saved_view_id=saved_view_id,
            request=payload,
            context=_context_from(request),
        )
    except SavedViewError as error:
        _raise_saved_view_error(error)
    return success_response(response.model_dump(mode="json"), request)


@router.delete("/{saved_view_id}", status_code=204)
async def delete_saved_view(
    saved_view_id: uuid.UUID,
    request: Request,
    current_user: CurrentUserDep,
    session: SessionDep,
) -> Response:
    try:
        await _service(session).delete_saved_view(
            current_user=current_user,
            saved_view_id=saved_view_id,
            context=_context_from(request),
        )
    except SavedViewError as error:
        _raise_saved_view_error(error)
    return Response(status_code=204)
