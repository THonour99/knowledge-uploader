from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    encryption_key: str = "change-me-fernet-key"

    ai_analysis_enabled: bool = True
    ragflow_base_url: str = "http://ragflow:9380"
    ragflow_api_key: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
