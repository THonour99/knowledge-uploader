from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg
from psycopg import sql

BACKEND_ROOT = Path(__file__).resolve().parents[2]
TEST_DATABASE_NAME = "knowledge_uploader_test"
TEST_ASYNC_DATABASE_URL = (
    f"postgresql+asyncpg://knowledge:knowledge_password@postgres:5432/{TEST_DATABASE_NAME}"
)
TEST_ALEMBIC_DATABASE_URL = (
    f"postgresql+psycopg://knowledge:knowledge_password@postgres:5432/{TEST_DATABASE_NAME}"
)
TEST_ADMIN_DATABASE_URL = "postgresql://knowledge:knowledge_password@postgres:5432/postgres"
TEST_CACHE_REDIS_URL = "redis://redis:6379/15"


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
