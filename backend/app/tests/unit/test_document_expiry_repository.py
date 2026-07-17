from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from importlib import import_module
from uuid import UUID, uuid4

import pytest
from redis.asyncio import from_url
from sqlalchemy import select

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


async def _create_user() -> UUID:
    from app.core.database import AsyncSessionFactory
    from app.core.security import hash_password
    from app.modules.user.models import User

    user = User(
        name=f"expiry-{uuid4().hex[:8]}",
        email=f"expiry-{uuid4().hex[:8]}@company.com",
        email_domain="company.com",
        password_hash=hash_password("password123"),
        role="employee",
        department="QA",
        status="active",
        email_verified=True,
    )
    async with AsyncSessionFactory() as session:
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user.id


async def _create_file(
    *,
    uploader_id: UUID,
    expires_at: datetime | None,
    status: str = "parsed",
    expiry_status: str = "never",
    expiry_warning_sent_at: datetime | None = None,
    expiry_expired_sent_at: datetime | None = None,
    is_current_version: bool = True,
) -> UUID:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File

    file_id = uuid4()
    file = File(
        id=file_id,
        original_name=f"{file_id}.txt",
        title=f"{file_id}.txt",
        stored_name=f"{file_id}.txt",
        extension="txt",
        mime_type="text/plain",
        size=128,
        hash=uuid4().hex + uuid4().hex,
        storage_type="minio",
        bucket="knowledge-files",
        object_key=f"uploads/{uploader_id}/{file_id}.txt",
        uploader_id=uploader_id,
        department="QA",
        visibility="company",
        description=None,
        tags=[],
        status=status,
        review_status="approved",
        ai_analysis_enabled_at_upload=False,
        expires_at=expires_at,
        expiry_status=expiry_status,
        expiry_warning_sent_at=expiry_warning_sent_at,
        expiry_expired_sent_at=expiry_expired_sent_at,
        is_current_version=is_current_version,
    )
    async with AsyncSessionFactory() as session:
        session.add(file)
        await session.commit()
        await session.refresh(file)
        return file.id


async def test_list_expiry_scan_candidates_filters_and_orders_due_notifications() -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.repository import DocumentRepository  # noqa: TID251

    uploader_id = await _create_user()
    now = datetime(2026, 6, 15, 9, 0, tzinfo=UTC)
    warning_deadline = now + timedelta(days=7)

    never_id = await _create_file(uploader_id=uploader_id, expires_at=None)
    active_id = await _create_file(
        uploader_id=uploader_id,
        expires_at=now + timedelta(days=8),
        expiry_status="active",
    )
    expiring_id = await _create_file(
        uploader_id=uploader_id,
        expires_at=now + timedelta(days=2),
        expiry_status="active",
    )
    await _create_file(
        uploader_id=uploader_id,
        expires_at=now + timedelta(days=3),
        expiry_status="expiring",
        expiry_warning_sent_at=now - timedelta(hours=1),
    )
    expired_id = await _create_file(
        uploader_id=uploader_id,
        expires_at=now - timedelta(days=1),
        expiry_status="expiring",
        expiry_warning_sent_at=now - timedelta(days=2),
    )
    await _create_file(
        uploader_id=uploader_id,
        expires_at=now - timedelta(days=2),
        expiry_status="expired",
        expiry_expired_sent_at=now - timedelta(hours=1),
    )
    await _create_file(
        uploader_id=uploader_id,
        expires_at=now - timedelta(days=3),
        status="deleted",
        expiry_status="expired",
    )
    await _create_file(
        uploader_id=uploader_id,
        expires_at=now - timedelta(days=4),
        status="disabled",
        expiry_status="expired",
    )

    async with AsyncSessionFactory() as session:
        candidates = await DocumentRepository(session).list_expiry_scan_candidates(
            now=now,
            warning_deadline=warning_deadline,
            limit=10,
        )

    assert never_id not in {candidate.file_id for candidate in candidates}
    assert active_id not in {candidate.file_id for candidate in candidates}
    assert [(candidate.file_id, candidate.notification_kind) for candidate in candidates] == [
        (expired_id, "expired"),
        (expiring_id, "warning"),
    ]


