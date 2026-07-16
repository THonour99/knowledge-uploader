from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from importlib import import_module
from typing import Any, cast

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def clean_dashboard_database() -> AsyncGenerator[None, None]:
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


async def _create_department(*, name: str, code: str) -> Any:
    from app.core.database import AsyncSessionFactory
    from app.modules.department.models import Department

    department = Department(name=name, code=code, status="active")
    async with AsyncSessionFactory() as session:
        session.add(department)
        await session.commit()
        await session.refresh(department)
    return department


async def _create_user(
    *,
    name: str,
    email: str,
    department_id: uuid.UUID,
    role: str = "employee",
    status: str = "active",
) -> Any:
    from app.core.database import AsyncSessionFactory
    from app.modules.user.models import User

    user = User(
        name=name,
        email=email,
        email_domain=email.rsplit("@", 1)[1],
        password_hash="argon2id-test-hash",
        department_id=department_id,
        role=role,
        status=status,
        email_verified=True,
    )
    async with AsyncSessionFactory() as session:
        session.add(user)
        await session.commit()
        await session.refresh(user)
    return user


async def _manage_department(*, user_id: uuid.UUID, department_id: uuid.UUID) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.department.models import UserManagedDepartment

    async with AsyncSessionFactory() as session:
        session.add(UserManagedDepartment(user_id=user_id, department_id=department_id))
        await session.commit()


async def _create_file(
    *,
    uploader_id: uuid.UUID,
    department_id: uuid.UUID,
    name: str,
    status: str = "uploaded",
    review_status: str = "pending",
    file_id: uuid.UUID | None = None,
    updated_at: datetime | None = None,
    submitted_at: datetime | None = None,
    review_due_at: datetime | None = None,
    claimed_by: uuid.UUID | None = None,
    claimed_at: datetime | None = None,
    claim_expires_at: datetime | None = None,
    expiry_status: str = "never",
    size: int = 100,
) -> Any:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File

    digest = hashlib.sha256(f"{uploader_id}:{name}:{file_id}".encode()).hexdigest()
    file = File(
        id=file_id or uuid.uuid4(),
        original_name=name,
        stored_name=f"{digest}.pdf",
        extension="pdf",
        mime_type="application/pdf",
        size=size,
        hash=digest,
        storage_type="minio",
        bucket="knowledge-files",
        object_key=f"dashboard/{digest}.pdf",
        uploader_id=uploader_id,
        department_id=department_id,
        visibility="private",
        status=status,
        review_status=review_status,
        submitted_at=submitted_at,
        review_due_at=review_due_at,
        claimed_by=claimed_by,
        claimed_at=claimed_at,
        claim_expires_at=claim_expires_at,
        expiry_status=expiry_status,
        updated_at=updated_at or datetime.now(UTC),
    )
    async with AsyncSessionFactory() as session:
        session.add(file)
        await session.commit()
        await session.refresh(file)
    return file


async def _create_analysis(*, file_id: uuid.UUID, risk: str) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ai.models import DocumentAnalysis

    async with AsyncSessionFactory() as session:
        session.add(
            DocumentAnalysis(
                file_id=file_id,
                status="succeeded",
                sensitive_risk_level=risk,
            )
        )
        await session.commit()


