from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.core.config import Settings, approved_ragflow_base_url

VALID_FERNET_KEY = "RZ1Sw_27VrN9c5Cfsq01qiwViwT6y7jDCuXYn7tgGJY="
PRODUCTION_FERNET_KEY = "x6TF85ulMkiMF3GSpxCRgYn5v_t7q8D2r5LJw8ZvcVY="
PRODUCTION_JWT_SECRET = "this-is-a-production-secret-with-32-bytes"


def _production_settings(**overrides: Any) -> dict[str, Any]:
    settings: dict[str, Any] = {
        "app_env": "production",
        "jwt_secret": PRODUCTION_JWT_SECRET,
        "encryption_key": PRODUCTION_FERNET_KEY,
        "database_url": (
            "postgresql+asyncpg://knowledge:strong-db-secret@postgres:5432/knowledge_uploader"
        ),
        "alembic_database_url": (
            "postgresql+psycopg://knowledge:strong-db-secret@postgres:5432/knowledge_uploader"
        ),
        "celery_broker_url": "amqp://knowledge:strong-rabbit-secret@rabbitmq:5672//",
        "cache_redis_url": "redis://:strong-redis-secret@redis:6379/1",
        "minio_access_key": "knowledge-prod",
        "minio_secret_key": "strong-minio-secret",
        "minio_secure": True,
        "minio_ca_cert_file": "/etc/ssl/certs/ca-certificates.crt",
        "minio_metrics_bearer_token_file": "",
        "smtp_host": "mail.internal",
        "smtp_from": "noreply@example.com",
        "smtp_tls": True,
    }
    settings.update(overrides)
    return settings


def test_development_allows_phase0_placeholder_secrets() -> None:
    settings = Settings(
        app_env="development",
        jwt_secret="change-me-change-me-change-me-change-me",
        encryption_key="change-me-fernet-key",
    )

    assert settings.app_env == "development"


def test_production_rejects_placeholder_jwt_secret() -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        Settings(**_production_settings(jwt_secret="change-me-change-me-change-me-change-me"))


def test_production_rejects_invalid_encryption_key() -> None:
    with pytest.raises(ValidationError, match="ENCRYPTION_KEY"):
        Settings(**_production_settings(encryption_key="change-me-fernet-key"))


def test_production_rejects_default_development_encryption_key() -> None:
    with pytest.raises(ValidationError, match="ENCRYPTION_KEY"):
        Settings(**_production_settings(encryption_key=VALID_FERNET_KEY))


@pytest.mark.parametrize("timeout_seconds", [59, 86401])
def test_ragflow_parse_poll_timeout_is_bounded(timeout_seconds: int) -> None:
    with pytest.raises(ValidationError, match="ragflow_parse_poll_timeout_seconds"):
        Settings(
            jwt_secret="test-jwt-secret-with-more-than-32-bytes",
            ragflow_parse_poll_timeout_seconds=timeout_seconds,
        )


def test_production_rejects_default_infrastructure_passwords() -> None:
    with pytest.raises(ValidationError, match="DATABASE_URL"):
        Settings(
            app_env="production",
            jwt_secret=PRODUCTION_JWT_SECRET,
            encryption_key=PRODUCTION_FERNET_KEY,
            minio_secure=True,
            minio_ca_cert_file="/etc/ssl/certs/ca-certificates.crt",
        )


def test_protected_environment_requires_explicit_minio_ca_file() -> None:
    with pytest.raises(ValidationError, match="MINIO_CA_CERT_FILE"):
        Settings(**_production_settings(minio_ca_cert_file=""))


def test_protected_data_plane_requires_empty_metrics_bearer_file() -> None:
    settings = Settings(**_production_settings())

    assert settings.minio_metrics_bearer_token_file == ""

    with pytest.raises(ValidationError, match="MINIO_METRICS_BEARER_TOKEN_FILE"):
        Settings(
            **_production_settings(
                minio_metrics_bearer_token_file="/run/secrets/minio-metrics/token"
            )
        )


