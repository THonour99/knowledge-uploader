from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest

from app.tests.safety import require_safe_test_database_reset

pytestmark = pytest.mark.asyncio


async def _reset_database() -> None:
    from importlib import import_module

    require_safe_test_database_reset()
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

    require_safe_test_database_reset()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def _create_system_admin(email: str) -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.security import hash_password
    from app.modules.user.models import User

    async with AsyncSessionFactory() as session:
        session.add(
            User(
                name="Existing Admin",
                email=email,
                email_domain=email.rsplit("@", 1)[1],
                password_hash=hash_password("password123"),
                role="system_admin",
                status="active",
                email_verified=True,
            )
        )
        await session.commit()


async def _create_employee(email: str) -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.security import hash_password
    from app.modules.user.models import User

    async with AsyncSessionFactory() as session:
        session.add(
            User(
                name="Employee",
                email=email,
                email_domain=email.rsplit("@", 1)[1],
                password_hash=hash_password("password123"),
                role="employee",
                status="active",
                email_verified=True,
            )
        )
        await session.commit()


async def test_seed_admin_refuses_when_system_admin_exists_without_force() -> None:
    from scripts.seed_admin import SeedAdminArgs, SeedAdminError, seed_admin
    from sqlalchemy import select

    from app.core.database import AsyncSessionFactory
    from app.modules.user.models import User

    await _create_system_admin("existing-admin@company.com")

    with pytest.raises(SeedAdminError, match="system_admin already exists"):
        await seed_admin(
            SeedAdminArgs(
                email="new-admin@company.com",
                name="New Admin",
                department=None,
                password="password123",
                force_existing_system_admin=False,
            )
        )

    async with AsyncSessionFactory() as session:
        result = await session.execute(select(User).where(User.email == "new-admin@company.com"))
        assert result.scalar_one_or_none() is None


async def test_seed_admin_force_refuses_new_or_non_admin_target() -> None:
    from scripts.seed_admin import SeedAdminArgs, SeedAdminError, seed_admin
    from sqlalchemy import select

    from app.core.database import AsyncSessionFactory
    from app.modules.user.models import User

    await _create_system_admin("existing-admin@company.com")
    await _create_employee("employee@company.com")

    with pytest.raises(SeedAdminError, match="existing system_admin"):
        await seed_admin(
            SeedAdminArgs(
                email="new-admin@company.com",
                name="New Admin",
                department=None,
                password="password123",
                force_existing_system_admin=True,
            )
        )
    with pytest.raises(SeedAdminError, match="existing system_admin"):
        await seed_admin(
            SeedAdminArgs(
                email="employee@company.com",
                name="Employee Admin",
                department=None,
                password="password123",
                force_existing_system_admin=True,
            )
        )

    async with AsyncSessionFactory() as session:
        employee_result = await session.execute(
            select(User).where(User.email == "employee@company.com")
        )
        new_admin_result = await session.execute(
            select(User).where(User.email == "new-admin@company.com")
        )
        employee = employee_result.scalar_one()

    assert employee.role == "employee"
    assert new_admin_result.scalar_one_or_none() is None


async def test_seed_admin_force_recovers_existing_system_admin_with_audit_log() -> None:
    from scripts.seed_admin import SeedAdminArgs, seed_admin
    from sqlalchemy import select

    from app.core.database import AsyncSessionFactory
    from app.modules.audit.models import AuditLog
    from app.modules.user.models import User

    await _create_system_admin("existing-admin@company.com")

    action = await seed_admin(
        SeedAdminArgs(
            email="existing-admin@company.com",
            name="Recovered Admin",
            department="IT",
            password="new-password123",
            force_existing_system_admin=True,
        )
    )

    async with AsyncSessionFactory() as session:
        user_result = await session.execute(
            select(User).where(User.email == "existing-admin@company.com")
        )
        user = user_result.scalar_one()
        audit_result = await session.execute(
            select(AuditLog).where(AuditLog.action == "user.seed_system_admin")
        )
        audit_log = audit_result.scalar_one()

    assert action == "recovered"
    assert user.name == "Recovered Admin"
    assert user.department == "IT"
    assert user.role == "system_admin"
    assert audit_log.reason == "forced system admin recovery"
    assert audit_log.metadata_json["force_existing_system_admin"] is True
    assert audit_log.metadata_json["existing_system_admin_id"] == str(user.id)
    assert audit_log.metadata_json["previous_role"] == "system_admin"
    assert audit_log.metadata_json["previous_status"] == "active"