async def _create_notification(
    *,
    user_id: uuid.UUID,
    title: str,
    created_at: datetime,
    metadata: dict[str, object] | None = None,
    channel: str = "in_app",
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.notification.models import Notification

    async with AsyncSessionFactory() as session:
        session.add(
            Notification(
                user_id=user_id,
                type="document_status_changed",
                channel=channel,
                title=title,
                body=f"{title} body",
                metadata_json=metadata or {},
                created_at=created_at,
            )
        )
        await session.commit()


def _auth_record(*, user: Any, department: Any | None, role: str | None = None) -> Any:
    from app.modules.user.schemas import AuthUserRecord

    return AuthUserRecord(
        id=user.id,
        name=user.name,
        email=user.email,
        email_domain=user.email_domain,
        password_hash=user.password_hash,
        role=role or user.role,
        status=user.status,
        email_verified=user.email_verified,
        department_id=user.department_id,
        department_name=department.name if department is not None else None,
        department_code=department.code if department is not None else None,
        department=department.name if department is not None else None,
        phone=None,
        failed_login_count=0,
        locked_until=None,
        session_version=0,
    )


def _dashboard_app(current_user: Any) -> FastAPI:
    from app.core.database import AsyncSessionFactory, get_session
    from app.core.deps import get_current_user
    from app.modules.dashboard.api import router

    app = FastAPI()
    app.include_router(router)

    async def override_current_user() -> Any:
        return current_user

    async def override_session() -> AsyncGenerator[Any, None]:
        async with AsyncSessionFactory() as session:
            yield session

    app.dependency_overrides[get_current_user] = override_current_user
    app.dependency_overrides[get_session] = override_session
    return app


async def _get_dashboard(
    current_user: Any,
    *,
    params: dict[str, str | int | float | bool | None] | None = None,
) -> Any:
    async with AsyncClient(
        transport=ASGITransport(app=cast(Any, _dashboard_app(current_user))),
        base_url="http://dashboard.test",
    ) as client:
        return await client.get("/api/dashboard", params=params)


async def test_employee_dashboard_is_owner_scoped_and_bounds_recent_lists() -> None:
    department = await _create_department(name="研发部", code="rd")
    employee = await _create_user(
        name="Employee",
        email="employee-dashboard@company.com",
        department_id=department.id,
    )
    other = await _create_user(
        name="Other",
        email="other-dashboard@company.com",
        department_id=department.id,
    )
    base_time = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)
    own_files = []
    for index in range(7):
        own_files.append(
            await _create_file(
                uploader_id=employee.id,
                department_id=department.id,
                name=f"own-{index}.pdf",
                status="rejected" if index == 0 else "uploaded",
                review_status="rejected" if index == 0 else "pending",
                updated_at=base_time + timedelta(minutes=index),
            )
        )
        await _create_notification(
            user_id=employee.id,
            title=f"own-notification-{index}",
            created_at=base_time + timedelta(minutes=index),
            metadata={
                "file_id": "not-a-uuid" if index == 6 else str(own_files[-1].id),
                "url": "https://attacker.invalid/should-not-be-returned",
                "secret": "sk-never-return-this",
            },
        )
    await _create_notification(
        user_id=employee.id,
        title="email-notification-must-not-appear",
        created_at=base_time + timedelta(hours=2),
        channel="email",
    )
    await _create_file(
        uploader_id=other.id,
        department_id=department.id,
        name="other-secret.pdf",
        status="parsed",
    )
    await _create_notification(
        user_id=other.id,
        title="other-notification",
        created_at=base_time + timedelta(hours=1),
    )

    response = await _get_dashboard(_auth_record(user=employee, department=department))

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["role"] == "employee"
    assert data["employee"]["status_counts"]["total"] == 7
    assert data["employee"]["status_counts"]["draft"] == 6
    assert data["employee"]["action_counts"]["revise_rejected"] == 1
    assert len(data["employee"]["recent_documents"]) == 5
    assert len(data["employee"]["recent_notifications"]) == 5
    assert all(
        item["original_name"] != "other-secret.pdf" for item in data["employee"]["recent_documents"]
    )
    encoded = response.text
    assert "other-notification" not in encoded
    assert "email-notification-must-not-appear" not in encoded
    assert "attacker.invalid" not in encoded
    assert "sk-never-return-this" not in encoded
    assert data["employee"]["recent_notifications"][0]["resource_type"] is None
    assert data["employee"]["recent_notifications"][1]["resource_type"] == "file"


