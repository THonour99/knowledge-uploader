from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from redis.asyncio import from_url
from sqlalchemy import select

pytestmark = pytest.mark.asyncio


async def _reset_database() -> None:
    from importlib import import_module

    import_module("app.db.models")

    from app.core.database import engine
    from app.db.base import Base

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()

    redis_client = from_url(  # type: ignore[no-untyped-call]
        os.environ["CACHE_REDIS_URL"],
        encoding="utf-8",
        decode_responses=True,
    )
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


@pytest.fixture
async def review_client() -> AsyncGenerator[AsyncClient, None]:
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
    async with AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client
    app.dependency_overrides.clear()


async def _create_user(
    *,
    email: str,
    password: str,
    role: str = "employee",
    assigned_department: bool = True,
) -> UUID:
    from app.core.database import AsyncSessionFactory
    from app.core.security import hash_password
    from app.modules.department.models import UNASSIGNED_DEPARTMENT_ID, Department
    from app.modules.user.models import User

    normalized_email = email.lower()
    async with AsyncSessionFactory() as session:
        department = (
            await session.execute(select(Department).where(Department.code == "review-tests"))
        ).scalar_one_or_none()
        if department is None:
            department = Department(name="审核测试部", code="review-tests", status="active")
            session.add(department)
            await session.flush()
        user = User(
            name=email.split("@", 1)[0],
            email=normalized_email,
            email_domain=normalized_email.rsplit("@", 1)[1],
            password_hash=hash_password(password),
            department_id=department.id if assigned_department else UNASSIGNED_DEPARTMENT_ID,
            department=department.name if assigned_department else None,
            role=role,
            status="active",
            email_verified=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user.id


async def _login(client: AsyncClient, *, email: str, password: str) -> str:
    response = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    return str(response.json()["data"]["access_token"])


async def _claim_review_file(client: AsyncClient, *, token: str, file_id: UUID) -> None:
    response = await client.post(
        f"/api/review/files/{file_id}/claim",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "pending_review"
    assert response.json()["data"]["review_status"] == "in_review"


async def _create_category_and_mapping(
    client: AsyncClient,
    *,
    token: str,
    suffix: str,
) -> tuple[dict[str, object], dict[str, object]]:
    category_response = await client.post(
        "/api/categories",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": f"审核草案 {suffix}", "code": f"review-draft-{suffix}"},
    )
    assert category_response.status_code == 201
    category = category_response.json()["data"]
    mapping_response = await client.post(
        "/api/datasets",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": f"审核草案 Dataset {suffix}",
            "category_id": category["id"],
            "ragflow_dataset_id": f"review-draft-dataset-{suffix}",
            "ragflow_dataset_name": f"审核草案库 {suffix}",
            "enabled": True,
        },
    )
    assert mapping_response.status_code == 201
    return category, mapping_response.json()["data"]


async def _create_file(
    *,
    uploader_id: UUID,
    status_value: str = "uploaded",
    review_status: str = "pending",
    original_name: str = "handbook.pdf",
    title: str | None = None,
    hash_value: str = "a" * 64,
    submitted_at: datetime | None = None,
    review_due_at: datetime | None = None,
) -> UUID:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File
    from app.modules.user.models import User

    async with AsyncSessionFactory() as session:
        uploader = await session.get(User, uploader_id)
        assert uploader is not None
        effective_submitted_at = submitted_at
        effective_review_due_at = review_due_at
        if status_value == "pending_review" and effective_submitted_at is None:
            effective_submitted_at = datetime.now(UTC)
        if status_value == "pending_review" and effective_review_due_at is None:
            assert effective_submitted_at is not None
            effective_review_due_at = effective_submitted_at + timedelta(hours=24)
        file = File(
            original_name=original_name,
            title=title or original_name,
            stored_name="file-handbook.pdf",
            extension="pdf",
            mime_type="application/pdf",
            size=128,
            hash=hash_value,
            storage_type="minio",
            bucket="knowledge-files",
            object_key=f"uploads/{uploader_id}/file-handbook.pdf",
            uploader_id=uploader_id,
            department_id=uploader.department_id,
            department=uploader.department,
            visibility="private",
            description="review target",
            tags=[],
            status=status_value,
            review_status=review_status,
            submitted_at=effective_submitted_at,
            review_due_at=effective_review_due_at,
            ai_analysis_enabled_at_upload=False,
        )
        session.add(file)
        await session.commit()
        await session.refresh(file)
        return file.id


async def _grant_managed_department(*, admin_id: UUID, department_id: UUID) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.department.models import UserManagedDepartment

    async with AsyncSessionFactory() as session:
        session.add(
            UserManagedDepartment(
                user_id=admin_id,
                department_id=department_id,
            )
        )
        await session.commit()


async def _get_user_department_id(user_id: UUID) -> UUID:
    from app.core.database import AsyncSessionFactory
    from app.modules.user.models import User

    async with AsyncSessionFactory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        return user.department_id


async def test_system_admin_creates_category_and_dataset_mapping(
    review_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.audit.models import AuditLog

    await _create_user(
        email="system-admin@company.com",
        password="password123",
        role="system_admin",
    )
    token = await _login(review_client, email="system-admin@company.com", password="password123")

    category_response = await review_client.post(
        "/api/categories",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "产品手册",
            "code": "product-handbook",
            "description": "产品和售后知识",
            "require_review": True,
            "allow_employee_select": True,
            "allow_ai_recommend": True,
            "default_visibility": "department",
            "keywords": ["产品", "手册"],
            "classification_prompt": "识别产品手册",
            "ai_analysis_enabled": True,
            "sensitive_detection_enabled": True,
            "auto_sync_enabled": False,
        },
    )

    assert category_response.status_code == 201
    category = category_response.json()["data"]
    assert category["name"] == "产品手册"
    assert category["code"] == "product-handbook"
    assert category["require_review"] is True
    assert category["default_visibility"] == "department"

    dataset_response = await review_client.post(
        "/api/datasets",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "产品手册 Dataset",
            "category_id": category["id"],
            "ragflow_dataset_id": "ragflow-product",
            "ragflow_dataset_name": "RAGFlow 产品手册",
            "enabled": True,
        },
    )

    assert dataset_response.status_code == 201
    dataset = dataset_response.json()["data"]
    assert dataset["category_id"] == category["id"]
    assert dataset["ragflow_dataset_id"] == "ragflow-product"
    assert dataset["enabled"] is True

    category_update_response = await review_client.patch(
        f"/api/categories/{category['id']}",
        headers={"Authorization": f"Bearer {token}"},
        json={"require_review": False, "ai_analysis_enabled": False},
    )

    assert category_update_response.status_code == 200
    updated_category = category_update_response.json()["data"]
    assert updated_category["require_review"] is False
    assert updated_category["ai_analysis_enabled"] is False

    dataset_update_response = await review_client.patch(
        f"/api/datasets/{dataset['id']}",
        headers={"Authorization": f"Bearer {token}"},
        json={"ragflow_dataset_name": "RAGFlow 产品手册 v2", "enabled": False},
    )

    assert dataset_update_response.status_code == 200
    updated_dataset = dataset_update_response.json()["data"]
    assert updated_dataset["ragflow_dataset_name"] == "RAGFlow 产品手册 v2"
    assert updated_dataset["enabled"] is False

    disable_response = await review_client.delete(
        f"/api/datasets/{dataset['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert disable_response.status_code == 204

    expected_actions = {
        "category.create",
        "dataset_mapping.create",
        "category.update",
        "dataset_mapping.update",
        "dataset_mapping.disable",
    }
    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(AuditLog).where(AuditLog.action.in_(expected_actions))
        )
        audit_logs = list(result.scalars())

    assert {log.action for log in audit_logs} == expected_actions
    category_create_log = next(log for log in audit_logs if log.action == "category.create")
    dataset_create_log = next(log for log in audit_logs if log.action == "dataset_mapping.create")
    assert category_create_log.target_type == "category"
    assert category_create_log.target_id == UUID(category["id"])
    assert dataset_create_log.target_type == "dataset_mapping"
    assert dataset_create_log.target_id == UUID(dataset["id"])


async def test_dataset_mapping_requires_allowed_ragflow_dataset_id(
    review_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import get_settings
    from app.core.database import AsyncSessionFactory
    from app.modules.audit.models import AuditLog

    monkeypatch.setenv("RAGFLOW_ALLOWED_DATASET_IDS", "allowed-dataset,allowed-updated")
    get_settings.cache_clear()
    await _create_user(
        email="allowlist-admin@company.com",
        password="password123",
        role="system_admin",
    )
    token = await _login(review_client, email="allowlist-admin@company.com", password="password123")
    category = (
        await review_client.post(
            "/api/categories",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "Allowlist", "code": "allowlist"},
        )
    ).json()["data"]

    rejected_response = await review_client.post(
        "/api/datasets",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "Blocked Dataset",
            "category_id": category["id"],
            "ragflow_dataset_id": "blocked-dataset",
            "ragflow_dataset_name": "Blocked",
            "enabled": True,
        },
    )
    allowed_response = await review_client.post(
        "/api/datasets",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "Allowed Dataset",
            "category_id": category["id"],
            "ragflow_dataset_id": "allowed-dataset",
            "ragflow_dataset_name": "Allowed",
            "enabled": True,
        },
    )
    dataset = allowed_response.json()["data"]
    rejected_update_response = await review_client.patch(
        f"/api/datasets/{dataset['id']}",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Should Not Persist", "ragflow_dataset_id": "blocked-dataset"},
    )
    allowed_update_response = await review_client.patch(
        f"/api/datasets/{dataset['id']}",
        headers={"Authorization": f"Bearer {token}"},
        json={"ragflow_dataset_id": "allowed-updated"},
    )
    get_settings.cache_clear()

    assert rejected_response.status_code == 422
    assert rejected_response.json()["error_code"] == "VALIDATION_ERROR"
    assert rejected_response.json()["message"] == "ragflow dataset id is not allowed"
    assert allowed_response.status_code == 201
    assert rejected_update_response.status_code == 422
    assert allowed_update_response.status_code == 200
    assert allowed_update_response.json()["data"]["name"] == "Allowed Dataset"
    assert allowed_update_response.json()["data"]["ragflow_dataset_id"] == "allowed-updated"

    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(AuditLog)
            .where(AuditLog.action == "dataset_mapping.ragflow_dataset_denied")
            .order_by(AuditLog.created_at, AuditLog.id)
        )
        denied_logs = list(result.scalars())

    assert [log.metadata_json["ragflow_dataset_id"] for log in denied_logs] == [
        "blocked-dataset",
        "blocked-dataset",
    ]


