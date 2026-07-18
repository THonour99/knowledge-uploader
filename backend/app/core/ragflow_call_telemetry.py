from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    MetaData,
    String,
    Table,
    delete,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import AsyncSessionFactory
from app.core.logging import get_logger
from app.core.metrics import observe_ragflow_api_call
from app.core.ragflow_metrics_contract import (
    RAGFLOW_COMPLETED_RESULTS,
    RAGFLOW_FAILURE_CATEGORIES,
    RAGFLOW_OPERATIONS,
)

logger = get_logger(__name__)
RAGFLOW_STALE_STARTED_AFTER = timedelta(minutes=15)
RAGFLOW_STALE_RECOVERY_AFTER = timedelta(minutes=30)
RAGFLOW_STALE_RECOVERY_BATCH_SIZE = 1000
RAGFLOW_COMPLETED_CALL_RETENTION = timedelta(days=400)

RAGFLOW_API_CALLS = Table(
    "ragflow_api_calls",
    MetaData(),
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("department_id", UUID(as_uuid=True)),
    Column("operation", String(40), nullable=False),
    Column("result", String(20), nullable=False),
    Column("failure_category", String(40)),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True)),
    Column("latency_ms", BigInteger),
)


class RagflowCallTelemetryError(RuntimeError):
    """A lifecycle contract violation in persisted RAGFlow call telemetry."""


async def start_ragflow_api_call(
    *,
    operation: str,
    department_id: uuid.UUID | None = None,
    started_at: datetime | None = None,
) -> uuid.UUID:
    """Persist call start in its own short transaction without request payload details."""
    if operation not in RAGFLOW_OPERATIONS:
        raise ValueError("unsupported RAGFlow operation")
    observed_at = _as_utc(started_at or datetime.now(UTC), field_name="started_at")
    call_id = uuid.uuid4()
    async with AsyncSessionFactory() as session:
        await session.execute(
            insert(RAGFLOW_API_CALLS).values(
                id=call_id,
                department_id=department_id,
                operation=operation,
                result="started",
                failure_category=None,
                started_at=observed_at,
                finished_at=None,
                latency_ms=None,
            )
        )
        await session.commit()
    return call_id


async def finish_ragflow_api_call(
    *,
    call_id: uuid.UUID,
    result: str,
    failure_category: str | None = None,
    finished_at: datetime | None = None,
) -> None:
    """Finish one started call in a transaction independent from its business transaction."""
    _validate_completion(result=result, failure_category=failure_category)
    observed_at = _as_utc(finished_at or datetime.now(UTC), field_name="finished_at")
    async with AsyncSessionFactory() as session:
        selected = await session.execute(
            select(
                RAGFLOW_API_CALLS.c.operation,
                RAGFLOW_API_CALLS.c.result,
                RAGFLOW_API_CALLS.c.started_at,
            )
            .where(RAGFLOW_API_CALLS.c.id == call_id)
            .with_for_update()
        )
        row = selected.mappings().one_or_none()
        if row is None:
            raise RagflowCallTelemetryError("RAGFlow call telemetry row not found")
        if row["result"] != "started":
            raise RagflowCallTelemetryError("RAGFlow call telemetry row is already finished")
        operation = cast(str, row["operation"])
        persisted_started_at = _as_utc(
            cast(datetime, row["started_at"]), field_name="persisted started_at"
        )
        if observed_at < persisted_started_at:
            raise RagflowCallTelemetryError("RAGFlow call finish precedes start")
        latency_ms = int((observed_at - persisted_started_at).total_seconds() * 1000)
        await session.execute(
            update(RAGFLOW_API_CALLS)
            .where(
                RAGFLOW_API_CALLS.c.id == call_id,
                RAGFLOW_API_CALLS.c.result == "started",
            )
            .values(
                result=result,
                failure_category=failure_category,
                finished_at=observed_at,
                latency_ms=latency_ms,
            )
        )
        await session.commit()
    observe_ragflow_api_call(
        operation=operation,
        result=result,
        failure_category=failure_category,
    )


@dataclass(frozen=True)
class RagflowTelemetryReconciliation:
    stale_started_count: int
    recovered_count: int


