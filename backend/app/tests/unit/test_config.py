from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings

VALID_FERNET_KEY = "RZ1Sw_27VrN9c5Cfsq01qiwViwT6y7jDCuXYn7tgGJY="
PRODUCTION_FERNET_KEY = "x6TF85ulMkiMF3GSpxCRgYn5v_t7q8D2r5LJw8ZvcVY="
PRODUCTION_JWT_SECRET = "this-is-a-production-secret-with-32-bytes"


def _production_settings(**overrides: object) -> dict[str, object]:
    settings: dict[str, object] = {
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
