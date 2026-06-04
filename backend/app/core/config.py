from __future__ import annotations

from functools import lru_cache
from typing import Self

from cryptography.fernet import Fernet
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROTECTED_ENVS = {"production", "prod", "staging"}
DEFAULT_DEV_ENCRYPTION_KEY = "RZ1Sw_27VrN9c5Cfsq01qiwViwT6y7jDCuXYn7tgGJY="
PLACEHOLDER_SECRETS = {
    "",
    "change-me-change-me-change-me-change-me",
    "change-me-fernet-key",
    DEFAULT_DEV_ENCRYPTION_KEY,
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "knowledge-uploader"
    app_env: str = "development"
    app_base_url: str = "http://localhost"

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

    jwt_secret: str = "change-me-change-me-change-me-change-me"
    jwt_expire_minutes: int = 1440
    encryption_key: str = DEFAULT_DEV_ENCRYPTION_KEY

    ai_analysis_enabled: bool = True
    ragflow_base_url: str = "http://ragflow:9380"
    ragflow_api_key: str = ""

    @model_validator(mode="after")
    def validate_protected_environment_secrets(self) -> Self:
        if self.app_env.strip().lower() not in PROTECTED_ENVS:
            return self

        if self.jwt_secret in PLACEHOLDER_SECRETS or len(self.jwt_secret) < 32:
            msg = "JWT_SECRET must be a non-placeholder value with at least 32 characters"
            raise ValueError(msg)

        if self.encryption_key in PLACEHOLDER_SECRETS:
            msg = "ENCRYPTION_KEY must be a non-placeholder Fernet key"
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
