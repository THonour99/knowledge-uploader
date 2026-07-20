from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from app.core.database import get_session
from app.core.permissions import CurrentUserDep
from app.core.responses import success_response

from .exceptions import (
    AnnouncementConflictError,
    AnnouncementNotFoundError,
    AnnouncementValidationError,
)
from .permissions import AnnouncementAdminDep
from .repository import AnnouncementRepository
from .schemas import (
    AnnouncementCreateRequest,
    AnnouncementPublishRequest,
    AnnouncementUpdateRequest,
    AnnouncementVersionRequest,
    AnnouncementWithdrawRequest,
    LifecycleFilter,
    PublicStateFilter,
)
from .service import (
    AnnouncementService,
    RequestAuditContext,
)

router = APIRouter(prefix="/api/announcements", tags=["announcement"])
admin_router = APIRouter(prefix="/api/admin/announcements", tags=["announcement-admin"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _service(session: AsyncSession) -> AnnouncementService:
    return AnnouncementService(session=session, repository=AnnouncementRepository(session))


def _audit_context(request: Request) -> RequestAuditContext:
    return RequestAuditContext(
        ip_address=request.client.host if request.client else "",
        user_agent=request.headers.get("user-agent", "")[:512],
    )


T = TypeVar("T")


def _translate_errors(operation: Callable[[], Awaitable[T]]) -> Awaitable[T]:
    async def run() -> T:
        try:
            return await operation()
        except AnnouncementNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error_code": "ANNOUNCEMENT_NOT_FOUND",
                    "message": "announcement not found",
                },
            ) from exc
        except AnnouncementConflictError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"error_code": "ANNOUNCEMENT_CONFLICT", "message": str(exc)},
            ) from exc
        except AnnouncementValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"error_code": "ANNOUNCEMENT_INVALID", "message": str(exc)},
            ) from exc

    return run()


@router.get("")
async def list_announcements(
    request: Request,
    current_user: CurrentUserDep,
    session: SessionDep,
    state: PublicStateFilter = "active",
    unread_only: bool = False,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, object]:
    result = await _service(session).list_public(
        current_user=current_user,
        state=state,
        unread_only=unread_only,
        page=page,
        page_size=page_size,
    )
    return success_response(result.model_dump(mode="json"), request)


@router.get("/{announcement_id}")
async def get_announcement(
    announcement_id: uuid.UUID,
    request: Request,
    current_user: CurrentUserDep,
    session: SessionDep,
) -> dict[str, object]:
    result = await _translate_errors(
        lambda: _service(session).get_public(
            announcement_id=announcement_id, current_user=current_user
        )
    )
    return success_response(result.model_dump(mode="json"), request)


@router.post("/{announcement_id}/read")
async def mark_announcement_read(
    announcement_id: uuid.UUID,
    request: Request,
    current_user: CurrentUserDep,
    session: SessionDep,
) -> dict[str, object]:
    result = await _translate_errors(
        lambda: _service(session).mark_read(
            announcement_id=announcement_id, current_user=current_user
        )
    )
    return success_response(result.model_dump(mode="json"), request)


@admin_router.get("")
async def list_admin_announcements(
    request: Request,
    current_user: AnnouncementAdminDep,
    session: SessionDep,
    state: LifecycleFilter = "all",
    search: Annotated[str | None, Query(max_length=200)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, object]:
    result = await _service(session).list_admin(
        actor=current_user,
        audit=_audit_context(request),
        state=state,
        search=search,
        page=page,
        page_size=page_size,
    )
    return success_response(result.model_dump(mode="json"), request)


@admin_router.post("")
async def create_announcement(
    payload: AnnouncementCreateRequest,
    request: Request,
    current_user: AnnouncementAdminDep,
    session: SessionDep,
) -> dict[str, object]:
    result = await _translate_errors(
        lambda: _service(session).create(
            payload=payload, actor=current_user, audit=_audit_context(request)
        )
    )
    return success_response(result.model_dump(mode="json"), request)


@admin_router.get("/{announcement_id}")
async def get_admin_announcement(
    announcement_id: uuid.UUID,
    request: Request,
    current_user: AnnouncementAdminDep,
    session: SessionDep,
) -> dict[str, object]:
    result = await _translate_errors(
        lambda: _service(session).get_admin(
            announcement_id=announcement_id, actor=current_user, audit=_audit_context(request)
        )
    )
    return success_response(result.model_dump(mode="json"), request)


@admin_router.patch("/{announcement_id}")
async def update_announcement(
    announcement_id: uuid.UUID,
    payload: AnnouncementUpdateRequest,
    request: Request,
    current_user: AnnouncementAdminDep,
    session: SessionDep,
) -> dict[str, object]:
    result = await _translate_errors(
        lambda: _service(session).update(
            announcement_id=announcement_id,
            payload=payload,
            actor=current_user,
            audit=_audit_context(request),
        )
    )
    return success_response(result.model_dump(mode="json"), request)


@admin_router.delete("/{announcement_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_announcement(
    announcement_id: uuid.UUID,
    payload: AnnouncementVersionRequest,
    request: Request,
    current_user: AnnouncementAdminDep,
    session: SessionDep,
) -> Response:
    await _translate_errors(
        lambda: _service(session).delete(
            announcement_id=announcement_id,
            row_version=payload.row_version,
            actor=current_user,
            audit=_audit_context(request),
        )
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@admin_router.post("/{announcement_id}/publish")
async def publish_announcement(
    announcement_id: uuid.UUID,
    payload: AnnouncementPublishRequest,
    request: Request,
    current_user: AnnouncementAdminDep,
    session: SessionDep,
) -> dict[str, object]:
    result = await _translate_errors(
        lambda: _service(session).publish(
            announcement_id=announcement_id,
            payload=payload,
            actor=current_user,
            audit=_audit_context(request),
        )
    )
    return success_response(result.model_dump(mode="json"), request)


@admin_router.post("/{announcement_id}/withdraw")
async def withdraw_announcement(
    announcement_id: uuid.UUID,
    payload: AnnouncementWithdrawRequest,
    request: Request,
    current_user: AnnouncementAdminDep,
    session: SessionDep,
) -> dict[str, object]:
    result = await _translate_errors(
        lambda: _service(session).withdraw(
            announcement_id=announcement_id,
            payload=payload,
            actor=current_user,
            audit=_audit_context(request),
        )
    )
    return success_response(result.model_dump(mode="json"), request)


@admin_router.post("/{announcement_id}/clone")
async def clone_announcement(
    announcement_id: uuid.UUID,
    payload: AnnouncementVersionRequest,
    request: Request,
    current_user: AnnouncementAdminDep,
    session: SessionDep,
) -> dict[str, object]:
    result = await _translate_errors(
        lambda: _service(session).clone(
            announcement_id=announcement_id,
            row_version=payload.row_version,
            actor=current_user,
            audit=_audit_context(request),
        )
    )
    return success_response(result.model_dump(mode="json"), request)


@admin_router.get("/{announcement_id}/stats")
async def announcement_stats(
    announcement_id: uuid.UUID,
    request: Request,
    current_user: AnnouncementAdminDep,
    session: SessionDep,
) -> dict[str, object]:
    result = await _translate_errors(
        lambda: _service(session).stats(
            announcement_id=announcement_id, actor=current_user, audit=_audit_context(request)
        )
    )
    return success_response(result.model_dump(mode="json"), request)
