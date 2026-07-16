from __future__ import annotations

from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.database import get_session
from app.core.deps import BearerCredentialsDep, get_app_settings, get_current_user
from app.core.identity import get_user_identity_store, has_assigned_department
from app.core.responses import success_response
from app.modules.auth.exceptions import AuthError
from app.modules.auth.schemas import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    LoginResponse,
    RegisterRequest,
    RegistrationDepartmentOption,
    ResetPasswordRequest,
    TokenRequest,
    UserProfile,
)
from app.modules.user.schemas import AuthUserRecord

from .repository import AuthRepository
from .service import AuthService, auth_error_detail

router = APIRouter(prefix="/api/auth", tags=["auth"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]
CurrentUserDep = Annotated[AuthUserRecord, Depends(get_current_user)]


def _service(session: AsyncSession, settings: Settings) -> AuthService:
    return AuthService(
        session=session,
        repository=AuthRepository(session),
        user_store=get_user_identity_store(session),
        settings=settings,
    )


def _profile(user: AuthUserRecord) -> UserProfile:
    return UserProfile(
        id=user.id,
        name=user.name,
        email=user.email,
        role=user.role,
        status=user.status,
        email_verified=user.email_verified,
        department_id=user.department_id,
        department_name=user.department_name,
        department_code=user.department_code,
        department=user.department,
        phone=user.phone,
        managed_department_ids=user.managed_department_ids,
        department_assigned=has_assigned_department(user),
    )


def _raise_auth_error(error: AuthError) -> NoReturn:
    raise HTTPException(status_code=error.status_code, detail=auth_error_detail(error))


def _request_id(request: Request) -> str | None:
    request_id = getattr(request.state, "request_id", None)
    return request_id if isinstance(request_id, str) else None


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client is not None else None


def _user_agent(request: Request) -> str:
    return request.headers.get("user-agent", "unknown")[:512] or "unknown"


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, object]:
    try:
        result = await _service(session, settings).register(
            name=payload.name,
            email=str(payload.email),
            password=payload.password,
            department_id=payload.department_id,
            phone=payload.phone,
            client_ip=_client_ip(request) or "unknown",
            trace_id=_request_id(request),
        )
    except AuthError as error:
        _raise_auth_error(error)
    return success_response({"accepted": result.accepted}, request)


@router.get("/registration-departments")
async def registration_departments(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, object]:
    departments = await _service(session, settings).list_registration_departments()
    payload = [
        RegistrationDepartmentOption(id=item.id, name=item.name, code=item.code).model_dump(
            mode="json"
        )
        for item in departments
    ]
    return success_response(payload, request)


@router.post("/login")
async def login(
    payload: LoginRequest,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, object]:
    try:
        result = await _service(session, settings).login(
            payload,
            client_ip=_client_ip(request),
            user_agent=_user_agent(request),
        )
    except AuthError as error:
        _raise_auth_error(error)
    response = LoginResponse(access_token=result.access_token, user=_profile(result.user))
    return success_response(response.model_dump(mode="json"), request)


@router.post("/logout")
async def logout(
    request: Request,
    credentials: BearerCredentialsDep,
    current_user: CurrentUserDep,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, object]:
    if credentials is not None:
        await _service(session, settings).logout(credentials.credentials)
    return success_response({}, request)


@router.get("/me")
async def me(request: Request, current_user: CurrentUserDep) -> dict[str, object]:
    return success_response(_profile(current_user).model_dump(mode="json"), request)


@router.post("/verify-email")
async def verify_email(
    payload: TokenRequest,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, object]:
    try:
        user = await _service(session, settings).verify_email(payload)
    except AuthError as error:
        _raise_auth_error(error)
    return success_response(_profile(user).model_dump(mode="json"), request)


@router.post("/resend-verification")
async def resend_verification(
    payload: ForgotPasswordRequest,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, object]:
    try:
        await _service(session, settings).resend_verification(
            payload,
            trace_id=_request_id(request),
        )
    except AuthError as error:
        _raise_auth_error(error)
    return success_response({}, request)


@router.post("/forgot-password")
async def forgot_password(
    payload: ForgotPasswordRequest,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, object]:
    try:
        await _service(session, settings).forgot_password(payload, trace_id=_request_id(request))
    except AuthError as error:
        _raise_auth_error(error)
    return success_response({}, request)


@router.post("/reset-password")
async def reset_password(
    payload: ResetPasswordRequest,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, object]:
    try:
        user = await _service(session, settings).reset_password(payload)
    except AuthError as error:
        _raise_auth_error(error)
    return success_response(_profile(user).model_dump(mode="json"), request)


@router.post("/change-password")
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    current_user: CurrentUserDep,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, object]:
    try:
        await _service(session, settings).change_password(payload, current_user)
    except AuthError as error:
        _raise_auth_error(error)
    return success_response({}, request)
