from __future__ import annotations

import importlib.util
import os
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from redis.asyncio import from_url
from sqlalchemy import delete, select
from sqlalchemy import text as sql_text

pytestmark = pytest.mark.asyncio

TAGS_MIGRATION_FILENAME = "d4e7a9b2c5f8_add_tags_tables.py"


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
async def tag_client() -> AsyncGenerator[AsyncClient, None]:
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


async def _create_file(
    *,
    uploader_id: UUID,
    tags: list[str] | None = None,
    extension: str = "pdf",
    status: str = "uploaded",
) -> UUID:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File

    unique = uuid.uuid4().hex
    mime_type = "application/pdf" if extension == "pdf" else "text/plain"
    submitted_at = datetime.now(UTC) if status == "pending_review" else None
    file = File(
        original_name=f"doc-{unique}.{extension}",
        title=f"doc-{unique}.{extension}",
        stored_name=f"file-{unique}.{extension}",
        extension=extension,
        mime_type=mime_type,
        size=128,
        hash=unique * 2,
        storage_type="minio",
        bucket="knowledge-files",
        object_key=f"uploads/{uploader_id}/file-{unique}.{extension}",
        uploader_id=uploader_id,
        department="QA",
        visibility="private",
        description="tag target",
        tags=tags or [],
        status=status,
        review_status="pending",
        submitted_at=submitted_at,
        review_due_at=(
            submitted_at + timedelta(hours=24) if submitted_at is not None else None
        ),
        ai_analysis_enabled_at_upload=False,
    )
    async with AsyncSessionFactory() as session:
        session.add(file)
        await session.commit()
        await session.refresh(file)
        return file.id


async def _create_tag(
    *,
    name: str,
    enabled: bool = True,
    is_system_generated: bool = False,
    usage_count: int = 0,
) -> UUID:
    from app.core.database import AsyncSessionFactory
    from app.modules.review.models import Tag

    tag = Tag(
        name=name,
        description=None,
        is_system_generated=is_system_generated,
        enabled=enabled,
        usage_count=usage_count,
    )
    async with AsyncSessionFactory() as session:
        session.add(tag)
        await session.commit()
        await session.refresh(tag)
        return tag.id


async def _link_file_tag(file_id: UUID, tag_id: UUID) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.review.models import FileTag

    async with AsyncSessionFactory() as session:
        session.add(FileTag(file_id=file_id, tag_id=tag_id))
        await session.commit()


async def _unlink_file_tag(file_id: UUID, tag_id: UUID) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.review.models import FileTag

    async with AsyncSessionFactory() as session:
        await session.execute(
            delete(FileTag).where(FileTag.file_id == file_id, FileTag.tag_id == tag_id)
        )
        await session.commit()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _system_admin_token(client: AsyncClient, *, email: str) -> str:
    await _create_user(email=email, password="password123", role="system_admin")
    return await _login(client, email=email, password="password123")


