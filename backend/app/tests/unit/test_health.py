from __future__ import annotations

import importlib

import pytest
from httpx import ASGITransport, AsyncClient

app = importlib.import_module("app.main").app
main_module = importlib.import_module("app.main")


async def _ok_check() -> None:
    return None


async def _failed_check() -> None:
    raise RuntimeError("dependency unavailable")


@pytest.mark.asyncio
async def test_health_endpoint_returns_ok() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/system/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_ready_endpoint_returns_dependency_statuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_module, "_check_database", _ok_check)
    monkeypatch.setattr(main_module, "_check_redis", _ok_check)
    monkeypatch.setattr(main_module, "_check_rabbitmq", _ok_check)
    monkeypatch.setattr(main_module, "_check_minio", _ok_check)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/system/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "dependencies": {
            "database": {"status": "ok"},
            "redis": {"status": "ok"},
            "rabbitmq": {"status": "ok"},
            "minio": {"status": "ok"},
        },
    }


@pytest.mark.asyncio
async def test_ready_endpoint_returns_503_when_dependency_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_module, "_check_database", _failed_check)
    monkeypatch.setattr(main_module, "_check_redis", _ok_check)
    monkeypatch.setattr(main_module, "_check_rabbitmq", _ok_check)
    monkeypatch.setattr(main_module, "_check_minio", _ok_check)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/system/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "error",
        "dependencies": {
            "database": {"status": "error", "detail": "RuntimeError"},
            "redis": {"status": "ok"},
            "rabbitmq": {"status": "ok"},
            "minio": {"status": "ok"},
        },
    }
