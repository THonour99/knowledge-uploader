"""Runtime configuration reader with an in-process TTL cache.

Core layer module: it must not import anything from ``app.modules``.
It reads the ``system_configs`` table through a lightweight table
clause. A successful lookup with no stored value may use the environment
fallback; a database outage instead uses last-known-good or a fail-closed
value, so an outage cannot silently broaden a policy.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

import sqlalchemy as sa
import structlog
from cryptography.fernet import InvalidToken
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings, get_settings
from app.core.database import AsyncSessionFactory
from app.core.metrics import observe_config_invariant_violation
from app.core.security import decrypt_secret

logger = structlog.get_logger(__name__)

DEFAULT_TTL_SECONDS = 15.0
GROUP_TTL_SECONDS: dict[str, float] = {"security": 5.0}
UNAVAILABLE_TTL_SECONDS = 1.0

_SYSTEM_CONFIGS = sa.table(
    "system_configs",
    sa.column("key", sa.String()),
    sa.column("group", sa.String()),
    sa.column("value", postgresql.JSONB(astext_type=sa.Text())),  # type: ignore[no-untyped-call]
    sa.column("is_secret", sa.Boolean()),
)

_cache: dict[str, tuple[object | None, float]] = {}
_last_known_good: dict[str, object | None] = {}


@dataclass(frozen=True)
class DatabaseConfigResult:
    available: bool
    value: object | None


def _csv_list(raw_value: str) -> list[str]:
    return [item.strip() for item in raw_value.split(",") if item.strip()]


FALLBACKS: dict[str, Callable[[Settings], object]] = {
    "upload.enabled": lambda _s: True,
    "upload.allowed_extensions": lambda s: _csv_list(s.upload_allowed_extensions),
    "upload.max_file_size_mb": lambda s: s.upload_max_file_size_bytes // (1024 * 1024),
    "upload.user_quota_mb": lambda _s: 0,
    "upload.allow_multi_file": lambda _s: True,
    "upload.allow_user_delete": lambda _s: False,
    "outbox.publish_max_retries": lambda _s: 3,
    "processing.parse_max_pages": lambda _s: 200,
    "processing.parse_max_chars": lambda _s: 20000,
    "security.allowed_email_domains": lambda s: _csv_list(s.allowed_email_domains),
    "security.password_min_length": lambda s: s.password_min_length,
    "security.login_max_failed_attempts": lambda s: s.login_max_failed_attempts,
    "security.login_lock_minutes": lambda s: s.login_lock_minutes,
    "security.require_email_verification": lambda s: s.require_email_verification,
    "security.block_critical_sensitive_sync": lambda _s: True,
    "review.claim_timeout_minutes": lambda _s: 30,
    "review.sla_hours": lambda _s: 24,
    "ragflow.base_url": lambda s: s.ragflow_base_url,
    "ragflow.api_key": lambda s: s.ragflow_api_key,
    "ragflow.allowed_dataset_ids": lambda s: _csv_list(s.ragflow_allowed_dataset_ids),
    "ragflow.sync_max_retries": lambda s: s.ragflow_max_retry_count,
    "ragflow.parse_poll_timeout_seconds": lambda s: s.ragflow_parse_poll_timeout_seconds,
    "ragflow.sync_timeout_seconds": lambda s: int(s.ragflow_request_timeout),
    "ragflow.allow_high_risk_sync": lambda _s: False,
    "ragflow.delete_remote_on_file_delete": lambda _s: False,
    "ragflow.keep_remote_on_archive": lambda _s: True,
    "ragflow.keep_replaced_remote": lambda _s: False,
}

# Used only when PostgreSQL is unavailable and this process has never observed
# a trusted value. Every value narrows behavior or disables an external side
# effect; these are deliberately different from ordinary environment fallbacks.
FAIL_CLOSED_DEFAULTS: dict[str, object] = {
    "upload.enabled": False,
    "upload.allowed_extensions": ["__blocked__"],
    "upload.max_file_size_mb": 1,
    "upload.user_quota_mb": 1,
    "upload.allow_multi_file": False,
    "upload.allow_user_delete": False,
    "processing.parse_max_pages": 1,
    "processing.parse_max_chars": 1,
    "security.allowed_email_domains": ["blocked.invalid"],
    "security.password_min_length": 128,
    "security.login_max_failed_attempts": 1,
    "security.login_lock_minutes": 1440,
    "security.require_email_verification": True,
    "security.block_critical_sensitive_sync": True,
    "review.claim_timeout_minutes": 5,
    "review.sla_hours": 1,
    "ragflow.base_url": "",
    "ragflow.api_key": "",
    "ragflow.allowed_dataset_ids": [],
    "ragflow.sync_max_retries": 0,
    "ragflow.sync_timeout_seconds": 1,
    "ragflow.parse_poll_timeout_seconds": 60,
    "ragflow.allow_high_risk_sync": False,
    "ragflow.delete_remote_on_file_delete": False,
    "ragflow.keep_remote_on_archive": True,
    "ragflow.keep_replaced_remote": True,
    "outbox.publish_max_retries": 0,
}


def _monotonic() -> float:
    return time.monotonic()


def _ttl_for(key: str) -> float:
    group = key.split(".", 1)[0]
    return GROUP_TTL_SECONDS.get(group, DEFAULT_TTL_SECONDS)


def _fallback_value(key: str) -> object | None:
    factory = FALLBACKS.get(key)
    if factory is None:
        return None
    return factory(get_settings())


def _enforce_security_floor(key: str, value: object | None) -> object | None:
    enforced_value: object | None = value
    if key == "security.block_critical_sensitive_sync" and value is not True:
        enforced_value = True
    elif (
        key == "security.require_email_verification"
        and get_settings().require_email_verification
        and value is not True
    ):
        enforced_value = True
    if enforced_value is not value:
        logger.error(
            "runtime_config_security_invariant_violation",
            config_key=key,
            enforced_value=enforced_value,
        )
        observe_config_invariant_violation(key)
    return enforced_value


class RuntimeConfigSecretError(RuntimeError):
    """Raised when a configured secret cannot be decrypted safely."""


def _decrypt_secret_value(key: str, encrypted_value: object) -> str:
    if not isinstance(encrypted_value, str) or not encrypted_value:
        logger.error(
            "runtime_config_secret_decrypt_failed",
            config_key=key,
            error_type="InvalidStoredValue",
        )
        raise RuntimeConfigSecretError(f"cannot decrypt runtime secret: {key}")
    try:
        return decrypt_secret(encrypted_value, get_settings().encryption_key)
    except (InvalidToken, ValueError) as error:
        logger.error(
            "runtime_config_secret_decrypt_failed",
            config_key=key,
            error_type=type(error).__name__,
        )
        raise RuntimeConfigSecretError(f"cannot decrypt runtime secret: {key}") from None


async def _load_db_value(key: str) -> DatabaseConfigResult:
    """Distinguish a successful missing value from an unavailable database."""
    try:
        async with AsyncSessionFactory() as session:
            result = await session.execute(
                sa.select(_SYSTEM_CONFIGS.c.value, _SYSTEM_CONFIGS.c.is_secret).where(
                    _SYSTEM_CONFIGS.c.key == key
                )
            )
            row = result.first()
    except (SQLAlchemyError, OSError) as error:
        logger.warning(
            "runtime_config_db_unavailable",
            config_key=key,
            error_type=type(error).__name__,
        )
        return DatabaseConfigResult(available=False, value=None)
    if row is None:
        return DatabaseConfigResult(available=True, value=None)
    value: object | None = row[0]
    if value is None:
        return DatabaseConfigResult(available=True, value=None)
    if bool(row[1]):
        value = _decrypt_secret_value(key, value)
    return DatabaseConfigResult(available=True, value=value)


async def stored_config_is_exact_false(key: str) -> bool:
    """Return true only for an available database row whose raw value is false.

    This is for destructive decisions where an environment fallback, a missing
    row, JSON null, a malformed value, or a database outage must not authorize
    the side effect.
    """
    loaded = await _load_db_value(key)
    return loaded.available and loaded.value is False


async def get_config(key: str) -> object | None:
    """Resolve one config value: cache, then database, then env fallback."""
    now = _monotonic()
    cached = _cache.get(key)
    if cached is not None and cached[1] > now:
        value = _enforce_security_floor(key, cached[0])
        if value != cached[0]:
            _cache[key] = (value, cached[1])
            if key in _last_known_good:
                _last_known_good[key] = value
        return value
    logger.debug("runtime_config_cache_miss", config_key=key)
    loaded = await _load_db_value(key)
    if loaded.available:
        value = loaded.value
        if value is None:
            value = _fallback_value(key)
        cache_ttl = _ttl_for(key)
    else:
        if key in _last_known_good:
            value = _last_known_good[key]
            logger.warning(
                "runtime_config_last_known_good_used",
                config_key=key,
            )
        else:
            value = FAIL_CLOSED_DEFAULTS.get(key)
            logger.error(
                "runtime_config_fail_closed_default_used",
                config_key=key,
            )
        cache_ttl = UNAVAILABLE_TTL_SECONDS
    value = _enforce_security_floor(key, value)
    if loaded.available:
        _last_known_good[key] = value
    _cache[key] = (value, now + cache_ttl)
    return value


async def get_config_group(group: str) -> dict[str, object]:
    """Resolve every known config key belonging to ``group``."""
    values: dict[str, object] = {}
    for key in sorted(key for key in FALLBACKS if key.startswith(f"{group}.")):
        value = await get_config(key)
        if value is not None:
            values[key] = value
    return values


def invalidate(
    key: str | None = None,
    *,
    forget_last_known_good: bool = False,
) -> None:
    """Drop one cached key, or the whole cache when ``key`` is None."""
    if key is None:
        _cache.clear()
        if forget_last_known_good:
            _last_known_good.clear()
        return
    _cache.pop(key, None)
    if forget_last_known_good:
        _last_known_good.pop(key, None)


def record_trusted_value(key: str, value: object | None) -> None:
    """Publish a value committed by the local config service into both caches."""
    value = resolve_committed_value(key, value)
    now = _monotonic()
    _last_known_good[key] = value
    _cache[key] = (value, now + _ttl_for(key))


def resolve_committed_value(key: str, stored_value: object | None) -> object | None:
    """Resolve a just-written DB value using the same DB-first fallback contract as reads."""
    if key not in FALLBACKS:
        raise KeyError(f"unknown runtime config key: {key}")
    value = _fallback_value(key) if stored_value is None else stored_value
    return _enforce_security_floor(key, value)
