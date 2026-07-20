from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from importlib import import_module

import pytest

from app.modules.user.schemas import AuthUserRecord

pytestmark = pytest.mark.asyncio


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


async def _department(name: str) -> uuid.UUID:
    from app.core.database import AsyncSessionFactory
    from app.modules.department.models import Department

    item = Department(name=name, code=f"dept-{uuid.uuid4().hex[:8]}")
    async with AsyncSessionFactory() as session:
        session.add(item)
        await session.commit()
        return item.id


async def _user(email: str, *, department_id: uuid.UUID, role: str = "employee") -> uuid.UUID:
    from app.core.database import AsyncSessionFactory
    from app.core.security import hash_password
    from app.modules.user.models import User

    item = User(
        name=email.split("@", maxsplit=1)[0],
        email=email,
        email_domain=email.rsplit("@", maxsplit=1)[1],
        password_hash=hash_password("password123"),
        department_id=department_id,
        role=role,
        status="active",
        email_verified=True,
    )
    async with AsyncSessionFactory() as session:
        session.add(item)
        await session.commit()
        return item.id


def _identity(
    user_id: uuid.UUID,
    *,
    email: str,
    department_id: uuid.UUID,
    role: str = "employee",
    managed_department_ids: list[uuid.UUID] | None = None,
) -> AuthUserRecord:
    return AuthUserRecord(
        id=user_id,
        name="Test User",
        email=email,
        email_domain=email.rsplit("@", maxsplit=1)[1],
        password_hash="unused",
        role=role,
        status="active",
        email_verified=True,
        department_id=department_id,
        department_name=None,
        department_code=None,
        department=None,
        phone=None,
        failed_login_count=0,
        locked_until=None,
        session_version=0,
        managed_department_ids=managed_department_ids or [],
    )


async def test_department_audience_includes_managed_admin_and_hides_out_of_scope() -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.announcement.exceptions import AnnouncementNotFoundError
    from app.modules.announcement.models import Announcement, AnnouncementDepartment
    from app.modules.announcement.repository import AnnouncementRepository
    from app.modules.announcement.service import AnnouncementService

    target_department = await _department("目标部门")
    other_department = await _department("其他部门")
    employee_id = await _user("target@company.com", department_id=target_department)
    admin_id = await _user("manager@company.com", department_id=other_department, role="dept_admin")
    outsider_id = await _user("outside@company.com", department_id=other_department)
    actor_id = await _user(
        "author@company.com", department_id=other_department, role="system_admin"
    )
    now = datetime.now(UTC)

    async with AsyncSessionFactory() as session:
        item = Announcement(
            title="部门公告",
            body_markdown="# 部门内容",
            audience_type="departments",
            lifecycle_state="released",
            visible_from=now - timedelta(minutes=1),
            expires_at=now + timedelta(days=1),
            is_pinned=True,
            created_by=actor_id,
            updated_by=actor_id,
            published_by=actor_id,
            published_at=now,
            departments=[AnnouncementDepartment(department_id=target_department)],
            roles=[],
        )
        session.add(item)
        await session.commit()
        service = AnnouncementService(session=session, repository=AnnouncementRepository(session))

        employee = _identity(
            employee_id, email="target@company.com", department_id=target_department
        )
        managed_admin = _identity(
            admin_id,
            email="manager@company.com",
            department_id=other_department,
            role="dept_admin",
            managed_department_ids=[target_department],
        )
        outsider = _identity(
            outsider_id, email="outside@company.com", department_id=other_department
        )

        assert (
            await service.get_public(announcement_id=item.id, current_user=employee)
        ).id == item.id
        assert (
            await service.get_public(announcement_id=item.id, current_user=managed_admin)
        ).id == item.id
        with pytest.raises(AnnouncementNotFoundError):
            await service.get_public(announcement_id=item.id, current_user=outsider)


