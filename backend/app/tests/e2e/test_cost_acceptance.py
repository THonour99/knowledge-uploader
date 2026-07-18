"""COST-001 local PostgreSQL/API acceptance: aggregate-only capacity and cost governance."""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from importlib import import_module
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from redis.asyncio import from_url
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.tests.safety import require_safe_test_database_reset, require_safe_test_redis_reset

pytestmark = pytest.mark.asyncio


async def _reset_database() -> None:
    require_safe_test_database_reset()
    require_safe_test_redis_reset()
    import_module("app.db.models")
    from app.core.database import engine
    from app.db.base import Base

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()
    redis_client = from_url(os.environ["CACHE_REDIS_URL"], encoding="utf-8", decode_responses=True)  # type: ignore[no-untyped-call]
    try:
        await redis_client.flushdb()
    finally:
        await redis_client.aclose()


@pytest.fixture(autouse=True)
async def clean_database() -> AsyncGenerator[None, None]:
    await _reset_database()
    yield
    from app.core.database import engine
    from app.db.base import Base

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@asynccontextmanager
async def _client() -> AsyncIterator[AsyncClient]:
    from app.core.config import Settings
    from app.core.database import AsyncSessionFactory, get_session
    from app.core.deps import get_app_settings
    from app.main import app

    settings = Settings(
        allowed_email_domains="company.com",
        jwt_secret="cost-acceptance-secret-longer-than-32-bytes",
        cache_redis_url=os.environ["CACHE_REDIS_URL"],
        require_email_verification=False,
    )

    async def override_session() -> AsyncGenerator[AsyncSession, None]:
        async with AsyncSessionFactory() as session:
            yield session

    app.dependency_overrides[get_app_settings] = lambda: settings
    app.dependency_overrides[get_session] = override_session
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),  # type: ignore[arg-type]
            base_url="http://testserver",
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


async def _seed_user(*, email: str, role: str, department_id: UUID, department: str) -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.security import hash_password
    from app.modules.user.models import User

    async with AsyncSessionFactory() as session:
        session.add(
            User(
                name=role,
                email=email,
                email_domain="company.com",
                password_hash=hash_password("password123"),
                department_id=department_id,
                department=department,
                role=role,
                status="active",
                email_verified=True,
            )
        )
        await session.commit()


