from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

BACKEND_ROOT = Path(__file__).resolve().parents[3]
sys.path = [path for path in sys.path if path != str(BACKEND_ROOT)]
sys.path.insert(0, str(BACKEND_ROOT))
for module_name in list(sys.modules):
    if module_name == "app" or module_name.startswith("app."):
        del sys.modules[module_name]

app = importlib.import_module("app.main").app


@pytest.mark.asyncio
async def test_health_endpoint_returns_ok() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/system/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_phase0_login_mock_returns_token() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/auth/login",
            json={
                "email": "admin@example.com",
                "password": "phase0-password",
                "remember_me": True,
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "access_token": "phase0-mock-token-for-admin@example.com",
        "token_type": "bearer",
        "user": {
            "id": "phase0-user",
            "name": "Phase 0 Mock User",
            "email": "admin@example.com",
            "role": "system_admin",
        },
    }