@pytest.mark.parametrize(
    ("access_key", "secret_key"),
    (
        ("metrics-bearer-only-no-data-plane", "strong-minio-secret"),
        ("knowledge-prod", "metrics-bearer-only-no-data-plane"),
    ),
)
def test_protected_environment_rejects_partial_metrics_only_credentials(
    access_key: str,
    secret_key: str,
) -> None:
    with pytest.raises(ValidationError, match="configured as a pair"):
        Settings(
            **_production_settings(
                minio_access_key=access_key,
                minio_secret_key=secret_key,
            )
        )


def test_protected_metrics_consumer_requires_minio_metrics_bearer_file() -> None:
    metrics_settings = {
        "minio_access_key": "metrics-bearer-only-no-data-plane",
        "minio_secret_key": "metrics-bearer-only-no-data-plane",
    }
    with pytest.raises(ValidationError, match="MINIO_METRICS_BEARER_TOKEN_FILE"):
        Settings(
            **_production_settings(
                **metrics_settings,
                minio_metrics_bearer_token_file="",
            )
        )

    settings = Settings(
        **_production_settings(
            **metrics_settings,
            minio_metrics_bearer_token_file="/run/secrets/minio-metrics/token",
        )
    )
    assert settings.minio_metrics_bearer_token_file == ("/run/secrets/minio-metrics/token")


def test_protected_environment_rejects_configured_plaintext_smtp() -> None:
    with pytest.raises(ValidationError, match="SMTP_TLS"):
        Settings(
            **_production_settings(
                smtp_host="mail.internal",
                smtp_from="noreply@example.com",
                smtp_tls=False,
            )
        )


def test_protected_environment_requires_smtp_configuration() -> None:
    with pytest.raises(ValidationError, match="SMTP must be configured"):
        Settings(**_production_settings(smtp_host="", smtp_from=""))


def test_protected_authenticated_smtp_rejects_placeholder_password() -> None:
    with pytest.raises(ValidationError, match="SMTP_PASSWORD"):
        Settings(
            **_production_settings(
                smtp_user="mailer",
                smtp_password="password",
            )
        )


def test_protected_environment_accepts_anonymous_tls_relay() -> None:
    settings = Settings(**_production_settings())

    assert settings.smtp_user == ""
    assert settings.smtp_password == ""
    assert settings.smtp_tls is True


def test_protected_environment_accepts_authenticated_tls_relay() -> None:
    settings = Settings(
        **_production_settings(
            smtp_user="mailer",
            smtp_password="strong-mail-secret",
        )
    )

    assert settings.smtp_user == "mailer"
    assert settings.smtp_tls is True


@pytest.mark.parametrize(
    "smtp_overrides",
    (
        {"smtp_host": "mail.internal"},
        {"smtp_from": "noreply@example.com"},
        {"smtp_user": "mailer"},
        {"smtp_password": "mail-secret"},
        {"smtp_ca_cert_file": "/run/secrets/mail-ca.pem"},
        {"smtp_port": 2525},
        {"smtp_timeout_seconds": 30.0},
        {"smtp_tls": False},
    ),
)
def test_smtp_partial_configuration_fails_at_settings_startup(
    smtp_overrides: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError, match="SMTP_"):
        Settings(**smtp_overrides)


@pytest.mark.parametrize("smtp_port", [0, 65536])
def test_smtp_port_is_bounded_at_settings_startup(smtp_port: int) -> None:
    with pytest.raises(ValidationError, match="smtp_port"):
        Settings(smtp_port=smtp_port)


@pytest.mark.parametrize("smtp_timeout_seconds", [0, -1, 300.1])
def test_smtp_timeout_is_bounded_at_settings_startup(
    smtp_timeout_seconds: float,
) -> None:
    with pytest.raises(ValidationError, match="smtp_timeout_seconds"):
        Settings(smtp_timeout_seconds=smtp_timeout_seconds)