async def _login(client: AsyncClient, email: str) -> str:
    response = await client.post(
        "/api/auth/login", json={"email": email, "password": "password123"}
    )
    assert response.status_code == 200, response.text
    return str(response.json()["data"]["access_token"])


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _seed_governance_rows() -> tuple[UUID, UUID]:
    from app.core.database import AsyncSessionFactory
    from app.modules.ai.models import AiUsageLog
    from app.modules.department.models import Department
    from app.modules.document.models import File
    from app.modules.governance_metrics.models import RagflowApiCall, StorageCapacitySnapshot
    from app.modules.user.models import User

    now = datetime.now(UTC)
    department_a = Department(name="成本部A", code="cost-a", status="active")
    department_b = Department(name="成本部B", code="cost-b", status="active")
    async with AsyncSessionFactory() as session:
        session.add_all([department_a, department_b])
        await session.flush()
        uploader_a = User(
            name="source-a",
            email="source-a@company.com",
            email_domain="company.com",
            password_hash="not-used",
            department_id=department_a.id,
            department=department_a.name,
            role="employee",
            status="active",
            email_verified=True,
        )
        uploader_b = User(
            name="source-b",
            email="source-b@company.com",
            email_domain="company.com",
            password_hash="not-used",
            department_id=department_b.id,
            department=department_b.name,
            role="employee",
            status="active",
            email_verified=True,
        )
        session.add_all([uploader_a, uploader_b])
        await session.flush()
        files = [
            File(
                original_name="never-return-secret-a.txt",
                stored_name="secret-object-a",
                extension="txt",
                mime_type="text/plain",
                size=100,
                hash="a" * 64,
                bucket="secret-bucket",
                object_key="secret/a",
                uploader_id=uploader_a.id,
                owner_id=uploader_a.id,
                department_id=department_a.id,
                department=department_a.name,
                visibility="department",
                status="parsed",
                review_status="approved",
                uploaded_at=now - timedelta(hours=2),
            ),
            File(
                original_name="never-return-secret-b.txt",
                stored_name="secret-object-b",
                extension="txt",
                mime_type="text/plain",
                size=40,
                hash="b" * 64,
                bucket="secret-bucket",
                object_key="secret/b",
                uploader_id=uploader_b.id,
                owner_id=uploader_b.id,
                department_id=department_b.id,
                department=department_b.name,
                visibility="department",
                status="disabled",
                review_status="approved",
                uploaded_at=now - timedelta(hours=1),
            ),
        ]
        session.add_all(files)
        await session.flush()
        session.add_all(
            [
                AiUsageLog(
                    file_id=files[0].id,
                    feature_name="analysis",
                    provider_name="provider-a",
                    model_name="model-a",
                    analysis_attempt=1,
                    call_sequence=1,
                    cost_status="known",
                    cost_currency="CNY",
                    estimated_cost_microunits=11,
                    prompt_tokens=10,
                    completion_tokens=1,
                    status="success",
                    created_at=now - timedelta(hours=2),
                ),
                AiUsageLog(
                    file_id=files[0].id,
                    feature_name="analysis",
                    provider_name="provider-a",
                    model_name="model-a",
                    analysis_attempt=1,
                    call_sequence=2,
                    cost_status="known",
                    cost_currency="USD",
                    estimated_cost_microunits=7,
                    prompt_tokens=2,
                    completion_tokens=3,
                    status="success",
                    created_at=now - timedelta(hours=2),
                ),
                AiUsageLog(
                    file_id=files[1].id,
                    feature_name="analysis",
                    provider_name="provider-b",
                    model_name="model-b",
                    analysis_attempt=1,
                    call_sequence=1,
                    cost_status="unknown_pricing",
                    cost_currency="USD",
                    estimated_cost_microunits=0,
                    prompt_tokens=4,
                    completion_tokens=5,
                    status="success",
                    created_at=now - timedelta(hours=1),
                ),
                AiUsageLog(
                    file_id=files[1].id,
                    feature_name="analysis",
                    provider_name="provider-b",
                    model_name="model-b",
                    analysis_attempt=1,
                    call_sequence=2,
                    cost_status="unknown_usage",
                    cost_currency="USD",
                    estimated_cost_microunits=0,
                    prompt_tokens=None,
                    completion_tokens=None,
                    status="failed",
                    created_at=now - timedelta(hours=1),
                ),
                AiUsageLog(
                    file_id=files[1].id,
                    feature_name="analysis",
                    provider_name="provider-b",
                    model_name="model-b",
                    analysis_attempt=1,
                    call_sequence=3,
                    cost_status="legacy_unverifiable",
                    cost_currency="USD",
                    estimated_cost_microunits=0,
                    prompt_tokens=None,
                    completion_tokens=None,
                    status="success",
                    created_at=now - timedelta(hours=1),
                ),
                RagflowApiCall(
                    department_id=department_a.id,
                    operation="upload_document",
                    result="success",
                    started_at=now - timedelta(hours=2),
                    finished_at=now - timedelta(hours=2),
                    latency_ms=12,
                ),
                RagflowApiCall(
                    department_id=department_b.id,
                    operation="upload_document",
                    result="failure",
                    failure_category="timeout",
                    started_at=now - timedelta(hours=1),
                    finished_at=now - timedelta(hours=1),
                    latency_ms=20,
                ),
                StorageCapacitySnapshot(
                    total_bytes=1000,
                    used_bytes=600,
                    free_bytes=400,
                    evidence_sha256="c" * 64,
                    captured_at=now - timedelta(minutes=1),
                    collected_at=now - timedelta(seconds=30),
                ),
            ]
        )
        await session.commit()
        return department_a.id, department_b.id


