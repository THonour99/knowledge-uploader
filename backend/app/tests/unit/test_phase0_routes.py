from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_phase0_does_not_mount_auth_login_route() -> None:
    transport = ASGITransport(app=app)  # type: ignore[arg-type]
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/auth/login",
            json={
                "email": "admin@example.com",
                "password": "phase0-password",
                "remember_me": True,
            },
        )

    assert response.status_code == 404
