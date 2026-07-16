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
        "celery_result_backend": "redis://:strong-redis-secret@redis:6379/0",
        "cache_redis_url": "redis://:strong-redis-secret@redis:6379/1",
        "minio_access_key": "knowledge-prod",
        "minio_secret_key": "strong-minio-secret",
        "minio_secure": True,
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
        )


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
    settings = Settings(
        **_production_settings(uvicorn_forwarded_allow_ips="127.0.0.1,10.0.0.5")
    )

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
