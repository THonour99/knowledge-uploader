from __future__ import annotations

from functools import lru_cache
from typing import Self
from urllib.parse import urlparse

from cryptography.fernet import Fernet
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.llm_endpoint import (
    normalize_llm_base_url,
    normalized_llm_allowed_base_urls,
    normalized_llm_tls_spki_pins,
)
from app.core.ragflow_endpoint import (
    normalized_ragflow_tls_spki_pins,
    ragflow_endpoint_identity,
)

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
DEFAULT_SMTP_PORT = 587
DEFAULT_SMTP_TIMEOUT_SECONDS = 10.0
MAX_SMTP_TIMEOUT_SECONDS = 300.0
MINIO_METRICS_ONLY_CREDENTIAL = "metrics-bearer-only-no-data-plane"


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
    cache_redis_url: str = "redis://redis:6379/1"

    minio_endpoint: str = "minio:9000"
    minio_access_key: str = "knowledge"
    minio_secret_key: str = "knowledge_password"
    minio_bucket: str = "knowledge-files"
    minio_secure: bool = False
    minio_ca_cert_file: str = ""
    minio_metrics_bearer_token_file: str = ""
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

    smtp_host: str = ""
    smtp_port: int = Field(default=DEFAULT_SMTP_PORT, ge=1, le=65535)
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_tls: bool = True
    smtp_ca_cert_file: str = ""
    smtp_timeout_seconds: float = Field(
        default=DEFAULT_SMTP_TIMEOUT_SECONDS,
        gt=0,
        le=MAX_SMTP_TIMEOUT_SECONDS,
    )

    ai_analysis_enabled: bool = True
    allow_external_llm: bool = False
    llm_provider: str = "disabled"
    llm_base_url: str = ""
    llm_allowed_base_urls: str = ""
    llm_tls_spki_pins: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    ai_request_timeout: int = Field(default=60, ge=1, le=240)
    ai_max_retry_count: int = Field(default=2, ge=0, le=10)
    ai_allow_sync_when_analysis_failed: bool = True
    enable_summary: bool = True
    enable_auto_category: bool = True
    enable_tag_generation: bool = True
    enable_sensitive_detection: bool = True
    enable_quality_score: bool = False
    enable_similarity_detection: bool = False

    ragflow_base_url: str = "http://ragflow:9380"
    ragflow_allowed_base_urls: str = ""
    ragflow_tls_spki_pins: str = ""
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
            ragflow_endpoint_identity(approved_url)
        ragflow_pins = normalized_ragflow_tls_spki_pins(self.ragflow_tls_spki_pins)
        approved_ragflow_identities = {
            ragflow_endpoint_identity(value)
            for value in {
                self.ragflow_base_url.strip(),
                *_normalized_csv_values(self.ragflow_allowed_base_urls),
            }
            if value
        }
        if not set(ragflow_pins).issubset(approved_ragflow_identities):
            raise ValueError("RAGFLOW_TLS_SPKI_PINS endpoints must be approved RAGFlow URLs")
        if self.ragflow_api_key.strip() and not _normalized_csv_values(
            self.ragflow_allowed_dataset_ids
        ):
            msg = "RAGFLOW_ALLOWED_DATASET_IDS must be configured when RAGFlow is enabled"
            raise ValueError(msg)

        smtp_configured = _validate_smtp_configuration(self)
        _validate_llm_seed_configuration(self)
        normalized_llm_allowed_base_urls(self.llm_allowed_base_urls)

        if not is_protected_environment(self.app_env, self.app_base_url):
            return self

        if self.ragflow_api_key.strip():
            endpoint_identity = ragflow_endpoint_identity(self.ragflow_base_url)
            if endpoint_identity[0] != "https":
                raise ValueError("RAGFLOW_BASE_URL must use HTTPS in protected environments")
            if endpoint_identity not in ragflow_pins:
                raise ValueError("RAGFLOW_TLS_SPKI_PINS must bind the protected RAGFlow endpoint")

        if self.allow_external_llm:
            msg = (
                "ALLOW_EXTERNAL_LLM cannot be enabled in protected environments "
                "until COST-002 is approved and implemented"
            )
            raise ValueError(msg)

        if "*" in _normalized_csv_values(self.uvicorn_forwarded_allow_ips):
            msg = "UVICORN_FORWARDED_ALLOW_IPS must not trust all proxies in protected environments"
            raise ValueError(msg)

        _ensure_non_placeholder_secret("JWT_SECRET", self.jwt_secret, min_length=32)
        _ensure_non_placeholder_secret("ENCRYPTION_KEY", self.encryption_key)
        _ensure_non_placeholder_url_password("DATABASE_URL", self.database_url)
        _ensure_non_placeholder_url_password("ALEMBIC_DATABASE_URL", self.alembic_database_url)
        _ensure_non_placeholder_url_password("CELERY_BROKER_URL", self.celery_broker_url)
        _ensure_non_placeholder_url_password("CACHE_REDIS_URL", self.cache_redis_url)
        _ensure_non_placeholder_identifier("MINIO_ACCESS_KEY", self.minio_access_key)
        _ensure_non_placeholder_secret("MINIO_SECRET_KEY", self.minio_secret_key)
        if not self.minio_secure:
            msg = "MINIO_SECURE must be true in protected environments"
            raise ValueError(msg)
        if not self.minio_ca_cert_file.strip():
            msg = "MINIO_CA_CERT_FILE must be configured in protected environments"
            raise ValueError(msg)
        metrics_access = self.minio_access_key == MINIO_METRICS_ONLY_CREDENTIAL
        metrics_secret = self.minio_secret_key == MINIO_METRICS_ONLY_CREDENTIAL
        if metrics_access != metrics_secret:
            msg = "MinIO metrics-only credentials must be configured as a pair"
            raise ValueError(msg)
        has_metrics_token_file = bool(self.minio_metrics_bearer_token_file.strip())
        if metrics_access and not has_metrics_token_file:
            msg = (
                "MINIO_METRICS_BEARER_TOKEN_FILE must be configured "
                "for the protected metrics consumer"
            )
            raise ValueError(msg)
        if not metrics_access and has_metrics_token_file:
            msg = "MINIO_METRICS_BEARER_TOKEN_FILE is restricted to the protected metrics consumer"
            raise ValueError(msg)
        if not smtp_configured:
            msg = "SMTP must be configured in protected environments"
            raise ValueError(msg)
        if not self.smtp_tls:
            msg = "SMTP_TLS must be enabled in protected environments"
            raise ValueError(msg)
        if self.smtp_user.strip():
            _ensure_non_placeholder_secret("SMTP_PASSWORD", self.smtp_password)
        try:
            Fernet(self.encryption_key.encode("utf-8"))
        except ValueError as exc:
            msg = "ENCRYPTION_KEY must be a valid Fernet key"
            raise ValueError(msg) from exc

        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


