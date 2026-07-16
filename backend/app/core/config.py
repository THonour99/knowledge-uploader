from __future__ import annotations

import ipaddress
from functools import lru_cache
from typing import Self
from urllib.parse import urlparse, urlsplit

from cryptography.fernet import Fernet
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROTECTED_ENVS = {"production", "prod", "staging"}
DEFAULT_DEV_ENCRYPTION_KEY = "RZ1Sw_27VrN9c5Cfsq01qiwViwT6y7jDCuXYn7tgGJY="
PLACEHOLDER_SECRETS = {
    "",
    "change-me",
    "change-me-change-me-change-me-change-me",
    "change-me-fernet-key",
    "changeme",
    DEFAULT_DEV_ENCRYPTION_KEY,
    "knowledge_password",
    "password",
}
PLACEHOLDER_IDENTIFIERS = {"", "knowledge", "minioadmin"}
LOCAL_APP_BASE_HOSTS = {"localhost", "127.0.0.1", "::1"}
MAX_IN_MEMORY_UPLOAD_BYTES = 200 * 1024 * 1024


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "knowledge-uploader"
    app_env: str = "development"
    app_base_url: str = "http://localhost"
    dependency_check_timeout_seconds: float = 3.0

    database_url: str = Field(
        default="postgresql+asyncpg://knowledge:knowledge_password@postgres:5432/knowledge_uploader"
    )
    alembic_database_url: str = Field(
        default="postgresql+psycopg://knowledge:knowledge_password@postgres:5432/knowledge_uploader"
    )

    celery_broker_url: str = "amqp://knowledge:knowledge_password@rabbitmq:5672//"
    celery_result_backend: str = "redis://redis:6379/0"
    cache_redis_url: str = "redis://redis:6379/1"

    minio_endpoint: str = "minio:9000"
    minio_access_key: str = "knowledge"
    minio_secret_key: str = "knowledge_password"
    minio_bucket: str = "knowledge-files"
    minio_secure: bool = False
    upload_max_file_size_bytes: int = Field(
        default=50 * 1024 * 1024,
        ge=1,
        le=MAX_IN_MEMORY_UPLOAD_BYTES,
    )
    upload_rate_limit_per_minute: int = 10
    upload_allowed_extensions: str = "pdf,docx,xlsx,pptx,txt,md,csv"
    upload_allowed_mime_types: str = (
        "application/pdf,"
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document,"
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,"
        "application/vnd.openxmlformats-officedocument.presentationml.presentation,"
        "text/plain,"
        "text/markdown,"
        "text/csv"
    )

    jwt_secret: str = "change-me-change-me-change-me-change-me"
    jwt_expire_minutes: int = 1440
    encryption_key: str = DEFAULT_DEV_ENCRYPTION_KEY

    allow_register: bool = True
    require_email_verification: bool = False
    allowed_email_domains: str = "company.com"
    password_min_length: int = 8
    login_max_failed_attempts: int = 5
    login_lock_minutes: int = 15
    auth_login_rate_limit_per_hour: int = 20
    email_verification_expire_hours: int = 24
    password_reset_expire_minutes: int = 30
    auth_register_rate_limit_per_hour: int = 5
    auth_password_reset_rate_limit_per_hour: int = 3
    auth_resend_verification_rate_limit_per_hour: int = 3

    ai_analysis_enabled: bool = True
    allow_external_llm: bool = False
    llm_provider: str = "disabled"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    embedding_provider: str = "disabled"
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = ""
    ai_request_timeout: float = 60.0
    ai_max_retry_count: int = 2
    ai_allow_sync_when_analysis_failed: bool = True
    enable_summary: bool = True
    enable_auto_category: bool = True
    enable_tag_generation: bool = True
    enable_sensitive_detection: bool = True
    enable_quality_score: bool = False
    enable_ocr: bool = False
    enable_similarity_detection: bool = False

    ragflow_base_url: str = "http://ragflow:9380"
    ragflow_allowed_base_urls: str = ""
    ragflow_api_key: str = ""
    ragflow_allowed_dataset_ids: str = ""
    ragflow_request_timeout: float = 300.0
    ragflow_max_retry_count: int = 3
    ragflow_parse_poll_timeout_seconds: int = Field(default=3600, ge=60, le=86400)
    uvicorn_forwarded_allow_ips: str = "127.0.0.1"

    @model_validator(mode="after")
    def validate_protected_environment_secrets(self) -> Self:
        approved_ragflow_base_url(self.ragflow_base_url, self)
        for approved_url in _normalized_csv_values(self.ragflow_allowed_base_urls):
            _ragflow_endpoint_identity(approved_url)
        if self.ragflow_api_key.strip() and not _normalized_csv_values(
            self.ragflow_allowed_dataset_ids
        ):
            msg = "RAGFLOW_ALLOWED_DATASET_IDS must be configured when RAGFlow is enabled"
            raise ValueError(msg)

        if not _requires_protected_secret_validation(self.app_env, self.app_base_url):
            return self

        if "*" in _normalized_csv_values(self.uvicorn_forwarded_allow_ips):
            msg = "UVICORN_FORWARDED_ALLOW_IPS must not trust all proxies in protected environments"
            raise ValueError(msg)

        _ensure_non_placeholder_secret("JWT_SECRET", self.jwt_secret, min_length=32)
        _ensure_non_placeholder_secret("ENCRYPTION_KEY", self.encryption_key)
        _ensure_non_placeholder_url_password("DATABASE_URL", self.database_url)
        _ensure_non_placeholder_url_password("ALEMBIC_DATABASE_URL", self.alembic_database_url)
        _ensure_non_placeholder_url_password("CELERY_BROKER_URL", self.celery_broker_url)
        _ensure_non_placeholder_url_password("CELERY_RESULT_BACKEND", self.celery_result_backend)
        _ensure_non_placeholder_url_password("CACHE_REDIS_URL", self.cache_redis_url)
        _ensure_non_placeholder_identifier("MINIO_ACCESS_KEY", self.minio_access_key)
        _ensure_non_placeholder_secret("MINIO_SECRET_KEY", self.minio_secret_key)
        if not self.minio_secure:
            msg = "MINIO_SECURE must be true in protected environments"
            raise ValueError(msg)
        try:
            Fernet(self.encryption_key.encode("utf-8"))
        except ValueError as exc:
            msg = "ENCRYPTION_KEY must be a valid Fernet key"
            raise ValueError(msg) from exc

        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


