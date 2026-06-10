"""Runtime configuration reader with an in-process TTL cache.

Core layer module: it must not import anything from ``app.modules``.
It reads the ``system_configs`` table through a lightweight table
clause and falls back to environment-derived settings defaults when
the database value is missing or unavailable.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import sqlalchemy as sa
import structlog
from cryptography.fernet import InvalidToken
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings, get_settings
from app.core.database import AsyncSessionFactory
from app.core.security import decrypt_secret

logger = structlog.get_logger(__name__)

DEFAULT_TTL_SECONDS = 60.0
GROUP_TTL_SECONDS: dict[str, float] = {"security": 30.0}

_SYSTEM_CONFIGS = sa.table(
    "system_configs",
    sa.column("key", sa.String()),
    sa.column("group", sa.String()),
    sa.column("value", postgresql.JSONB(astext_type=sa.Text())),  # type: ignore[no-untyped-call]
    sa.column("is_secret", sa.Boolean()),
)

_cache: dict[str, tuple[object | None, float]] = {}


def _csv_list(raw_value: str) -> list[str]:
    return [item.strip() for item in raw_value.split(",") if item.strip()]


def _first_csv_item(raw_value: str) -> str:
    values = _csv_list(raw_value)
    return values[0] if values else ""


FALLBACKS: dict[str, Callable[[Settings], object]] = {
    "upload.allowed_extensions": lambda s: _csv_list(s.upload_allowed_extensions),
    "upload.max_file_size_mb": lambda s: s.upload_max_file_size_bytes // (1024 * 1024),
    "upload.user_quota_mb": lambda _s: 0,
    "upload.allow_multi_file": lambda _s: True,
    "upload.allow_user_delete": lambda _s: False,
    "upload.enable_duplicate_check": lambda _s: True,
    "processing.auto_parse_on_upload": lambda _s: True,
    "processing.auto_sync_after_parse": lambda _s: False,
    "processing.sync_after_ai_analysis": lambda _s: True,
    "processing.task_max_retries": lambda _s: 3,
    "processing.task_timeout_seconds": lambda _s: 600,
    "processing.parse_max_pages": lambda _s: 200,
    "processing.parse_max_chars": lambda _s: 20000,
    "security.allowed_email_domains": lambda s: _csv_list(s.allowed_email_domains),
    "security.password_min_length": lambda s: s.password_min_length,
    "security.login_max_failed_attempts": lambda s: s.login_max_failed_attempts,
    "security.login_lock_minutes": lambda s: s.login_lock_minutes,
    "security.require_email_verification": lambda s: s.require_email_verification,
    "security.require_review_before_sync": lambda _s: True,
    "security.block_critical_sensitive_sync": lambda _s: True,
    "basic.system_name": lambda s: s.app_name,
    "basic.system_logo_url": lambda _s: "",
    "basic.default_language": lambda _s: "zh-CN",
    "basic.default_timezone": lambda _s: "Asia/Shanghai",
    "basic.notification_channels": lambda _s: ["email"],
    "basic.admin_contact_email": lambda _s: "",
    "ragflow.base_url": lambda s: s.ragflow_base_url,
    "ragflow.api_key": lambda s: s.ragflow_api_key,
    "ragflow.default_dataset_id": lambda s: _first_csv_item(s.ragflow_allowed_dataset_ids),
    "ragflow.auto_sync_enabled": lambda _s: False,
    "ragflow.sync_max_retries": lambda s: s.ragflow_max_retry_count,
    "ragflow.sync_timeout_seconds": lambda s: int(s.ragflow_request_timeout),
    "ragflow.allow_high_risk_sync": lambda _s: False,
    "ragflow.delete_remote_on_file_delete": lambda _s: False,
    "ragflow.keep_remote_on_archive": lambda _s: True,
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


def _decrypt_secret_value(key: str, encrypted_value: object) -> str | None:
    if not isinstance(encrypted_value, str) or not encrypted_value:
        return None
    try:
        return decrypt_secret(encrypted_value, get_settings().encryption_key)
    except (InvalidToken, ValueError):
        logger.debug("runtime_config_secret_decrypt_failed", config_key=key)
        return None


async def _load_db_value(key: str) -> object | None:
    """Return the stored value for ``key`` or None when it must fall back."""
    try:
        async with AsyncSessionFactory() as session:
            result = await session.execute(
                sa.select(_SYSTEM_CONFIGS.c.value, _SYSTEM_CONFIGS.c.is_secret).where(
                    _SYSTEM_CONFIGS.c.key == key
                )
            )
            row = result.first()
    except (SQLAlchemyError, OSError) as error:
        logger.debug(
            "runtime_config_db_unavailable",
            config_key=key,
            error_type=type(error).__name__,
        )
        return None
    if row is None:
        return None
    value: object | None = row[0]
    if value is None:
        return None
    if bool(row[1]):
        return _decrypt_secret_value(key, value)
    return value


async def get_config(key: str) -> object | None:
    """Resolve one config value: cache, then database, then env fallback."""
    now = _monotonic()
    cached = _cache.get(key)
    if cached is not None and cached[1] > now:
        return cached[0]
    logger.debug("runtime_config_cache_miss", config_key=key)
    value = await _load_db_value(key)
    if value is None:
        value = _fallback_value(key)
    _cache[key] = (value, now + _ttl_for(key))
    return value


async def get_config_group(group: str) -> dict[str, object]:
    """Resolve every known config key belonging to ``group``."""
    values: dict[str, object] = {}
    for key in sorted(key for key in FALLBACKS if key.startswith(f"{group}.")):
        value = await get_config(key)
        if value is not None:
            values[key] = value
    return values


def invalidate(key: str | None = None) -> None:
    """Drop one cached key, or the whole cache when ``key`` is None."""
    if key is None:
        _cache.clear()
        return
    _cache.pop(key, None)