async def test_department_admin_scope_priority_pagination_and_audit_are_bounded() -> None:
    from app.core.database import AsyncSessionFactory, engine
    from app.modules.audit.models import AuditLog

    department_a = await _create_department(name="产品部", code="product")
    department_b = await _create_department(name="财务部", code="finance")
    admin = await _create_user(
        name="Department Admin",
        email="dept-admin-dashboard@company.com",
        department_id=department_a.id,
        role="dept_admin",
    )
    uploader_a = await _create_user(
        name="Uploader A",
        email="uploader-a-dashboard@company.com",
        department_id=department_a.id,
    )
    uploader_b = await _create_user(
        name="Uploader B",
        email="uploader-b-dashboard@company.com",
        department_id=department_b.id,
    )
    await _manage_department(user_id=admin.id, department_id=department_a.id)
    now = datetime.now(UTC)
    overdue = await _create_file(
        uploader_id=uploader_a.id,
        department_id=department_a.id,
        name="a-overdue.pdf",
        status="pending_review",
        submitted_at=now - timedelta(days=2),
        review_due_at=now - timedelta(hours=1),
    )
    await _create_analysis(file_id=overdue.id, risk="low")
    due_soon = await _create_file(
        uploader_id=uploader_a.id,
        department_id=department_a.id,
        name="a-critical.pdf",
        status="pending_review",
        review_status="in_review",
        submitted_at=now - timedelta(hours=2),
        review_due_at=now + timedelta(hours=2),
        claimed_by=admin.id,
        claimed_at=now - timedelta(minutes=5),
        claim_expires_at=now + timedelta(minutes=25),
    )
    await _create_analysis(file_id=due_soon.id, risk="critical")
    cross_scope = await _create_file(
        uploader_id=uploader_b.id,
        department_id=department_b.id,
        name="b-overdue-secret.pdf",
        status="pending_review",
        submitted_at=now - timedelta(days=3),
        review_due_at=now - timedelta(days=1),
    )
    await _create_analysis(file_id=cross_scope.id, risk="critical")

    statement_count = 0

    def count_statement(
        _connection: object,
        _cursor: object,
        _statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        nonlocal statement_count
        statement_count += 1

    event.listen(engine.sync_engine, "before_cursor_execute", count_statement)
    try:
        response = await _get_dashboard(
            _auth_record(user=admin, department=department_a),
            params={"page": 1, "page_size": 1},
        )
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", count_statement)

    assert response.status_code == 200
    data = response.json()["data"]
    counts = data["admin"]["counts"]
    queue = data["admin"]["priority_queue"]
    assert counts == {
        "scope_total_pending": 2,
        "unclaimed": 1,
        "mine": 1,
        "due_soon": 1,
        "overdue": 1,
        "sync_failed": 0,
        "claim_sla_available": True,
    }
    assert queue["total"] == 2
    assert queue["total_pages"] == 2
    assert [item["original_name"] for item in queue["items"]] == ["a-overdue.pdf"]
    assert "b-overdue-secret.pdf" not in response.text
    assert statement_count <= 6

    async with AsyncSessionFactory() as session:
        audits = list(
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.actor_id == admin.id,
                        AuditLog.action == "dashboard.view",
                    )
                )
            ).scalars()
        )
    assert len(audits) == 1
    assert audits[0].metadata_json["scope"] == "managed_departments"
    assert audits[0].metadata_json["department_count"] == 1
    assert "q" not in audits[0].metadata_json


async def test_department_admin_without_managed_scope_fails_closed_and_is_audited() -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.audit.models import AuditLog

    department = await _create_department(name="法务部", code="legal")
    admin = await _create_user(
        name="Unscoped Admin",
        email="unscoped-admin-dashboard@company.com",
        department_id=department.id,
        role="dept_admin",
    )
    uploader = await _create_user(
        name="Legal Uploader",
        email="legal-uploader-dashboard@company.com",
        department_id=department.id,
    )
    await _create_file(
        uploader_id=uploader.id,
        department_id=department.id,
        name="must-not-leak.pdf",
        status="pending_review",
        submitted_at=datetime.now(UTC),
        review_due_at=datetime.now(UTC) + timedelta(hours=2),
    )

    response = await _get_dashboard(_auth_record(user=admin, department=department))

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["access"]["ready"] is False
    assert data["access"]["blocker"] == "managed_departments_required"
    assert data["admin"]["counts"]["scope_total_pending"] == 0
    assert data["admin"]["priority_queue"]["items"] == []
    assert "must-not-leak.pdf" not in response.text
    async with AsyncSessionFactory() as session:
        audit_count = len(
            list(
                (
                    await session.execute(select(AuditLog).where(AuditLog.actor_id == admin.id))
                ).scalars()
            )
        )
    assert audit_count == 1


