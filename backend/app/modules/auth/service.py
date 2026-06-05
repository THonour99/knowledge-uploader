from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any

import jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.ratelimit import (
    blacklist_jwt,
    email_verification_rate_limit_key,
    is_within_rate_limit,
    password_reset_rate_limit_key,
    register_rate_limit_key,
)
from app.core.security import (
    create_jwt,
    decode_jwt,
    hash_password,
    password_fingerprint,
    verify_password,
)
from app.modules.auth import exceptions
from app.modules.auth.exceptions import AuthError
from app.modules.auth.repository import AuthRepository
from app.modules.auth.schemas import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    ResetPasswordRequest,
    TokenRequest,
)
from app.modules.user.models import User


@dataclass(frozen=True)
class RegistrationResult:
    user: User
    verification_token: str | None


@dataclass(frozen=True)
class LoginResult:
    user: User
    access_token: str


class AuthService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        repository: AuthRepository,
        settings: Settings,
    ) -> None:
        self._session = session
        self._repository = repository
        self._settings = settings

    async def register(
        self,
        *,
        name: str,
        email: str,
        password: str,
        department: str | None,
        phone: str | None,
        client_ip: str,
    ) -> RegistrationResult:
        await self._enforce_rate_limit(
            key=register_rate_limit_key(client_ip),
            limit=self._settings.auth_register_rate_limit_per_hour,
        )
        if not self._settings.allow_register:
            raise exceptions.registration_disabled()

        normalized_email = normalize_email(email)
        email_domain = extract_email_domain(normalized_email)
        if email_domain not in allowed_email_domains(self._settings):
            raise exceptions.email_domain_not_allowed()

        ensure_password_strength(password, self._settings)
        existing_user = await self._repository.get_user_by_email(normalized_email)
        if existing_user is not None:
            raise exceptions.email_already_registered()

        email_verified = not self._settings.require_email_verification
        status = "active" if email_verified else "pending_email_verification"
        user = await self._repository.create_user(
            name=name.strip(),
            email=normalized_email,
            email_domain=email_domain,
            password_hash=hash_password(password),
            department=department,
            phone=phone,
            status=status,
            email_verified=email_verified,
        )
        verification_token = None
        if self._settings.require_email_verification:
            verification_token = await self._create_email_verification_token(user)
        await self._session.commit()
        return RegistrationResult(user=user, verification_token=verification_token)

    async def verify_email(self, request: TokenRequest) -> User:
        token = await self._repository.get_email_verification_token(hash_token(request.token))
        now = datetime.now(UTC)
        if token is None or token.used_at is not None or token.expires_at < now:
            raise exceptions.invalid_token()

        user = await self._repository.get_user_by_id(token.user_id)
        if user is None:
            raise exceptions.invalid_token()
        if user.status == "disabled":
            raise exceptions.user_disabled()

        user.email_verified = True
        user.status = "active"
        token.used_at = now
        await self._session.commit()
        return user

    async def resend_verification(self, request: ForgotPasswordRequest) -> None:
        normalized_email = normalize_email(request.email)
        await self._enforce_rate_limit(
            key=email_verification_rate_limit_key(normalized_email),
            limit=self._settings.auth_resend_verification_rate_limit_per_hour,
        )
        user = await self._repository.get_user_by_email(normalized_email)
        if user is None or user.email_verified or user.status == "disabled":
            return
        await self._create_email_verification_token(user)
        await self._session.commit()

    async def login(self, request: LoginRequest, client_ip: str | None) -> LoginResult:
        user = await self._repository.get_user_by_email(normalize_email(request.email))
        if user is None:
            raise exceptions.authentication_failed()

        now = datetime.now(UTC)
        if user.status == "disabled":
            raise exceptions.user_disabled()
        if is_user_locked(user, now):
            raise exceptions.user_locked()
        if user.status == "locked":
            unlock_user(user)
        if not user.email_verified or user.status == "pending_email_verification":
            raise exceptions.email_not_verified()

        if not verify_password(request.password, user.password_hash):
            await self._record_failed_login(user, now)
            raise exceptions.authentication_failed()

        user.failed_login_count = 0
        user.locked_until = None
        user.last_login_at = now
        user.last_login_ip = client_ip
        user.status = "active"
        await self._session.commit()
        access_token = create_jwt(
            {
                "sub": str(user.id),
                "email": user.email,
                "role": user.role,
                "pwd": password_fingerprint(user.password_hash, self._settings.jwt_secret),
            },
            self._settings.jwt_secret,
            self._settings.jwt_expire_minutes,
        )
        return LoginResult(user=user, access_token=access_token)

    async def forgot_password(self, request: ForgotPasswordRequest) -> None:
        normalized_email = normalize_email(request.email)
        await self._enforce_rate_limit(
            key=password_reset_rate_limit_key(normalized_email),
            limit=self._settings.auth_password_reset_rate_limit_per_hour,
        )
        user = await self._repository.get_user_by_email(normalized_email)
        if user is None or user.status == "disabled":
            return
        await self._create_password_reset_token(user)
        await self._session.commit()

    async def reset_password(self, request: ResetPasswordRequest) -> User:
        ensure_password_strength(request.new_password, self._settings)
        token = await self._repository.get_password_reset_token(hash_token(request.token))
        now = datetime.now(UTC)
        if token is None or token.used_at is not None or token.expires_at < now:
            raise exceptions.invalid_token()

        user = await self._repository.get_user_by_id(token.user_id)
        if user is None:
            raise exceptions.invalid_token()
        if user.status == "disabled":
            raise exceptions.user_disabled()

        user.password_hash = hash_password(request.new_password)
        user.failed_login_count = 0
        user.locked_until = None
        if user.email_verified:
            user.status = "active"
        token.used_at = now
        await self._session.commit()
        return user

    async def change_password(self, request: ChangePasswordRequest, user: User) -> None:
        ensure_password_strength(request.new_password, self._settings)
        if not verify_password(request.current_password, user.password_hash):
            raise exceptions.authentication_failed()
        user.password_hash = hash_password(request.new_password)
        await self._session.commit()

    async def get_user_by_id(self, user_id: uuid.UUID) -> User | None:
        return await self._repository.get_user_by_id(user_id)

    async def logout(self, token: str) -> None:
        try:
            payload = decode_jwt(token, self._settings.jwt_secret)
        except jwt.InvalidTokenError:
            return
        jti = payload.get("jti")
        if not isinstance(jti, str):
            return
        await blacklist_jwt(
            redis_url=self._settings.cache_redis_url,
            jti=jti,
            ttl_seconds=jwt_ttl_seconds(payload),
        )

    async def _create_email_verification_token(self, user: User) -> str:
        raw_token = secrets.token_urlsafe(32)
        await self._repository.create_email_verification_token(
            user_id=user.id,
            token_hash=hash_token(raw_token),
            expires_at=datetime.now(UTC)
            + timedelta(hours=self._settings.email_verification_expire_hours),
        )
        return raw_token

    async def _create_password_reset_token(self, user: User) -> str:
        raw_token = secrets.token_urlsafe(32)
        await self._repository.create_password_reset_token(
            user_id=user.id,
            token_hash=hash_token(raw_token),
            expires_at=datetime.now(UTC)
            + timedelta(minutes=self._settings.password_reset_expire_minutes),
        )
        return raw_token

    async def _record_failed_login(self, user: User, now: datetime) -> None:
        user.failed_login_count += 1
        if user.failed_login_count >= self._settings.login_max_failed_attempts:
            user.status = "locked"
            user.locked_until = now + timedelta(minutes=self._settings.login_lock_minutes)
        await self._session.commit()

    async def _enforce_rate_limit(self, *, key: str, limit: int) -> None:
        allowed = await is_within_rate_limit(
            redis_url=self._settings.cache_redis_url,
            key=key,
            limit=limit,
            window_seconds=3600,
        )
        if not allowed:
            raise exceptions.rate_limited()


def normalize_email(email: str) -> str:
    return email.strip().lower()


def extract_email_domain(email: str) -> str:
    return email.rsplit("@", 1)[1]


def allowed_email_domains(settings: Settings) -> set[str]:
    return {
        domain.strip().lower()
        for domain in settings.allowed_email_domains.split(",")
        if domain.strip()
    }


def ensure_password_strength(password: str, settings: Settings) -> None:
    if len(password) < settings.password_min_length:
        raise exceptions.weak_password(settings.password_min_length)


def hash_token(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


def is_user_locked(user: User, now: datetime) -> bool:
    return user.status == "locked" and (user.locked_until is None or user.locked_until > now)


def unlock_user(user: User) -> None:
    user.status = "active"
    user.locked_until = None
    user.failed_login_count = 0


def auth_error_detail(error: AuthError) -> dict[str, str]:
    return {"error_code": error.error_code, "message": error.message}


def jwt_ttl_seconds(payload: dict[str, Any]) -> int:
    exp = payload.get("exp")
    if not isinstance(exp, int):
        return 0
    return max(0, exp - int(datetime.now(UTC).timestamp()))
