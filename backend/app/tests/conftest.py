from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg
from psycopg import sql

BACKEND_ROOT = Path(__file__).resolve().parents[2]
TEST_DATABASE_NAME = os.getenv("TEST_DATABASE_NAME", "knowledge_uploader_test")
TEST_POSTGRES_HOST = os.getenv("TEST_POSTGRES_HOST", "postgres")
TEST_POSTGRES_PORT = os.getenv("TEST_POSTGRES_PORT", "5432")
TEST_POSTGRES_USER = os.getenv("TEST_POSTGRES_USER", "knowledge")
TEST_POSTGRES_PASSWORD = os.getenv("TEST_POSTGRES_PASSWORD", "knowledge_password")
TEST_REDIS_HOST = os.getenv("TEST_REDIS_HOST", "redis")
TEST_REDIS_PORT = os.getenv("TEST_REDIS_PORT", "6379")
TEST_ASYNC_DATABASE_URL = (
    "postgresql+asyncpg://"
    f"{TEST_POSTGRES_USER}:{TEST_POSTGRES_PASSWORD}"
    f"@{TEST_POSTGRES_HOST}:{TEST_POSTGRES_PORT}/{TEST_DATABASE_NAME}"
)
TEST_ALEMBIC_DATABASE_URL = (
    "postgresql+psycopg://"
    f"{TEST_POSTGRES_USER}:{TEST_POSTGRES_PASSWORD}"
    f"@{TEST_POSTGRES_HOST}:{TEST_POSTGRES_PORT}/{TEST_DATABASE_NAME}"
)
TEST_ADMIN_DATABASE_URL = (
    "postgresql://"
    f"{TEST_POSTGRES_USER}:{TEST_POSTGRES_PASSWORD}"
    f"@{TEST_POSTGRES_HOST}:{TEST_POSTGRES_PORT}/postgres"
)
TEST_CACHE_REDIS_URL = os.getenv(
    "TEST_CACHE_REDIS_URL",
    f"redis://{TEST_REDIS_HOST}:{TEST_REDIS_PORT}/15",
)


def _ensure_test_database() -> None:
    with psycopg.connect(TEST_ADMIN_DATABASE_URL, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "select 1 from pg_database where datname = %s",
                (TEST_DATABASE_NAME,),
            )
            if cursor.fetchone() is None:
                cursor.execute(
                    sql.SQL("create database {}").format(sql.Identifier(TEST_DATABASE_NAME))
                )


sys.path = [path for path in sys.path if path != str(BACKEND_ROOT)]
sys.path.insert(0, str(BACKEND_ROOT))
os.environ["DATABASE_URL"] = TEST_ASYNC_DATABASE_URL
os.environ["ALEMBIC_DATABASE_URL"] = TEST_ALEMBIC_DATABASE_URL
os.environ["CACHE_REDIS_URL"] = TEST_CACHE_REDIS_URL
_ensure_test_database()

for module_name in list(sys.modules):
    if module_name == "app" or module_name.startswith("app."):
        del sys.modules[module_name]
