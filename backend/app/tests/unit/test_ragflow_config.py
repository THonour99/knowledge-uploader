"""Unit tests for Task 5: RAGFlow configuration consumption and test-connection endpoint.

Tests cover:
- test-connection success / failure (mock client ping)
- employee / dept_admin get 403
- response and logs do not contain api_key
- sync task uses runtime_config values (mock get_config, assert client receives them)
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from importlib import import_module
from typing import Any
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# DB / infra helpers (mirrored from test_ragflow_task_api.py pattern)
# ---------------------------------------------------------------------------


async def _reset_database() -> None:
    import_module("app.db.models")

    from app.core.database import engine
    from app.db.base import Base

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()


@pytest.fixture(autouse=True)
async def clean_database() -> AsyncGenerator[None, None]:
    await _reset_database()
    yield
    from app.core.database import engine
    from app.db.base import Base

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def api_client() -> AsyncGenerator[AsyncClient, None]:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.config import Settings
    from app.core.database import AsyncSessionFactory, get_session
    from app.core.deps import get_app_settings
    from app.main import app

    settings = Settings(
        allowed_email_domains="company.com",
        jwt_secret="test-jwt-secret-with-more-than-32-bytes",
        cache_redis_url=os.environ["CACHE_REDIS_URL"],
        require_email_verification=False,
    )

    async def override_session() -> AsyncGenerator[AsyncSession, None]:
        async with AsyncSessionFactory() as session:
            yield session

    app.dependency_overrides[get_app_settings] = lambda: settings
    app.dependency_overrides[get_session] = override_session
    transport = ASGITransport(app=app)  # type: ignore[arg-type]
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
    app.dependency_overrides.clear()


async def _create_user(*, email: str, password: str, role: str = "employee") -> UUID:
    from app.core.database import AsyncSessionFactory
    from app.core.security import hash_password
    from app.modules.user.models import User

    normalized = email.lower()
    user = User(
        name=email.split("@", 1)[0],
        email=normalized,
        email_domain=normalized.rsplit("@", 1)[1],
        password_hash=hash_password(password),
        role=role,
        status="active",
        email_verified=True,
    )
    async with AsyncSessionFactory() as session:
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user.id


async def _login(client: AsyncClient, *, email: str, password: str) -> str:
    response = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    return str(response.json()["data"]["access_token"])


async def _create_token(client: AsyncClient, *, role: str) -> str:
    email = f"ragflow-test-{role}@company.com"
    await _create_user(email=email, password="password123", role=role)
    return await _login(client, email=email, password="password123")


# ---------------------------------------------------------------------------
# Fake HttpRagflowClient for injection
# ---------------------------------------------------------------------------


class _FakeProbeClient:
    def __init__(self, *, raise_on_check: Exception | None = None) -> None:
        self._raise_on_check = raise_on_check
        self.constructed_with: dict[str, Any] = {}

    async def check_connection(self) -> None:
        if self._raise_on_check is not None:
            raise self._raise_on_check


# ---------------------------------------------------------------------------
# Test: test-connection endpoint permissions
# ---------------------------------------------------------------------------


async def test_test_connection_employee_is_403(
    api_client: AsyncClient,
) -> None:
    token = await _create_token(api_client, role="employee")
    response = await api_client.post(
        "/api/admin/ragflow/test-connection",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


async def test_test_connection_dept_admin_is_403(
    api_client: AsyncClient,
) -> None:
    token = await _create_token(api_client, role="dept_admin")
    response = await api_client.post(
        "/api/admin/ragflow/test-connection",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Test: test-connection endpoint success state
# ---------------------------------------------------------------------------


async def test_test_connection_success(
    api_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.modules.ragflow.api as ragflow_api

    constructed_args: dict[str, Any] = {}

    def _fake_client(**kwargs: Any) -> _FakeProbeClient:
        constructed_args.update(kwargs)
        return _FakeProbeClient()

    monkeypatch.setattr(ragflow_api, "HttpRagflowClient", _fake_client)

    async def _fake_get_config(key: str) -> object | None:
        return {
            "ragflow.base_url": "http://ragflow-server:9380",
            "ragflow.api_key": "sk-runtime-key-99",
            "ragflow.sync_timeout_seconds": 45,
        }.get(key)

    monkeypatch.setattr(ragflow_api, "get_config", _fake_get_config)

    token = await _create_token(api_client, role="system_admin")
    response = await api_client.post(
        "/api/admin/ragflow/test-connection",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["ok"] is True
    assert data["error"] is None
    assert isinstance(data["latency_ms"], float)
    assert data["latency_ms"] >= 0.0

    # Verify client was constructed with correct values
    assert constructed_args["base_url"] == "http://ragflow-server:9380"
    assert constructed_args["api_key"] == "sk-runtime-key-99"
    assert constructed_args["timeout_seconds"] == 45.0


async def test_test_connection_failure_keeps_error_detail_without_api_key(
    api_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.modules.ragflow.api as ragflow_api
    from app.adapters.ragflow.base import RagflowClientError

    def _fake_client(**kwargs: Any) -> _FakeProbeClient:
        return _FakeProbeClient(
            raise_on_check=RagflowClientError("RAGFlow request failed: HTTP 502"),
        )

    monkeypatch.setattr(ragflow_api, "HttpRagflowClient", _fake_client)

    async def _fake_get_config(key: str) -> object | None:
        return {
            "ragflow.base_url": "http://ragflow-bad:9380",
            "ragflow.api_key": "sk-secret-key",
            "ragflow.sync_timeout_seconds": 10,
        }.get(key)

    monkeypatch.setattr(ragflow_api, "get_config", _fake_get_config)

    token = await _create_token(api_client, role="system_admin")
    response = await api_client.post(
        "/api/admin/ragflow/test-connection",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["ok"] is False
    # Error detail must be preserved for the administrator
    assert data["error"] == "RAGFlow request failed: HTTP 502"
    # API key must not appear in response
    assert "sk-secret-key" not in str(data)


async def test_test_connection_failure_on_client_error(
    api_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.modules.ragflow.api as ragflow_api
    from app.adapters.ragflow.base import RagflowClientError

    def _fake_client(**kwargs: Any) -> _FakeProbeClient:
        return _FakeProbeClient(
            raise_on_check=RagflowClientError("connection refused (base_url=http://bad:9380)"),
        )

    monkeypatch.setattr(ragflow_api, "HttpRagflowClient", _fake_client)

    async def _fake_get_config(key: str) -> object | None:
        return {
            "ragflow.base_url": "http://bad:9380",
            "ragflow.api_key": "sk-ultra-secret",
            "ragflow.sync_timeout_seconds": 5,
        }.get(key)

    monkeypatch.setattr(ragflow_api, "get_config", _fake_get_config)

    token = await _create_token(api_client, role="system_admin")
    response = await api_client.post(
        "/api/admin/ragflow/test-connection",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["ok"] is False
    assert data["error"] == "connection refused (base_url=http://bad:9380)"
    # API key must not appear in error
    assert "sk-ultra-secret" not in str(data)


async def test_test_connection_empty_api_key_keeps_error_message_intact(
    api_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """api_key 为空字符串时不能腐化错误消息 (空串 replace 会在每个字符间插 ****)。"""
    import app.modules.ragflow.api as ragflow_api
    from app.adapters.ragflow.base import RagflowClientError

    def _fake_client(**kwargs: Any) -> _FakeProbeClient:
        return _FakeProbeClient(
            raise_on_check=RagflowClientError("RAGFlow API key is not configured"),
        )

    monkeypatch.setattr(ragflow_api, "HttpRagflowClient", _fake_client)

    async def _fake_get_config(key: str) -> object | None:
        return {
            "ragflow.base_url": "http://ragflow:9380",
            "ragflow.api_key": "",
            "ragflow.sync_timeout_seconds": 10,
        }.get(key)

    monkeypatch.setattr(ragflow_api, "get_config", _fake_get_config)

    token = await _create_token(api_client, role="system_admin")
    response = await api_client.post(
        "/api/admin/ragflow/test-connection",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["ok"] is False
    assert data["error"] == "RAGFlow API key is not configured"


# ---------------------------------------------------------------------------
# Test: api_key does not appear in logs
# ---------------------------------------------------------------------------


async def test_test_connection_api_key_not_in_logs(
    api_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import app.modules.ragflow.api as ragflow_api

    def _fake_client(**kwargs: Any) -> _FakeProbeClient:
        return _FakeProbeClient()

    monkeypatch.setattr(ragflow_api, "HttpRagflowClient", _fake_client)

    secret_api_key = "sk-must-not-appear-in-logs"

    async def _fake_get_config(key: str) -> object | None:
        return {
            "ragflow.base_url": "http://ragflow:9380",
            "ragflow.api_key": secret_api_key,
            "ragflow.sync_timeout_seconds": 30,
        }.get(key)

    monkeypatch.setattr(ragflow_api, "get_config", _fake_get_config)

    token = await _create_token(api_client, role="system_admin")
    await api_client.post(
        "/api/admin/ragflow/test-connection",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Check captured stdout/stderr does not contain the API key
    captured = capsys.readouterr()
    assert secret_api_key not in captured.out
    assert secret_api_key not in captured.err


# ---------------------------------------------------------------------------
# Test: sync task uses runtime_config values
# ---------------------------------------------------------------------------


async def test_sync_task_build_ragflow_client_uses_runtime_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """build_ragflow_client_from_runtime_config() passes DB config values to HttpRagflowClient."""
    import app.core.ragflow_runtime as ragflow_runtime
    import app.modules.ragflow.tasks as tasks_module

    constructed_kwargs: dict[str, Any] = {}

    class _CapturingClient:
        def __init__(self, **kwargs: Any) -> None:
            constructed_kwargs.update(kwargs)

    monkeypatch.setattr(tasks_module, "HttpRagflowClient", _CapturingClient)

    async def _fake_get_config(key: str) -> object | None:
        return {
            "ragflow.base_url": "http://runtime-ragflow:9999",
            "ragflow.api_key": "sk-from-db-runtime",
            "ragflow.sync_timeout_seconds": 77,
        }.get(key)

    monkeypatch.setattr(ragflow_runtime, "get_config", _fake_get_config)

    await tasks_module.build_ragflow_client_from_runtime_config()

    assert constructed_kwargs["base_url"] == "http://runtime-ragflow:9999"
    assert constructed_kwargs["api_key"] == "sk-from-db-runtime"
    assert constructed_kwargs["timeout_seconds"] == 77.0


async def test_sync_task_build_ragflow_client_falls_back_to_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When runtime_config returns None, env-derived settings are used as fallback."""
    import app.core.ragflow_runtime as ragflow_runtime
    import app.modules.ragflow.tasks as tasks_module

    constructed_kwargs: dict[str, Any] = {}

    class _CapturingClient:
        def __init__(self, **kwargs: Any) -> None:
            constructed_kwargs.update(kwargs)

    monkeypatch.setattr(tasks_module, "HttpRagflowClient", _CapturingClient)

    # Return None for all keys to trigger fallback path
    async def _fake_get_config(_key: str) -> object | None:
        return None

    monkeypatch.setattr(ragflow_runtime, "get_config", _fake_get_config)

    from app.core.config import get_settings

    settings = get_settings()
    await tasks_module.build_ragflow_client_from_runtime_config()

    assert constructed_kwargs["base_url"] == settings.ragflow_base_url
    assert constructed_kwargs["api_key"] == settings.ragflow_api_key
    assert constructed_kwargs["timeout_seconds"] == settings.ragflow_request_timeout
