from __future__ import annotations

from dataclasses import dataclass

from starlette import status

from app.core.exceptions import ErrorCode


@dataclass(frozen=True)
class AuthError(Exception):
    error_code: ErrorCode
    message: str
    status_code: int


def registration_disabled() -> AuthError:
    return AuthError(
        ErrorCode.PERMISSION_DENIED,
        "registration is disabled",
        status.HTTP_403_FORBIDDEN,
    )


def email_domain_not_allowed() -> AuthError:
    return AuthError(
        ErrorCode.EMAIL_DOMAIN_NOT_ALLOWED,
        "email domain is not allowed",
        status.HTTP_400_BAD_REQUEST,
    )


def registration_department_not_available() -> AuthError:
    return AuthError(
        ErrorCode.DEPARTMENT_NOT_FOUND,
        "department is not available for registration",
        status.HTTP_400_BAD_REQUEST,
    )


def email_already_registered() -> AuthError:
    return AuthError(
        ErrorCode.EMAIL_ALREADY_REGISTERED,
        "email is already registered",
        status.HTTP_409_CONFLICT,
    )


def weak_password(min_length: int) -> AuthError:
    return AuthError(
        ErrorCode.WEAK_PASSWORD,
        f"password must be at least {min_length} characters",
        status.HTTP_400_BAD_REQUEST,
    )


def rate_limited() -> AuthError:
    return AuthError(
        ErrorCode.RATE_LIMITED,
        "too many requests",
        status.HTTP_429_TOO_MANY_REQUESTS,
    )


def authentication_failed() -> AuthError:
    return AuthError(
        ErrorCode.AUTHENTICATION_FAILED,
        "invalid email or password",
        status.HTTP_401_UNAUTHORIZED,
    )


def user_disabled() -> AuthError:
    return AuthError(
        ErrorCode.USER_DISABLED,
        "user is disabled",
        status.HTTP_403_FORBIDDEN,
    )


def user_locked() -> AuthError:
    return AuthError(
        ErrorCode.USER_LOCKED,
        "user is temporarily locked",
        status.HTTP_403_FORBIDDEN,
    )


def email_not_verified() -> AuthError:
    return AuthError(
        ErrorCode.EMAIL_NOT_VERIFIED,
        "email is not verified",
        status.HTTP_403_FORBIDDEN,
    )


def invalid_token() -> AuthError:
    return AuthError(
        ErrorCode.INVALID_TOKEN,
        "token is invalid or expired",
        status.HTTP_400_BAD_REQUEST,
    )