async def test_read_is_idempotent_and_expired_item_remains_in_history() -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.announcement.models import Announcement
    from app.modules.announcement.repository import AnnouncementRepository
    from app.modules.announcement.service import AnnouncementService

    department_id = await _department("通用部门")
    user_id = await _user("reader@company.com", department_id=department_id)
    actor_id = await _user(
        "publisher@company.com", department_id=department_id, role="system_admin"
    )
    now = datetime.now(UTC)
    identity = _identity(user_id, email="reader@company.com", department_id=department_id)

    async with AsyncSessionFactory() as session:
        item = Announcement(
            title="历史公告",
            body_markdown="正文",
            audience_type="all",
            lifecycle_state="released",
            visible_from=now - timedelta(days=2),
            expires_at=now - timedelta(days=1),
            is_pinned=False,
            created_by=actor_id,
            updated_by=actor_id,
            published_by=actor_id,
            published_at=now - timedelta(days=2),
            departments=[],
            roles=[],
        )
        session.add(item)
        await session.commit()
        service = AnnouncementService(session=session, repository=AnnouncementRepository(session))
        first = await service.mark_read(announcement_id=item.id, current_user=identity)
        second = await service.mark_read(announcement_id=item.id, current_user=identity)
        assert first.read_at == second.read_at

        history = await service.list_public(
            current_user=identity,
            state="expired",
            unread_only=False,
            page=1,
            page_size=20,
        )
        assert history.total == 1
        assert history.items[0].state == "expired"
        assert history.items[0].is_read is True
        assert history.unread_count == 0


async def test_stats_use_current_active_role_audience() -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.announcement.models import Announcement, AnnouncementRead, AnnouncementRole
    from app.modules.announcement.repository import AnnouncementRepository

    department_id = await _department("角色部门")
    employee_id = await _user("employee@company.com", department_id=department_id)
    await _user("admin@company.com", department_id=department_id, role="dept_admin")
    actor_id = await _user("system@company.com", department_id=department_id, role="system_admin")
    now = datetime.now(UTC)

    async with AsyncSessionFactory() as session:
        item = Announcement(
            title="员工公告",
            body_markdown="正文",
            audience_type="roles",
            lifecycle_state="released",
            visible_from=now,
            expires_at=None,
            is_pinned=False,
            created_by=actor_id,
            updated_by=actor_id,
            published_by=actor_id,
            published_at=now,
            departments=[],
            roles=[AnnouncementRole(role="employee")],
        )
        session.add(item)
        await session.flush()
        session.add(AnnouncementRead(announcement_id=item.id, user_id=employee_id, read_at=now))
        await session.commit()

        target_count, read_count = await AnnouncementRepository(session).stats(item)
        assert target_count == 1
        assert read_count == 1


async def test_clone_consumes_source_version_and_rejects_duplicate_request() -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.announcement.exceptions import AnnouncementConflictError
    from app.modules.announcement.models import Announcement
    from app.modules.announcement.repository import AnnouncementRepository
    from app.modules.announcement.service import AnnouncementService, RequestAuditContext

    department_id = await _department("复制公告部门")
    actor_id = await _user(
        "clone-admin@company.com", department_id=department_id, role="system_admin"
    )
    actor = _identity(
        actor_id,
        email="clone-admin@company.com",
        department_id=department_id,
        role="system_admin",
    )

    async with AsyncSessionFactory() as session:
        source = Announcement(
            title="待复制公告",
            body_markdown="正文",
            audience_type="all",
            lifecycle_state="draft",
            visible_from=None,
            expires_at=None,
            is_pinned=False,
            created_by=actor_id,
            updated_by=actor_id,
            departments=[],
            roles=[],
        )
        session.add(source)
        await session.commit()
        service = AnnouncementService(session=session, repository=AnnouncementRepository(session))
        audit = RequestAuditContext(ip_address="127.0.0.1", user_agent="pytest")

        clone = await service.clone(
            announcement_id=source.id,
            row_version=1,
            actor=actor,
            audit=audit,
        )
        assert clone.title == "待复制公告 (副本)"

        with pytest.raises(AnnouncementConflictError):
            await service.clone(
                announcement_id=source.id,
                row_version=1,
                actor=actor,
                audit=audit,
            )