async def test_admin_read_operations_write_audit_logs(review_client: AsyncClient) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.audit.models import AuditLog

    actor_id = await _create_user(
        email="read-audit-admin@company.com",
        password="password123",
        role="system_admin",
    )
    uploader_id = await _create_user(
        email="read-audit-uploader@company.com",
        password="password123",
    )
    token = await _login(
        review_client,
        email="read-audit-admin@company.com",
        password="password123",
    )
    await _create_file(uploader_id=uploader_id)
    category = (
        await review_client.post(
            "/api/categories",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "审计分类", "code": "audit-category"},
        )
    ).json()["data"]
    await review_client.post(
        "/api/datasets",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "审计 Dataset",
            "category_id": category["id"],
            "ragflow_dataset_id": "audit-dataset",
            "ragflow_dataset_name": "审计库",
            "enabled": True,
        },
    )

    categories_response = await review_client.get(
        "/api/categories",
        headers={"Authorization": f"Bearer {token}"},
    )
    datasets_response = await review_client.get(
        "/api/datasets",
        headers={"Authorization": f"Bearer {token}"},
    )
    files_response = await review_client.get(
        "/api/review/files",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert categories_response.status_code == 200
    assert datasets_response.status_code == 200
    assert files_response.status_code == 200

    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(AuditLog).where(AuditLog.actor_id == actor_id).order_by(AuditLog.created_at)
        )
        audit_actions = [log.action for log in result.scalars()]

    assert "category.list" in audit_actions
    assert "dataset_mapping.list" in audit_actions
    assert "file.review_list" in audit_actions


async def test_review_request_context_is_sanitized() -> None:
    from starlette.requests import Request

    from app.modules.review.api import _context_from

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/categories",
            "headers": [(b"user-agent", b"   ")],
            "client": ("1" * 80, 12345),
        }
    )

    context = _context_from(request)

    assert context.ip_address == "1" * 45
    assert context.user_agent == "unknown"


