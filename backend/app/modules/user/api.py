from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from app.core.database import get_session
from app.core.deps import get_current_user
from app.core.exceptions import ErrorCode
from app.core.responses import success_response
from app.modules.user.models import User
from app.modules.user.schemas import UserProfile
from app.modules.user.service import UserNotFoundError, UserService

router = APIRouter(prefix="/api/users", tags=["users"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
CurrentUserDep = Annotated[User, Depends(get_current_user)]


def _profile(user: User) -> UserProfile:
    return UserProfile(
        id=user.id,
        name=user.name,
        email=user.email,
        role=user.role,
        status=user.status,
        email_verified=user.email_verified,
        department=user.department,
        phone=user.phone,
    )


def _ensure_admin(user: User) -> None:
    if user.role not in {"knowledge_admin", "system_admin"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": ErrorCode.PERMISSION_DENIED, "message": "permission denied"},
        )


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error_code": ErrorCode.VALIDATION_ERROR, "message": "user not found"},
    )


def _client_ip(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"


def _user_agent(request: Request) -> str:
    return request.headers.get("user-agent", "unknown")[:512]


@router.get("")
async def list_users(
    request: Request,
    current_user: CurrentUserDep,
    session: SessionDep,
) -> dict[str, object]:
    _ensure_admin(current_user)
    users = await UserService.from_session(session).list_users()
    return success_response([_profile(user).model_dump(mode="json") for user in users], request)


@router.get("/{user_id}")
async def get_user(
    user_id: uuid.UUID,
    request: Request,
    current_user: CurrentUserDep,
    session: SessionDep,
) -> dict[str, object]:
    _ensure_admin(current_user)
    try:
        user = await UserService.from_session(session).get_user(user_id)
    except UserNotFoundError as exc:
        raise _not_found() from exc
    return success_response(_profile(user).model_dump(mode="json"), request)


@router.post("/{user_id}/disable")
async def disable_user(
    user_id: uuid.UUID,
    request: Request,
    current_user: CurrentUserDep,
    session: SessionDep,
) -> dict[str, object]:
    _ensure_admin(current_user)
    try:
        user = await UserService.from_session(session).disable_user(
            actor=current_user,
            target_id=user_id,
            ip_address=_client_ip(request),
            user_agent=_user_agent(request),
        )
    except UserNotFoundError as exc:
        raise _not_found() from exc
    return success_response(_profile(user).model_dump(mode="json"), request)


@router.post("/{user_id}/enable")
async def enable_user(
    user_id: uuid.UUID,
    request: Request,
    current_user: CurrentUserDep,
    session: SessionDep,
) -> dict[str, object]:
    _ensure_admin(current_user)
    try:
        user = await UserService.from_session(session).enable_user(
            actor=current_user,
            target_id=user_id,
            ip_address=_client_ip(request),
            user_agent=_user_agent(request),
        )
    except UserNotFoundError as exc:
        raise _not_found() from exc
    return success_response(_profile(user).model_dump(mode="json"), request)
