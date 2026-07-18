from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from importlib import import_module

import pytest
from sqlalchemy import insert, select

from app.core import ragflow_call_telemetry
from app.core.ragflow_call_telemetry import (
    RAGFLOW_API_CALLS,
    reconcile_stale_ragflow_api_calls,
)
from app.tests.safety import require_safe_test_database_reset

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def clean_database() -> AsyncGenerator[None, None]:
    require_safe_test_database_reset()
    import_module("app.db.models")

    from app.core.database import engine
    from app.db.base import Base

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def test_reconciliation_recovers_only_calls_beyond_recovery_threshold() -> None:
    from app.core.database import AsyncSessionFactory

    now = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)
    recover_id = uuid.uuid4()
    stale_id = uuid.uuid4()
    fresh_id = uuid.uuid4()
    expired_id = uuid.uuid4()
    recently_finished_id = uuid.uuid4()
    async with AsyncSessionFactory() as session:
        await session.execute(
            insert(RAGFLOW_API_CALLS),
            [
                {
                    "id": recover_id,
                    "department_id": None,
                    "operation": "upload_document",
                    "result": "started",
                    "started_at": now - timedelta(minutes=31),
                    "finished_at": None,
                    "latency_ms": None,
                },
                {
                    "id": stale_id,
                    "department_id": None,
                    "operation": "get_document_status",
                    "result": "started",
                    "started_at": now - timedelta(minutes=16),
                    "finished_at": None,
                    "latency_ms": None,
                },
                {
                    "id": fresh_id,
                    "department_id": None,
                    "operation": "ping",
                    "result": "started",
                    "started_at": now - timedelta(minutes=1),
                    "finished_at": None,
                    "latency_ms": None,
                },
                {
                    "id": expired_id,
                    "department_id": None,
                    "operation": "ping",
                    "result": "success",
                    "started_at": now - timedelta(days=401),
                    "finished_at": now - timedelta(days=401) + timedelta(seconds=1),
                    "latency_ms": 1000,
                },
                {
                    "id": recently_finished_id,
                    "department_id": None,
                    "operation": "ping",
                    "result": "success",
                    "started_at": now - timedelta(days=401),
                    "finished_at": now - timedelta(minutes=1),
                    "latency_ms": 401 * 24 * 60 * 60 * 1000,
                },
            ],
        )
        await session.commit()

    first = await reconcile_stale_ragflow_api_calls(now=now)
    second = await reconcile_stale_ragflow_api_calls(now=now)

    assert first.stale_started_count == 1
    assert first.recovered_count == 1
    assert second.stale_started_count == 1
    assert second.recovered_count == 0
    async with AsyncSessionFactory() as session:
        rows = (
            await session.execute(
                select(
                    RAGFLOW_API_CALLS.c.id,
                    RAGFLOW_API_CALLS.c.result,
                    RAGFLOW_API_CALLS.c.failure_category,
                    RAGFLOW_API_CALLS.c.finished_at,
                    RAGFLOW_API_CALLS.c.latency_ms,
                ).order_by(RAGFLOW_API_CALLS.c.id)
            )
        ).mappings()
        by_id = {row["id"]: row for row in rows}

    assert by_id[recover_id]["result"] == "failure"
    assert by_id[recover_id]["failure_category"] == "unknown"
    assert by_id[recover_id]["finished_at"] == now
    assert by_id[recover_id]["latency_ms"] == 31 * 60 * 1000
    assert by_id[stale_id]["result"] == "started"
    assert by_id[fresh_id]["result"] == "started"
    assert expired_id not in by_id
    assert by_id[recently_finished_id]["result"] == "success"


async def test_best_effort_failure_logs_use_only_bounded_contract_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warnings: list[tuple[str, dict[str, object]]] = []

    class WarningLogger:
        def warning(self, event: str, **values: object) -> None:
            warnings.append((event, values))

    async def fail_start(**_: object) -> uuid.UUID:
        raise RuntimeError("private-start-value")

    async def fail_finish(**_: object) -> None:
        raise RuntimeError("private-finish-value")

    monkeypatch.setattr(ragflow_call_telemetry, "logger", WarningLogger())
    monkeypatch.setattr(ragflow_call_telemetry, "start_ragflow_api_call", fail_start)
    monkeypatch.setattr(ragflow_call_telemetry, "finish_ragflow_api_call", fail_finish)

    call_id = await ragflow_call_telemetry.best_effort_start_ragflow_api_call(
        operation="https://secret.invalid/private",
    )
    await ragflow_call_telemetry.best_effort_finish_ragflow_api_call(
        call_id=uuid.uuid4(),
        operation="dataset-secret",
        result="response-secret",
    )

    assert call_id is None
    assert warnings[0][1]["operation"] == "other"
    assert warnings[1][1]["operation"] == "other"
    assert warnings[1][1]["result"] == "failure"
    assert "secret" not in str(warnings)