def _validate_smtp_configuration(settings: Settings) -> bool:
    host = settings.smtp_host.strip()
    username = settings.smtp_user.strip()
    password_configured = bool(settings.smtp_password)
    sender = settings.smtp_from.strip() or username
    requested = bool(
        host
        or username
        or password_configured
        or settings.smtp_from.strip()
        or settings.smtp_ca_cert_file.strip()
        or settings.smtp_port != DEFAULT_SMTP_PORT
        or settings.smtp_timeout_seconds != DEFAULT_SMTP_TIMEOUT_SECONDS
        or not settings.smtp_tls
    )
    if not requested:
        return False
    if bool(username) != password_configured:
        msg = "SMTP_USER and SMTP_PASSWORD must be configured together"
        raise ValueError(msg)
    if not host or not sender:
        msg = "SMTP_HOST and SMTP_FROM or SMTP_USER must be configured together"
        raise ValueError(msg)
    if settings.smtp_ca_cert_file.strip() and not settings.smtp_tls:
        msg = "SMTP_CA_CERT_FILE requires SMTP_TLS=true"
        raise ValueError(msg)
    return True


def _validate_llm_seed_configuration(settings: Settings) -> None:
    provider_type = settings.llm_provider.strip().lower() or "disabled"
    allowed_provider_types = {
        "openai_compatible",
        "local_openai_compatible",
        "ollama",
        "vllm",
        "lmstudio",
        "custom",
        "mock",
        "disabled",
    }
    if provider_type not in allowed_provider_types:
        raise ValueError("LLM_PROVIDER is not supported")
    protected = is_protected_environment(settings.app_env, settings.app_base_url)
    allowed_base_urls = normalized_llm_allowed_base_urls(settings.llm_allowed_base_urls)
    tls_spki_pins = normalized_llm_tls_spki_pins(settings.llm_tls_spki_pins)
    if not set(tls_spki_pins).issubset(allowed_base_urls):
        raise ValueError("LLM_TLS_SPKI_PINS endpoints must be approved LLM URLs")
    if provider_type == "disabled":
        return
    if provider_type == "mock":
        if protected:
            raise ValueError("LLM_PROVIDER=mock is forbidden in protected environments")
        return

    raw_base_url = settings.llm_base_url.strip()
    model = settings.llm_model.strip()
    if not raw_base_url or not model:
        raise ValueError("LLM_BASE_URL and LLM_MODEL are required when LLM_PROVIDER is enabled")
    invalid_base_url = False
    try:
        base_url = normalize_llm_base_url(raw_base_url)
    except ValueError:
        invalid_base_url = True
        base_url = ""
    if invalid_base_url:
        raise ValueError("LLM_BASE_URL must be a safe absolute HTTP(S) endpoint")
    if base_url not in allowed_base_urls:
        raise ValueError("LLM_BASE_URL must exactly match LLM_ALLOWED_BASE_URLS")
    if protected and (not base_url.startswith("https://") or base_url not in tls_spki_pins):
        raise ValueError("LLM_TLS_SPKI_PINS must bind the protected LLM endpoint over HTTPS")


def _normalized_csv_values(raw_value: str) -> set[str]:
    return {item.strip() for item in raw_value.split(",") if item.strip()}


def approved_ragflow_base_url(raw_value: str, settings: Settings) -> str:
    """Return an env-approved endpoint, comparing parsed identities rather than prefixes."""
    cleaned = raw_value.strip().rstrip("/")
    if not cleaned:
        return ""
    candidate = ragflow_endpoint_identity(cleaned)
    approved_values = {
        settings.ragflow_base_url.strip(),
        *_normalized_csv_values(settings.ragflow_allowed_base_urls),
    }
    approved_identities = {
        ragflow_endpoint_identity(value) for value in approved_values if value.strip()
    }
    if candidate not in approved_identities:
        raise ValueError("RAGFlow base URL is not approved by the deployment environment")
    return cleaned


def is_protected_environment(app_env: str, app_base_url: str) -> bool:
    normalized_env = app_env.strip().lower()
    return normalized_env in PROTECTED_ENVS or _looks_like_deployed_base_url(app_base_url)


def _looks_like_deployed_base_url(raw_value: str) -> bool:
    cleaned = raw_value.strip()
    if not cleaned:
        return False
    try:
        parsed = urlparse(cleaned)
        hostname = parsed.hostname
        _port = parsed.port
    except ValueError:
        return True
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.netloc
        or hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return True
    return hostname.rstrip(".").lower() not in LOCAL_APP_BASE_HOSTS


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