async def test_system_admin_tag_crud_lifecycle_writes_audit_logs(
    tag_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.audit.models import AuditLog

    token = await _system_admin_token(tag_client, email="tag-admin@company.com")

    create_response = await tag_client.post(
        "/api/tags",
        headers=_auth(token),
        json={"name": "合同", "description": "合同相关文档"},
    )

    assert create_response.status_code == 201
    created = create_response.json()["data"]
    assert created["name"] == "合同"
    assert created["description"] == "合同相关文档"
    assert created["usage_count"] == 0
    assert created["is_system_generated"] is False
    assert created["enabled"] is True

    list_response = await tag_client.get("/api/tags", headers=_auth(token))

    assert list_response.status_code == 200
    listed = list_response.json()["data"]
    assert listed["total"] == 1
    assert listed["page"] == 1
    assert listed["page_size"] == 50
    assert listed["items"][0]["id"] == created["id"]

    update_response = await tag_client.patch(
        f"/api/tags/{created['id']}",
        headers=_auth(token),
        json={"name": "合同文档", "description": None, "enabled": False},
    )

    assert update_response.status_code == 200
    updated = update_response.json()["data"]
    assert updated["name"] == "合同文档"
    assert updated["description"] is None
    assert updated["enabled"] is False

    delete_response = await tag_client.delete(
        f"/api/tags/{created['id']}",
        headers=_auth(token),
    )

    assert delete_response.status_code == 200
    assert delete_response.json()["data"] == {}

    empty_list_response = await tag_client.get("/api/tags", headers=_auth(token))
    assert empty_list_response.json()["data"]["total"] == 0

    async with AsyncSessionFactory() as session:
        result = await session.execute(select(AuditLog.action).where(AuditLog.action.like("tag.%")))
        actions = {row[0] for row in result}
    assert {"tag.create", "tag.update", "tag.delete", "tag.list"} <= actions


async def test_create_tag_with_duplicate_name_returns_conflict(tag_client: AsyncClient) -> None:
    token = await _system_admin_token(tag_client, email="tag-dup-admin@company.com")

    first = await tag_client.post("/api/tags", headers=_auth(token), json={"name": "法务"})
    second = await tag_client.post("/api/tags", headers=_auth(token), json={"name": "法务"})

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["error_code"] == "VALIDATION_ERROR"


async def test_rename_tag_to_existing_name_returns_conflict(tag_client: AsyncClient) -> None:
    token = await _system_admin_token(tag_client, email="tag-rename-admin@company.com")
    await _create_tag(name="制度")
    other_id = await _create_tag(name="流程")

    response = await tag_client.patch(
        f"/api/tags/{other_id}",
        headers=_auth(token),
        json={"name": "制度"},
    )

    assert response.status_code == 409
    assert response.json()["error_code"] == "VALIDATION_ERROR"


async def test_tag_list_supports_search_enabled_filter_and_pagination(
    tag_client: AsyncClient,
) -> None:
    token = await _system_admin_token(tag_client, email="tag-list-admin@company.com")
    await _create_tag(name="产品手册")
    await _create_tag(name="产品规格")
    await _create_tag(name="售后政策", enabled=False)

    search_response = await tag_client.get(
        "/api/tags",
        headers=_auth(token),
        params={"search": "产品"},
    )
    enabled_response = await tag_client.get(
        "/api/tags",
        headers=_auth(token),
        params={"enabled": "false"},
    )
    paged_response = await tag_client.get(
        "/api/tags",
        headers=_auth(token),
        params={"page": 2, "page_size": 2},
    )

    assert search_response.status_code == 200
    search_data = search_response.json()["data"]
    assert search_data["total"] == 2
    assert {item["name"] for item in search_data["items"]} == {"产品手册", "产品规格"}

    assert enabled_response.status_code == 200
    enabled_data = enabled_response.json()["data"]
    assert enabled_data["total"] == 1
    assert enabled_data["items"][0]["name"] == "售后政策"

    assert paged_response.status_code == 200
    paged_data = paged_response.json()["data"]
    assert paged_data["total"] == 3
    assert paged_data["page"] == 2
    assert paged_data["page_size"] == 2
    assert len(paged_data["items"]) == 1


async def test_tag_search_treats_percent_and_underscore_as_literals(
    tag_client: AsyncClient,
) -> None:
    token = await _system_admin_token(tag_client, email="tag-literal-admin@company.com")
    await _create_tag(name="预算 100%_最终版")
    await _create_tag(name="预算 100AX最终版")

    response = await tag_client.get(
        "/api/tags",
        headers=_auth(token),
        params={"search": "%_"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 1
    assert [item["name"] for item in data["items"]] == ["预算 100%_最终版"]


async def test_usage_count_reflects_file_tag_associations(tag_client: AsyncClient) -> None:
    token = await _system_admin_token(tag_client, email="tag-usage-admin@company.com")
    uploader_id = await _create_user(email="tag-usage-user@company.com", password="password123")
    tag_id = await _create_tag(name="培训")
    file_a = await _create_file(uploader_id=uploader_id)
    file_b = await _create_file(uploader_id=uploader_id)

    await _link_file_tag(file_a, tag_id)
    await _link_file_tag(file_b, tag_id)
    linked_response = await tag_client.get("/api/tags", headers=_auth(token))

    await _unlink_file_tag(file_b, tag_id)
    unlinked_response = await tag_client.get("/api/tags", headers=_auth(token))

    assert linked_response.json()["data"]["items"][0]["usage_count"] == 2
    assert unlinked_response.json()["data"]["items"][0]["usage_count"] == 1


async def test_merge_moves_associations_dedupes_and_updates_usage_count(
    tag_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.audit.models import AuditLog
    from app.modules.review.models import FileTag, Tag

    token = await _system_admin_token(tag_client, email="tag-merge-admin@company.com")
    uploader_id = await _create_user(email="tag-merge-user@company.com", password="password123")
    source_id = await _create_tag(name="售后", usage_count=2)
    target_id = await _create_tag(name="售后服务", usage_count=1)
    file_a = await _create_file(uploader_id=uploader_id)
    file_b = await _create_file(uploader_id=uploader_id)
    await _link_file_tag(file_a, source_id)
    await _link_file_tag(file_b, source_id)
    await _link_file_tag(file_b, target_id)

    response = await tag_client.post(
        f"/api/tags/{source_id}/merge",
        headers=_auth(token),
        json={"target_tag_id": str(target_id)},
    )

    assert response.status_code == 200
    merged = response.json()["data"]
    assert merged["id"] == str(target_id)
    assert merged["name"] == "售后服务"
    assert merged["usage_count"] == 2

    async with AsyncSessionFactory() as session:
        source_tag = await session.get(Tag, source_id)
        target_tag = await session.get(Tag, target_id)
        link_result = await session.execute(select(FileTag.file_id, FileTag.tag_id))
        links = {(row[0], row[1]) for row in link_result}
        audit_result = await session.execute(select(AuditLog).where(AuditLog.action == "tag.merge"))
        merge_log = audit_result.scalar_one()

    assert source_tag is None
    assert target_tag is not None
    assert target_tag.usage_count == 2
    assert links == {(file_a, target_id), (file_b, target_id)}
    assert merge_log.target_id == target_id
    assert merge_log.metadata_json["source_tag_id"] == str(source_id)


async def test_merge_rejects_self_merge_and_missing_tags(tag_client: AsyncClient) -> None:
    token = await _system_admin_token(tag_client, email="tag-merge-edge@company.com")
    tag_id = await _create_tag(name="安全")
    missing_id = uuid.uuid4()

    self_response = await tag_client.post(
        f"/api/tags/{tag_id}/merge",
        headers=_auth(token),
        json={"target_tag_id": str(tag_id)},
    )
    missing_source_response = await tag_client.post(
        f"/api/tags/{missing_id}/merge",
        headers=_auth(token),
        json={"target_tag_id": str(tag_id)},
    )
    missing_target_response = await tag_client.post(
        f"/api/tags/{tag_id}/merge",
        headers=_auth(token),
        json={"target_tag_id": str(missing_id)},
    )

    assert self_response.status_code == 400
    assert missing_source_response.status_code == 404
    assert missing_target_response.status_code == 404


async def test_delete_tag_with_associations_returns_conflict(tag_client: AsyncClient) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.review.models import Tag

    token = await _system_admin_token(tag_client, email="tag-delete-admin@company.com")
    uploader_id = await _create_user(email="tag-delete-user@company.com", password="password123")
    tag_id = await _create_tag(name="客服")
    file_id = await _create_file(uploader_id=uploader_id)
    await _link_file_tag(file_id, tag_id)

    response = await tag_client.delete(f"/api/tags/{tag_id}", headers=_auth(token))

    assert response.status_code == 409
    assert response.json()["error_code"] == "VALIDATION_ERROR"
    assert "merge" in response.json()["message"]
    async with AsyncSessionFactory() as session:
        assert await session.get(Tag, tag_id) is not None


async def test_my_files_filters_by_extension_and_tag_id(tag_client: AsyncClient) -> None:
    uploader_id = await _create_user(email="filter-owner@company.com", password="password123")
    token = await _login(tag_client, email="filter-owner@company.com", password="password123")
    tag_id = await _create_tag(name="筛选标签")
    matching_file_id = await _create_file(
        uploader_id=uploader_id,
        extension="pdf",
    )
    other_extension_id = await _create_file(
        uploader_id=uploader_id,
        extension="txt",
    )
    other_tag_id = await _create_file(
        uploader_id=uploader_id,
        extension="pdf",
    )
    await _link_file_tag(matching_file_id, tag_id)

    response = await tag_client.get(
        f"/api/files?extension=pdf&tag_id={tag_id}",
        headers=_auth(token),
    )

    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["data"]["items"]}
    assert ids == {str(matching_file_id)}
    assert str(other_extension_id) not in ids
    assert str(other_tag_id) not in ids


async def test_review_files_filters_by_extension_and_tag_id(tag_client: AsyncClient) -> None:
    await _create_user(
        email="review-filter-admin@company.com",
        password="password123",
        role="system_admin",
    )
    token = await _login(
        tag_client,
        email="review-filter-admin@company.com",
        password="password123",
    )
    uploader_id = await _create_user(
        email="review-filter-owner@company.com",
        password="password123",
    )
    tag_id = await _create_tag(name="管理筛选标签")
    matching_file_id = await _create_file(
        uploader_id=uploader_id,
        extension="pdf",
        status="pending_review",
    )
    other_extension_id = await _create_file(
        uploader_id=uploader_id,
        extension="txt",
        status="pending_review",
    )
    other_tag_id = await _create_file(
        uploader_id=uploader_id,
        extension="pdf",
        status="pending_review",
    )
    await _link_file_tag(matching_file_id, tag_id)

    response = await tag_client.get(
        f"/api/review/files?extension=pdf&tag_id={tag_id}",
        headers=_auth(token),
    )

    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["data"]["items"]}
    assert ids == {str(matching_file_id)}
    assert str(other_extension_id) not in ids
    assert str(other_tag_id) not in ids


async def test_employee_and_dept_admin_tag_permissions(tag_client: AsyncClient) -> None:
    await _create_user(email="tag-employee@company.com", password="password123")
    await _create_user(
        email="tag-knowledge-admin@company.com",
        password="password123",
        role="dept_admin",
    )
    employee_token = await _login(
        tag_client, email="tag-employee@company.com", password="password123"
    )
    knowledge_token = await _login(
        tag_client, email="tag-knowledge-admin@company.com", password="password123"
    )
    tag_id = await _create_tag(name="权限标签")
    other_id = await _create_tag(name="权限目标")

    employee_list = await tag_client.get("/api/tags", headers=_auth(employee_token))
    knowledge_list = await tag_client.get("/api/tags", headers=_auth(knowledge_token))

    assert employee_list.status_code == 200
    assert str(tag_id) in {item["id"] for item in employee_list.json()["data"]["items"]}
    assert knowledge_list.status_code == 200

    for token in (employee_token, knowledge_token):
        create_response = await tag_client.post(
            "/api/tags", headers=_auth(token), json={"name": "新标签"}
        )
        update_response = await tag_client.patch(
            f"/api/tags/{tag_id}", headers=_auth(token), json={"enabled": False}
        )
        merge_response = await tag_client.post(
            f"/api/tags/{tag_id}/merge",
            headers=_auth(token),
            json={"target_tag_id": str(other_id)},
        )
        delete_response = await tag_client.delete(f"/api/tags/{tag_id}", headers=_auth(token))
        responses = [create_response, update_response, merge_response, delete_response]
        assert [item.status_code for item in responses] == [403, 403, 403, 403]
        assert {item.json()["error_code"] for item in responses} == {"PERMISSION_DENIED"}


async def test_create_tag_rejects_blank_name(tag_client: AsyncClient) -> None:
    token = await _system_admin_token(tag_client, email="tag-blank-admin@company.com")

    response = await tag_client.post("/api/tags", headers=_auth(token), json={"name": "   "})

    assert response.status_code == 400
    assert response.json()["error_code"] == "VALIDATION_ERROR"


def _load_tags_migration() -> ModuleType:
    import app

    migration_path = (
        Path(app.__file__).resolve().parent
        / "db"
        / "migrations"
        / "versions"
        / TAGS_MIGRATION_FILENAME
    )
    spec = importlib.util.spec_from_file_location("tags_migration_module", migration_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _run_backfill_statements(statements: tuple[str, ...]) -> None:
    from app.core.database import engine

    async with engine.begin() as connection:
        for statement in statements:
            await connection.execute(sql_text(statement))


async def _tags_state_snapshot() -> (
    tuple[
        dict[str, tuple[bool, bool, int]],
        set[tuple[UUID, str]],
    ]
):
    from app.core.database import AsyncSessionFactory
    from app.modules.review.models import FileTag, Tag

    async with AsyncSessionFactory() as session:
        tag_result = await session.execute(select(Tag))
        tags = list(tag_result.scalars())
        tag_names = {tag.id: tag.name for tag in tags}
        link_result = await session.execute(select(FileTag.file_id, FileTag.tag_id))
        links = {(row[0], tag_names[row[1]]) for row in link_result}
    snapshot = {tag.name: (tag.is_system_generated, tag.enabled, tag.usage_count) for tag in tags}
    return snapshot, links


async def test_backfill_statements_populate_tags_and_are_idempotent() -> None:
    uploader_id = await _create_user(
        email="tag-backfill-user@company.com",
        password="password123",
    )
    file_a = await _create_file(uploader_id=uploader_id, tags=["合同", "法务", " 合同 "])
    file_b = await _create_file(uploader_id=uploader_id, tags=["合同"])
    await _create_file(uploader_id=uploader_id, tags=[])
    migration = _load_tags_migration()
    statements: tuple[str, ...] = migration.BACKFILL_STATEMENTS

    await _run_backfill_statements(statements)
    first_snapshot, first_links = await _tags_state_snapshot()
    await _run_backfill_statements(statements)
    second_snapshot, second_links = await _tags_state_snapshot()

    assert first_snapshot == {
        "合同": (True, True, 2),
        "法务": (True, True, 1),
    }
    assert first_links == {
        (file_a, "合同"),
        (file_a, "法务"),
        (file_b, "合同"),
    }
    assert second_snapshot == first_snapshot
    assert second_links == first_links