async def test_refresh_statuses_and_mark_notification_sent_are_idempotent() -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File
    from app.modules.document.repository import DocumentRepository  # noqa: TID251

    uploader_id = await _create_user()
    now = datetime(2026, 6, 15, 9, 0, tzinfo=UTC)
    warning_deadline = now + timedelta(days=7)
    never_id = await _create_file(uploader_id=uploader_id, expires_at=None)
    active_id = await _create_file(
        uploader_id=uploader_id,
        expires_at=now + timedelta(days=8),
    )
    expiring_id = await _create_file(
        uploader_id=uploader_id,
        expires_at=now + timedelta(days=2),
    )
    expired_id = await _create_file(
        uploader_id=uploader_id,
        expires_at=now - timedelta(minutes=1),
    )

    async with AsyncSessionFactory() as session:
        repository = DocumentRepository(session)
        updated = await repository.refresh_expiry_statuses(
            now=now,
            warning_deadline=warning_deadline,
        )
        await session.commit()

        rows = await session.execute(select(File.id, File.expiry_status))
        statuses = {file_id: status for file_id, status in rows}

        first_warning = await repository.mark_expiry_notification_sent(
            file_id=expiring_id,
            notification_kind="warning",
            expected_expires_at=now + timedelta(days=2),
            now=now,
            warning_deadline=warning_deadline,
            sent_at=now,
        )
        second_warning = await repository.mark_expiry_notification_sent(
            file_id=expiring_id,
            notification_kind="warning",
            expected_expires_at=now + timedelta(days=2),
            now=now,
            warning_deadline=warning_deadline,
            sent_at=now + timedelta(minutes=1),
        )
        first_expired = await repository.mark_expiry_notification_sent(
            file_id=expired_id,
            notification_kind="expired",
            expected_expires_at=now - timedelta(minutes=1),
            now=now,
            warning_deadline=warning_deadline,
            sent_at=now,
        )
        second_expired = await repository.mark_expiry_notification_sent(
            file_id=expired_id,
            notification_kind="expired",
            expected_expires_at=now - timedelta(minutes=1),
            now=now,
            warning_deadline=warning_deadline,
            sent_at=now + timedelta(minutes=1),
        )
        await session.commit()
        expiring_file = await session.get(File, expiring_id)
        expired_file = await session.get(File, expired_id)

    assert updated == 4
    assert statuses[never_id] == "never"
    assert statuses[active_id] == "active"
    assert statuses[expiring_id] == "expiring"
    assert statuses[expired_id] == "expired"
    assert first_warning is True
    assert second_warning is False
    assert first_expired is True
    assert second_expired is False
    assert expiring_file is not None
    assert expiring_file.expiry_warning_sent_at == now
    assert expired_file is not None
    assert expired_file.expiry_expired_sent_at == now


async def test_expiry_scan_cas_skips_patch_archive_and_historical_version_races() -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File
    from app.modules.document.repository import DocumentRepository  # noqa: TID251

    uploader_id = await _create_user()
    now = datetime(2026, 6, 15, 9, 0, tzinfo=UTC)
    warning_deadline = now + timedelta(days=7)
    scanned_expires_at = now + timedelta(days=2)
    patched_expires_at = now + timedelta(days=3)
    patched_id = await _create_file(
        uploader_id=uploader_id,
        expires_at=scanned_expires_at,
        expiry_status="expiring",
    )
    archived_id = await _create_file(
        uploader_id=uploader_id,
        expires_at=scanned_expires_at,
        expiry_status="expiring",
    )
    historical_id = await _create_file(
        uploader_id=uploader_id,
        expires_at=scanned_expires_at,
        expiry_status="never",
        is_current_version=False,
    )

    async with AsyncSessionFactory() as session:
        repository = DocumentRepository(session)
        candidates = await repository.list_expiry_scan_candidates(
            now=now,
            warning_deadline=warning_deadline,
            limit=10,
        )
        assert {candidate.file_id for candidate in candidates} == {patched_id, archived_id}

        patched = await session.get(File, patched_id)
        archived = await session.get(File, archived_id)
        historical = await session.get(File, historical_id)
        assert patched is not None and archived is not None and historical is not None
        patched.expires_at = patched_expires_at
        patched.expiry_status = "expiring"
        archived.status = "disabled"
        await session.flush()

        patched_accepted = await repository.mark_expiry_notification_sent(
            file_id=patched_id,
            notification_kind="warning",
            expected_expires_at=scanned_expires_at,
            now=now,
            warning_deadline=warning_deadline,
            sent_at=now,
        )
        archived_accepted = await repository.mark_expiry_notification_sent(
            file_id=archived_id,
            notification_kind="warning",
            expected_expires_at=scanned_expires_at,
            now=now,
            warning_deadline=warning_deadline,
            sent_at=now,
        )
        historical_accepted = await repository.mark_expiry_notification_sent(
            file_id=historical_id,
            notification_kind="warning",
            expected_expires_at=scanned_expires_at,
            now=now,
            warning_deadline=warning_deadline,
            sent_at=now,
        )
        refreshed = await repository.refresh_expiry_statuses(
            now=now,
            warning_deadline=warning_deadline,
        )
        await session.commit()

        patched = await session.get(File, patched_id)
        archived = await session.get(File, archived_id)
        historical = await session.get(File, historical_id)

    assert patched_accepted is False
    assert archived_accepted is False
    assert historical_accepted is False
    assert refreshed == 1
    assert patched is not None and patched.expiry_warning_sent_at is None
    assert archived is not None and archived.expiry_warning_sent_at is None
    assert historical is not None
    assert historical.expiry_status == "never"
    assert historical.expiry_warning_sent_at is None
