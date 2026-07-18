from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from importlib import import_module
from types import SimpleNamespace
from typing import TYPE_CHECKING
from uuid import UUID

import pytest

from app.tests.safety import require_safe_test_database_reset

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.modules.document.models import File

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


async def test_operational_metrics_module_declares_every_consumed_file_column() -> None:
    from app.workers import operational_metrics

    assert {
        "status",
        "id",
        "review_status",
        "review_due_at",
        "size",
        "storage_type",
        "ragflow_parse_status",
        "ragflow_error_message",
        "last_sync_at",
    } <= set(operational_metrics._FILES.c.keys())


async def test_polling_hops_count_as_one_successful_document_outcome() -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ragflow.models import SyncTask
    from app.workers.operational_metrics import _collect_ragflow_outcome_counts

    now = datetime.now(UTC)
    async with AsyncSessionFactory() as session:
        user_id, department_id = await _owner(session, suffix="polling")
        file = _file(
            uploader_id=user_id,
            department_id=department_id,
            hash_value="a" * 64,
            status="parsed",
            parse_status="DONE",
            ragflow_error=None,
            last_sync_at=now,
        )
        session.add(file)
        await session.flush()
        for retry_count in range(4):
            session.add(
                SyncTask(
                    file_id=file.id,
                    task_type="ragflow_status_check",
                    status="succeeded",
                    retry_count=retry_count,
                    max_retry_count=20,
                    finished_at=now,
                )
            )
        await session.commit()

        counts = await _collect_ragflow_outcome_counts(
            session,
            window_start=now - timedelta(minutes=15),
        )

    assert counts == {"succeeded": 1, "failed": 0, "canceled": 0}


async def test_later_ai_failure_is_not_attributed_to_ragflow() -> None:
    from app.core.database import AsyncSessionFactory
    from app.workers.operational_metrics import _collect_ragflow_outcome_counts

    now = datetime.now(UTC)
    async with AsyncSessionFactory() as session:
        user_id, department_id = await _owner(session, suffix="ai-isolation")
        session.add(
            _file(
                uploader_id=user_id,
                department_id=department_id,
                hash_value="b" * 64,
                status="analysis_failed",
                parse_status="DONE",
                ragflow_error=None,
                last_sync_at=now,
            )
        )
        await session.commit()

        counts = await _collect_ragflow_outcome_counts(
            session,
            window_start=now - timedelta(minutes=15),
        )

    assert counts["failed"] == 0
    assert counts["succeeded"] == 0


async def test_cancellation_before_success_counts_only_latest_success() -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ragflow.models import SyncTask
    from app.workers.operational_metrics import _collect_ragflow_outcome_counts

    now = datetime.now(UTC)
    async with AsyncSessionFactory() as session:
        user_id, department_id = await _owner(session, suffix="cancel-then-success")
        file = _file(
            uploader_id=user_id,
            department_id=department_id,
            hash_value="c" * 64,
            status="parsed",
            parse_status="DONE",
            ragflow_error="",
            last_sync_at=now,
        )
        session.add(file)
        await session.flush()
        session.add(
            SyncTask(
                file_id=file.id,
                task_type="ragflow_upload",
                status="canceled",
                retry_count=0,
                max_retry_count=3,
                finished_at=now - timedelta(minutes=1),
            )
        )
        await session.commit()

        counts = await _collect_ragflow_outcome_counts(
            session,
            window_start=now - timedelta(minutes=15),
        )

    assert counts == {"succeeded": 1, "failed": 0, "canceled": 0}


async def test_cancellation_after_success_counts_only_latest_cancellation() -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ragflow.models import SyncTask
    from app.workers.operational_metrics import _collect_ragflow_outcome_counts

    now = datetime.now(UTC)
    async with AsyncSessionFactory() as session:
        user_id, department_id = await _owner(session, suffix="success-then-cancel")
        file = _file(
            uploader_id=user_id,
            department_id=department_id,
            hash_value="d" * 64,
            status="parsed",
            parse_status="DONE",
            ragflow_error=None,
            last_sync_at=now - timedelta(minutes=1),
        )
        session.add(file)
        await session.flush()
        session.add(
            SyncTask(
                file_id=file.id,
                task_type="ragflow_upload",
                status="canceled",
                retry_count=0,
                max_retry_count=3,
                finished_at=now,
            )
        )
        await session.commit()

        counts = await _collect_ragflow_outcome_counts(
            session,
            window_start=now - timedelta(minutes=15),
        )

    assert counts == {"succeeded": 0, "failed": 0, "canceled": 1}


