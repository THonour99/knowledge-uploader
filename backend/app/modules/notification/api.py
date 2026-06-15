from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from app.core.database import get_session
from app.core.permissions import CurrentUserDep
from app.core.responses import success_response

from .repository import NotificationRepository  # noqa: TID251 - same-module repository dependency
from .schemas import NotificationItem
from .service import NotificationPage, NotificationService  # noqa: TID251 - same-module service

router = APIRouter(prefix="/api/notifications", tags=["notification"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _service(session: AsyncSession) -> NotificationService:
    return NotificationService(
        session=session,
        repository=NotificationRepository(session),
    )


@router.get("")
async def list_notifications(
    request: Request,
    current_user: CurrentUserDep,
    session: SessionDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    unread_only: bool = False,
) -> dict[str, object]:
    response = await _service(session).list_user_notifications(
        user_id=current_user.id,
        page=NotificationPage(page=page, page_size=page_size, unread_only=unread_only),
    )
    return success_response(response.model_dump(mode="json"), request)


@router.post("/{notification_id}/read")
async def mark_notification_read(
    notification_id: uuid.UUID,
    request: Request,
    current_user: CurrentUserDep,
    session: SessionDep,
) -> dict[str, object]:
    notification = await _service(session).mark_read(
        notification_id=notification_id,
        user_id=current_user.id,
    )
    if notification is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "NOTIFICATION_NOT_FOUND",
                "message": "notification not found",
            },
        )
    return success_response(
        NotificationItem.from_model(notification).model_dump(mode="json"),
        request,
    )
