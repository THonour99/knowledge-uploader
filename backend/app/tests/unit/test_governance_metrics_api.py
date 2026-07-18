from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable, Coroutine
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.deps import get_current_user
from app.modules.governance_metrics import api, exceptions
from app.modules.user.schemas import AuthUserRecord

pytestmark = pytest.mark.asyncio

_Message = dict[str, Any]
_ASGIApp = Callable[
    [
        _Message,
        Callable[[], Awaitable[_Message]],
        Callable[[_Message], Coroutine[None, None, None]],
    ],
    Coroutine[None, None, None],
]


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def model_dump(self, *, mode: str) -> dict[str, object]:
        assert mode == "json"
        return self._payload


class _FakeGovernanceService:
    def __init__(self, *, fail: bool, invariant_failure: bool = False) -> None:
        self._fail = fail
        self._invariant_failure = invariant_failure
        self.calls: list[str] = []

    async def capacity(self, **_values: object) -> _FakeResponse:
        return self._result(
            "capacity",
            basis="database_file_rows_uploaded_in_window",
        )

    async def llm_usage(self, **_values: object) -> _FakeResponse:
        return self._result(
            "llm_usage",
            basis="ai_usage_logs_created_in_window",
        )

    async def ragflow_usage(self, **_values: object) -> _FakeResponse:
        return self._result(
            "ragflow_usage",
            basis="ragflow_api_calls_started_in_window",
        )

    def _result(self, operation: str, *, basis: str) -> _FakeResponse:
        self.calls.append(operation)
        if self._invariant_failure:
            raise exceptions.aggregate_invariant_violation()
        if self._fail:
            raise exceptions.invalid_query("invalid governance query")
        return _FakeResponse(
            {
                "basis": basis,
                "group_by": "none",
                "window": {
                    "start_at": "2026-06-17T00:00:00Z",
                    "end_before": "2026-07-17T00:00:00Z",
                    "timezone": "UTC",
                },
                "items": [],
                "pagination": {
                    "page": 1,
                    "page_size": 20,
                    "total": 0,
                    "total_pages": 0,
                },
            }
        )


def _user(role: str) -> AuthUserRecord:
    return AuthUserRecord(
        id=uuid.uuid4(),
        name="governance tester",
        email="governance@company.com",
        role=role,
        status="active",
        email_verified=True,
        department_id=uuid.uuid4(),
        department_name="治理部",
        department_code="governance",
        department="治理部",
        phone=None,
        email_domain="company.com",
        password_hash="not-used",
        failed_login_count=0,
        locked_until=None,
        session_version=1,
    )


async def _request(
    monkeypatch: pytest.MonkeyPatch,
    *,
    path: str,
    role: str = "system_admin",
    fail: bool = False,
    invariant_failure: bool = False,
) -> tuple[Response, _FakeGovernanceService]:
    service = _FakeGovernanceService(
        fail=fail,
        invariant_failure=invariant_failure,
    )
    app = FastAPI()
    app.include_router(api.router)

    async def override_current_user() -> AuthUserRecord:
        return _user(role)

    async def override_session() -> AsyncGenerator[AsyncSession, None]:
        yield cast(AsyncSession, SimpleNamespace())

    app.dependency_overrides[get_current_user] = override_current_user
    app.dependency_overrides[get_session] = override_session
    monkeypatch.setattr(api, "_service", lambda _session: service)
    transport = ASGITransport(app=cast(_ASGIApp, app))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            path,
            params={
                "start_at": datetime(2026, 6, 17, tzinfo=UTC).isoformat(),
                "end_before": (datetime(2026, 6, 17, tzinfo=UTC) + timedelta(days=30)).isoformat(),
            },
        )
    return response, service


@pytest.mark.parametrize(
    ("path", "operation", "basis"),
    [
        (
            "/api/admin/statistics/capacity",
            "capacity",
            "database_file_rows_uploaded_in_window",
        ),
        (
            "/api/admin/statistics/llm-usage",
            "llm_usage",
            "ai_usage_logs_created_in_window",
        ),
        (
            "/api/admin/statistics/ragflow-usage",
            "ragflow_usage",
            "ragflow_api_calls_started_in_window",
        ),
    ],
)
async def test_governance_statistics_routes_return_bounded_contracts(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    operation: str,
    basis: str,
) -> None:
    response, service = await _request(monkeypatch, path=path)

    assert response.status_code == 200
    assert response.json()["data"]["basis"] == basis
    assert response.json()["data"]["pagination"]["page_size"] == 20
    assert service.calls == [operation]


@pytest.mark.parametrize(
    ("path", "operation"),
    [
        ("/api/admin/statistics/capacity", "capacity"),
        ("/api/admin/statistics/llm-usage", "llm_usage"),
        ("/api/admin/statistics/ragflow-usage", "ragflow_usage"),
    ],
)
async def test_governance_statistics_routes_map_domain_validation_errors(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    operation: str,
) -> None:
    response, service = await _request(monkeypatch, path=path, fail=True)

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "error_code": "VALIDATION_ERROR",
        "message": "invalid governance query",
    }
    assert service.calls == [operation]


async def test_ragflow_invariant_failure_returns_stable_non_leaking_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response, service = await _request(
        monkeypatch,
        path="/api/admin/statistics/ragflow-usage",
        invariant_failure=True,
    )

    assert response.status_code == 500
    assert response.json()["detail"] == {
        "error_code": "INTERNAL_ERROR",
        "message": "governance metrics aggregate invariant violation",
    }
    assert "dimension" not in response.text
    assert service.calls == ["ragflow_usage"]


@pytest.mark.parametrize("role", ["dept_admin", "employee"])
async def test_governance_statistics_routes_reject_non_system_admin(
    monkeypatch: pytest.MonkeyPatch,
    role: str,
) -> None:
    response, service = await _request(
        monkeypatch,
        path="/api/admin/statistics/capacity",
        role=role,
    )

    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "PERMISSION_DENIED"
    assert service.calls == []
