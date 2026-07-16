from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from urllib.request import urlopen

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import AsyncSessionFactory, engine
from app.core.email_delivery_metrics import read_email_delivery_metrics
from app.core.logging import configure_logging, get_logger
from app.core.metrics import (
    observe_operational_collector_component,
    start_metrics_server,
    update_db_pool,
    update_email_delivery_snapshot,
    update_operational_database_snapshot,
    update_ready_probe,
)

logger = get_logger(__name__)
RAGFLOW_WINDOW_MINUTES = 15

_FILES = sa.table(
    "files",
    sa.column("id", sa.Uuid()),
    sa.column("status", sa.String()),
    sa.column("review_status", sa.String()),
    sa.column("review_due_at", sa.DateTime(timezone=True)),
    sa.column("size", sa.BigInteger()),
    sa.column("storage_type", sa.String()),
    sa.column("ragflow_parse_status", sa.String()),
    sa.column("ragflow_error_message", sa.Text()),
    sa.column("last_sync_at", sa.DateTime(timezone=True)),
)
_SYNC_TASKS = sa.table(
    "sync_tasks",
    sa.column("file_id", sa.Uuid()),
    sa.column("status", sa.String()),
    sa.column("finished_at", sa.DateTime(timezone=True)),
)

_RAGFLOW_SUCCESS_RUNS = ("3", "DONE")
_RAGFLOW_FAILED_RUNS = ("4", "FAIL", "FAILED", "ERROR")


@dataclass(frozen=True)
class OperationalSnapshot:
    review_overdue: int
    ragflow_success: int
    ragflow_failure: int
    ragflow_canceled: int
    minio_bytes: int
    postgres_bytes: int
    collected_at: datetime


async def collect_snapshot() -> OperationalSnapshot:
    now = datetime.now(UTC)
    window_start = now - timedelta(minutes=RAGFLOW_WINDOW_MINUTES)
    async with AsyncSessionFactory() as session:
        overdue_result = await session.execute(
            sa.select(sa.func.count())
            .select_from(_FILES)
            .where(
                _FILES.c.review_due_at.is_not(None),
                _FILES.c.review_due_at < now,
                _FILES.c.review_status.in_(("pending", "in_review")),
            )
        )
        logical_storage_bytes = await _collect_logical_storage_bytes(session)
        terminal_counts = await _collect_ragflow_outcome_counts(
            session,
            window_start=window_start,
        )
        database_size_result = await session.execute(
            sa.text("SELECT pg_database_size(current_database())")
        )
    return OperationalSnapshot(
        review_overdue=int(overdue_result.scalar_one()),
        ragflow_success=terminal_counts.get("succeeded", 0),
        ragflow_failure=terminal_counts.get("failed", 0),
        ragflow_canceled=terminal_counts.get("canceled", 0),
        minio_bytes=logical_storage_bytes,
        postgres_bytes=int(database_size_result.scalar_one()),
        collected_at=now,
    )


async def _collect_logical_storage_bytes(session: AsyncSession) -> int:
    """Count active logical MinIO references, not hidden deleted records."""
    result = await session.execute(
        sa.select(sa.func.coalesce(sa.func.sum(_FILES.c.size), 0)).where(
            _FILES.c.storage_type == "minio",
            _FILES.c.status.not_in(("deleted", "ragflow_cleanup_failed")),
        )
    )
    return int(result.scalar_one())


async def _collect_ragflow_outcome_counts(
    session: AsyncSession,
    *,
    window_start: datetime,
) -> dict[str, int]:
    """Count the latest mutually exclusive terminal RAGFlow outcome per document."""
    normalized_parse_status = sa.func.upper(sa.func.coalesce(_FILES.c.ragflow_parse_status, ""))
    has_ragflow_error = (
        sa.func.length(sa.func.trim(sa.func.coalesce(_FILES.c.ragflow_error_message, ""))) > 0
    )
    latest_cancellation = (
        sa.select(
            _SYNC_TASKS.c.file_id,
            sa.func.max(_SYNC_TASKS.c.finished_at).label("canceled_at"),
        )
        .where(
            _SYNC_TASKS.c.status == "canceled",
            _SYNC_TASKS.c.finished_at.is_not(None),
        )
        .group_by(_SYNC_TASKS.c.file_id)
        .subquery()
    )
    canceled_at = latest_cancellation.c.canceled_at
    success_outcome = sa.and_(
        _FILES.c.status == "parsed",
        normalized_parse_status.in_(_RAGFLOW_SUCCESS_RUNS),
        sa.not_(has_ragflow_error),
    )
    failure_outcome = sa.and_(
        _FILES.c.status == "failed",
        sa.or_(
            has_ragflow_error,
            normalized_parse_status.in_(_RAGFLOW_FAILED_RUNS),
        ),
    )
    file_outcome_is_latest = sa.or_(
        canceled_at.is_(None),
        _FILES.c.last_sync_at >= canceled_at,
    )
    canceled_outcome_is_latest = sa.and_(
        canceled_at.is_not(None),
        sa.or_(
            _FILES.c.last_sync_at.is_(None),
            canceled_at > _FILES.c.last_sync_at,
        ),
    )
    result = await session.execute(
        sa.select(
            sa.func.count()
            .filter(
                success_outcome,
                _FILES.c.last_sync_at >= window_start,
                file_outcome_is_latest,
            )
            .label("success"),
            sa.func.count()
            .filter(
                failure_outcome,
                _FILES.c.last_sync_at >= window_start,
                file_outcome_is_latest,
            )
            .label("failure"),
            sa.func.count()
            .filter(
                canceled_at >= window_start,
                canceled_outcome_is_latest,
            )
            .label("canceled"),
        )
        .select_from(_FILES)
        .outerjoin(latest_cancellation, latest_cancellation.c.file_id == _FILES.c.id)
    )
    success, failure, canceled = result.one()
    return {
        "succeeded": int(success),
        "failed": int(failure),
        "canceled": int(canceled),
    }


