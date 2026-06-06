from __future__ import annotations

import os
from collections.abc import AsyncGenerator
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


async def _create_user(*, email: str, password: str, role: str = "employee") -> UUID:
    from app.core.database import AsyncSessionFactory
    from app.core.security import hash_password
    from app.modules.user.models import User

    normalized_email = email.lower()
    user = User(
        name=email.split("@", 1)[0],
        email=normalized_email,
        email_domain=normalized_email.rsplit("@", 1)[1],
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


async def _create_file(*, uploader_id: UUID, status_value: str = "uploaded") -> UUID:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File

    file = File(
        original_name="handbook.pdf",
        stored_name="file-handbook.pdf",
        extension="pdf",
        mime_type="application/pdf",
        size=128,
        hash="a" * 64,
        storage_type="minio",
        bucket="knowledge-files",
        object_key=f"uploads/{uploader_id}/file-handbook.pdf",
        uploader_id=uploader_id,
        department="QA",
        visibility="private",
        description="review target",
        tags=[],
        status=status_value,
        review_status="pending",
        ai_analysis_enabled_at_upload=False,
    )
    async with AsyncSessionFactory() as session:
        session.add(file)
        await session.commit()
        await session.refresh(file)
        return file.id


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
    uploader_id = await _create_user(email="mutation-employee@company.com", password="password123")
    token = await _login(
        review_client,
        email="mutation-employee@company.com",
        password="password123",
    )
    file_id = await _create_file(uploader_id=uploader_id, status_value="pending_review")

    submit_response = await review_client.post(
        f"/api/files/{file_id}/submit-review",
        headers={"Authorization": f"Bearer {token}"},
    )
    approve_response = await review_client.post(
        f"/api/files/{file_id}/approve",
        headers={"Authorization": f"Bearer {token}"},
        json={},
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


async def test_knowledge_admin_reviews_file_and_audit_log_is_written(
    review_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.audit.models import AuditLog
    from app.modules.document.models import File

    uploader_id = await _create_user(email="uploader@company.com", password="password123")
    await _create_user(
        email="reviewer@company.com",
        password="password123",
        role="knowledge_admin",
    )
    admin_token = await _login(review_client, email="reviewer@company.com", password="password123")
    file_id = await _create_file(uploader_id=uploader_id)

    files_response = await review_client.get(
        "/api/review/files",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert files_response.status_code == 200
    assert files_response.json()["data"]["items"][0]["id"] == str(file_id)

    submit_response = await review_client.post(
        f"/api/files/{file_id}/submit-review",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert submit_response.status_code == 200
    assert submit_response.json()["data"]["status"] == "pending_review"

    category_response = await review_client.post(
        "/api/categories",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "制度", "code": "policy"},
    )
    assert category_response.status_code == 403

    system_admin_id = await _create_user(
        email="system@company.com",
        password="password123",
        role="system_admin",
    )
    system_token = await _login(review_client, email="system@company.com", password="password123")
    category = (
        await review_client.post(
            "/api/categories",
            headers={"Authorization": f"Bearer {system_token}"},
            json={"name": "制度", "code": "policy"},
        )
    ).json()["data"]
    dataset = (
        await review_client.post(
            "/api/datasets",
            headers={"Authorization": f"Bearer {system_token}"},
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

    approve_response = await review_client.post(
        f"/api/files/{file_id}/approve",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
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

    assert [log.action for log in audit_logs] == ["file.submit_review", "file.approve"]
    assert audit_logs[-1].reason == "内容有效"
    assert audit_logs[-1].actor_id != system_admin_id
    assert [event.event_type for event in outbox_events] == [
        "review.file.submitted",
        "review.file.approved",
    ]
    assert outbox_events[-1].payload["file_id"] == str(file_id)
    assert outbox_events[-1].payload["status"] == "queued"
    assert outbox_events[-1].payload["ragflow_dataset_id"] == "ragflow-policy"


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

    response = await review_client.post(
        f"/api/files/{file_id}/approve",
        headers={"Authorization": f"Bearer {token}"},
        json={"category_id": category["id"], "dataset_mapping_id": dataset["id"]},
    )

    assert response.status_code == 400
    classification_response = await review_client.patch(
        f"/api/files/{file_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"category_id": category["id"], "dataset_mapping_id": dataset["id"]},
    )
    assert classification_response.status_code == 400
    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        assert file is not None
        assert file.status == "pending_review"
        assert file.review_status == "pending"


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

    response = await review_client.post(
        f"/api/files/{file_id}/approve",
        headers={"Authorization": f"Bearer {token}"},
        json={},
    )

    assert response.status_code == 400
    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        assert file is not None
        assert file.status == "pending_review"
        assert file.review_status == "pending"


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

    response = await review_client.post(
        f"/api/files/{file_id}/approve",
        headers={"Authorization": f"Bearer {token}"},
        json={"category_id": category["id"], "dataset_mapping_id": dataset["id"]},
    )

    assert response.status_code == 400
    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        assert file is not None
        assert file.status == "pending_review"
        assert file.review_status == "pending"


async def test_review_rejects_invalid_file_state_and_dataset_category_mismatch(
    review_client: AsyncClient,
) -> None:
    uploader_id = await _create_user(email="invalid-uploader@company.com", password="password123")
    await _create_user(
        email="invalid-reviewer@company.com",
        password="password123",
        role="knowledge_admin",
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
    system_token = await _login(
        review_client,
        email="invalid-system@company.com",
        password="password123",
    )
    approved_file_id = await _create_file(uploader_id=uploader_id, status_value="approved")

    submit_response = await review_client.post(
        f"/api/files/{approved_file_id}/submit-review",
        headers={"Authorization": f"Bearer {reviewer_token}"},
    )

    assert submit_response.status_code == 400
    assert submit_response.json()["error_code"] == "VALIDATION_ERROR"

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

    approve_response = await review_client.post(
        f"/api/files/{pending_file_id}/approve",
        headers={"Authorization": f"Bearer {reviewer_token}"},
        json={
            "category_id": second_category["id"],
            "dataset_mapping_id": mapping["id"],
        },
    )

    assert approve_response.status_code == 400
    assert approve_response.json()["error_code"] == "VALIDATION_ERROR"

    classification_file_id = await _create_file(uploader_id=uploader_id, status_value="uploaded")
    classification_response = await review_client.patch(
        f"/api/files/{classification_file_id}",
        headers={"Authorization": f"Bearer {reviewer_token}"},
        json={
            "category_id": second_category["id"],
            "dataset_mapping_id": mapping["id"],
        },
    )

    assert classification_response.status_code == 400
    assert classification_response.json()["error_code"] == "VALIDATION_ERROR"


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


async def test_knowledge_admin_rejects_file_with_reason(review_client: AsyncClient) -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.audit.models import AuditLog
    from app.modules.document.models import File

    uploader_id = await _create_user(email="reject-uploader@company.com", password="password123")
    await _create_user(
        email="reject-reviewer@company.com",
        password="password123",
        role="knowledge_admin",
    )
    token = await _login(review_client, email="reject-reviewer@company.com", password="password123")
    file_id = await _create_file(uploader_id=uploader_id, status_value="pending_review")

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
        result = await session.execute(select(AuditLog).where(AuditLog.target_id == file_id))
        audit_log = result.scalar_one()
        event_result = await session.execute(
            select(EventOutbox).where(EventOutbox.aggregate_id == str(file_id))
        )
        outbox_event = event_result.scalar_one()

    assert audit_log.action == "file.reject"
    assert audit_log.reason == "文件内容重复且缺少上下文"
    assert outbox_event.event_type == "review.file.rejected"
    assert outbox_event.payload["reason"] == "文件内容重复且缺少上下文"
