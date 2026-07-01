from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import NotRequired, TypedDict
from urllib.request import urlopen

import anyio
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from kombu import Connection
from redis.asyncio import from_url
from sqlalchemy import text
from starlette import status
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.config import get_settings
from app.core.database import engine
from app.core.exceptions import ErrorCode
from app.core.logging import configure_logging
from app.core.middlewares import request_id_middleware
from app.core.responses import error_response
from app.modules.ai.api import router as ai_router
from app.modules.audit.api import router as audit_router
from app.modules.auth.api import router as auth_router
from app.modules.config.api import router as config_router
from app.modules.department.api import managed_router as department_managed_router
from app.modules.department.api import router as department_router
from app.modules.document.api import admin_router as document_admin_router
from app.modules.document.api import policy_router as document_policy_router
from app.modules.document.api import router as document_router
from app.modules.notification.api import router as notification_router
from app.modules.ragflow.api import router as ragflow_router
from app.modules.review.api import router as review_router
from app.modules.statistics.api import router as statistics_router
from app.modules.user.api import router as user_router

configure_logging()
app = FastAPI(title="Knowledge Uploader", version="0.1.0")
app.middleware("http")(request_id_middleware)
app.include_router(ai_router)
app.include_router(audit_router)
app.include_router(auth_router)
app.include_router(config_router)
app.include_router(department_router)
app.include_router(department_managed_router)
app.include_router(document_admin_router)
app.include_router(document_router)
app.include_router(document_policy_router)
app.include_router(notification_router)
app.include_router(ragflow_router)
app.include_router(review_router)
app.include_router(statistics_router)
app.include_router(user_router)


class DependencyHealth(TypedDict):
    status: str
    detail: NotRequired[str]


class ReadinessHealth(TypedDict):
    status: str
    dependencies: dict[str, DependencyHealth]


DependencyCheck = Callable[[], Awaitable[None]]


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict):
        error_code = str(detail.get("error_code", ErrorCode.INTERNAL_ERROR))
        message = str(detail.get("message", "request failed"))
    else:
        error_code = ErrorCode.INTERNAL_ERROR
        message = str(detail)
    return JSONResponse(
        status_code=exc.status_code,
        content=error_response(str(error_code), message, request),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=error_response(
            str(ErrorCode.VALIDATION_ERROR),
            "request validation failed",
            request,
        ),
    )


@app.get("/api/system/health", tags=["system"])
async def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/system/ready", tags=["system"])
async def readiness_check() -> JSONResponse:
    payload = await collect_readiness()
    response_status = (
        status.HTTP_200_OK if payload["status"] == "ok" else status.HTTP_503_SERVICE_UNAVAILABLE
    )
    return JSONResponse(status_code=response_status, content=payload)


async def collect_readiness() -> ReadinessHealth:
    checks = _readiness_checks()
    check_results = await asyncio.gather(
        *(_run_dependency_check(check) for check in checks.values())
    )
    dependencies = dict(zip(checks.keys(), check_results, strict=True))
    readiness_status = (
        "ok" if all(result["status"] == "ok" for result in dependencies.values()) else "error"
    )
    return {"status": readiness_status, "dependencies": dependencies}


def _readiness_checks() -> dict[str, DependencyCheck]:
    return {
        "database": _check_database,
        "redis": _check_redis,
        "rabbitmq": _check_rabbitmq,
        "minio": _check_minio,
    }


async def _run_dependency_check(check: DependencyCheck) -> DependencyHealth:
    settings = get_settings()
    try:
        await asyncio.wait_for(check(), timeout=settings.dependency_check_timeout_seconds)
    except Exception as exc:
        return {"status": "error", "detail": exc.__class__.__name__}
    return {"status": "ok"}


async def _check_database() -> None:
    async with engine.connect() as connection:
        await connection.execute(text("select 1"))


async def _check_redis() -> None:
    settings = get_settings()
    client = from_url(  # type: ignore[no-untyped-call]
        settings.cache_redis_url,
        encoding="utf-8",
        decode_responses=True,
    )
    try:
        await client.ping()
    finally:
        await client.aclose()


async def _check_rabbitmq() -> None:
    await anyio.to_thread.run_sync(_check_rabbitmq_sync)


def _check_rabbitmq_sync() -> None:
    settings = get_settings()
    with Connection(settings.celery_broker_url, connect_timeout=3) as connection:
        connection.connect()


async def _check_minio() -> None:
    await anyio.to_thread.run_sync(_check_minio_sync)


def _check_minio_sync() -> None:
    settings = get_settings()
    with urlopen(
        _minio_health_url(settings.minio_endpoint, secure=settings.minio_secure),
        timeout=settings.dependency_check_timeout_seconds,
    ) as response:
        if response.status >= 400:
            msg = "MinIO health endpoint returned an error"
            raise RuntimeError(msg)


def _minio_health_url(endpoint: str, *, secure: bool) -> str:
    scheme = "https" if secure else "http"
    normalized_endpoint = (
        endpoint.strip().removeprefix("http://").removeprefix("https://").rstrip("/")
    )
    return f"{scheme}://{normalized_endpoint}/minio/health/live"