def _normalized_csv_values(raw_value: str) -> set[str]:
    return {item.strip() for item in raw_value.split(",") if item.strip()}


def _ragflow_endpoint_identity(raw_value: str) -> tuple[str, str, int, str]:
    value = raw_value.strip()
    parsed = urlsplit(value)
    if (
        not value
        or parsed.scheme.lower() not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("RAGFlow base URL must be an absolute HTTP(S) endpoint")
    hostname = parsed.hostname
    if hostname is None:
        raise ValueError("RAGFlow base URL must include a hostname")
    try:
        normalized_hostname = hostname.rstrip(".").encode("idna").decode("ascii").lower()
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    except (UnicodeError, ValueError) as error:
        raise ValueError("RAGFlow base URL contains an invalid hostname or port") from error
    if normalized_hostname in {"metadata.google.internal", "metadata.google.internal."}:
        raise ValueError("RAGFlow base URL must not target an instance metadata endpoint")
    try:
        address = ipaddress.ip_address(normalized_hostname)
    except ValueError:
        address = None
    if address is not None and (address.is_link_local or address.is_unspecified):
        raise ValueError("RAGFlow base URL must not target a link-local endpoint")
    path = parsed.path.rstrip("/")
    if any(segment in {".", ".."} for segment in path.split("/")):
        raise ValueError("RAGFlow base URL path must not contain dot segments")
    return parsed.scheme.lower(), normalized_hostname, port, path


def approved_ragflow_base_url(raw_value: str, settings: Settings) -> str:
    """Return an env-approved endpoint, comparing parsed identities rather than prefixes."""
    cleaned = raw_value.strip().rstrip("/")
    if not cleaned:
        return ""
    candidate = _ragflow_endpoint_identity(cleaned)
    approved_values = {
        settings.ragflow_base_url.strip(),
        *_normalized_csv_values(settings.ragflow_allowed_base_urls),
    }
    approved_identities = {
        _ragflow_endpoint_identity(value) for value in approved_values if value.strip()
    }
    if candidate not in approved_identities:
        raise ValueError("RAGFlow base URL is not approved by the deployment environment")
    return cleaned


def _requires_protected_secret_validation(app_env: str, app_base_url: str) -> bool:
    normalized_env = app_env.strip().lower()
    return normalized_env in PROTECTED_ENVS or _looks_like_deployed_base_url(app_base_url)


def _looks_like_deployed_base_url(raw_value: str) -> bool:
    parsed = urlparse(raw_value.strip())
    hostname = parsed.hostname
    if hostname is None:
        return False
    return hostname.lower() not in LOCAL_APP_BASE_HOSTS


def _ensure_non_placeholder_secret(name: str, raw_value: str, *, min_length: int = 1) -> None:
    value = raw_value.strip()
    if value in PLACEHOLDER_SECRETS or value.lower() in PLACEHOLDER_SECRETS:
        msg = f"{name} must be a non-placeholder value"
        raise ValueError(msg)
    if len(value) < min_length:
        msg = f"{name} must be at least {min_length} characters"
        raise ValueError(msg)


def _ensure_non_placeholder_identifier(name: str, raw_value: str) -> None:
    value = raw_value.strip()
    if value in PLACEHOLDER_IDENTIFIERS or value.lower() in PLACEHOLDER_IDENTIFIERS:
        msg = f"{name} must be a non-placeholder value"
        raise ValueError(msg)


def _ensure_non_placeholder_url_password(name: str, raw_value: str) -> None:
    password = urlparse(raw_value).password or ""
    _ensure_non_placeholder_secret(name, password)