def test_smtp_anonymous_relay_does_not_require_password() -> None:
    settings = Settings(
        smtp_host="mail.internal",
        smtp_from="noreply@example.com",
    )

    assert settings.smtp_user == ""
    assert settings.smtp_password == ""


def test_smtp_authenticated_relay_requires_complete_credentials() -> None:
    settings = Settings(
        smtp_host="mail.internal",
        smtp_user="mailer@example.com",
        smtp_password="mail-secret",
    )

    assert settings.smtp_from == ""
    assert settings.smtp_user == "mailer@example.com"


def test_deployed_base_url_rejects_placeholder_secrets_when_app_env_is_omitted() -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        Settings(
            app_base_url="https://knowledge.company.com",
            jwt_secret="change-me-change-me-change-me-change-me",
            encryption_key=PRODUCTION_FERNET_KEY,
        )


def test_production_requires_ragflow_dataset_allowlist_when_key_is_configured() -> None:
    with pytest.raises(ValidationError, match="RAGFLOW_ALLOWED_DATASET_IDS"):
        Settings(
            **_production_settings(
                ragflow_api_key="test-ragflow-key",
                ragflow_allowed_dataset_ids="",
            )
        )


def test_development_requires_ragflow_dataset_allowlist_when_key_is_configured() -> None:
    with pytest.raises(ValidationError, match="RAGFLOW_ALLOWED_DATASET_IDS"):
        Settings(
            app_env="development",
            ragflow_api_key="test-ragflow-key",
            ragflow_allowed_dataset_ids="",
        )


def test_ragflow_dataset_allowlist_must_have_normalized_values() -> None:
    with pytest.raises(ValidationError, match="RAGFLOW_ALLOWED_DATASET_IDS"):
        Settings(
            app_env="development",
            ragflow_api_key="test-ragflow-key",
            ragflow_allowed_dataset_ids=", ,",
        )


def test_production_accepts_ragflow_dataset_allowlist() -> None:
    settings = Settings(
        **_production_settings(
            ragflow_api_key="test-ragflow-key",
            ragflow_allowed_dataset_ids="dataset-1,dataset-2",
        )
    )

    assert settings.ragflow_allowed_dataset_ids == "dataset-1,dataset-2"


def test_protected_environment_rejects_trusting_all_forwarded_ips() -> None:
    with pytest.raises(ValidationError, match="UVICORN_FORWARDED_ALLOW_IPS"):
        Settings(**_production_settings(uvicorn_forwarded_allow_ips="*"))


def test_protected_environment_accepts_explicit_forwarded_ips() -> None:
    settings = Settings(**_production_settings(uvicorn_forwarded_allow_ips="127.0.0.1,10.0.0.5"))

    assert settings.uvicorn_forwarded_allow_ips == "127.0.0.1,10.0.0.5"


@pytest.mark.parametrize(
    "base_url",
    (
        "http://user:password@ragflow:9380",
        "http://ragflow:9380?token=secret",
        "http://ragflow:9380/#fragment",
        "http://169.254.169.254/latest/meta-data",
        "http://metadata.google.internal/computeMetadata/v1",
    ),
)
def test_ragflow_environment_endpoint_rejects_credential_and_metadata_urls(
    base_url: str,
) -> None:
    with pytest.raises(ValidationError, match="RAGFlow base URL"):
        Settings(ragflow_base_url=base_url)


def test_ragflow_runtime_endpoint_requires_exact_environment_approval() -> None:
    settings = Settings(
        ragflow_base_url="http://ragflow:9380/root",
        ragflow_allowed_base_urls="https://ragflow.internal:9443/api",
    )

    assert (
        approved_ragflow_base_url("https://ragflow.internal:9443/api/", settings)
        == "https://ragflow.internal:9443/api"
    )
    with pytest.raises(ValueError, match="not approved"):
        approved_ragflow_base_url("http://ragflow:9380/root.evil", settings)
    with pytest.raises(ValueError, match="not approved"):
        approved_ragflow_base_url("https://ragflow.internal:9443/api-extra", settings)