async def test_system_admin_dashboard_reports_global_database_facts_without_fake_health() -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.identity import UNASSIGNED_DEPARTMENT_ID
    from app.core.outbox import EventOutbox
    from app.modules.audit.models import AuditLog
    from app.modules.ragflow.models import SyncTask

    department_a = await _create_department(name="运营部", code="operations")
    department_b = await _create_department(name="市场部", code="marketing")
    admin = await _create_user(
        name="System Admin",
        email="system-admin-dashboard@company.com",
        department_id=department_a.id,
        role="system_admin",
    )
    uploader_a = await _create_user(
        name="Operations User",
        email="operations-dashboard@company.com",
        department_id=department_a.id,
    )
    uploader_b = await _create_user(
        name="Marketing User",
        email="marketing-dashboard@company.com",
        department_id=department_b.id,
    )
    await _create_user(
        name="Unassigned User",
        email="unassigned-count-dashboard@company.com",
        department_id=UNASSIGNED_DEPARTMENT_ID,
    )
    await _create_user(
        name="Disabled Unassigned User",
        email="disabled-unassigned-dashboard@company.com",
        department_id=UNASSIGNED_DEPARTMENT_ID,
        status="disabled",
    )
    first = await _create_file(
        uploader_id=uploader_a.id,
        department_id=department_a.id,
        name="operations-pending.pdf",
        status="pending_review",
        submitted_at=datetime.now(UTC),
        review_due_at=datetime.now(UTC) + timedelta(hours=1),
        expiry_status="expiring",
        size=120,
    )
    await _create_file(
        uploader_id=uploader_b.id,
        department_id=department_b.id,
        name="marketing-failed.pdf",
        status="failed",
        review_status="approved",
        expiry_status="expired",
        size=180,
    )
    async with AsyncSessionFactory() as session:
        session.add(
            EventOutbox(
                event_type="dashboard.test",
                aggregate_type="file",
                aggregate_id=str(first.id),
                payload={"file_id": str(first.id), "secret": "not-returned"},
            )
        )
        session.add(
            SyncTask(
                file_id=first.id,
                task_type="ragflow_upload",
                status="running",
                started_at=datetime.now(UTC) - timedelta(hours=1),
            )
        )
        await session.commit()

    response = await _get_dashboard(_auth_record(user=admin, department=department_a))

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["role"] == "system_admin"
    assert data["admin"]["counts"]["scope_total_pending"] == 1
    assert data["admin"]["counts"]["sync_failed"] == 1
    system = data["system"]
    assert system["database"] == {
        "status": "ok",
        "source": "dashboard_database_query",
    }
    assert system["worker_heartbeats"] == {
        "status": "unavailable",
        "source": "not_collected",
    }
    assert system["outbox"]["pending"] == 1
    assert system["dead_letters"]["metric_scope"] == "outbox_event_dead_letters"
    assert system["dead_letters"]["rabbitmq_queue_depth_available"] is False
    assert system["unassigned_users"] == {"count": 1, "metric_scope": "active_users"}
    assert system["expiry"] == {"expiring": 1, "expired": 1}
    assert system["logical_storage"]["file_count"] == 2
    assert system["logical_storage"]["total_bytes"] == 300
    assert system["logical_storage"]["physical_capacity_available"] is False
    assert system["processing"]["active_sync_tasks"] == 1
    assert system["processing"]["stale_running_candidates"] == 1
    assert "not-returned" not in response.text
    async with AsyncSessionFactory() as session:
        audit = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.actor_id == admin.id,
                    AuditLog.action == "dashboard.view",
                )
            )
        ).scalar_one()
    assert audit.metadata_json["scope"] == "all"


