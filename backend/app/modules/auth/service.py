from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any

import jwt
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit_log
from app.core.config import Settings
from app.core.email_delivery_metrics import record_email_delivery_result
from app.core.identity import NULL_VALUE, RegistrationDepartment, UserIdentityStore
from app.core.outbox import OutboxRepository
from app.core.ratelimit import (
    blacklist_jwt,
    email_verification_rate_limit_key,
    is_within_rate_limit,
    login_ip_rate_limit_key,
    login_rate_limit_key,
    password_reset_rate_limit_key,
    register_rate_limit_key,
)
from app.core.runtime_config import get_config
from app.core.security import (
    create_jwt,
    decode_jwt,
    hash_password,
    password_fingerprint,
    verify_password,
)
from app.modules.auth import events, exceptions
from app.modules.auth.exceptions import AuthError
from app.modules.auth.schemas import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    ResetPasswordRequest,
    TokenRequest,
)
from app.modules.notification.tasks import enqueue_email
from app.modules.user.schemas import AuthUserRecord

from .repository import AuthRepository

DUMMY_PASSWORD_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$BwCmlBBM/kQX9HTdYlyFfA"
    "$rsYt6WMtudEz8Vt9y34tothMNtzC4iEySJLI/E/UXCI"
)
ANONYMOUS_AUDIT_ID = uuid.UUID(int=0)
logger = structlog.get_logger(__name__)
__all__ = [
    "DUMMY_PASSWORD_HASH",
    "AuthService",
    "LoginResult",
    "RegistrationResult",
    "auth_error_detail",
    "hash_token",
    "verify_password",
]


@dataclass(frozen=True)
class RegistrationResult:
    accepted: bool


@dataclass(frozen=True)
class LoginResult:
    user: AuthUserRecord
    access_token: str


@dataclass(frozen=True)
class IssuedToken:
    raw_token: str
    expires_at: datetime


@dataclass(frozen=True)
class QueuedEmail:
    recipient: str
    subject: str
    body: str
    expires_at: datetime