@pytest.mark.parametrize("ai_request_timeout", [0, 241, 60.5])
def test_ai_request_timeout_is_bounded_integer_at_settings_startup(
    ai_request_timeout: float,
) -> None:
    with pytest.raises(ValidationError, match="ai_request_timeout"):
        Settings(ai_request_timeout=ai_request_timeout)


@pytest.mark.parametrize("ai_max_retry_count", [-1, 11])
def test_ai_max_retry_count_is_bounded_at_settings_startup(ai_max_retry_count: int) -> None:
    with pytest.raises(ValidationError, match="ai_max_retry_count"):
        Settings(ai_max_retry_count=ai_max_retry_count)


def test_ai_runtime_boundaries_map_without_clamping() -> None:
    settings = Settings(ai_request_timeout=240, ai_max_retry_count=10)
    assert (settings.ai_request_timeout, settings.ai_max_retry_count) == (240, 10)


@pytest.mark.parametrize(
    ("llm_base_url", "llm_model"),
    [
        ("", "analysis-model"),
        ("https://llm.example.test/v1", ""),
        ("ftp://llm.example.test/v1", "analysis-model"),
        ("https://user:pass@llm.example.test/v1", "analysis-model"),
        ("https://llm.example.test/v1?api_key=secret", "analysis-model"),
    ],
)
def test_enabled_llm_seed_rejects_missing_or_unsafe_endpoint_configuration(
    llm_base_url: str,
    llm_model: str,
) -> None:
    with pytest.raises(ValidationError, match="LLM_"):
        Settings(
            llm_provider="openai_compatible",
            llm_base_url=llm_base_url,
            llm_model=llm_model,
        )


def test_enabled_llm_seed_accepts_complete_internal_endpoint_configuration() -> None:
    settings = Settings(
        llm_provider="local_openai_compatible",
        llm_base_url="http://vllm:8000/v1/",
        llm_model="qwen-analysis",
        llm_allowed_base_urls="http://vllm:8000/v1",
    )

    assert settings.llm_base_url == "http://vllm:8000/v1/"
    assert settings.llm_model == "qwen-analysis"


@pytest.mark.parametrize("app_env", ["staging", "production"])
def test_protected_environment_rejects_external_llm_until_cost_002(app_env: str) -> None:
    with pytest.raises(ValidationError, match="COST-002"):
        Settings(
            **_production_settings(
                app_env=app_env,
                allow_external_llm=True,
                llm_provider="openai_compatible",
                llm_base_url="https://llm.example.test/v1",
                llm_model="analysis-model",
                llm_allowed_base_urls="https://llm.example.test/v1",
            )
        )


def test_development_allows_external_llm_gate() -> None:
    settings = Settings(
        app_env="development",
        allow_external_llm=True,
        llm_provider="openai_compatible",
        llm_base_url="https://llm.example.test/v1",
        llm_model="analysis-model",
        llm_allowed_base_urls="https://llm.example.test/v1",
    )

    assert settings.allow_external_llm is True
    assert settings.llm_provider == "openai_compatible"


def test_protected_environment_allows_internal_llm_with_external_gate_disabled() -> None:
    settings = Settings(
        **_production_settings(
            allow_external_llm=False,
            llm_provider="local_openai_compatible",
            llm_base_url="http://vllm:8000/v1",
            llm_model="qwen-analysis",
            llm_allowed_base_urls="http://vllm:8000/v1",
        )
    )

    assert settings.allow_external_llm is False
    assert settings.llm_provider == "local_openai_compatible"


def test_protected_environment_rejects_mock_llm_at_startup() -> None:
    with pytest.raises(ValidationError, match="LLM_PROVIDER=mock"):
        Settings(**_production_settings(llm_provider="mock"))