async def test_employee_cannot_access_admin_review_files(review_client: AsyncClient) -> None:
    await _create_user(email="employee@company.com", password="password123")
    token = await _login(review_client, email="employee@company.com", password="password123")

    response = await review_client.get(
        "/api/review/files",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert response.json()["error_code"] == "PERMISSION_DENIED"


async def test_employee_cannot_access_review_configuration_lists(
    review_client: AsyncClient,
) -> None:
    await _create_user(email="config-employee@company.com", password="password123")
    token = await _login(
        review_client,
        email="config-employee@company.com",
        password="password123",
    )

    categories_response = await review_client.get(
        "/api/categories",
        headers={"Authorization": f"Bearer {token}"},
    )
    datasets_response = await review_client.get(
        "/api/datasets",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert categories_response.status_code == 403
    assert categories_response.json()["error_code"] == "PERMISSION_DENIED"
    assert datasets_response.status_code == 403
    assert datasets_response.json()["error_code"] == "PERMISSION_DENIED"


async def test_employee_cannot_mutate_review_configuration(
    review_client: AsyncClient,
) -> None:
    await _create_user(
        email="config-system-admin@company.com",
        password="password123",
        role="system_admin",
    )
    await _create_user(email="config-mutation-employee@company.com", password="password123")
    system_token = await _login(
        review_client,
        email="config-system-admin@company.com",
        password="password123",
    )
    employee_token = await _login(
        review_client,
        email="config-mutation-employee@company.com",
        password="password123",
    )
    category = (
        await review_client.post(
            "/api/categories",
            headers={"Authorization": f"Bearer {system_token}"},
            json={"name": "权限分类", "code": "permission-category"},
        )
    ).json()["data"]
    dataset = (
        await review_client.post(
            "/api/datasets",
            headers={"Authorization": f"Bearer {system_token}"},
            json={
                "name": "权限 Dataset",
                "category_id": category["id"],
                "ragflow_dataset_id": "permission-dataset",
                "ragflow_dataset_name": "权限库",
                "enabled": True,
            },
        )
    ).json()["data"]

    create_category_response = await review_client.post(
        "/api/categories",
        headers={"Authorization": f"Bearer {employee_token}"},
        json={"name": "员工分类", "code": "employee-category"},
    )
    update_category_response = await review_client.patch(
        f"/api/categories/{category['id']}",
        headers={"Authorization": f"Bearer {employee_token}"},
        json={"name": "员工不能改"},
    )
    create_dataset_response = await review_client.post(
        "/api/datasets",
        headers={"Authorization": f"Bearer {employee_token}"},
        json={
            "name": "员工 Dataset",
            "category_id": category["id"],
            "ragflow_dataset_id": "employee-dataset",
            "ragflow_dataset_name": "员工库",
            "enabled": True,
        },
    )
    update_dataset_response = await review_client.patch(
        f"/api/datasets/{dataset['id']}",
        headers={"Authorization": f"Bearer {employee_token}"},
        json={"enabled": False},
    )
    delete_dataset_response = await review_client.delete(
        f"/api/datasets/{dataset['id']}",
        headers={"Authorization": f"Bearer {employee_token}"},
    )

    responses = [
        create_category_response,
        update_category_response,
        create_dataset_response,
        update_dataset_response,
        delete_dataset_response,
    ]
    assert [response.status_code for response in responses] == [403, 403, 403, 403, 403]
    assert {response.json()["error_code"] for response in responses} == {"PERMISSION_DENIED"}


async def test_employee_cannot_mutate_review_workflow(review_client: AsyncClient) -> None:
    await _create_user(email="mutation-employee@company.com", password="password123")
    other_uploader_id = await _create_user(
        email="mutation-owner@company.com",
        password="password123",
    )
    token = await _login(
        review_client,
        email="mutation-employee@company.com",
        password="password123",
    )
    file_id = await _create_file(uploader_id=other_uploader_id, status_value="pending_review")

    submit_response = await review_client.post(
        f"/api/files/{file_id}/submit-review",
        headers={"Authorization": f"Bearer {token}"},
    )
    approve_response = await review_client.post(
        f"/api/files/{file_id}/approve",
        headers={"Authorization": f"Bearer {token}"},
        json={"sync_decision": "approve_only"},
    )
    reject_response = await review_client.post(
        f"/api/files/{file_id}/reject",
        headers={"Authorization": f"Bearer {token}"},
        json={"reason": "无权限"},
    )
    classification_response = await review_client.patch(
        f"/api/files/{file_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={},
    )

    assert submit_response.status_code == 403
    assert approve_response.status_code == 403
    assert reject_response.status_code == 403
    assert classification_response.status_code == 403


async def test_employee_can_submit_own_uploaded_file_for_review(
    review_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.audit.models import AuditLog
    from app.modules.document.models import File

    uploader_id = await _create_user(email="owner-submit@company.com", password="password123")
    token = await _login(review_client, email="owner-submit@company.com", password="password123")
    file_id = await _create_file(uploader_id=uploader_id, status_value="uploaded")

    response = await review_client.post(
        f"/api/files/{file_id}/submit-review",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    submitted = response.json()["data"]
    assert submitted["status"] == "pending_review"
    assert submitted["review_status"] == "pending"

    async with AsyncSessionFactory() as session:
        saved_file = await session.get(File, file_id)
        audit_result = await session.execute(select(AuditLog).where(AuditLog.target_id == file_id))
        audit_log = audit_result.scalar_one()
        event_result = await session.execute(
            select(EventOutbox).where(EventOutbox.aggregate_id == str(file_id))
        )
        outbox_event = event_result.scalar_one()

    assert saved_file is not None
    assert saved_file.status == "pending_review"
    assert saved_file.review_status == "pending"
    assert audit_log.action == "file.submit_review"
    assert audit_log.actor_id == uploader_id
    assert audit_log.metadata_json["submitted_by_owner"] is True
    assert outbox_event.event_type == "review.file.submitted"
    assert outbox_event.payload["previous_status"] == "uploaded"
    assert outbox_event.payload["status"] == "pending_review"


@pytest.mark.parametrize("role", ["dept_admin", "system_admin"])
async def test_admin_cannot_submit_another_users_draft(
    review_client: AsyncClient,
    role: str,
) -> None:
    uploader_id = await _create_user(
        email=f"owned-draft-{role}@company.com",
        password="password123",
    )
    await _create_user(
        email=f"draft-substitute-{role}@company.com",
        password="password123",
        role=role,
    )
    token = await _login(
        review_client,
        email=f"draft-substitute-{role}@company.com",
        password="password123",
    )
    file_id = await _create_file(uploader_id=uploader_id)

    response = await review_client.post(
        f"/api/files/{file_id}/submit-review",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert response.json()["error_code"] == "PERMISSION_DENIED"


async def test_employee_can_resubmit_own_rejected_file_for_review(
    review_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.document.models import File

    uploader_id = await _create_user(email="owner-resubmit@company.com", password="password123")
    token = await _login(review_client, email="owner-resubmit@company.com", password="password123")
    file_id = await _create_file(
        uploader_id=uploader_id,
        status_value="rejected",
        review_status="rejected",
    )

    response = await review_client.post(
        f"/api/files/{file_id}/submit-review",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    submitted = response.json()["data"]
    assert submitted["status"] == "pending_review"
    assert submitted["review_status"] == "pending"

    async with AsyncSessionFactory() as session:
        saved_file = await session.get(File, file_id)
        event_result = await session.execute(
            select(EventOutbox).where(EventOutbox.aggregate_id == str(file_id))
        )
        outbox_event = event_result.scalar_one()

    assert saved_file is not None
    assert saved_file.status == "pending_review"
    assert saved_file.review_status == "pending"
    assert outbox_event.payload["previous_status"] == "rejected"
    assert outbox_event.payload["previous_review_status"] == "rejected"


async def test_analysis_failed_submission_respects_disabled_policy(
    review_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ai.models import AiFeatureConfig, DocumentAnalysis

    uploader_id = await _create_user(
        email="failed-submit-owner@company.com",
        password="password123",
    )
    token = await _login(
        review_client,
        email="failed-submit-owner@company.com",
        password="password123",
    )
    file_id = await _create_file(uploader_id=uploader_id, status_value="analysis_failed")
    async with AsyncSessionFactory() as session:
        session.add_all(
            [
                DocumentAnalysis(
                    file_id=file_id,
                    status="failed",
                    sensitive_risk_level="none",
                    error_message="RuntimeError",
                ),
                AiFeatureConfig(
                    feature_name="allow_sync_when_analysis_failed",
                    enabled=False,
                    config_json={},
                ),
            ]
        )
        await session.commit()

    response = await review_client.post(
        f"/api/files/{file_id}/submit-review",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 409
    assert response.json()["error_code"] == "ANALYSIS_FAILED_SUBMISSION_DISABLED"


@pytest.mark.parametrize(
    ("source_status", "risk_level"),
    [
        ("sensitive_review_required", "medium"),
        ("rejected", "high"),
        ("rejected", "critical"),
    ],
)
async def test_sensitive_submission_requires_explicit_acknowledgement_every_time(
    review_client: AsyncClient,
    source_status: str,
    risk_level: str,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.ai.models import DocumentAnalysis
    from app.modules.audit.models import AuditLog

    uploader_id = await _create_user(
        email=f"sensitive-resubmit-{source_status}-{risk_level}@company.com",
        password="password123",
    )
    token = await _login(
        review_client,
        email=f"sensitive-resubmit-{source_status}-{risk_level}@company.com",
        password="password123",
    )
    file_id = await _create_file(
        uploader_id=uploader_id,
        status_value=source_status,
        review_status="rejected" if source_status == "rejected" else "pending",
    )
    async with AsyncSessionFactory() as session:
        session.add(
            DocumentAnalysis(
                file_id=file_id,
                status="succeeded",
                sensitive_risk_level=risk_level,
                sensitive_hits=[
                    {
                        "rule_id": "00000000-0000-0000-0000-000000000001",
                        "action": "require_review",
                        "risk_level": risk_level,
                    }
                ],
            )
        )
        await session.commit()

    denied = await review_client.post(
        f"/api/files/{file_id}/submit-review",
        headers={"Authorization": f"Bearer {token}"},
    )
    accepted = await review_client.post(
        f"/api/files/{file_id}/submit-review",
        headers={"Authorization": f"Bearer {token}"},
        json={"acknowledge_sensitive_risk": True},
    )

    assert denied.status_code == 422
    assert denied.json()["error_code"] == "SENSITIVE_RISK_ACKNOWLEDGEMENT_REQUIRED"
    assert accepted.status_code == 200
    async with AsyncSessionFactory() as session:
        audit = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.target_id == file_id,
                    AuditLog.action == "file.submit_review",
                )
            )
        ).scalar_one()
        event = (
            await session.execute(
                select(EventOutbox).where(
                    EventOutbox.aggregate_id == str(file_id),
                    EventOutbox.event_type == "review.file.submitted",
                )
            )
        ).scalar_one()
    assert audit.metadata_json["sensitive_risk_level"] == risk_level
    assert audit.metadata_json["sensitive_risk_acknowledged"] is True
    assert event.payload["sensitive_risk_level"] == risk_level
    assert event.payload["sensitive_risk_acknowledged"] is True


async def test_system_admin_reviews_file_and_audit_log_is_written(
    review_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.audit.models import AuditLog
    from app.modules.document.models import File

    uploader_id = await _create_user(email="uploader@company.com", password="password123")
    reviewer_id = await _create_user(
        email="reviewer@company.com",
        password="password123",
        role="system_admin",
    )
    uploader_token = await _login(
        review_client,
        email="uploader@company.com",
        password="password123",
    )
    admin_token = await _login(review_client, email="reviewer@company.com", password="password123")
    file_id = await _create_file(uploader_id=uploader_id)

    files_response = await review_client.get(
        "/api/review/files",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert files_response.status_code == 200
    assert files_response.json()["data"]["items"] == []
    assert files_response.json()["data"]["total"] == 0

    submit_response = await review_client.post(
        f"/api/files/{file_id}/submit-review",
        headers={"Authorization": f"Bearer {uploader_token}"},
    )
    assert submit_response.status_code == 200
    assert submit_response.json()["data"]["status"] == "pending_review"
    files_response = await review_client.get(
        "/api/review/files",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert files_response.json()["data"]["items"][0]["id"] == str(file_id)

    category_response = await review_client.post(
        "/api/categories",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "制度", "code": "policy"},
    )
    assert category_response.status_code == 201
    category = category_response.json()["data"]
    dataset = (
        await review_client.post(
            "/api/datasets",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "name": "制度 Dataset",
                "category_id": category["id"],
                "ragflow_dataset_id": "ragflow-policy",
                "ragflow_dataset_name": "制度库",
                "enabled": True,
            },
        )
    ).json()["data"]

    categories_response = await review_client.get(
        "/api/categories",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    datasets_response = await review_client.get(
        "/api/datasets",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert categories_response.status_code == 200
    assert datasets_response.status_code == 200
    await _claim_review_file(review_client, token=admin_token, file_id=file_id)

    approve_response = await review_client.post(
        f"/api/files/{file_id}/approve",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "sync_decision": "sync",
            "category_id": category["id"],
            "dataset_mapping_id": dataset["id"],
            "reason": "内容有效",
        },
    )

    assert approve_response.status_code == 200
    approved = approve_response.json()["data"]
    assert approved["status"] == "queued"
    assert approved["review_status"] == "approved"
    assert approved["category_id"] == category["id"]
    assert approved["dataset_mapping_id"] == dataset["id"]
    assert approved["ragflow_dataset_id"] == "ragflow-policy"
    assert approved["sync_decision"] == "sync"
    assert approved["sync_task_id"] is None

    async with AsyncSessionFactory() as session:
        saved_file = await session.get(File, file_id)
        assert saved_file is not None
        assert saved_file.status == "queued"
        assert saved_file.review_status == "approved"
        assert saved_file.category_id == UUID(category["id"])
        assert saved_file.dataset_mapping_id == UUID(dataset["id"])
        assert saved_file.ragflow_dataset_id == "ragflow-policy"

        result = await session.execute(
            select(AuditLog).where(AuditLog.target_id == file_id).order_by(AuditLog.created_at)
        )
        audit_logs = list(result.scalars())
        event_result = await session.execute(
            select(EventOutbox)
            .where(EventOutbox.aggregate_id == str(file_id))
            .order_by(EventOutbox.id)
        )
        outbox_events = list(event_result.scalars())

    assert [log.action for log in audit_logs] == [
        "file.submit_review",
        "file.review_claim",
        "file.approve",
    ]
    assert audit_logs[-1].reason == "内容有效"
    assert audit_logs[-1].actor_id == reviewer_id
    assert [event.event_type for event in outbox_events] == [
        "review.file.submitted",
        "review.file.approved",
    ]
    assert outbox_events[-1].payload["file_id"] == str(file_id)
    assert outbox_events[-1].payload["status"] == "queued"
    assert outbox_events[-1].payload["ragflow_dataset_id"] == "ragflow-policy"


async def test_runtime_ragflow_api_key_requires_dataset_allowlist_for_review_paths(
    review_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    set_secret_system_config: Callable[[str, str], Awaitable[None]],
) -> None:
    from app.core.config import get_settings
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File
    from app.modules.review.models import DatasetMapping

    monkeypatch.delenv("RAGFLOW_ALLOWED_DATASET_IDS", raising=False)
    get_settings.cache_clear()
    await set_secret_system_config("ragflow.api_key", "sk-runtime-review-abcd")
    uploader_id = await _create_user(
        email="runtime-allowlist-uploader@company.com",
        password="password123",
    )
    await _create_user(
        email="runtime-allowlist-admin@company.com",
        password="password123",
        role="system_admin",
    )
    token = await _login(
        review_client,
        email="runtime-allowlist-admin@company.com",
        password="password123",
    )
    category = (
        await review_client.post(
            "/api/categories",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "运行时 Key", "code": "runtime-key"},
        )
    ).json()["data"]

    create_response = await review_client.post(
        "/api/datasets",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "Blocked Runtime Dataset",
            "category_id": category["id"],
            "ragflow_dataset_id": "runtime-blocked",
            "ragflow_dataset_name": "Blocked",
            "enabled": True,
        },
    )
    async with AsyncSessionFactory() as session:
        legacy_mapping = DatasetMapping(
            name="Legacy Runtime Dataset",
            category_id=UUID(category["id"]),
            ragflow_dataset_id="legacy-runtime-dataset",
            ragflow_dataset_name="Legacy",
            enabled=True,
        )
        session.add(legacy_mapping)
        await session.commit()
        await session.refresh(legacy_mapping)
        mapping_id = legacy_mapping.id
    update_response = await review_client.patch(
        f"/api/datasets/{mapping_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"ragflow_dataset_id": "runtime-updated"},
    )
    file_id = await _create_file(uploader_id=uploader_id, status_value="pending_review")
    await _claim_review_file(review_client, token=token, file_id=file_id)
    approve_response = await review_client.post(
        f"/api/files/{file_id}/approve",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "sync_decision": "sync",
            "category_id": category["id"],
            "dataset_mapping_id": str(mapping_id),
        },
    )

    assert create_response.status_code == 422
    assert update_response.status_code == 422
    assert approve_response.status_code == 422
    assert create_response.json()["message"] == "ragflow dataset id is not allowed"
    assert update_response.json()["message"] == "ragflow dataset id is not allowed"
    assert approve_response.json()["message"] == "ragflow dataset id is not allowed"
    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        mapping = await session.get(DatasetMapping, mapping_id)
        assert file is not None
        assert mapping is not None

    assert file.status == "pending_review"
    assert file.dataset_mapping_id is None
    assert mapping.ragflow_dataset_id == "legacy-runtime-dataset"


async def test_runtime_ragflow_api_key_allows_dataset_in_allowlist_for_review_paths(
    review_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    set_secret_system_config: Callable[[str, str], Awaitable[None]],
) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("RAGFLOW_ALLOWED_DATASET_IDS", "runtime-allowed")
    get_settings.cache_clear()
    await set_secret_system_config("ragflow.api_key", "sk-runtime-review-allowed")
    uploader_id = await _create_user(
        email="runtime-allowed-uploader@company.com",
        password="password123",
    )
    await _create_user(
        email="runtime-allowed-admin@company.com",
        password="password123",
        role="system_admin",
    )
    token = await _login(
        review_client,
        email="runtime-allowed-admin@company.com",
        password="password123",
    )
    category = (
        await review_client.post(
            "/api/categories",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "运行时允许", "code": "runtime-allowed"},
        )
    ).json()["data"]
    dataset_response = await review_client.post(
        "/api/datasets",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "Runtime Allowed Dataset",
            "category_id": category["id"],
            "ragflow_dataset_id": "runtime-allowed",
            "ragflow_dataset_name": "Allowed",
            "enabled": True,
        },
    )
    dataset = dataset_response.json()["data"]
    file_id = await _create_file(uploader_id=uploader_id, status_value="pending_review")
    await _claim_review_file(review_client, token=token, file_id=file_id)
    approve_response = await review_client.post(
        f"/api/files/{file_id}/approve",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "sync_decision": "sync",
            "category_id": category["id"],
            "dataset_mapping_id": dataset["id"],
        },
    )

    assert dataset_response.status_code == 201
    assert approve_response.status_code == 200
    approved = approve_response.json()["data"]
    assert approved["status"] == "queued"
    assert approved["ragflow_dataset_id"] == "runtime-allowed"


async def test_review_rejects_dataset_mapping_removed_from_allowlist(
    review_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import get_settings
    from app.core.database import AsyncSessionFactory
    from app.modules.audit.models import AuditLog
    from app.modules.document.models import File

    monkeypatch.setenv("RAGFLOW_ALLOWED_DATASET_IDS", "legacy-dataset")
    get_settings.cache_clear()
    uploader_id = await _create_user(
        email="allowlist-uploader@company.com",
        password="password123",
    )
    await _create_user(
        email="allowlist-system@company.com",
        password="password123",
        role="system_admin",
    )
    system_token = await _login(
        review_client,
        email="allowlist-system@company.com",
        password="password123",
    )
    category = (
        await review_client.post(
            "/api/categories",
            headers={"Authorization": f"Bearer {system_token}"},
            json={"name": "历史映射", "code": "legacy-mapping"},
        )
    ).json()["data"]
    dataset = (
        await review_client.post(
            "/api/datasets",
            headers={"Authorization": f"Bearer {system_token}"},
            json={
                "name": "历史 Dataset",
                "category_id": category["id"],
                "ragflow_dataset_id": "legacy-dataset",
                "ragflow_dataset_name": "历史知识库",
                "enabled": True,
            },
        )
    ).json()["data"]
    review_file_id = await _create_file(
        uploader_id=uploader_id,
        status_value="pending_review",
    )
    classification_file_id = await _create_file(
        uploader_id=uploader_id,
        status_value="pending_review",
        hash_value="b" * 64,
    )

    monkeypatch.setenv("RAGFLOW_ALLOWED_DATASET_IDS", "current-dataset")
    get_settings.cache_clear()
    await _claim_review_file(review_client, token=system_token, file_id=review_file_id)
    await _claim_review_file(review_client, token=system_token, file_id=classification_file_id)
    approve_response = await review_client.post(
        f"/api/files/{review_file_id}/approve",
        headers={"Authorization": f"Bearer {system_token}"},
        json={
            "sync_decision": "sync",
            "category_id": category["id"],
            "dataset_mapping_id": dataset["id"],
        },
    )
    classification_response = await review_client.patch(
        f"/api/files/{classification_file_id}",
        headers={"Authorization": f"Bearer {system_token}"},
        json={
            "sync_decision": "sync",
            "category_id": category["id"],
            "dataset_mapping_id": dataset["id"],
        },
    )
    get_settings.cache_clear()

    assert approve_response.status_code == 422
    assert classification_response.status_code == 422
    assert approve_response.json()["message"] == "ragflow dataset id is not allowed"
    assert classification_response.json()["message"] == "ragflow dataset id is not allowed"
    async with AsyncSessionFactory() as session:
        review_file = await session.get(File, review_file_id)
        classification_file = await session.get(File, classification_file_id)
        audit_result = await session.execute(
            select(AuditLog)
            .where(AuditLog.action == "dataset_mapping.ragflow_dataset_denied")
            .order_by(AuditLog.created_at, AuditLog.id)
        )
        denied_logs = list(audit_result.scalars())

    assert review_file is not None
    assert classification_file is not None
    assert review_file.status == "pending_review"
    assert review_file.review_status == "in_review"
    assert classification_file.status == "pending_review"
    assert classification_file.dataset_mapping_id is None
    assert [log.metadata_json["ragflow_dataset_id"] for log in denied_logs] == [
        "legacy-dataset",
        "legacy-dataset",
    ]


async def test_critical_sensitive_file_cannot_be_queued_for_ragflow(
    review_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ai.models import DocumentAnalysis
    from app.modules.document.models import File

    uploader_id = await _create_user(email="critical-uploader@company.com", password="password123")
    await _create_user(
        email="critical-admin@company.com",
        password="password123",
        role="system_admin",
    )
    token = await _login(review_client, email="critical-admin@company.com", password="password123")
    file_id = await _create_file(uploader_id=uploader_id, status_value="pending_review")
    async with AsyncSessionFactory() as session:
        session.add(
            DocumentAnalysis(
                file_id=file_id,
                status="succeeded",
                sensitive_risk_level="critical",
                sensitive_hits=[
                    {
                        "rule_name": "生产环境凭据",
                        "risk_level": "critical",
                        "action": "block_sync",
                    }
                ],
            )
        )
        await session.commit()

    category = (
        await review_client.post(
            "/api/categories",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "安全", "code": "security"},
        )
    ).json()["data"]
    dataset = (
        await review_client.post(
            "/api/datasets",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": "安全 Dataset",
                "category_id": category["id"],
                "ragflow_dataset_id": "ragflow-security",
                "ragflow_dataset_name": "安全库",
                "enabled": True,
            },
        )
    ).json()["data"]

    await _claim_review_file(review_client, token=token, file_id=file_id)
    response = await review_client.post(
        f"/api/files/{file_id}/approve",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "sync_decision": "sync",
            "category_id": category["id"],
            "dataset_mapping_id": dataset["id"],
        },
    )

    assert response.status_code == 400
    classification_response = await review_client.patch(
        f"/api/files/{file_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"category_id": category["id"], "dataset_mapping_id": dataset["id"]},
    )
    assert classification_response.status_code == 200
    assert classification_response.json()["data"]["dataset_mapping_id"] == dataset["id"]
    assert classification_response.json()["data"]["ragflow_dataset_id"] is None
    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        assert file is not None
        assert file.status == "pending_review"
        assert file.review_status == "in_review"


async def test_critical_sensitive_file_with_existing_dataset_cannot_be_queued(
    review_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ai.models import DocumentAnalysis
    from app.modules.document.models import File

    uploader_id = await _create_user(
        email="critical-existing-uploader@company.com",
        password="password123",
    )
    await _create_user(
        email="critical-existing-admin@company.com",
        password="password123",
        role="system_admin",
    )
    token = await _login(
        review_client,
        email="critical-existing-admin@company.com",
        password="password123",
    )
    file_id = await _create_file(uploader_id=uploader_id, status_value="pending_review")

    category = (
        await review_client.post(
            "/api/categories",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "安全预绑定", "code": "security-existing"},
        )
    ).json()["data"]
    dataset = (
        await review_client.post(
            "/api/datasets",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": "安全预绑定 Dataset",
                "category_id": category["id"],
                "ragflow_dataset_id": "ragflow-security-existing",
                "ragflow_dataset_name": "安全预绑定库",
                "enabled": True,
            },
        )
    ).json()["data"]

    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        assert file is not None
        file.category_id = UUID(category["id"])
        file.dataset_mapping_id = UUID(dataset["id"])
        file.ragflow_dataset_id = "ragflow-security-existing"
        session.add(
            DocumentAnalysis(
                file_id=file_id,
                status="succeeded",
                sensitive_risk_level="critical",
                sensitive_hits=[
                    {
                        "rule_name": "生产环境凭据",
                        "risk_level": "critical",
                        "action": "block_sync",
                    }
                ],
            )
        )
        await session.commit()

    await _claim_review_file(review_client, token=token, file_id=file_id)
    response = await review_client.post(
        f"/api/files/{file_id}/approve",
        headers={"Authorization": f"Bearer {token}"},
        json={"sync_decision": "sync", "dataset_mapping_id": dataset["id"]},
    )

    assert response.status_code == 400
    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        assert file is not None
        assert file.status == "pending_review"
        assert file.review_status == "in_review"


async def test_analysis_failed_file_cannot_sync_when_feature_disabled(
    review_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ai.models import AiFeatureConfig, DocumentAnalysis
    from app.modules.document.models import File

    uploader_id = await _create_user(
        email="analysis-failed-uploader@company.com",
        password="password123",
    )
    await _create_user(
        email="analysis-failed-admin@company.com",
        password="password123",
        role="system_admin",
    )
    token = await _login(
        review_client,
        email="analysis-failed-admin@company.com",
        password="password123",
    )
    file_id = await _create_file(uploader_id=uploader_id, status_value="pending_review")
    async with AsyncSessionFactory() as session:
        session.add(
            DocumentAnalysis(
                file_id=file_id,
                status="failed",
                sensitive_risk_level="none",
                error_message="RuntimeError",
            )
        )
        session.add(
            AiFeatureConfig(
                feature_name="allow_sync_when_analysis_failed",
                enabled=False,
                config_json={},
            )
        )
        await session.commit()

    category = (
        await review_client.post(
            "/api/categories",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "失败分析", "code": "analysis-failed"},
        )
    ).json()["data"]
    dataset = (
        await review_client.post(
            "/api/datasets",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": "失败分析 Dataset",
                "category_id": category["id"],
                "ragflow_dataset_id": "ragflow-analysis-failed",
                "ragflow_dataset_name": "失败分析库",
                "enabled": True,
            },
        )
    ).json()["data"]

    await _claim_review_file(review_client, token=token, file_id=file_id)
    response = await review_client.post(
        f"/api/files/{file_id}/approve",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "sync_decision": "sync",
            "category_id": category["id"],
            "dataset_mapping_id": dataset["id"],
        },
    )

    assert response.status_code == 400
    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        assert file is not None
        assert file.status == "pending_review"
        assert file.review_status == "in_review"