async def best_effort_start_ragflow_api_call(
    *,
    operation: str,
    department_id: uuid.UUID | None = None,
) -> uuid.UUID | None:
    try:
        return await start_ragflow_api_call(
            operation=operation,
            department_id=department_id,
        )
    except Exception as error:
        logger.warning(
            "ragflow_api_call_telemetry_start_failed",
            operation=operation if operation in RAGFLOW_OPERATIONS else "other",
            error_type=type(error).__name__,
        )
        return None


async def best_effort_finish_ragflow_api_call(
    *,
    call_id: uuid.UUID | None,
    operation: str,
    result: str,
    failure_category: str | None = None,
) -> None:
    if call_id is None:
        return
    try:
        await finish_ragflow_api_call(
            call_id=call_id,
            result=result,
            failure_category=failure_category,
        )
    except Exception as error:
        logger.warning(
            "ragflow_api_call_telemetry_finish_failed",
            operation=operation if operation in RAGFLOW_OPERATIONS else "other",
            result=result if result in RAGFLOW_COMPLETED_RESULTS else "failure",
            error_type=type(error).__name__,
        )


async def reconcile_stale_ragflow_api_calls(
    *,
    now: datetime | None = None,
) -> RagflowTelemetryReconciliation:
    observed_at = _as_utc(now or datetime.now(UTC), field_name="reconciliation time")
    stale_cutoff = observed_at - RAGFLOW_STALE_STARTED_AFTER
    recovery_cutoff = observed_at - RAGFLOW_STALE_RECOVERY_AFTER
    recovered_operations: list[str] = []
    async with AsyncSessionFactory() as session:
        selected = await session.execute(
            select(
                RAGFLOW_API_CALLS.c.id,
                RAGFLOW_API_CALLS.c.operation,
                RAGFLOW_API_CALLS.c.started_at,
            )
            .where(
                RAGFLOW_API_CALLS.c.result == "started",
                RAGFLOW_API_CALLS.c.started_at <= recovery_cutoff,
            )
            .order_by(RAGFLOW_API_CALLS.c.started_at.asc(), RAGFLOW_API_CALLS.c.id.asc())
            .limit(RAGFLOW_STALE_RECOVERY_BATCH_SIZE)
            .with_for_update(skip_locked=True)
        )
        for row in selected.mappings():
            started_at = _as_utc(
                cast(datetime, row["started_at"]),
                field_name="persisted started_at",
            )
            latency_ms = max(int((observed_at - started_at).total_seconds() * 1000), 0)
            await session.execute(
                update(RAGFLOW_API_CALLS)
                .where(
                    RAGFLOW_API_CALLS.c.id == row["id"],
                    RAGFLOW_API_CALLS.c.result == "started",
                )
                .values(
                    result="failure",
                    failure_category="unknown",
                    finished_at=observed_at,
                    latency_ms=latency_ms,
                )
            )
            recovered_operations.append(cast(str, row["operation"]))
        stale_result = await session.execute(
            select(func.count())
            .select_from(RAGFLOW_API_CALLS)
            .where(
                RAGFLOW_API_CALLS.c.result == "started",
                RAGFLOW_API_CALLS.c.started_at <= stale_cutoff,
            )
        )
        stale_started_count = int(stale_result.scalar_one())
        await session.execute(
            delete(RAGFLOW_API_CALLS).where(
                RAGFLOW_API_CALLS.c.result.in_(tuple(sorted(RAGFLOW_COMPLETED_RESULTS))),
                RAGFLOW_API_CALLS.c.finished_at < observed_at - RAGFLOW_COMPLETED_CALL_RETENTION,
            )
        )
        await session.commit()
    for operation in recovered_operations:
        observe_ragflow_api_call(
            operation=operation,
            result="failure",
            failure_category="unknown",
        )
    return RagflowTelemetryReconciliation(
        stale_started_count=stale_started_count,
        recovered_count=len(recovered_operations),
    )


def _validate_completion(*, result: str, failure_category: str | None) -> None:
    if result not in RAGFLOW_COMPLETED_RESULTS:
        raise ValueError("unsupported RAGFlow call result")
    if result == "failure":
        if failure_category not in RAGFLOW_FAILURE_CATEGORIES:
            raise ValueError("unsupported RAGFlow failure category")
        return
    if failure_category is not None:
        raise ValueError("successful RAGFlow call cannot have a failure category")


def _as_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value.astimezone(UTC)
