from __future__ import annotations

import os
import sys
from collections.abc import Awaitable, Callable, Generator
from pathlib import Path

import psycopg
import pytest
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
os.environ["APP_ENV"] = "test"
_ensure_test_database()

for module_name in list(sys.modules):
    if module_name == "app" or module_name == "app.tests" or module_name.startswith("app.tests."):
        continue
    if module_name.startswith("app."):
        del sys.modules[module_name]

SetSystemConfig = Callable[[str, object], Awaitable[None]]
SetSecretSystemConfig = Callable[[str, str], Awaitable[None]]


@pytest.fixture(autouse=True)
def clear_runtime_config_cache() -> Generator[None, None, None]:
    """隔离 runtime_config 进程内 TTL 缓存, 防止配置值在测试间污染。"""
    from app.core import runtime_config
    from app.core.config import get_settings

    runtime_config.invalidate()
    get_settings.cache_clear()
    yield
    runtime_config.invalidate()
    get_settings.cache_clear()


@pytest.fixture
def set_system_config() -> SetSystemConfig:
    """向 system_configs 表 upsert 一个配置值并失效 runtime_config 缓存。

    单测建表走 ``Base.metadata.create_all``, 不执行种子迁移, 因此该 helper
    需要时插入新行; 已有行则原地更新。DB 值优先于环境变量,
    用于替代以前 monkeypatch settings 的配置覆盖方式。
    """

    async def _set(key: str, value: object) -> None:
        from sqlalchemy import select

        from app.core import runtime_config
        from app.core.database import AsyncSessionFactory
        from app.modules.config.defaults import DEFINITIONS_BY_KEY
        from app.modules.config.models import SystemConfig

        definition = DEFINITIONS_BY_KEY[key]
        async with AsyncSessionFactory() as session:
            result = await session.execute(select(SystemConfig).where(SystemConfig.key == key))
            row = result.scalar_one_or_none()
            if row is None:
                session.add(
                    SystemConfig(
                        key=key,
                        group=definition.group,
                        value=value,
                        value_type=definition.value_type,
                        is_secret=definition.is_secret,
                        description=definition.description,
                    )
                )
            else:
                row.value = value
            await session.commit()
        runtime_config.invalidate(key)

    return _set


@pytest.fixture
def set_secret_system_config() -> SetSecretSystemConfig:
    """向 system_configs 表 upsert 一个加密 secret 配置值。"""

    async def _set(key: str, value: str) -> None:
        from sqlalchemy import select

        from app.core import runtime_config
        from app.core.config import get_settings
        from app.core.database import AsyncSessionFactory
        from app.core.security import encrypt_secret
        from app.modules.config.defaults import DEFINITIONS_BY_KEY
        from app.modules.config.models import SystemConfig

        definition = DEFINITIONS_BY_KEY[key]
        if not definition.is_secret:
            raise ValueError(f"config key is not secret: {key}")
        encrypted_value = encrypt_secret(value, get_settings().encryption_key)
        async with AsyncSessionFactory() as session:
            result = await session.execute(select(SystemConfig).where(SystemConfig.key == key))
            row = result.scalar_one_or_none()
            if row is None:
                session.add(
                    SystemConfig(
                        key=key,
                        group=definition.group,
                        value=encrypted_value,
                        value_type=definition.value_type,
                        is_secret=definition.is_secret,
                        description=definition.description,
                    )
                )
            else:
                row.value = encrypted_value
            await session.commit()
        runtime_config.invalidate(key)

    return _set