async def test_review_rejects_invalid_file_state_and_dataset_category_mismatch(
    review_client: AsyncClient,
) -> None:
    uploader_id = await _create_user(email="invalid-uploader@company.com", password="password123")
    await _create_user(
        email="invalid-reviewer@company.com",
        password="password123",
        role="system_admin",
    )
    await _create_user(
        email="invalid-system@company.com",
        password="password123",
        role="system_admin",
    )
    reviewer_token = await _login(
        review_client,
        email="invalid-reviewer@company.com",
        password="password123",
    )
    uploader_token = await _login(
        review_client,
        email="invalid-uploader@company.com",
        password="password123",
    )
    system_token = await _login(
        review_client,
        email="invalid-system@company.com",
        password="password123",
    )
    approved_file_id = await _create_file(uploader_id=uploader_id, status_value="approved")

    submit_response = await review_client.post(
        f"/api/files/{approved_file_id}/submit-review",
        headers={"Authorization": f"Bearer {uploader_token}"},
    )

    assert submit_response.status_code == 409
    assert submit_response.json()["error_code"] == "REVIEW_ALREADY_DECIDED"

    first_category = (
        await review_client.post(
            "/api/categories",
            headers={"Authorization": f"Bearer {system_token}"},
            json={"name": "第一分类", "code": "first-category"},
        )
    ).json()["data"]
    second_category = (
        await review_client.post(
            "/api/categories",
            headers={"Authorization": f"Bearer {system_token}"},
            json={"name": "第二分类", "code": "second-category"},
        )
    ).json()["data"]
    mapping = (
        await review_client.post(
            "/api/datasets",
            headers={"Authorization": f"Bearer {system_token}"},
            json={
                "name": "第一分类 Dataset",
                "category_id": first_category["id"],
                "ragflow_dataset_id": "first-dataset",
                "ragflow_dataset_name": "第一知识库",
                "enabled": True,
            },
        )
    ).json()["data"]
    pending_file_id = await _create_file(uploader_id=uploader_id, status_value="pending_review")

    await _claim_review_file(review_client, token=reviewer_token, file_id=pending_file_id)
    approve_response = await review_client.post(
        f"/api/files/{pending_file_id}/approve",
        headers={"Authorization": f"Bearer {reviewer_token}"},
        json={
            "sync_decision": "sync",
            "category_id": second_category["id"],
            "dataset_mapping_id": mapping["id"],
        },
    )

    assert approve_response.status_code == 422
    assert approve_response.json()["error_code"] == "VALIDATION_ERROR"

    classification_file_id = await _create_file(
        uploader_id=uploader_id,
        status_value="pending_review",
        hash_value="b" * 64,
    )
    await _claim_review_file(
        review_client,
        token=reviewer_token,
        file_id=classification_file_id,
    )
    classification_response = await review_client.patch(
        f"/api/files/{classification_file_id}",
        headers={"Authorization": f"Bearer {reviewer_token}"},
        json={
            "category_id": second_category["id"],
            "dataset_mapping_id": mapping["id"],
        },
    )

    assert classification_response.status_code == 422
    assert classification_response.json()["error_code"] == "VALIDATION_ERROR"