async def test_unassigned_employee_sees_only_recovery_blocker() -> None:
    from app.core.identity import UNASSIGNED_DEPARTMENT_ID

    actual_department = await _create_department(name="人事部", code="hr")
    employee = await _create_user(
        name="Unassigned Employee",
        email="unassigned-dashboard@company.com",
        department_id=UNASSIGNED_DEPARTMENT_ID,
    )
    await _create_file(
        uploader_id=employee.id,
        department_id=actual_department.id,
        name="legacy-owned-but-hidden.pdf",
    )

    response = await _get_dashboard(_auth_record(user=employee, department=None))

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["access"] == {
        "scope": "self",
        "ready": False,
        "blocker": "department_required",
        "department_ids": [],
    }
    assert data["employee"] is None
    assert "legacy-owned-but-hidden.pdf" not in response.text


async def test_analysis_failure_does_not_promise_employee_retry_action() -> None:
    department = await _create_department(name="分析失败部", code="analysis-failed")
    employee = await _create_user(
        name="Analysis Failed Employee",
        email="analysis-failed-dashboard@company.com",
        department_id=department.id,
    )
    await _create_file(
        uploader_id=employee.id,
        department_id=department.id,
        name="analysis-failed.pdf",
        status="analysis_failed",
    )

    response = await _get_dashboard(_auth_record(user=employee, department=department))

    assert response.status_code == 200
    employee_data = response.json()["data"]["employee"]
    assert employee_data["action_counts"]["analysis_failed"] == 1
    assert employee_data["recent_documents"][0]["next_action"] == "view_detail"
    assert "retry_analysis" not in response.text


async def test_empty_dashboard_is_truthful_and_invalid_request_fails_cleanly() -> None:
    department = await _create_department(name="空部门", code="empty")
    employee = await _create_user(
        name="Empty Employee",
        email="empty-dashboard@company.com",
        department_id=department.id,
    )
    auth = _auth_record(user=employee, department=department)

    response = await _get_dashboard(auth)
    invalid_page = await _get_dashboard(auth, params={"page": 0})
    unsupported = await _get_dashboard(
        _auth_record(user=employee, department=department, role="auditor")
    )

    assert response.status_code == 200
    employee_data = response.json()["data"]["employee"]
    assert employee_data["status_counts"]["total"] == 0
    assert employee_data["action_counts"]["total"] == 0
    assert employee_data["recent_documents"] == []
    assert employee_data["recent_notifications"] == []
    assert invalid_page.status_code == 422
    assert unsupported.status_code == 403
    assert unsupported.json()["detail"] == {
        "error_code": "DASHBOARD_PERMISSION_DENIED",
        "message": "dashboard access is not permitted for this role",
    }


async def test_database_failure_returns_stable_non_sensitive_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy.exc import OperationalError

    from app.modules.dashboard.repository import DashboardRepository

    department = await _create_department(name="错误测试部", code="error")
    employee = await _create_user(
        name="Error Employee",
        email="error-dashboard@company.com",
        department_id=department.id,
    )

    async def fail_counts(_repository: DashboardRepository, _user_id: uuid.UUID) -> Any:
        raise OperationalError("SELECT secret", {"api_key": "sk-leak"}, Exception("db"))

    monkeypatch.setattr(DashboardRepository, "get_employee_counts", fail_counts)
    response = await _get_dashboard(_auth_record(user=employee, department=department))

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "error_code": "DASHBOARD_UNAVAILABLE",
        "message": "dashboard data is temporarily unavailable",
    }
    assert "sk-leak" not in response.text
    assert "SELECT secret" not in response.text


async def test_real_application_exposes_dashboard_route() -> None:
    from app.core.database import AsyncSessionFactory, get_session
    from app.core.deps import get_current_user
    from app.main import app

    department = await _create_department(name="真实应用部", code="real-app")
    employee = await _create_user(
        name="Real App Employee",
        email="real-app-dashboard@company.com",
        department_id=department.id,
    )
    current_user = _auth_record(user=employee, department=department)

    async def override_current_user() -> Any:
        return current_user

    async def override_session() -> AsyncGenerator[Any, None]:
        async with AsyncSessionFactory() as session:
            yield session

    app.dependency_overrides[get_current_user] = override_current_user
    app.dependency_overrides[get_session] = override_session
    try:
        async with AsyncClient(
            transport=ASGITransport(app=cast(Any, app)),
            base_url="http://real-application.test",
        ) as client:
            response = await client.get("/api/dashboard")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["role"] == "employee"