async def test_cost_001_real_postgresql_api_sql_reconciliation_and_privacy() -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ai.models import AiUsageLog
    from app.modules.audit.models import AuditLog
    from app.modules.document.models import File
    from app.modules.governance_metrics.models import RagflowApiCall, StorageCapacitySnapshot

    department_a, department_b = await _seed_governance_rows()
    await _seed_user(
        email="cost-system@company.com",
        role="system_admin",
        department_id=department_a,
        department="成本部A",
    )
    await _seed_user(
        email="cost-employee@company.com",
        role="employee",
        department_id=department_a,
        department="成本部A",
    )
    await _seed_user(
        email="cost-admin@company.com",
        role="dept_admin",
        department_id=department_a,
        department="成本部A",
    )
    recent_start = datetime.now(UTC) - timedelta(hours=1, minutes=30)
    recent_end = datetime.now(UTC) + timedelta(minutes=1)
    wide_start = recent_start - timedelta(hours=2)
    query: dict[str, str | int] = {
        "start_at": recent_start.isoformat(),
        "end_before": recent_end.isoformat(),
        "page_size": 100,
    }

    async with _client() as client:
        system = await _login(client, "cost-system@company.com")
        employee = await _login(client, "cost-employee@company.com")
        dept_admin = await _login(client, "cost-admin@company.com")
        for endpoint in ("capacity", "llm-usage", "ragflow-usage"):
            for token in (employee, dept_admin):
                response = await client.get(
                    f"/api/admin/statistics/{endpoint}", headers=_headers(token)
                )
                assert response.status_code == 403
                assert "data" not in response.json()
        invalid = await client.get(
            "/api/admin/statistics/capacity",
            params={"start_at": recent_end.isoformat(), "end_before": recent_end.isoformat()},
            headers=_headers(system),
        )
        assert invalid.status_code == 422
        empty = await client.get(
            "/api/admin/statistics/ragflow-usage",
            params={
                "start_at": (recent_start - timedelta(days=10)).isoformat(),
                "end_before": (recent_start - timedelta(days=9)).isoformat(),
            },
            headers=_headers(system),
        )
        assert empty.status_code == 200
        assert empty.json()["data"]["items"] == []

        capacity = await client.get(
            "/api/admin/statistics/capacity",
            params={**query, "group_by": "department", "physical_dimension": "cluster"},
            headers=_headers(system),
        )
        unsupported = await client.get(
            "/api/admin/statistics/capacity",
            params={**query, "group_by": "department", "physical_dimension": "department"},
            headers=_headers(system),
        )
        usage = await client.get(
            "/api/admin/statistics/llm-usage",
            params={**query, "group_by": "department"},
            headers=_headers(system),
        )
        ragflow = await client.get(
            "/api/admin/statistics/ragflow-usage",
            params={**query, "group_by": "result"},
            headers=_headers(system),
        )
        day_usage = await client.get(
            "/api/admin/statistics/llm-usage",
            params={**query, "group_by": "day"},
            headers=_headers(system),
        )
        assert all(
            response.status_code == 200
            for response in (capacity, unsupported, usage, ragflow, day_usage)
        )

        wide_query: dict[str, str | int] = {**query, "start_at": wide_start.isoformat()}
        wide_usage = await client.get(
            "/api/admin/statistics/llm-usage",
            params={**wide_query, "group_by": "department"},
            headers=_headers(system),
        )
        wide_ragflow = await client.get(
            "/api/admin/statistics/ragflow-usage",
            params={**wide_query, "group_by": "result"},
            headers=_headers(system),
        )
        assert wide_usage.status_code == wide_ragflow.status_code == 200
        async with AsyncSessionFactory() as session:
            snapshot = await session.scalar(select(StorageCapacitySnapshot))
            assert snapshot is not None
            snapshot.captured_at = datetime.now(UTC) - timedelta(minutes=16)
            snapshot.collected_at = datetime.now(UTC) - timedelta(minutes=15)
            await session.commit()
        stale = await client.get(
            "/api/admin/statistics/capacity",
            params={**query, "physical_dimension": "cluster"},
            headers=_headers(system),
        )
        assert stale.status_code == 200
        async with AsyncSessionFactory() as session:
            snapshot = await session.scalar(select(StorageCapacitySnapshot))
            assert snapshot is not None
            await session.delete(snapshot)
            await session.commit()
        unavailable = await client.get(
            "/api/admin/statistics/capacity",
            params={**query, "physical_dimension": "cluster"},
            headers=_headers(system),
        )
        assert unavailable.status_code == 200

    capacity_data = capacity.json()["data"]
    unsupported_data = unsupported.json()["data"]
    usage_data = usage.json()["data"]
    ragflow_data = ragflow.json()["data"]
    wide_usage_data = wide_usage.json()["data"]
    wide_ragflow_data = wide_ragflow.json()["data"]
    assert capacity_data["physical"] == {
        "status": "available",
        "requested_dimension": "cluster",
        "scope": "cluster",
        "measurement_basis": "minio_raw_cluster_capacity",
        "source_kind": "minio_cluster_metrics",
        "total_bytes": "1000",
        "used_bytes": "600",
        "free_bytes": "400",
        "captured_at": capacity_data["physical"]["captured_at"],
        "collected_at": capacity_data["physical"]["collected_at"],
    }
    assert unsupported_data["physical"]["status"] == "unsupported_dimension"
    assert unsupported_data["physical"]["scope"] == "cluster"
    assert unsupported_data["physical"]["total_bytes"] is None
    assert stale.json()["data"]["physical"]["status"] == "stale"
    assert unavailable.json()["data"]["physical"] == {
        "status": "unavailable",
        "requested_dimension": "cluster",
        "scope": "cluster",
        "measurement_basis": None,
        "source_kind": None,
        "total_bytes": None,
        "used_bytes": None,
        "free_bytes": None,
        "captured_at": None,
        "collected_at": None,
    }

    async with AsyncSessionFactory() as session:
        capacity_rows = list(
            (
                await session.execute(
                    select(
                        File.department_id,
                        func.count(File.id),
                        func.coalesce(
                            func.sum(
                                case(
                                    (
                                        File.status.not_in(
                                            ("disabled", "deleted", "ragflow_cleanup_failed")
                                        ),
                                        File.size,
                                    ),
                                    else_=0,
                                )
                            ),
                            0,
                        ),
                        func.coalesce(
                            func.sum(
                                case(
                                    (
                                        File.status.in_(
                                            ("disabled", "deleted", "ragflow_cleanup_failed")
                                        ),
                                        File.size,
                                    ),
                                    else_=0,
                                )
                            ),
                            0,
                        ),
                        func.coalesce(func.sum(File.size), 0),
                    )
                    .where(File.uploaded_at >= recent_start, File.uploaded_at < recent_end)
                    .group_by(File.department_id)
                )
            ).all()
        )
        known_costs = list(
            (
                await session.execute(
                    select(
                        File.department_id,
                        AiUsageLog.cost_currency,
                        func.count(AiUsageLog.id),
                        func.sum(AiUsageLog.estimated_cost_microunits),
                    )
                    .join(File, File.id == AiUsageLog.file_id)
                    .where(
                        AiUsageLog.created_at >= recent_start,
                        AiUsageLog.created_at < recent_end,
                        AiUsageLog.cost_status == "known",
                    )
                    .group_by(File.department_id, AiUsageLog.cost_currency)
                )
            ).all()
        )
        wide_known_costs = list(
            (
                await session.execute(
                    select(
                        File.department_id,
                        AiUsageLog.cost_currency,
                        func.count(AiUsageLog.id),
                        func.sum(AiUsageLog.estimated_cost_microunits),
                    )
                    .join(File, File.id == AiUsageLog.file_id)
                    .where(
                        AiUsageLog.created_at >= wide_start,
                        AiUsageLog.created_at < recent_end,
                        AiUsageLog.cost_status == "known",
                    )
                    .group_by(File.department_id, AiUsageLog.cost_currency)
                )
            ).all()
        )
        wide_ragflow_rows = list(
            (
                await session.execute(
                    select(
                        RagflowApiCall.result,
                        func.count(RagflowApiCall.id),
                        func.count(RagflowApiCall.id).filter(
                            RagflowApiCall.result.in_(("success", "failure"))
                        ),
                        func.count(RagflowApiCall.id).filter(RagflowApiCall.result == "failure"),
                    )
                    .where(
                        RagflowApiCall.started_at >= wide_start,
                        RagflowApiCall.started_at < recent_end,
                    )
                    .group_by(RagflowApiCall.result)
                )
            ).all()
        )
        unknown_statuses = list(
            (
                await session.execute(
                    select(File.department_id, AiUsageLog.cost_status, func.count(AiUsageLog.id))
                    .join(File, File.id == AiUsageLog.file_id)
                    .where(
                        AiUsageLog.created_at >= recent_start,
                        AiUsageLog.created_at < recent_end,
                        AiUsageLog.cost_status != "known",
                    )
                    .group_by(File.department_id, AiUsageLog.cost_status)
                )
            ).all()
        )
        ragflow_rows = list(
            (
                await session.execute(
                    select(
                        RagflowApiCall.result,
                        func.count(RagflowApiCall.id),
                        func.count(RagflowApiCall.id).filter(
                            RagflowApiCall.result.in_(("success", "failure"))
                        ),
                        func.count(RagflowApiCall.id).filter(RagflowApiCall.result == "failure"),
                    )
                    .where(
                        RagflowApiCall.started_at >= recent_start,
                        RagflowApiCall.started_at < recent_end,
                    )
                    .group_by(RagflowApiCall.result)
                )
            ).all()
        )
        audits = list(
            (
                await session.execute(select(AuditLog).where(AuditLog.action.like("statistics.%")))
            ).scalars()
        )

    capacity_by_department = {row["dimension_key"]: row for row in capacity_data["items"]}
    for department_id, count, active, retained, total in capacity_rows:
        response_row = capacity_by_department[str(department_id)]
        assert response_row == {
            **response_row,
            "file_count": str(count),
            "active_logical_bytes": str(active),
            "retained_inactive_bytes": str(retained),
            "total_referenced_bytes": str(total),
        }
    assert capacity_data["pagination"]["total"] == 1
    assert str(department_b) in capacity_by_department

    usage_by_department = {row["dimension_key"]: row for row in usage_data["items"]}
    assert usage_data["pagination"]["total"] == 1
    b_usage = usage_by_department[str(department_b)]
    assert b_usage["known_costs"] == []
    assert {bucket["status"] for bucket in b_usage["unknown_costs"]} == {
        "unknown_pricing",
        "unknown_usage",
        "legacy_unverifiable",
    }
    for bucket in b_usage["unknown_costs"]:
        assert "estimated_cost_microunits" not in bucket
    assert known_costs == []
    wide_usage_by_department = {row["dimension_key"]: row for row in wide_usage_data["items"]}
    a_usage = wide_usage_by_department[str(department_a)]
    assert {
        (cost["currency"], cost["calls"], cost["estimated_cost_microunits"])
        for cost in a_usage["known_costs"]
    } == {
        ("CNY", "1", "11"),
        ("USD", "1", "7"),
    }
    assert a_usage["unknown_costs"] == []
    assert {
        (department_id, currency, calls, cost)
        for department_id, currency, calls, cost in wide_known_costs
    } == {
        (department_a, "CNY", 1, 11),
        (department_a, "USD", 1, 7),
    }
    assert {
        (department_id, status, count) for department_id, status, count in unknown_statuses
    } == {
        (department_b, "unknown_pricing", 1),
        (department_b, "unknown_usage", 1),
        (department_b, "legacy_unverifiable", 1),
    }
    assert (
        day_usage.json()["data"]["items"][0]["dimension_key"]
        == datetime.now(UTC).date().isoformat()
    )

    ragflow_by_result = {row["dimension_key"]: row for row in ragflow_data["items"]}
    assert ragflow_by_result == {
        "failure": {
            **ragflow_by_result["failure"],
            "calls": "1",
            "completed_calls": "1",
            "failure_calls": "1",
            "in_progress_calls": "0",
            "total_latency_ms": "20",
        }
    }
    assert [
        (result, calls, completed, failed) for result, calls, completed, failed in ragflow_rows
    ] == [("failure", 1, 1, 1)]

    wide_ragflow_by_result = {row["dimension_key"]: row for row in wide_ragflow_data["items"]}
    assert {
        key: (row["calls"], row["completed_calls"], row["failure_calls"])
        for key, row in wide_ragflow_by_result.items()
    } == {"success": ("1", "1", "0"), "failure": ("1", "1", "1")}
    assert {
        (result, calls, completed, failed) for result, calls, completed, failed in wide_ragflow_rows
    } == {
        ("success", 1, 1, 0),
        ("failure", 1, 1, 1),
    }
    serialized = str(
        [
            capacity_data,
            unsupported_data,
            usage_data,
            ragflow_data,
            [audit.metadata_json for audit in audits],
        ]
    )
    for secret in (
        "never-return-secret",
        "secret-object",
        "secret/a",
        "secret/b",
        "@company.com",
        "password123",
        "bearer ",
        "api_key",
        "access_token",
        "verification_token",
        "reset_token",
        "prompt_template",
        "original_name",
        "object_key",
        "raw_text",
    ):
        assert secret not in serialized.lower()
    assert len(audits) >= 9