class AuthService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        repository: AuthRepository,
        user_store: UserIdentityStore,
        settings: Settings,
    ) -> None:
        self._session = session
        self._repository = repository
        self._user_store = user_store
        self._settings = settings

    async def register(
        self,
        *,
        name: str,
        email: str,
        password: str,
        department_id: uuid.UUID | None,
        phone: str | None,
        client_ip: str,
        trace_id: str | None = None,
    ) -> RegistrationResult:
        await self._enforce_rate_limit(
            key=register_rate_limit_key(client_ip),
            limit=self._settings.auth_register_rate_limit_per_hour,
        )
        if not self._settings.allow_register:
            raise exceptions.registration_disabled()

        normalized_email = normalize_email(email)
        email_domain = extract_email_domain(normalized_email)
        if email_domain not in await resolve_allowed_email_domains(self._settings):
            raise exceptions.email_domain_not_allowed()

        ensure_password_strength(password, await resolve_password_min_length(self._settings))
        registration_department: RegistrationDepartment | None = None
        if department_id is not None:
            registration_department = await self._user_store.get_registration_department(
                department_id
            )
            if registration_department is None:
                raise exceptions.registration_department_not_available()

        require_verification = await resolve_require_email_verification(self._settings)
        existing_user = await self._user_store.get_by_email(normalized_email)
        if existing_user is not None:
            if (
                require_verification
                and not existing_user.email_verified
                and existing_user.status == "pending_email_verification"
            ):
                resent_token = await self._create_email_verification_token(existing_user)
                await self._append_email_event(
                    event_type=events.AUTH_USER_VERIFICATION_RESENT,
                    user=existing_user,
                    token=resent_token,
                    trace_id=trace_id,
                )
                resent_email = self._build_email_verification_email(
                    user=existing_user,
                    token=resent_token,
                )
                await self._session.commit()
                await self._enqueue_email_if_needed(
                    resent_email,
                    purpose="verification",
                )
            return RegistrationResult(accepted=True)

        email_verified = not require_verification
        status = "active" if email_verified else "pending_email_verification"
        user = await self._user_store.create_user(
            name=name.strip(),
            email=normalized_email,
            email_domain=email_domain,
            password_hash=hash_password(password),
            department=registration_department,
            phone=phone,
            status=status,
            email_verified=email_verified,
        )
        queued_email: QueuedEmail | None = None
        if require_verification:
            registration_token = await self._create_email_verification_token(user)
            await self._append_email_event(
                event_type=events.AUTH_USER_REGISTERED,
                user=user,
                token=registration_token,
                trace_id=trace_id,
            )
            queued_email = self._build_email_verification_email(
                user=user,
                token=registration_token,
            )
        await self._session.commit()
        await self._enqueue_email_if_needed(
            queued_email,
            purpose="verification",
        )
        return RegistrationResult(accepted=True)

    async def verify_email(self, request: TokenRequest) -> AuthUserRecord:
        token_hash = hash_token(request.token)
        token_hint = await self._repository.get_email_verification_token(token_hash)
        if token_hint is None:
            raise exceptions.invalid_token()
        await self._repository.lock_email_verification_tokens(token_hint.user_id)
        token = await self._repository.get_email_verification_token(token_hash)
        now = datetime.now(UTC)
        if token is None or token.used_at is not None or token.expires_at <= now:
            raise exceptions.invalid_token()

        user = await self._user_store.get_by_id(token.user_id)
        if user is None:
            raise exceptions.invalid_token()
        if user.status == "disabled":
            raise exceptions.user_disabled()

        user = await self._user_store.mark_email_verified(user.id)
        await self._repository.invalidate_email_verification_tokens(
            user.id,
            invalidated_at=now,
        )
        await OutboxRepository(self._session).append(
            event_type=events.AUTH_USER_VERIFIED,
            aggregate_type="user",
            aggregate_id=str(user.id),
            payload={
                "user_id": str(user.id),
                "email": user.email,
                "name": user.name,
                "verified_at": now.isoformat(),
            },
        )
        await self._session.commit()
        return user

    async def resend_verification(
        self,
        request: ForgotPasswordRequest,
        trace_id: str | None = None,
    ) -> None:
        normalized_email = normalize_email(request.email)
        await self._enforce_rate_limit(
            key=email_verification_rate_limit_key(normalized_email),
            limit=self._settings.auth_resend_verification_rate_limit_per_hour,
        )
        user = await self._user_store.get_by_email(normalized_email)
        if user is None or user.email_verified or user.status == "disabled":
            return
        token = await self._create_email_verification_token(user)
        queued_email = self._build_email_verification_email(user=user, token=token)
        await self._append_email_event(
            event_type=events.AUTH_USER_VERIFICATION_RESENT,
            user=user,
            token=token,
            trace_id=trace_id,
        )
        await self._session.commit()
        await self._enqueue_email_if_needed(
            queued_email,
            purpose="verification",
        )

    async def login(
        self,
        request: LoginRequest,
        *,
        client_ip: str | None,
        user_agent: str,
    ) -> LoginResult:
        normalized_email = normalize_email(request.email)
        await self._enforce_rate_limit(
            key=login_rate_limit_key(normalized_email),
            limit=self._settings.auth_login_rate_limit_per_hour,
        )
        await self._enforce_rate_limit(
            key=login_ip_rate_limit_key(client_ip or "unknown"),
            limit=self._settings.auth_login_rate_limit_per_hour,
        )

        user = await self._user_store.get_by_email(normalized_email)
        if user is None:
            verify_password(request.password, DUMMY_PASSWORD_HASH)
            await self._record_login_audit(
                user=None,
                email=normalized_email,
                success=False,
                failure_reason="unknown_user",
                client_ip=client_ip,
                user_agent=user_agent,
                commit=True,
            )
            raise exceptions.authentication_failed()

        now = datetime.now(UTC)
        locked = is_user_locked(user, now)

        if not verify_password(request.password, user.password_hash):
            if user.status != "disabled" and not locked:
                await self._record_failed_login(user, now)
            await self._record_login_audit(
                user=user,
                email=normalized_email,
                success=False,
                failure_reason="invalid_password",
                client_ip=client_ip,
                user_agent=user_agent,
                commit=True,
            )
            raise exceptions.authentication_failed()

        if user.status == "disabled":
            await self._record_login_audit(
                user=user,
                email=normalized_email,
                success=False,
                failure_reason="disabled",
                client_ip=client_ip,
                user_agent=user_agent,
                commit=True,
            )
            raise exceptions.user_disabled()
        if locked:
            await self._record_login_audit(
                user=user,
                email=normalized_email,
                success=False,
                failure_reason="locked",
                client_ip=client_ip,
                user_agent=user_agent,
                commit=True,
            )
            raise exceptions.user_locked()
        if user.status == "pending_email_verification" or not user.email_verified:
            await self._record_login_audit(
                user=user,
                email=normalized_email,
                success=False,
                failure_reason="email_not_verified",
                client_ip=client_ip,
                user_agent=user_agent,
                commit=True,
            )
            raise exceptions.email_not_verified()
        if user.status == "locked":
            user = await self._user_store.record_verification_state(
                user_id=user.id,
                status="active",
                locked_until=NULL_VALUE,
                failed_login_count=0,
            )
        user = await self._user_store.record_verification_state(
            user_id=user.id,
            status="active",
            failed_login_count=0,
            locked_until=NULL_VALUE,
            last_login_at=now,
            last_login_ip=client_ip if client_ip is not None else NULL_VALUE,
        )
        await self._record_login_audit(
            user=user,
            email=normalized_email,
            success=True,
            failure_reason=None,
            client_ip=client_ip,
            user_agent=user_agent,
            commit=False,
        )
        await self._session.commit()
        access_token = create_jwt(
            {
                "sub": str(user.id),
                "email": user.email,
                "role": user.role,
                "pwd": password_fingerprint(user.password_hash, self._settings.jwt_secret),
                "sv": user.session_version,
            },
            self._settings.jwt_secret,
            self._settings.jwt_expire_minutes,
        )
        return LoginResult(user=user, access_token=access_token)

    async def forgot_password(
        self,
        request: ForgotPasswordRequest,
        trace_id: str | None = None,
    ) -> None:
        normalized_email = normalize_email(request.email)
        await self._enforce_rate_limit(
            key=password_reset_rate_limit_key(normalized_email),
            limit=self._settings.auth_password_reset_rate_limit_per_hour,
        )
        user = await self._user_store.get_by_email(normalized_email)
        if user is None or user.status == "disabled":
            return
        token = await self._create_password_reset_token(user)
        queued_email = self._build_password_reset_email(user=user, token=token)
        await self._append_email_event(
            event_type=events.AUTH_PASSWORD_RESET_REQUESTED,
            user=user,
            token=token,
            trace_id=trace_id,
        )
        await self._session.commit()
        await self._enqueue_email_if_needed(
            queued_email,
            purpose="password_reset",
        )

    async def reset_password(self, request: ResetPasswordRequest) -> AuthUserRecord:
        ensure_password_strength(
            request.new_password,
            await resolve_password_min_length(self._settings),
        )
        token_hash = hash_token(request.token)
        token_hint = await self._repository.get_password_reset_token(token_hash)
        if token_hint is None:
            raise exceptions.invalid_token()
        await self._repository.lock_password_reset_tokens(token_hint.user_id)
        token = await self._repository.get_password_reset_token(token_hash)
        now = datetime.now(UTC)
        if token is None or token.used_at is not None or token.expires_at <= now:
            raise exceptions.invalid_token()

        user = await self._user_store.get_by_id(token.user_id)
        if user is None:
            raise exceptions.invalid_token()
        if user.status == "disabled":
            raise exceptions.user_disabled()

        user = await self._user_store.record_verification_state(
            user_id=user.id,
            password_hash=hash_password(request.new_password),
            failed_login_count=0,
            locked_until=NULL_VALUE,
            status="active" if user.email_verified else "pending_email_verification",
        )
        await self._repository.invalidate_password_reset_tokens(
            user.id,
            invalidated_at=now,
        )
        await self._session.commit()
        return user

    async def change_password(self, request: ChangePasswordRequest, user: AuthUserRecord) -> None:
        ensure_password_strength(
            request.new_password,
            await resolve_password_min_length(self._settings),
        )
        if not verify_password(request.current_password, user.password_hash):
            raise exceptions.authentication_failed()
        await self._user_store.record_verification_state(
            user_id=user.id,
            password_hash=hash_password(request.new_password),
        )
        await self._session.commit()

    async def get_user_by_id(self, user_id: uuid.UUID) -> AuthUserRecord | None:
        return await self._user_store.get_by_id(user_id)

    async def list_registration_departments(self) -> list[RegistrationDepartment]:
        return await self._user_store.list_registration_departments()

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

    async def _create_email_verification_token(self, user: AuthUserRecord) -> IssuedToken:
        raw_token = secrets.token_urlsafe(32)
        issued_at = datetime.now(UTC)
        expires_at = issued_at + timedelta(
            hours=self._settings.email_verification_expire_hours
        )
        await self._repository.replace_email_verification_token(
            user_id=user.id,
            token_hash=hash_token(raw_token),
            issued_at=issued_at,
            expires_at=expires_at,
        )
        return IssuedToken(raw_token=raw_token, expires_at=expires_at)

    async def _create_password_reset_token(self, user: AuthUserRecord) -> IssuedToken:
        raw_token = secrets.token_urlsafe(32)
        issued_at = datetime.now(UTC)
        expires_at = issued_at + timedelta(
            minutes=self._settings.password_reset_expire_minutes
        )
        await self._repository.replace_password_reset_token(
            user_id=user.id,
            token_hash=hash_token(raw_token),
            issued_at=issued_at,
            expires_at=expires_at,
        )
        return IssuedToken(raw_token=raw_token, expires_at=expires_at)

    async def _append_email_event(
        self,
        *,
        event_type: str,
        user: AuthUserRecord,
        token: IssuedToken,
        trace_id: str | None,
    ) -> None:
        await OutboxRepository(self._session).append(
            event_type=event_type,
            aggregate_type="user",
            aggregate_id=str(user.id),
            payload={
                "user_id": str(user.id),
                "email": user.email,
                "name": user.name,
                "token_expires_at": token.expires_at.isoformat(),
            },
            trace_id=trace_id,
        )

    def _build_email_verification_email(
        self,
        *,
        user: AuthUserRecord,
        token: IssuedToken,
    ) -> QueuedEmail:
        verification_url = self._public_url(f"/verify-email?token={token.raw_token}")
        body = (
            f"{user.name}, 您好:\n\n"
            "请点击下面的链接完成邮箱验证。\n"
            f"{verification_url}\n\n"
            f"链接有效期至: {token.expires_at.isoformat()}\n"
            "如果不是您本人操作, 请忽略本邮件。"
        )
        return QueuedEmail(
            recipient=user.email,
            subject="请完成邮箱验证",
            body=body,
            expires_at=token.expires_at,
        )

    def _build_password_reset_email(
        self,
        *,
        user: AuthUserRecord,
        token: IssuedToken,
    ) -> QueuedEmail:
        reset_url = self._public_url(f"/reset-password/{token.raw_token}")
        body = (
            f"{user.name}, 您好:\n\n"
            "请点击下面的链接重置密码。\n"
            f"{reset_url}\n\n"
            f"链接有效期至: {token.expires_at.isoformat()}\n"
            "如果不是您本人操作, 请忽略本邮件。"
        )
        return QueuedEmail(
            recipient=user.email,
            subject="密码重置通知",
            body=body,
            expires_at=token.expires_at,
        )

    def _public_url(self, path: str) -> str:
        base_url = self._settings.app_base_url.rstrip("/")
        return f"{base_url}/{path.lstrip('/')}"

    async def _enqueue_email_if_needed(
        self,
        email: QueuedEmail | None,
        *,
        purpose: str,
    ) -> None:
        if email is None:
            return
        try:
            enqueue_email(
                recipient=email.recipient,
                subject=email.subject,
                body=email.body,
                expires_at=email.expires_at,
            )
        except Exception as exc:
            logger.error(
                "auth.notification_publish_failed",
                purpose=purpose if purpose in {"verification", "password_reset"} else "invalid",
                error_type=type(exc).__name__,
            )
            try:
                await record_email_delivery_result(
                    redis_url=self._settings.cache_redis_url,
                    result="publish_failure",
                )
            except Exception as metric_error:
                logger.error(
                    "auth.notification_publish_metric_failed",
                    purpose=(
                        purpose
                        if purpose in {"verification", "password_reset"}
                        else "invalid"
                    ),
                    error_type=type(metric_error).__name__,
                )

    async def _record_failed_login(self, user: AuthUserRecord, now: datetime) -> None:
        failed_login_count = user.failed_login_count + 1
        max_failed_attempts = await resolve_login_max_failed_attempts(self._settings)
        if failed_login_count >= max_failed_attempts:
            lock_minutes = await resolve_login_lock_minutes(self._settings)
            await self._user_store.record_verification_state(
                user_id=user.id,
                failed_login_count=failed_login_count,
                status="locked",
                locked_until=now + timedelta(minutes=lock_minutes),
                increment_session_version=True,
            )
            return
        await self._user_store.record_verification_state(
            user_id=user.id,
            failed_login_count=failed_login_count,
        )

    async def _enforce_rate_limit(self, *, key: str, limit: int) -> None:
        allowed = await is_within_rate_limit(
            redis_url=self._settings.cache_redis_url,
            key=key,
            limit=limit,
            window_seconds=3600,
        )
        if not allowed:
            raise exceptions.rate_limited()

    async def _record_login_audit(
        self,
        *,
        user: AuthUserRecord | None,
        email: str,
        success: bool,
        failure_reason: str | None,
        client_ip: str | None,
        user_agent: str,
        commit: bool,
    ) -> None:
        actor_id = user.id if user is not None else ANONYMOUS_AUDIT_ID
        await record_audit_log(
            self._session,
            actor_id=actor_id,
            action="auth.login.success" if success else "auth.login.failed",
            target_type="auth_login",
            target_id=actor_id,
            ip_address=client_ip or "unknown",
            user_agent=user_agent[:512] or "unknown",
            metadata_json={
                "email": email,
                "success": success,
                "failure_reason": failure_reason,
                "user_id": str(user.id) if user is not None else None,
                "role": user.role if user is not None else None,
                "status": user.status if user is not None else None,
            },
        )
        if commit:
            await self._session.commit()


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