async def test_classification_draft_requires_active_claim_and_defers_ragflow_target(
    review_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File

    uploader_id = await _create_user(
        email="draft-claim-owner@company.com",
        password="password123",
    )
    await _create_user(
        email="draft-claim-first@company.com",
        password="password123",
        role="system_admin",
    )
    await _create_user(
        email="draft-claim-second@company.com",
        password="password123",
        role="system_admin",
    )
    first_token = await _login(
        review_client,
        email="draft-claim-first@company.com",
        password="password123",
    )
    second_token = await _login(
        review_client,
        email="draft-claim-second@company.com",
        password="password123",
    )
    category, mapping = await _create_category_and_mapping(
        review_client,
        token=first_token,
        suffix="claim",
    )
    file_id = await _create_file(uploader_id=uploader_id, status_value="pending_review")
    payload = {"category_id": category["id"], "dataset_mapping_id": mapping["id"]}

    unclaimed = await review_client.patch(
        f"/api/files/{file_id}",
        headers={"Authorization": f"Bearer {first_token}"},
        json=payload,
    )
    await _claim_review_file(review_client, token=first_token, file_id=file_id)
    claimed_by_other = await review_client.patch(
        f"/api/files/{file_id}",
        headers={"Authorization": f"Bearer {second_token}"},
        json=payload,
    )
    updated = await review_client.patch(
        f"/api/files/{file_id}",
        headers={"Authorization": f"Bearer {first_token}"},
        json=payload,
    )

    assert unclaimed.status_code == 409
    assert unclaimed.json()["error_code"] == "REVIEW_CLAIM_REQUIRED"
    assert claimed_by_other.status_code == 409
    assert claimed_by_other.json()["error_code"] == "REVIEW_CLAIM_REQUIRED"
    assert updated.status_code == 200
    assert updated.json()["data"]["dataset_mapping_id"] == mapping["id"]
    assert updated.json()["data"]["ragflow_dataset_id"] is None
    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        assert file is not None
    assert file.status == "pending_review"
    assert file.review_status == "in_review"
    assert file.dataset_mapping_id == UUID(str(mapping["id"]))
    assert file.ragflow_dataset_id is None


async def test_classification_patch_preserves_omitted_fields_and_rejects_empty_payload(
    review_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File

    uploader_id = await _create_user(
        email="draft-patch-owner@company.com",
        password="password123",
    )
    await _create_user(
        email="draft-patch-admin@company.com",
        password="password123",
        role="system_admin",
    )
    token = await _login(
        review_client,
        email="draft-patch-admin@company.com",
        password="password123",
    )
    category, mapping = await _create_category_and_mapping(
        review_client,
        token=token,
        suffix="partial",
    )
    file_id = await _create_file(uploader_id=uploader_id, status_value="pending_review")
    await _claim_review_file(review_client, token=token, file_id=file_id)

    selected = await review_client.patch(
        f"/api/files/{file_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"dataset_mapping_id": mapping["id"]},
    )
    cleared_mapping = await review_client.patch(
        f"/api/files/{file_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"dataset_mapping_id": None},
    )
    empty = await review_client.patch(
        f"/api/files/{file_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={},
    )

    assert selected.status_code == 200
    assert selected.json()["data"]["category_id"] == category["id"]
    assert cleared_mapping.status_code == 200
    assert cleared_mapping.json()["data"]["category_id"] == category["id"]
    assert cleared_mapping.json()["data"]["dataset_mapping_id"] is None
    assert empty.status_code == 422
    assert empty.json()["error_code"] == "VALIDATION_ERROR"
    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
    assert file is not None
    assert file.category_id == UUID(str(category["id"]))
    assert file.dataset_mapping_id is None


async def test_classification_draft_rejects_remote_active_and_reviewed_files(
    review_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File
    from app.modules.ragflow.models import SyncTask

    uploader_id = await _create_user(
        email="draft-locked-owner@company.com",
        password="password123",
    )
    await _create_user(
        email="draft-locked-admin@company.com",
        password="password123",
        role="system_admin",
    )
    token = await _login(
        review_client,
        email="draft-locked-admin@company.com",
        password="password123",
    )
    category, _mapping = await _create_category_and_mapping(
        review_client,
        token=token,
        suffix="locked",
    )
    remote_id = await _create_file(
        uploader_id=uploader_id,
        status_value="pending_review",
        hash_value="b" * 64,
    )
    active_id = await _create_file(
        uploader_id=uploader_id,
        status_value="pending_review",
        hash_value="c" * 64,
    )
    reviewed_id = await _create_file(
        uploader_id=uploader_id,
        status_value="approved",
        review_status="approved",
        hash_value="d" * 64,
    )
    await _claim_review_file(review_client, token=token, file_id=remote_id)
    await _claim_review_file(review_client, token=token, file_id=active_id)
    async with AsyncSessionFactory() as session:
        remote_file = await session.get(File, remote_id)
        assert remote_file is not None
        remote_file.ragflow_document_id = "existing-remote-document"
        remote_file.ragflow_dataset_id = "existing-remote-dataset"
        session.add(
            SyncTask(
                file_id=active_id,
                task_type="ragflow_upload",
                status="queued",
                retry_count=0,
                max_retry_count=3,
            )
        )
        await session.commit()

    responses = [
        await review_client.patch(
            f"/api/files/{target_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"category_id": category["id"]},
        )
        for target_id in (remote_id, active_id, reviewed_id)
    ]

    assert [response.status_code for response in responses] == [409, 409, 409]
    assert all(
        response.json()["message"]
        == "file classification can only be changed by the active reviewer before approval"
        for response in responses
    )


async def test_concurrent_classification_drafts_are_serialized(
    review_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.audit.models import AuditLog
    from app.modules.document.models import File

    uploader_id = await _create_user(
        email="draft-concurrent-owner@company.com",
        password="password123",
    )
    await _create_user(
        email="draft-concurrent-admin@company.com",
        password="password123",
        role="system_admin",
    )
    token = await _login(
        review_client,
        email="draft-concurrent-admin@company.com",
        password="password123",
    )
    first_category, _ = await _create_category_and_mapping(
        review_client,
        token=token,
        suffix="concurrent-first",
    )
    second_category, _ = await _create_category_and_mapping(
        review_client,
        token=token,
        suffix="concurrent-second",
    )
    file_id = await _create_file(uploader_id=uploader_id, status_value="pending_review")
    await _claim_review_file(review_client, token=token, file_id=file_id)

    responses = await asyncio.gather(
        review_client.patch(
            f"/api/files/{file_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"category_id": first_category["id"]},
        ),
        review_client.patch(
            f"/api/files/{file_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"category_id": second_category["id"]},
        ),
    )

    assert [response.status_code for response in responses] == [200, 200]
    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        result = await session.execute(
            select(AuditLog).where(
                AuditLog.target_id == file_id,
                AuditLog.action == "file.update_classification",
            )
        )
        logs = list(result.scalars())
        assert file is not None
    assert file.category_id in {
        UUID(str(first_category["id"])),
        UUID(str(second_category["id"])),
    }
    assert file.review_version == 3
    assert len(logs) == 2


async def test_approval_revalidates_mapping_after_concurrent_disable(
    review_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File
    from app.modules.review.models import DatasetMapping

    uploader_id = await _create_user(
        email="mapping-race-owner@company.com",
        password="password123",
    )
    await _create_user(
        email="mapping-race-admin@company.com",
        password="password123",
        role="system_admin",
    )
    token = await _login(
        review_client,
        email="mapping-race-admin@company.com",
        password="password123",
    )
    category, mapping = await _create_category_and_mapping(
        review_client,
        token=token,
        suffix="mapping-race",
    )
    file_id = await _create_file(uploader_id=uploader_id, status_value="pending_review")
    await _claim_review_file(review_client, token=token, file_id=file_id)

    async with AsyncSessionFactory() as lock_session:
        result = await lock_session.execute(
            select(DatasetMapping)
            .where(DatasetMapping.id == UUID(str(mapping["id"])))
            .with_for_update()
        )
        locked_mapping = result.scalar_one()
        locked_mapping.enabled = False
        await lock_session.flush()

        approval_task = asyncio.create_task(
            review_client.post(
                f"/api/files/{file_id}/approve",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "sync_decision": "sync",
                    "category_id": category["id"],
                    "dataset_mapping_id": mapping["id"],
                },
            )
        )
        await asyncio.sleep(0.1)
        assert approval_task.done() is False
        await lock_session.commit()
        approval = await asyncio.wait_for(approval_task, timeout=3)

    assert approval.status_code == 422, approval.text
    assert approval.json()["error_code"] == "VALIDATION_ERROR"
    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
    assert file is not None
    assert file.status == "pending_review"
    assert file.dataset_mapping_id is None


async def test_review_queue_extension_filter_has_server_side_bounds(
    review_client: AsyncClient,
) -> None:
    await _create_user(
        email="extension-filter-admin@company.com",
        password="password123",
        role="system_admin",
    )
    token = await _login(
        review_client,
        email="extension-filter-admin@company.com",
        password="password123",
    )
    headers = {"Authorization": f"Bearer {token}"}

    too_long = await review_client.get(
        f"/api/review/files?extension={'x' * 21}",
        headers=headers,
    )
    invalid_format = await review_client.get(
        "/api/review/files?extension=.pdf",
        headers=headers,
    )
    uppercase = await review_client.get(
        "/api/review/files?extension=PDF",
        headers=headers,
    )

    assert too_long.status_code == 422
    assert invalid_format.status_code == 422
    assert uppercase.status_code == 200


@pytest.mark.parametrize(
    ("method", "path_template", "terminal_status", "event_type"),
    [
        ("DELETE", "/api/files/{file_id}", "deleted", "document.file.deleted"),
        ("POST", "/api/admin/files/{file_id}/archive", "disabled", "document.file.archived"),
    ],
)
async def test_approval_and_destructive_action_share_row_lock(
    review_client: AsyncClient,
    method: str,
    path_template: str,
    terminal_status: str,
    event_type: str,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.document.models import File

    uploader_id = await _create_user(
        email=f"row-lock-owner-{terminal_status}@company.com",
        password="password123",
    )
    await _create_user(
        email=f"row-lock-admin-{terminal_status}@company.com",
        password="password123",
        role="system_admin",
    )
    token = await _login(
        review_client,
        email=f"row-lock-admin-{terminal_status}@company.com",
        password="password123",
    )
    file_id = await _create_file(uploader_id=uploader_id, status_value="pending_review")
    await _claim_review_file(review_client, token=token, file_id=file_id)

    approval, destructive = await asyncio.gather(
        review_client.post(
            f"/api/files/{file_id}/approve",
            headers={"Authorization": f"Bearer {token}"},
            json={"sync_decision": "approve_only"},
        ),
        review_client.request(
            method,
            path_template.format(file_id=file_id),
            headers={"Authorization": f"Bearer {token}"},
        ),
    )

    assert approval.status_code == 200
    assert destructive.status_code in {200, 409}
    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        event_result = await session.execute(
            select(EventOutbox)
            .where(EventOutbox.aggregate_id == str(file_id))
            .order_by(EventOutbox.id)
        )
        events_for_file = list(event_result.scalars())
        assert file is not None
    if destructive.status_code == 409:
        assert file.status == "approved"
        assert [event.event_type for event in events_for_file] == ["review.file.approved"]
    else:
        assert file.status == terminal_status
        assert [event.event_type for event in events_for_file] == [
            "review.file.approved",
            event_type,
        ]


async def test_system_admin_can_clear_optional_category_fields(
    review_client: AsyncClient,
) -> None:
    await _create_user(
        email="clear-fields-admin@company.com",
        password="password123",
        role="system_admin",
    )
    token = await _login(
        review_client,
        email="clear-fields-admin@company.com",
        password="password123",
    )
    category = (
        await review_client.post(
            "/api/categories",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": "可清空分类",
                "code": "clearable-category",
                "description": "原说明",
                "default_dataset_id": "default-dataset",
                "classification_prompt": "原 Prompt",
            },
        )
    ).json()["data"]

    response = await review_client.patch(
        f"/api/categories/{category['id']}",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "description": None,
            "default_dataset_id": None,
            "classification_prompt": None,
        },
    )

    assert response.status_code == 200
    updated = response.json()["data"]
    assert updated["description"] is None
    assert updated["default_dataset_id"] is None
    assert updated["classification_prompt"] is None


async def test_system_admin_rejects_file_with_reason(review_client: AsyncClient) -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.audit.models import AuditLog
    from app.modules.document.models import File

    uploader_id = await _create_user(email="reject-uploader@company.com", password="password123")
    await _create_user(
        email="reject-reviewer@company.com",
        password="password123",
        role="system_admin",
    )
    token = await _login(review_client, email="reject-reviewer@company.com", password="password123")
    file_id = await _create_file(uploader_id=uploader_id, status_value="pending_review")

    await _claim_review_file(review_client, token=token, file_id=file_id)
    response = await review_client.post(
        f"/api/files/{file_id}/reject",
        headers={"Authorization": f"Bearer {token}"},
        json={"reason": "文件内容重复且缺少上下文"},
    )

    assert response.status_code == 200
    rejected = response.json()["data"]
    assert rejected["status"] == "rejected"
    assert rejected["review_status"] == "rejected"

    async with AsyncSessionFactory() as session:
        saved_file = await session.get(File, file_id)
        assert saved_file is not None
        assert saved_file.status == "rejected"
        assert saved_file.review_status == "rejected"
        result = await session.execute(
            select(AuditLog).where(
                AuditLog.target_id == file_id,
                AuditLog.action == "file.reject",
            )
        )
        audit_log = result.scalar_one()
        event_result = await session.execute(
            select(EventOutbox).where(EventOutbox.aggregate_id == str(file_id))
        )
        outbox_event = event_result.scalar_one()

    assert audit_log.action == "file.reject"
    assert audit_log.reason == "文件内容重复且缺少上下文"
    assert outbox_event.event_type == "review.file.rejected"
    assert outbox_event.payload["reason"] == "文件内容重复且缺少上下文"


@pytest.mark.parametrize(
    ("with_claimant", "with_claimed_at", "with_expiry"),
    [
        (True, False, False),
        (False, True, True),
        (True, True, False),
        (False, False, True),
    ],
)
async def test_review_claim_columns_reject_orphan_combinations(
    review_client: AsyncClient,
    with_claimant: bool,
    with_claimed_at: bool,
    with_expiry: bool,
) -> None:
    from sqlalchemy.exc import IntegrityError

    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File

    uploader_id = await _create_user(
        email=f"claim-constraint-owner-{with_claimant}-{with_claimed_at}-{with_expiry}@company.com",
        password="password123",
    )
    claimant_id = await _create_user(
        email=f"claim-constraint-admin-{with_claimant}-{with_claimed_at}-{with_expiry}@company.com",
        password="password123",
        role="dept_admin",
    )
    file_id = await _create_file(uploader_id=uploader_id, status_value="pending_review")
    now = datetime.now(UTC)
    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        assert file is not None
        file.claimed_by = claimant_id if with_claimant else None
        file.claimed_at = now if with_claimed_at else None
        file.claim_expires_at = now + timedelta(minutes=30) if with_expiry else None
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


@pytest.mark.parametrize(
    ("claimed", "review_status"),
    [
        (False, "in_review"),
        (True, "pending"),
    ],
)
async def test_review_claim_and_review_status_must_be_consistent(
    review_client: AsyncClient,
    claimed: bool,
    review_status: str,
) -> None:
    from sqlalchemy.exc import IntegrityError

    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File

    uploader_id = await _create_user(
        email=f"claim-status-owner-{claimed}@company.com",
        password="password123",
    )
    claimant_id = await _create_user(
        email=f"claim-status-admin-{claimed}@company.com",
        password="password123",
        role="system_admin",
    )
    file_id = await _create_file(uploader_id=uploader_id, status_value="pending_review")
    now = datetime.now(UTC)
    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        assert file is not None
        file.review_status = review_status
        if claimed:
            file.claimed_by = claimant_id
            file.claimed_at = now
            file.claim_expires_at = now + timedelta(minutes=30)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


async def test_malformed_legacy_claim_is_released_before_new_claim(
    review_client: AsyncClient,
) -> None:
    from sqlalchemy import text

    from app.core.database import AsyncSessionFactory
    from app.modules.audit.models import AuditLog

    uploader_id = await _create_user(
        email="legacy-claim-owner@company.com",
        password="password123",
    )
    stale_claimant_id = await _create_user(
        email="legacy-claim-stale@company.com",
        password="password123",
        role="dept_admin",
    )
    replacement_id = await _create_user(
        email="legacy-claim-replacement@company.com",
        password="password123",
        role="system_admin",
    )
    replacement_token = await _login(
        review_client,
        email="legacy-claim-replacement@company.com",
        password="password123",
    )
    file_id = await _create_file(uploader_id=uploader_id, status_value="pending_review")

    async with AsyncSessionFactory() as session:
        await session.execute(
            text("ALTER TABLE files DROP CONSTRAINT ck_files_claim_expiry_after_claim")
        )
        await session.execute(
            text("ALTER TABLE files DROP CONSTRAINT ck_files_claim_review_status_consistent")
        )
        await session.execute(
            text(
                "UPDATE files SET claimed_by = :claimant_id, claimed_at = NULL, "
                "claim_expires_at = NULL, review_status = 'in_review' WHERE id = :file_id"
            ),
            {"claimant_id": stale_claimant_id, "file_id": file_id},
        )
        await session.commit()

    try:
        response = await review_client.post(
            f"/api/review/files/{file_id}/claim",
            headers={"Authorization": f"Bearer {replacement_token}"},
        )
        assert response.status_code == 200
        claimed = response.json()["data"]
        assert claimed["claimed_by"] == str(replacement_id)
        assert claimed["claimed_at"] is not None
        assert claimed["claim_expires_at"] is not None
    finally:
        async with AsyncSessionFactory() as session:
            await session.execute(
                text(
                    "UPDATE files SET claimed_by = NULL, claimed_at = NULL, "
                    "claim_expires_at = NULL, review_status = 'pending' "
                    "WHERE NOT ((claimed_by IS NULL AND claimed_at IS NULL "
                    "AND claim_expires_at IS NULL) OR (claimed_by IS NOT NULL "
                    "AND claimed_at IS NOT NULL AND claim_expires_at IS NOT NULL "
                    "AND claim_expires_at > claimed_at))"
                )
            )
            await session.execute(
                text(
                    "ALTER TABLE files ADD CONSTRAINT ck_files_claim_expiry_after_claim "
                    "CHECK ((claimed_by IS NULL AND claimed_at IS NULL "
                    "AND claim_expires_at IS NULL) OR (claimed_by IS NOT NULL "
                    "AND claimed_at IS NOT NULL AND claim_expires_at IS NOT NULL "
                    "AND claim_expires_at > claimed_at))"
                )
            )
            await session.execute(
                text(
                    "ALTER TABLE files ADD CONSTRAINT ck_files_claim_review_status_consistent "
                    "CHECK ((status = 'pending_review' AND ("
                    "(review_status = 'pending' AND claimed_by IS NULL "
                    "AND claimed_at IS NULL AND claim_expires_at IS NULL) OR "
                    "(review_status = 'in_review' AND claimed_by IS NOT NULL "
                    "AND claimed_at IS NOT NULL AND claim_expires_at IS NOT NULL))) OR "
                    "(status <> 'pending_review' AND claimed_by IS NULL "
                    "AND claimed_at IS NULL AND claim_expires_at IS NULL "
                    "AND review_status <> 'in_review'))"
                )
            )
            await session.commit()

    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(AuditLog)
            .where(AuditLog.target_id == file_id)
            .order_by(AuditLog.created_at, AuditLog.id)
        )
        logs = list(result.scalars())
    assert {log.action for log in logs} == {
        "file.review_claim_expired",
        "file.review_claim",
    }
    invalid_release = next(log for log in logs if log.action == "file.review_claim_expired")
    assert invalid_release.metadata_json["invalid_claim_state"] is True
    assert invalid_release.metadata_json["previous_claimed_by"] == str(stale_claimant_id)


async def test_idempotent_claim_and_empty_release_are_audited(
    review_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.audit.models import AuditLog

    uploader_id = await _create_user(
        email="idempotent-claim-owner@company.com",
        password="password123",
    )
    await _create_user(
        email="idempotent-claim-admin@company.com",
        password="password123",
        role="system_admin",
    )
    token = await _login(
        review_client,
        email="idempotent-claim-admin@company.com",
        password="password123",
    )
    file_id = await _create_file(uploader_id=uploader_id, status_value="pending_review")

    for _ in range(2):
        response = await review_client.post(
            f"/api/review/files/{file_id}/claim",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
    for _ in range(2):
        response = await review_client.delete(
            f"/api/review/files/{file_id}/claim",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200

    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(AuditLog)
            .where(AuditLog.target_id == file_id)
            .order_by(AuditLog.id)
        )
        logs = list(result.scalars())
    assert [log.action for log in logs].count("file.review_claim") == 2
    assert [log.action for log in logs].count("file.review_claim_release") == 2
    idempotent_claim = next(
        log
        for log in logs
        if log.action == "file.review_claim" and log.metadata_json.get("idempotent") is True
    )
    empty_release = next(
        log
        for log in logs
        if log.action == "file.review_claim_release"
        and log.metadata_json.get("no_claim") is True
    )
    assert idempotent_claim.metadata_json["idempotent"] is True
    assert empty_release.metadata_json["idempotent"] is True


async def test_claimed_reviewer_cannot_be_physically_deleted(
    review_client: AsyncClient,
) -> None:
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    from app.core.database import AsyncSessionFactory

    uploader_id = await _create_user(
        email="claim-delete-owner@company.com",
        password="password123",
    )
    reviewer_id = await _create_user(
        email="claim-delete-reviewer@company.com",
        password="password123",
        role="system_admin",
    )
    reviewer_token = await _login(
        review_client,
        email="claim-delete-reviewer@company.com",
        password="password123",
    )
    file_id = await _create_file(uploader_id=uploader_id, status_value="pending_review")
    await _claim_review_file(review_client, token=reviewer_token, file_id=file_id)

    async with AsyncSessionFactory() as session:
        with pytest.raises(IntegrityError) as exc_info:
            await session.execute(
                text("DELETE FROM users WHERE id = :reviewer_id"),
                {"reviewer_id": reviewer_id},
            )
            await session.commit()
        await session.rollback()

    assert "claimed_by" in str(exc_info.value)


async def test_review_claim_is_atomic_and_decision_requires_winning_claim(
    review_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.audit.models import AuditLog

    uploader_id = await _create_user(email="claim-owner@company.com", password="password123")
    department_id = await _get_user_department_id(uploader_id)
    first_admin_id = await _create_user(
        email="claim-first@company.com",
        password="password123",
        role="dept_admin",
    )
    second_admin_id = await _create_user(
        email="claim-second@company.com",
        password="password123",
        role="dept_admin",
    )
    await _grant_managed_department(admin_id=first_admin_id, department_id=department_id)
    await _grant_managed_department(admin_id=second_admin_id, department_id=department_id)
    first_token = await _login(
        review_client,
        email="claim-first@company.com",
        password="password123",
    )
    second_token = await _login(
        review_client,
        email="claim-second@company.com",
        password="password123",
    )
    file_id = await _create_file(uploader_id=uploader_id, status_value="pending_review")

    responses = await asyncio.gather(
        review_client.post(
            f"/api/review/files/{file_id}/claim",
            headers={"Authorization": f"Bearer {first_token}"},
        ),
        review_client.post(
            f"/api/review/files/{file_id}/claim",
            headers={"Authorization": f"Bearer {second_token}"},
        ),
    )

    assert sorted(response.status_code for response in responses) == [200, 409]
    winner_index = next(
        index for index, response in enumerate(responses) if response.status_code == 200
    )
    winner_token = (first_token, second_token)[winner_index]
    loser_token = (first_token, second_token)[1 - winner_index]
    conflict = responses[1 - winner_index]
    assert conflict.json()["error_code"] == "REVIEW_CLAIM_CONFLICT"
    claimed = responses[winner_index].json()["data"]
    assert claimed["status"] == "pending_review"
    assert claimed["claimed_by"] in {str(first_admin_id), str(second_admin_id)}
    assert claimed["review_status"] == "in_review"
    assert claimed["claimed_at"] is not None
    assert claimed["claim_expires_at"] is not None

    loser_decision = await review_client.post(
        f"/api/files/{file_id}/approve",
        headers={"Authorization": f"Bearer {loser_token}"},
        json={"sync_decision": "approve_only"},
    )
    winner_decision = await review_client.post(
        f"/api/files/{file_id}/approve",
        headers={"Authorization": f"Bearer {winner_token}"},
        json={"sync_decision": "approve_only"},
    )
    repeated_decision = await review_client.post(
        f"/api/files/{file_id}/reject",
        headers={"Authorization": f"Bearer {winner_token}"},
        json={"reason": "late"},
    )

    assert loser_decision.status_code == 409
    assert loser_decision.json()["error_code"] == "REVIEW_CLAIM_REQUIRED"
    assert winner_decision.status_code == 200
    assert winner_decision.json()["data"]["status"] == "approved"
    assert winner_decision.json()["data"]["sync_decision"] == "approve_only"
    assert winner_decision.json()["data"]["claimed_by"] is None
    assert repeated_decision.status_code == 409
    assert repeated_decision.json()["error_code"] == "REVIEW_ALREADY_DECIDED"
    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(AuditLog.action)
            .where(AuditLog.target_id == file_id)
            .order_by(AuditLog.created_at)
        )
        assert list(result.scalars()) == ["file.review_claim", "file.approve"]


async def test_system_admin_cannot_decide_without_own_active_claim(
    review_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.audit.models import AuditLog
    from app.modules.document.models import File

    uploader_id = await _create_user(
        email="system-claim-owner@company.com",
        password="password123",
    )
    department_id = await _get_user_department_id(uploader_id)
    dept_admin_id = await _create_user(
        email="system-claim-dept-admin@company.com",
        password="password123",
        role="dept_admin",
    )
    await _grant_managed_department(admin_id=dept_admin_id, department_id=department_id)
    await _create_user(
        email="system-claim-system-admin@company.com",
        password="password123",
        role="system_admin",
    )
    dept_token = await _login(
        review_client,
        email="system-claim-dept-admin@company.com",
        password="password123",
    )
    system_token = await _login(
        review_client,
        email="system-claim-system-admin@company.com",
        password="password123",
    )
    unclaimed_file_id = await _create_file(
        uploader_id=uploader_id,
        status_value="pending_review",
    )
    claimed_file_id = await _create_file(
        uploader_id=uploader_id,
        status_value="pending_review",
        hash_value="b" * 64,
    )

    unclaimed_decision = await review_client.post(
        f"/api/files/{unclaimed_file_id}/approve",
        headers={"Authorization": f"Bearer {system_token}"},
        json={"sync_decision": "approve_only"},
    )
    await _claim_review_file(review_client, token=dept_token, file_id=claimed_file_id)
    claimed_by_other_decision = await review_client.post(
        f"/api/files/{claimed_file_id}/approve",
        headers={"Authorization": f"Bearer {system_token}"},
        json={"sync_decision": "approve_only"},
    )

    assert unclaimed_decision.status_code == 409
    assert unclaimed_decision.json()["error_code"] == "REVIEW_CLAIM_REQUIRED"
    assert claimed_by_other_decision.status_code == 409
    assert claimed_by_other_decision.json()["error_code"] == "REVIEW_CLAIM_REQUIRED"
    async with AsyncSessionFactory() as session:
        unclaimed_file = await session.get(File, unclaimed_file_id)
        claimed_file = await session.get(File, claimed_file_id)
        audit_result = await session.execute(
            select(AuditLog.action).where(AuditLog.target_id == claimed_file_id)
        )
        assert unclaimed_file is not None
        assert claimed_file is not None

    assert unclaimed_file.status == "pending_review"
    assert unclaimed_file.review_status == "pending"
    assert unclaimed_file.claimed_by is None
    assert claimed_file.status == "pending_review"
    assert claimed_file.review_status == "in_review"
    assert claimed_file.claimed_by == dept_admin_id
    assert list(audit_result.scalars()) == ["file.review_claim"]


async def test_unclaimed_queue_keeps_more_than_cleanup_batch_of_expired_claims_visible(
    review_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File
    from app.modules.user.models import User

    uploader_id = await _create_user(
        email="expired-batch-owner@company.com",
        password="password123",
    )
    department_id = await _get_user_department_id(uploader_id)
    stale_admin_id = await _create_user(
        email="expired-batch-stale@company.com",
        password="password123",
        role="dept_admin",
    )
    replacement_admin_id = await _create_user(
        email="expired-batch-replacement@company.com",
        password="password123",
        role="dept_admin",
    )
    await _grant_managed_department(admin_id=stale_admin_id, department_id=department_id)
    await _grant_managed_department(admin_id=replacement_admin_id, department_id=department_id)
    replacement_token = await _login(
        review_client,
        email="expired-batch-replacement@company.com",
        password="password123",
    )
    now = datetime.now(UTC)
    async with AsyncSessionFactory() as session:
        uploader = await session.get(User, uploader_id)
        assert uploader is not None
        for index in range(101):
            session.add(
                File(
                    original_name=f"expired-{index:03d}.pdf",
                    title=f"expired-{index:03d}.pdf",
                    stored_name=f"expired-{index:03d}.pdf",
                    extension="pdf",
                    mime_type="application/pdf",
                    size=128,
                    hash=f"{index:064x}",
                    storage_type="minio",
                    bucket="knowledge-files",
                    object_key=f"uploads/{uploader_id}/expired-{index:03d}.pdf",
                    uploader_id=uploader_id,
                    department_id=uploader.department_id,
                    department=uploader.department,
                    visibility="private",
                    description="expired review claim",
                    tags=[],
                    status="pending_review",
                    review_status="in_review",
                    submitted_at=now - timedelta(hours=2),
                    review_due_at=now + timedelta(hours=22),
                    claimed_by=stale_admin_id,
                    claimed_at=now - timedelta(minutes=31),
                    claim_expires_at=now - timedelta(seconds=1),
                    ai_analysis_enabled_at_upload=False,
                )
            )
        await session.commit()

    response = await review_client.get(
        "/api/review/files?queue=unclaimed&page=1&page_size=100",
        headers={"Authorization": f"Bearer {replacement_token}"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["total"] == 101
    assert len(response.json()["data"]["items"]) == 100
    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(File.id).where(
                File.claimed_by == stale_admin_id,
                File.claim_expires_at <= datetime.now(UTC),
            )
        )
        leftover_id = result.scalar_one()

    claim_response = await review_client.post(
        f"/api/review/files/{leftover_id}/claim",
        headers={"Authorization": f"Bearer {replacement_token}"},
    )

    assert claim_response.status_code == 200
    assert claim_response.json()["data"]["claimed_by"] == str(replacement_admin_id)
    assert claim_response.json()["data"]["review_status"] == "in_review"


async def test_expired_claim_can_be_reclaimed_and_force_release_is_audited(
    review_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.audit.models import AuditLog
    from app.modules.document.models import File

    uploader_id = await _create_user(email="expiry-owner@company.com", password="password123")
    department_id = await _get_user_department_id(uploader_id)
    first_admin_id = await _create_user(
        email="expiry-first@company.com",
        password="password123",
        role="dept_admin",
    )
    second_admin_id = await _create_user(
        email="expiry-second@company.com",
        password="password123",
        role="dept_admin",
    )
    await _grant_managed_department(admin_id=first_admin_id, department_id=department_id)
    await _grant_managed_department(admin_id=second_admin_id, department_id=department_id)
    await _create_user(
        email="expiry-system@company.com",
        password="password123",
        role="system_admin",
    )
    first_token = await _login(
        review_client,
        email="expiry-first@company.com",
        password="password123",
    )
    second_token = await _login(
        review_client,
        email="expiry-second@company.com",
        password="password123",
    )
    system_token = await _login(
        review_client,
        email="expiry-system@company.com",
        password="password123",
    )
    file_id = await _create_file(uploader_id=uploader_id, status_value="pending_review")
    first_claim = await review_client.post(
        f"/api/review/files/{file_id}/claim",
        headers={"Authorization": f"Bearer {first_token}"},
    )
    assert first_claim.status_code == 200
    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        assert file is not None
        now = datetime.now(UTC)
        file.claimed_at = now - timedelta(minutes=31)
        file.claim_expires_at = now - timedelta(seconds=1)
        await session.commit()

    second_claim = await review_client.post(
        f"/api/review/files/{file_id}/claim",
        headers={"Authorization": f"Bearer {second_token}"},
    )
    forbidden_release = await review_client.delete(
        f"/api/review/files/{file_id}/claim",
        headers={"Authorization": f"Bearer {first_token}"},
    )
    missing_reason = await review_client.request(
        "DELETE",
        f"/api/review/files/{file_id}/claim",
        headers={"Authorization": f"Bearer {system_token}"},
    )
    force_release = await review_client.request(
        "DELETE",
        f"/api/review/files/{file_id}/claim",
        headers={"Authorization": f"Bearer {system_token}"},
        json={"reason": "值班交接"},
    )

    assert second_claim.status_code == 200
    assert second_claim.json()["data"]["status"] == "pending_review"
    assert second_claim.json()["data"]["claimed_by"] == str(second_admin_id)
    assert second_claim.json()["data"]["review_status"] == "in_review"
    assert forbidden_release.status_code == 403
    assert missing_reason.status_code == 422
    assert force_release.status_code == 200
    assert force_release.json()["data"]["status"] == "pending_review"
    assert force_release.json()["data"]["claimed_by"] is None
    assert force_release.json()["data"]["review_status"] == "pending"
    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(AuditLog).where(AuditLog.target_id == file_id).order_by(AuditLog.created_at)
        )
        logs = list(result.scalars())
    assert [log.action for log in logs] == [
        "file.review_claim",
        "file.review_claim_expired",
        "file.review_claim",
        "file.review_claim_release",
    ]
    assert logs[-1].reason == "值班交接"
    assert logs[-1].metadata_json["force_release"] is True


async def test_review_queue_paginates_searches_and_sorts_risk_semantically(
    review_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ai.models import DocumentAnalysis
    from app.modules.document.models import File

    uploader_id = await _create_user(email="queue-owner@company.com", password="password123")
    await _create_user(
        email="queue-system@company.com",
        password="password123",
        role="system_admin",
    )
    token = await _login(review_client, email="queue-system@company.com", password="password123")
    low_id = await _create_file(
        uploader_id=uploader_id,
        status_value="pending_review",
        original_name="risk-low.pdf",
    )
    critical_id = await _create_file(
        uploader_id=uploader_id,
        status_value="pending_review",
        original_name="risk-critical.pdf",
    )
    high_id = await _create_file(
        uploader_id=uploader_id,
        status_value="pending_review",
        original_name="risk-high.pdf",
    )
    async with AsyncSessionFactory() as session:
        high_file = await session.get(File, high_id)
        assert high_file is not None
        high_file.title = "董事会专项知识"
        session.add_all(
            [
                DocumentAnalysis(
                    file_id=low_id,
                    status="succeeded",
                    sensitive_risk_level="low",
                ),
                DocumentAnalysis(
                    file_id=critical_id,
                    status="succeeded",
                    sensitive_risk_level="critical",
                ),
                DocumentAnalysis(
                    file_id=high_id,
                    status="succeeded",
                    sensitive_risk_level="high",
                ),
            ]
        )
        await session.commit()

    response = await review_client.get(
        "/api/review/files",
        headers={"Authorization": f"Bearer {token}"},
        params={
            "page": 1,
            "page_size": 2,
            "q": "risk-",
            "sort": "risk",
            "order": "asc",
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 3
    assert data["page"] == 1
    assert data["page_size"] == 2
    assert data["total_pages"] == 2
    assert [item["sensitive_risk_level"] for item in data["items"]] == [
        "critical",
        "high",
    ]

    title_response = await review_client.get(
        "/api/review/files",
        headers={"Authorization": f"Bearer {token}"},
        params={"q": "董事会专项知识"},
    )
    assert title_response.status_code == 200
    assert title_response.json()["data"]["total"] == 1
    assert title_response.json()["data"]["items"][0]["id"] == str(high_id)


async def test_review_queue_search_treats_percent_and_underscore_as_literals(
    review_client: AsyncClient,
) -> None:
    uploader_id = await _create_user(
        email="literal-review-owner@company.com",
        password="password123",
    )
    await _create_user(
        email="literal-review-admin@company.com",
        password="password123",
        role="system_admin",
    )
    token = await _login(
        review_client,
        email="literal-review-admin@company.com",
        password="password123",
    )
    target_id = await _create_file(
        uploader_id=uploader_id,
        status_value="pending_review",
        original_name="literal-review-target.pdf",
        title="预算 100%_最终版",
        hash_value="7" * 64,
    )
    await _create_file(
        uploader_id=uploader_id,
        status_value="pending_review",
        original_name="literal-review-decoy.pdf",
        title="预算 100AX最终版",
        hash_value="8" * 64,
    )

    response = await review_client.get(
        "/api/review/files",
        headers={"Authorization": f"Bearer {token}"},
        params={"q": "%_"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 1
    assert [item["id"] for item in data["items"]] == [str(target_id)]


async def test_review_submission_snapshots_sla_and_requires_department(
    review_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core import review_policy
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File

    configured_sla_hours = 36

    async def review_config(key: str) -> object | None:
        if key == "review.sla_hours":
            return configured_sla_hours
        if key == "review.claim_timeout_minutes":
            return 30
        return None

    monkeypatch.setattr(review_policy, "get_config", review_config)
    uploader_id = await _create_user(email="sla-owner@company.com", password="password123")
    token = await _login(review_client, email="sla-owner@company.com", password="password123")
    file_id = await _create_file(uploader_id=uploader_id)
    response = await review_client.post(
        f"/api/files/{file_id}/submit-review",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        assert file is not None
        assert file.submitted_at is not None
        assert file.review_due_at is not None
        original_due_at = file.review_due_at
        assert file.review_due_at - file.submitted_at == timedelta(hours=36)

    configured_sla_hours = 1
    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        assert file is not None
        assert file.review_due_at == original_due_at

    unassigned_id = await _create_user(
        email="sla-unassigned@company.com",
        password="password123",
        assigned_department=False,
    )
    unassigned_token = await _login(
        review_client,
        email="sla-unassigned@company.com",
        password="password123",
    )
    unassigned_file_id = await _create_file(uploader_id=unassigned_id)
    denied = await review_client.post(
        f"/api/files/{unassigned_file_id}/submit-review",
        headers={"Authorization": f"Bearer {unassigned_token}"},
    )
    assert denied.status_code == 403
    assert denied.json()["error_code"] == "DEPARTMENT_ASSIGNMENT_REQUIRED"


@pytest.mark.parametrize("role", ["employee", "dept_admin", "system_admin"])
async def test_all_uploader_roles_require_an_assigned_department_to_submit(
    review_client: AsyncClient,
    role: str,
) -> None:
    email = f"unassigned-submit-{role}@company.com"
    uploader_id = await _create_user(
        email=email,
        password="password123",
        role=role,
        assigned_department=False,
    )
    token = await _login(review_client, email=email, password="password123")
    file_id = await _create_file(uploader_id=uploader_id)

    response = await review_client.post(
        f"/api/files/{file_id}/submit-review",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert response.json()["error_code"] == "DEPARTMENT_ASSIGNMENT_REQUIRED"


async def test_sync_decision_never_falls_back_and_approve_only_rejects_dataset(
    review_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.document.models import File

    uploader_id = await _create_user(email="decision-owner@company.com", password="password123")
    await _create_user(
        email="decision-system@company.com",
        password="password123",
        role="system_admin",
    )
    token = await _login(review_client, email="decision-system@company.com", password="password123")
    category = (
        await review_client.post(
            "/api/categories",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "显式决定", "code": "explicit-decision"},
        )
    ).json()["data"]
    mapping = (
        await review_client.post(
            "/api/datasets",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": "显式决定 Dataset",
                "category_id": category["id"],
                "ragflow_dataset_id": "explicit-decision-dataset",
                "ragflow_dataset_name": "显式决定库",
            },
        )
    ).json()["data"]
    file_id = await _create_file(uploader_id=uploader_id, status_value="pending_review")
    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        assert file is not None
        file.category_id = UUID(category["id"])
        file.dataset_mapping_id = UUID(mapping["id"])
        file.ragflow_dataset_id = "explicit-decision-dataset"
        await session.commit()

    await _claim_review_file(review_client, token=token, file_id=file_id)
    missing_mapping = await review_client.post(
        f"/api/files/{file_id}/approve",
        headers={"Authorization": f"Bearer {token}"},
        json={"sync_decision": "sync"},
    )
    forbidden_mapping = await review_client.post(
        f"/api/files/{file_id}/approve",
        headers={"Authorization": f"Bearer {token}"},
        json={"sync_decision": "approve_only", "dataset_mapping_id": mapping["id"]},
    )
    approve_only = await review_client.post(
        f"/api/files/{file_id}/approve",
        headers={"Authorization": f"Bearer {token}"},
        json={"sync_decision": "approve_only"},
    )

    assert missing_mapping.status_code == 422
    assert missing_mapping.json()["message"] == "dataset mapping is required when sync is selected"
    assert forbidden_mapping.status_code == 422
    assert approve_only.status_code == 200
    assert approve_only.json()["data"]["status"] == "approved"
    assert approve_only.json()["data"]["sync_decision"] == "approve_only"
    assert approve_only.json()["data"]["category_id"] == category["id"]
    assert approve_only.json()["data"]["dataset_mapping_id"] is None
    assert approve_only.json()["data"]["ragflow_dataset_id"] is None
    async with AsyncSessionFactory() as session:
        stored_file = await session.get(File, file_id)
        assert stored_file is not None
        assert stored_file.category_id == UUID(category["id"])
        assert stored_file.dataset_mapping_id is None
        assert stored_file.ragflow_dataset_id is None
        event_result = await session.execute(
            select(EventOutbox).where(
                EventOutbox.aggregate_id == str(file_id),
                EventOutbox.event_type == "review.file.approved",
            )
        )
        approved_event = event_result.scalar_one()
        assert approved_event.payload["sync_decision"] == "approve_only"
        assert approved_event.payload["dataset_mapping_id"] is None
        assert approved_event.payload["ragflow_dataset_id"] is None
