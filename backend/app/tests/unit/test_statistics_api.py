from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from importlib import import_module
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from redis.asyncio import from_url
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


async def _reset_database() -> None:
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
async def statistics_client() -> AsyncGenerator[AsyncClient, None]:
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
    name: str,
    department: str | None,
    role: str = "employee",
) -> UUID:
    from app.core.database import AsyncSessionFactory
    from app.core.security import hash_password
    from app.modules.user.models import User

    normalized_email = email.lower()
    user = User(
        name=name,
        email=normalized_email,
        email_domain=normalized_email.rsplit("@", 1)[1],
        password_hash=hash_password(password),
        role=role,
        department=department,
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


async def _create_category(*, name: str, code: str) -> UUID:
    from app.core.database import AsyncSessionFactory
    from app.modules.review.models import Category

    category = Category(name=name, code=code, default_visibility="company")
    async with AsyncSessionFactory() as session:
        session.add(category)
        await session.commit()
        await session.refresh(category)
        return category.id


async def _create_file(
    *,
    uploader_id: UUID,
    category_id: UUID,
    department: str,
    status_value: str,
    review_status: str,
    size: int,
    uploaded_at: datetime,
    hash_value: str,
    last_sync_at: datetime | None = None,
) -> UUID:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File

    file = File(
        original_name=f"{hash_value[:8]}.txt",
        stored_name=f"{hash_value[:8]}.txt",
        extension="txt",
        mime_type="text/plain",
        size=size,
        hash=hash_value,
        storage_type="minio",
        bucket="knowledge-files",
        object_key=f"uploads/{uploader_id}/{hash_value[:8]}.txt",
        uploader_id=uploader_id,
        department=department,
        category_id=category_id,
        visibility="company",
        description="statistics fixture",
        tags=[],
        status=status_value,
        review_status=review_status,
        ai_analysis_enabled_at_upload=False,
        uploaded_at=uploaded_at,
        last_sync_at=last_sync_at,
    )
    async with AsyncSessionFactory() as session:
        session.add(file)
        await session.commit()
        await session.refresh(file)
        return file.id


async def _seed_statistics_fixture() -> dict[str, UUID]:
    from app.core.database import AsyncSessionFactory
    from app.modules.ai.models import DocumentAnalysis
    from app.modules.ragflow.models import SyncTask

    admin_id = await _create_user(
        email="stats-admin@company.com",
        password="password123",
        name="统计管理员",
        department="运营部",
        role="system_admin",
    )
    employee_id = await _create_user(
        email="stats-employee@company.com",
        password="password123",
        name="普通员工",
        department="研发中心",
    )
    user_a_id = await _create_user(
        email="li-ming@company.com",
        password="password123",
        name="李明",
        department="研发中心",
    )
    user_b_id = await _create_user(
        email="wang-fang@company.com",
        password="password123",
        name="王芳",
        department="产品部",
    )
    tech_id = await _create_category(name="技术文档", code="tech-doc")
    product_id = await _create_category(name="产品文档", code="product-doc")
    synced_file_id = await _create_file(
        uploader_id=user_a_id,
        category_id=tech_id,
        department="研发中心",
        status_value="parsed",
        review_status="approved",
        size=1_000,
        uploaded_at=datetime(2026, 6, 1, 9, 0, tzinfo=UTC),
        last_sync_at=datetime(2026, 6, 1, 10, 0, tzinfo=UTC),
        hash_value="1" * 64,
    )
    pending_file_id = await _create_file(
        uploader_id=user_a_id,
        category_id=tech_id,
        department="研发中心",
        status_value="pending_review",
        review_status="pending",
        size=2_000,
        uploaded_at=datetime(2026, 6, 2, 9, 0, tzinfo=UTC),
        hash_value="2" * 64,
    )
    failed_file_id = await _create_file(
        uploader_id=user_b_id,
        category_id=product_id,
        department="产品部",
        status_value="failed",
        review_status="approved",
        size=3_000,
        uploaded_at=datetime(2026, 6, 3, 9, 0, tzinfo=UTC),
        hash_value="3" * 64,
    )
    rejected_file_id = await _create_file(
        uploader_id=user_b_id,
        category_id=product_id,
        department="产品部",
        status_value="rejected",
        review_status="rejected",
        size=4_000,
        uploaded_at=datetime(2026, 6, 4, 9, 0, tzinfo=UTC),
        hash_value="4" * 64,
    )
    async with AsyncSessionFactory() as session:
        session.add(
            DocumentAnalysis(
                file_id=pending_file_id,
                status="succeeded",
                sensitive_risk_level="high",
                sensitive_hits=[{"rule_name": "测试敏感项", "risk_level": "high"}],
            )
        )
        session.add(
            SyncTask(
                file_id=failed_file_id,
                task_type="ragflow_upload",
                status="failed",
                error_message="RuntimeError",
                started_at=datetime(2026, 6, 3, 10, 0, tzinfo=UTC),
                finished_at=datetime(2026, 6, 3, 10, 5, tzinfo=UTC),
            )
        )
        await session.commit()
    return {
        "admin_id": admin_id,
        "employee_id": employee_id,
        "user_a_id": user_a_id,
        "user_b_id": user_b_id,
        "tech_id": tech_id,
        "product_id": product_id,
        "synced_file_id": synced_file_id,
        "pending_file_id": pending_file_id,
        "failed_file_id": failed_file_id,
        "rejected_file_id": rejected_file_id,
    }


async def test_admin_reads_overview_users_departments_categories_and_trends(
    statistics_client: AsyncClient,
) -> None:
    ids = await _seed_statistics_fixture()
    token = await _login(
        statistics_client,
        email="stats-admin@company.com",
        password="password123",
    )
    headers = {"Authorization": f"Bearer {token}"}

    overview_response = await statistics_client.get(
        "/api/admin/statistics/overview",
        headers=headers,
        params={"start_date": "2026-06-01", "end_date": "2026-06-04"},
    )
    users_response = await statistics_client.get(
        "/api/admin/statistics/users",
        headers=headers,
        params={"department": "研发中心", "page_size": 5},
    )
    departments_response = await statistics_client.get(
        "/api/admin/statistics/departments",
        headers=headers,
    )
    categories_response = await statistics_client.get(
        "/api/admin/statistics/categories",
        headers=headers,
    )
    trends_response = await statistics_client.get(
        "/api/admin/statistics/trends",
        headers=headers,
        params={
            "start_date": "2026-06-01",
            "end_date": "2026-06-04",
            "group_by": "day",
        },
    )
    user_detail_response = await statistics_client.get(
        f"/api/admin/statistics/users/{ids['user_a_id']}",
        headers=headers,
    )

    assert overview_response.status_code == 200
    overview = overview_response.json()["data"]
    assert overview["total_files"] == 4
    assert overview["active_uploaders"] == 2
    assert overview["synced_files"] == 1
    assert overview["pending_review_files"] == 1
    assert overview["failed_files"] == 1
    assert overview["failed_tasks"] == 1
    assert overview["rejected_files"] == 1
    assert overview["sensitive_files"] == 1
    assert overview["total_file_size"] == 10_000
    assert overview["sync_success_rate"] == pytest.approx(0.5)

    assert users_response.status_code == 200
    users = users_response.json()["data"]
    assert users["total"] == 1
    user = users["items"][0]
    assert user["user_name"] == "李明"
    assert user["department"] == "研发中心"
    assert user["total_files"] == 2
    assert user["synced_files"] == 1
    assert user["pending_review_files"] == 1
    assert user["total_file_size"] == 3_000
    assert user["last_success_sync_at"].startswith("2026-06-01T10:00:00")

    assert departments_response.status_code == 200
    departments = departments_response.json()["data"]["items"]
    assert departments[0]["department"] == "产品部"
    assert departments[0]["total_files"] == 2
    assert {item["department"] for item in departments} == {"研发中心", "产品部"}

    assert categories_response.status_code == 200
    categories = categories_response.json()["data"]["items"]
    category_by_name = {item["category_name"]: item for item in categories}
    assert category_by_name["技术文档"]["total_files"] == 2
    assert category_by_name["产品文档"]["failed_files"] == 1

    assert trends_response.status_code == 200
    trends = trends_response.json()["data"]
    assert trends["group_by"] == "day"
    assert [point["period"] for point in trends["items"]] == [
        "2026-06-01",
        "2026-06-02",
        "2026-06-03",
        "2026-06-04",
    ]
    assert [point["total_files"] for point in trends["items"]] == [1, 1, 1, 1]

    assert user_detail_response.status_code == 200
    user_detail = user_detail_response.json()["data"]
    assert user_detail["user"]["user_id"] == str(ids["user_a_id"])
    assert user_detail["category_breakdown"][0]["category_name"] == "技术文档"


async def test_statistics_failures_export_and_permission(
    statistics_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.audit.models import AuditLog

    await _seed_statistics_fixture()
    admin_token = await _login(
        statistics_client,
        email="stats-admin@company.com",
        password="password123",
    )
    employee_token = await _login(
        statistics_client,
        email="stats-employee@company.com",
        password="password123",
    )
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    denied_response = await statistics_client.get(
        "/api/admin/statistics/overview",
        headers={"Authorization": f"Bearer {employee_token}"},
    )
    failures_response = await statistics_client.get(
        "/api/admin/statistics/failures",
        headers=admin_headers,
    )
    export_response = await statistics_client.get(
        "/api/admin/statistics/export",
        headers=admin_headers,
        params={"department": "研发中心"},
    )

    assert denied_response.status_code == 403
    assert denied_response.json()["error_code"] == "PERMISSION_DENIED"

    assert failures_response.status_code == 200
    failures = failures_response.json()["data"]
    assert failures["total"] == 1
    assert failures["items"][0]["reason"] == "RuntimeError"
    assert failures["items"][0]["failed_tasks"] == 1

    assert export_response.status_code == 200
    assert export_response.headers["content-type"].startswith("text/csv")
    assert "attachment;" in export_response.headers["content-disposition"]
    assert "用户,部门,上传文件总数" in export_response.text
    assert "李明,研发中心,2" in export_response.text
    assert "王芳,产品部" not in export_response.text

    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(AuditLog).where(AuditLog.action == "statistics.export")
        )
        audit_log = result.scalar_one()
        assert audit_log.target_type == "statistics"
        assert audit_log.metadata_json["department"] == "研发中心"