async def collect_loop() -> None:
    consecutive_ready_failures = 0
    while True:
        ready = await asyncio.to_thread(_probe_ready)
        consecutive_ready_failures = 0 if ready else consecutive_ready_failures + 1
        update_ready_probe(
            ready=ready,
            consecutive_failures=consecutive_ready_failures,
        )
        await collect_once()
        await asyncio.sleep(_collection_interval_seconds())


async def collect_once() -> None:
    """Collect independent components without letting one erase another's gauges."""
    try:
        snapshot = await collect_snapshot()
    except Exception as error:
        observe_operational_collector_component("database", succeeded=False)
        logger.error(
            "operational_metrics_database_collection_failed",
            error_type=type(error).__name__,
        )
    else:
        update_operational_database_snapshot(
            review_overdue=snapshot.review_overdue,
            ragflow_success=snapshot.ragflow_success,
            ragflow_failure=snapshot.ragflow_failure,
            ragflow_canceled=snapshot.ragflow_canceled,
            minio_bytes=snapshot.minio_bytes,
            postgres_bytes=snapshot.postgres_bytes,
            collected_at_timestamp=snapshot.collected_at.timestamp(),
        )
        update_db_pool(**_database_pool_snapshot())
        observe_operational_collector_component(
            "database",
            succeeded=True,
            timestamp=snapshot.collected_at.timestamp(),
        )

    try:
        email_delivery = await read_email_delivery_metrics(
            redis_url=get_settings().cache_redis_url,
        )
    except Exception as error:
        observe_operational_collector_component("email_redis", succeeded=False)
        logger.error(
            "operational_metrics_email_collection_failed",
            error_type=type(error).__name__,
        )
    else:
        update_email_delivery_snapshot(
            totals=email_delivery.totals,
            last_timestamps=email_delivery.last_timestamps,
        )
        observe_operational_collector_component("email_redis", succeeded=True)


def _probe_ready() -> bool:
    url = os.environ.get(
        "OPERATIONAL_READY_URL",
        "http://backend-api:8000/api/system/ready",
    )
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        logger.error("operational_ready_url_invalid")
        return False
    try:
        with urlopen(url, timeout=5) as response:
            if response.status != 200:
                return False
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        logger.warning(
            "operational_ready_probe_failed",
            error_type=type(error).__name__,
        )
        return False
    return isinstance(payload, dict) and payload.get("status") == "ok"


def _database_pool_snapshot() -> dict[str, int]:
    pool = engine.sync_engine.pool
    values: dict[str, int] = {}
    for key, attribute_name in (
        ("size", "size"),
        ("checked_out", "checkedout"),
        ("overflow", "overflow"),
    ):
        attribute = getattr(pool, attribute_name, None)
        values[key] = int(attribute()) if callable(attribute) else 0
    return values


def _metrics_port() -> int:
    return _bounded_integer_env(
        "OPERATIONAL_METRICS_PORT",
        default=9102,
        minimum=1,
        maximum=65535,
    )


def _collection_interval_seconds() -> int:
    return _bounded_integer_env(
        "OPERATIONAL_METRICS_INTERVAL_SECONDS",
        default=30,
        minimum=5,
        maximum=3600,
    )


def _bounded_integer_env(name: str, *, default: int, minimum: int, maximum: int) -> int:
    raw_value = os.environ.get(name, str(default))
    try:
        value = int(raw_value)
    except ValueError as error:
        raise RuntimeError(f"{name} must be an integer") from error
    if value < minimum or value > maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value


def main() -> None:
    configure_logging()
    start_metrics_server(_metrics_port())
    asyncio.run(collect_loop())


if __name__ == "__main__":
    main()
