from __future__ import annotations

from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from app.core.config import Settings, get_settings
from app.core.database import get_session
from app.core.exceptions import ErrorCode
from app.core.identity import get_user_identity_store
from app.core.ratelimit import is_jwt_blacklisted
from app.core.security import decode_jwt, password_fingerprint
from app.modules.user.schemas import AuthUserRecord

bearer_scheme = HTTPBearer(auto_error=False)


def get_app_settings() -> Settings:
    return get_settings()


BearerCredentialsDep = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]


def http_error(error_code: ErrorCode, message: str, status_code: int) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"error_code": error_code, "message": message},
    )


async def get_current_user(
    credentials: BearerCredentialsDep,
    session: SessionDep,
    settings: SettingsDep,
) -> AuthUserRecord:
    if credentials is None:
        raise http_error(
            ErrorCode.AUTHENTICATION_FAILED,
            "missing bearer token",
            status.HTTP_401_UNAUTHORIZED,
        )

    try:
        payload = decode_jwt(credentials.credentials, settings.jwt_secret)
        user_id = UUID(str(payload.get("sub")))
        jti = str(payload.get("jti"))
        session_version = int(payload.get("sv", -1))
    except (ValueError, jwt.InvalidTokenError) as exc:
        raise http_error(
            ErrorCode.AUTHENTICATION_FAILED,
            "invalid bearer token",
            status.HTTP_401_UNAUTHORIZED,
        ) from exc

    if jti == "None" or await is_jwt_blacklisted(redis_url=settings.cache_redis_url, jti=jti):
        raise http_error(
            ErrorCode.AUTHENTICATION_FAILED,
            "invalid bearer token",
            status.HTTP_401_UNAUTHORIZED,
        )

    user = await get_user_identity_store(session).get_by_id(user_id)
    if user is None:
        raise http_error(
            ErrorCode.AUTHENTICATION_FAILED,
            "invalid bearer token",
            status.HTTP_401_UNAUTHORIZED,
        )
    if user.status == "disabled":
        raise http_error(ErrorCode.USER_DISABLED, "user is disabled", status.HTTP_403_FORBIDDEN)
    if user.status == "locked":
        raise http_error(
            ErrorCode.USER_LOCKED,
            "user is temporarily locked",
            status.HTTP_403_FORBIDDEN,
        )
    if payload.get("pwd") != password_fingerprint(user.password_hash, settings.jwt_secret):
        raise http_error(
            ErrorCode.AUTHENTICATION_FAILED,
            "invalid bearer token",
            status.HTTP_401_UNAUTHORIZED,
        )
    if session_version != user.session_version:
        raise http_error(
            ErrorCode.AUTHENTICATION_FAILED,
            "invalid bearer token",
            status.HTTP_401_UNAUTHORIZED,
        )
    return user
