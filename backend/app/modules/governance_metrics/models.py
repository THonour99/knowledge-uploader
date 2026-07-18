from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.ragflow_metrics_contract import (
    RAGFLOW_FAILURE_CATEGORIES,
    RAGFLOW_OPERATIONS,
    RAGFLOW_PERSISTED_RESULTS,
)
from app.db.base import Base


def _sql_values(values: frozenset[str]) -> str:
    return ", ".join(f"'{value}'" for value in sorted(values))


class RagflowApiCall(Base):
    __tablename__ = "ragflow_api_calls"
    __table_args__ = (
        CheckConstraint(
            f"operation IN ({_sql_values(RAGFLOW_OPERATIONS)})",
            name="ck_ragflow_api_calls_operation",
        ),
        CheckConstraint(
            f"result IN ({_sql_values(RAGFLOW_PERSISTED_RESULTS)})",
            name="ck_ragflow_api_calls_result",
        ),
        CheckConstraint(
            "failure_category IS NULL OR failure_category IN "
            f"({_sql_values(RAGFLOW_FAILURE_CATEGORIES)})",
            name="ck_ragflow_api_calls_failure_category",
        ),
        CheckConstraint(
            "(result = 'failure' AND failure_category IS NOT NULL) OR "
            "(result <> 'failure' AND failure_category IS NULL)",
            name="ck_ragflow_api_calls_failure_result",
        ),
        CheckConstraint(
            "(result = 'started' AND finished_at IS NULL AND latency_ms IS NULL) OR "
            "(result IN ('success', 'failure') AND finished_at IS NOT NULL "
            "AND latency_ms IS NOT NULL AND latency_ms >= 0)",
            name="ck_ragflow_api_calls_lifecycle",
        ),
        Index("idx_ragflow_api_calls_started_at", "started_at"),
        Index("idx_ragflow_api_calls_finished_at", "finished_at"),
        Index("idx_ragflow_api_calls_operation_result", "operation", "result"),
        Index("idx_ragflow_api_calls_department_started", "department_id", "started_at"),
        Index(
            "idx_ragflow_api_calls_started_pending",
            "started_at",
            "id",
            postgresql_where=text("result = 'started'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL")
    )
    operation: Mapped[str] = mapped_column(String(40), nullable=False)
    result: Mapped[str] = mapped_column(String(20), nullable=False, server_default="started")
    failure_category: Mapped[str | None] = mapped_column(String(40))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latency_ms: Mapped[int | None] = mapped_column(BigInteger)


class StorageCapacitySnapshot(Base):
    __tablename__ = "storage_capacity_snapshots"
    __table_args__ = (
        CheckConstraint("backend = 'minio'", name="ck_storage_capacity_snapshots_backend"),
        CheckConstraint("scope = 'cluster'", name="ck_storage_capacity_snapshots_scope"),
        CheckConstraint(
            "source_kind = 'minio_cluster_metrics'",
            name="ck_storage_capacity_snapshots_source_kind",
        ),
        CheckConstraint("total_bytes > 0", name="ck_storage_capacity_snapshots_total_positive"),
        CheckConstraint("used_bytes >= 0", name="ck_storage_capacity_snapshots_used_non_negative"),
        CheckConstraint("free_bytes >= 0", name="ck_storage_capacity_snapshots_free_non_negative"),
        CheckConstraint(
            "used_bytes <= total_bytes AND free_bytes <= total_bytes "
            "AND used_bytes + free_bytes <= total_bytes",
            name="ck_storage_capacity_snapshots_bytes_consistent",
        ),
        CheckConstraint(
            "evidence_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_storage_capacity_snapshots_evidence_sha256",
        ),
        CheckConstraint(
            "collected_at >= captured_at",
            name="ck_storage_capacity_snapshots_collection_order",
        ),
        Index(
            "uq_storage_capacity_snapshots_source_capture",
            "source_kind",
            "captured_at",
            unique=True,
        ),
        Index("idx_storage_capacity_snapshots_captured_at", "captured_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    backend: Mapped[str] = mapped_column(String(20), nullable=False, server_default="minio")
    scope: Mapped[str] = mapped_column(String(20), nullable=False, server_default="cluster")
    source_kind: Mapped[str] = mapped_column(
        String(40), nullable=False, server_default="minio_cluster_metrics"
    )
    total_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    used_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    free_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
