from __future__ import annotations

import os
from urllib.parse import urlparse

SAFE_TEST_DATABASE_SUFFIX = "_test"
SAFE_TEST_REDIS_DB = "15"


def require_safe_test_database_reset() -> None:
    if os.environ.get("APP_ENV") != "test":
        raise RuntimeError("destructive test reset requires APP_ENV=test")

    database_name = urlparse(os.environ["DATABASE_URL"]).path.lstrip("/")
    if not database_name.endswith(SAFE_TEST_DATABASE_SUFFIX):
        raise RuntimeError("destructive test reset requires a *_test database")


def require_safe_test_redis_reset() -> None:
    if os.environ.get("APP_ENV") != "test":
        raise RuntimeError("destructive test Redis reset requires APP_ENV=test")

    redis_db = urlparse(os.environ["CACHE_REDIS_URL"]).path.lstrip("/") or "0"
    if redis_db != SAFE_TEST_REDIS_DB:
        raise RuntimeError("destructive test Redis reset requires Redis DB 15")