async def test_null_outcome_timestamps_are_not_counted() -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ragflow.models import SyncTask
    from app.workers.operational_metrics import _collect_ragflow_outcome_counts

    now = datetime.now(UTC)
    async with AsyncSessionFactory() as session:
        user_id, department_id = await _owner(session, suffix="null-timestamps")
        file = _file(
            uploader_id=user_id,
            department_id=department_id,
            hash_value="e" * 64,
            status="parsed",
            parse_status="DONE",
            ragflow_error=None,
            last_sync_at=None,
        )
        session.add(file)
        await session.flush()
        session.add(
            SyncTask(
                file_id=file.id,
                task_type="ragflow_upload",
                status="canceled",
                retry_count=0,
                max_retry_count=3,
                finished_at=None,
            )
        )
        await session.commit()

        counts = await _collect_ragflow_outcome_counts(
            session,
            window_start=now - timedelta(minutes=15),
        )

    assert counts == {"succeeded": 0, "failed": 0, "canceled": 0}


async def test_inactive_rows_are_excluded_from_active_logical_storage() -> None:
    from app.core.database import AsyncSessionFactory
    from app.workers.operational_metrics import _collect_logical_storage_bytes

    async with AsyncSessionFactory() as session:
        user_id, department_id = await _owner(session, suffix="logical-storage")
        session.add_all(
            [
                _file(
                    uploader_id=user_id,
                    department_id=department_id,
                    hash_value="f" * 64,
                    status="uploaded",
                    parse_status="",
                    ragflow_error=None,
                    last_sync_at=None,
                    size=101,
                ),
                _file(
                    uploader_id=user_id,
                    department_id=department_id,
                    hash_value="1" * 64,
                    status="ragflow_cleanup_failed",
                    parse_status="",
                    ragflow_error="remote delete failed",
                    last_sync_at=datetime.now(UTC),
                    size=202,
                ),
                _file(
                    uploader_id=user_id,
                    department_id=department_id,
                    hash_value="2" * 64,
                    status="deleted",
                    parse_status="",
                    ragflow_error=None,
                    last_sync_at=None,
                    size=303,
                ),
                _file(
                    uploader_id=user_id,
                    department_id=department_id,
                    hash_value="3" * 64,
                    status="disabled",
                    parse_status="",
                    ragflow_error=None,
                    last_sync_at=None,
                    size=404,
                ),
            ]
        )
        await session.commit()

        logical_bytes = await _collect_logical_storage_bytes(session)

    assert logical_bytes == 101


@pytest.mark.parametrize("interval_seconds", [30, 60, 300])
async def test_collection_interval_accepts_supported_release_cadences(
    monkeypatch: pytest.MonkeyPatch,
    interval_seconds: int,
) -> None:
    from app.workers import operational_metrics

    monkeypatch.setenv("OPERATIONAL_METRICS_INTERVAL_SECONDS", str(interval_seconds))

    assert operational_metrics._collection_interval_seconds() == interval_seconds