async def resolve_allowed_email_domains(settings: Settings) -> set[str]:
    """解析允许注册的邮箱域名 (security.allowed_email_domains), 非法值回退环境变量。"""
    value = await get_config("security.allowed_email_domains")
    if isinstance(value, list):
        domains = {str(item).strip().lower() for item in value if str(item).strip()}
        if domains:
            return domains
    return allowed_email_domains(settings)


async def resolve_require_email_verification(settings: Settings) -> bool:
    """以环境设置为安全下限, 数据库运行时配置只能进一步收紧验证门禁。"""
    value = await get_config("security.require_email_verification")
    if isinstance(value, bool):
        return settings.require_email_verification or value
    return settings.require_email_verification


async def resolve_login_max_failed_attempts(settings: Settings) -> int:
    """解析连续登录失败锁定阈值 (security.login_max_failed_attempts), 非法值回退环境变量。"""
    value = await get_config("security.login_max_failed_attempts")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return settings.login_max_failed_attempts
    return value


async def resolve_login_lock_minutes(settings: Settings) -> int:
    """解析登录锁定时长分钟 (security.login_lock_minutes), 非法值回退环境变量。"""
    value = await get_config("security.login_lock_minutes")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return settings.login_lock_minutes
    return value


async def resolve_password_min_length(settings: Settings) -> int:
    """解析密码最小长度 (security.password_min_length), 非法值回退环境变量。"""
    value = await get_config("security.password_min_length")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return settings.password_min_length
    return value


def ensure_password_strength(password: str, min_length: int) -> None:
    if len(password) < min_length:
        raise exceptions.weak_password(min_length)


def hash_token(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


def is_user_locked(user: AuthUserRecord, now: datetime) -> bool:
    return user.status == "locked" and (user.locked_until is None or user.locked_until > now)


def auth_error_detail(error: AuthError) -> dict[str, str]:
    return {"error_code": error.error_code, "message": error.message}


def jwt_ttl_seconds(payload: dict[str, Any]) -> int:
    exp = payload.get("exp")
    if not isinstance(exp, int):
        return 0
    return max(0, exp - int(datetime.now(UTC).timestamp()))