async def test_email_redis_failure_preserves_database_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workers import operational_metrics

    collected_at = datetime.now(UTC)
    database_updates: list[dict[str, object]] = []
    email_updates: list[dict[str, object]] = []
    component_results: list[tuple[str, bool]] = []
    ragflow_updates: list[dict[str, object]] = []

    async def fake_collect_snapshot() -> operational_metrics.OperationalSnapshot:
        return operational_metrics.OperationalSnapshot(
            review_overdue=2,
            ragflow_success=3,
            ragflow_failure=1,
            ragflow_canceled=0,
            minio_bytes=123,
            postgres_bytes=456,
            collected_at=collected_at,
        )

    async def fake_capacity(_settings: object) -> object:
        return SimpleNamespace(captured_at=collected_at)

    async def fake_reconciliation() -> object:
        return SimpleNamespace(stale_started_count=2, recovered_count=1)

    async def fail_email_metrics(*, redis_url: str) -> object:
        assert redis_url
        raise OSError("redis unavailable with sensitive endpoint")

    monkeypatch.setattr(operational_metrics, "collect_snapshot", fake_collect_snapshot)
    monkeypatch.setattr(operational_metrics, "collect_and_persist_minio_capacity", fake_capacity)
    monkeypatch.setattr(
        operational_metrics,
        "reconcile_stale_ragflow_api_calls",
        fake_reconciliation,
    )
    monkeypatch.setattr(
        operational_metrics,
        "read_email_delivery_metrics",
        fail_email_metrics,
    )
    monkeypatch.setattr(
        operational_metrics,
        "update_operational_database_snapshot",
        lambda **values: database_updates.append(values),
    )
    monkeypatch.setattr(
        operational_metrics,
        "update_email_delivery_snapshot",
        lambda **values: email_updates.append(values),
    )
    monkeypatch.setattr(
        operational_metrics,
        "update_ragflow_call_telemetry_health",
        lambda **values: ragflow_updates.append(values),
    )
    monkeypatch.setattr(operational_metrics, "update_db_pool", lambda **_values: None)
    monkeypatch.setattr(
        operational_metrics,
        "_database_pool_snapshot",
        lambda: {"size": 1, "checked_out": 0, "overflow": 0},
    )
    monkeypatch.setattr(
        operational_metrics,
        "observe_operational_collector_component",
        lambda component, *, succeeded, timestamp=None: component_results.append(
            (component, succeeded)
        ),
    )
    monkeypatch.setattr(
        operational_metrics,
        "get_settings",
        lambda: SimpleNamespace(cache_redis_url="redis://redacted/0"),
    )

    await operational_metrics.collect_once()

    assert database_updates == [
        {
            "review_overdue": 2,
            "ragflow_success": 3,
            "ragflow_failure": 1,
            "ragflow_canceled": 0,
            "minio_bytes": 123,
            "postgres_bytes": 456,
            "collected_at_timestamp": collected_at.timestamp(),
        }
    ]
    assert email_updates == []
    assert ragflow_updates == [{"stale_started": 2, "recovered": 1}]
    assert component_results == [
        ("database", True),
        ("minio_capacity", True),
        ("ragflow_call_telemetry", True),
        ("email_redis", False),
    ]


async def _owner(session: AsyncSession, *, suffix: str) -> tuple[UUID, UUID]:
    from app.modules.department.models import Department
    from app.modules.user.models import User

    department = Department(name=f"指标测试部 {suffix}", code=f"metrics-{suffix}")
    session.add(department)
    await session.flush()
    user = User(
        name=f"Metrics {suffix}",
        email=f"metrics-{suffix}@company.com",
        email_domain="company.com",
        password_hash="not-used",
        department_id=department.id,
        role="employee",
        status="active",
        email_verified=True,
    )
    session.add(user)
    await session.flush()
    return user.id, department.id


def _file(
    *,
    uploader_id: UUID,
    department_id: UUID,
    hash_value: str,
    status: str,
    parse_status: str,
    ragflow_error: str | None,
    last_sync_at: datetime | None,
    size: int = 64,
) -> File:
    from app.modules.document.models import File

    return File(
        title="指标测试文档",
        original_name="metrics.txt",
        stored_name=f"{hash_value[:8]}-metrics.txt",
        extension="txt",
        mime_type="text/plain",
        size=size,
        hash=hash_value,
        storage_type="minio",
        bucket="knowledge-files",
        object_key=f"metrics/{hash_value}.txt",
        uploader_id=uploader_id,
        department_id=department_id,
        department="指标测试部",
        visibility="department",
        tags=[],
        status=status,
        review_status="approved",
        ragflow_dataset_id="metrics-dataset",
        ragflow_document_id=f"ragflow-{hash_value[:8]}",
        ragflow_parse_status=parse_status,
        ragflow_error_message=ragflow_error,
        last_sync_at=last_sync_at,
        ai_analysis_enabled_at_upload=False,
    )
