from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    MetaData,
    String,
    Table,
    Text,
    and_,
    case,
    false,
    func,
    literal,
    or_,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

_METADATA = MetaData()

FILES = Table(
    "files",
    _METADATA,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("original_name", String(255), nullable=False),
    Column("extension", String(20), nullable=False),
    Column("size", BigInteger, nullable=False),
    Column("uploader_id", UUID(as_uuid=True), nullable=False),
    Column("department_id", UUID(as_uuid=True), nullable=False),
    Column("status", String(40), nullable=False),
    Column("review_status", String(40), nullable=False),
    # These columns are capability-gated so the dashboard remains truthful during rolling deploys.
    Column("submitted_at", DateTime(timezone=True)),
    Column("review_due_at", DateTime(timezone=True)),
    Column("claimed_by", UUID(as_uuid=True)),
    Column("claimed_at", DateTime(timezone=True)),
    Column("claim_expires_at", DateTime(timezone=True)),
    Column("expires_at", DateTime(timezone=True)),
    Column("expiry_status", String(20), nullable=False),
    Column("uploaded_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

USERS = Table(
    "users",
    _METADATA,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("name", String(100), nullable=False),
    Column("department_id", UUID(as_uuid=True), nullable=False),
    Column("status", String(40), nullable=False),
)

DEPARTMENTS = Table(
    "departments",
    _METADATA,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("name", String(100), nullable=False),
    Column("status", String(20), nullable=False),
)

USER_MANAGED_DEPARTMENTS = Table(
    "user_managed_departments",
    _METADATA,
    Column("user_id", UUID(as_uuid=True), primary_key=True),
    Column("department_id", UUID(as_uuid=True), primary_key=True),
)

DOCUMENT_ANALYSIS = Table(
    "document_analysis",
    _METADATA,
    Column("file_id", UUID(as_uuid=True), primary_key=True),
    Column("sensitive_risk_level", String(20), nullable=False),
)

NOTIFICATIONS = Table(
    "notifications",
    _METADATA,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("user_id", UUID(as_uuid=True), nullable=False),
    Column("type", String(80), nullable=False),
    Column("channel", String(20), nullable=False),
    Column("title", String(200), nullable=False),
    Column("body", Text, nullable=False),
    Column("metadata_json", JSONB, nullable=False),
    Column("read_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

SYNC_TASKS = Table(
    "sync_tasks",
    _METADATA,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("status", String(40), nullable=False),
    Column("started_at", DateTime(timezone=True)),
)

EVENT_OUTBOX = Table(
    "event_outbox",
    _METADATA,
    Column("id", BigInteger, primary_key=True),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("published_at", DateTime(timezone=True)),
)

OUTBOX_DEAD_LETTERS = Table(
    "outbox_dead_letters",
    _METADATA,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("status", String(20), nullable=False),
)

HIDDEN_FILE_STATUSES = ("deleted", "ragflow_cleanup_failed")
DRAFT_STATUSES = ("uploaded", "analyzed")
AI_PROCESSING_STATUSES = ("extracting_text", "analysis_queued", "analyzing")
SYNC_PROCESSING_STATUSES = ("queued", "syncing", "uploaded_to_ragflow", "parsing")
SYNC_FAILED_STATUSES = ("failed", "ragflow_cleanup_failed")
STALE_RUNNING_MINUTES = 30


@dataclass(frozen=True)
class SchemaCapabilities:
    review_claim_sla: bool
    outbox_dead_letters: bool


@dataclass(frozen=True)
class EmployeeCountsRecord:
    total: int
    draft: int
    ai_processing: int
    analysis_failed: int
    sensitive_review: int
    pending_review: int
    approved: int
    rejected: int
    sync_processing: int
    parsed: int
    sync_failed: int
    archived: int


@dataclass(frozen=True)
class RecentDocumentRecord:
    id: uuid.UUID
    original_name: str
    extension: str
    status: str
    review_status: str
    updated_at: datetime


@dataclass(frozen=True)
class RecentNotificationRecord:
    id: uuid.UUID
    type: str
    title: str
    body: str
    read_at: datetime | None
    created_at: datetime
    resource_type: str | None
    resource_id: str | None
    file_id: str | None
    task_id: str | None


@dataclass(frozen=True)
class ReviewQueueCountsRecord:
    scope_total_pending: int
    unclaimed: int | None
    mine: int | None
    due_soon: int | None
    overdue: int | None
    sync_failed: int


@dataclass(frozen=True)
class ReviewQueueRecord:
    id: uuid.UUID
    original_name: str
    extension: str
    uploader_name: str
    department_id: uuid.UUID
    department_name: str
    sensitive_risk_level: str | None
    uploaded_at: datetime
    submitted_at: datetime | None
    review_due_at: datetime | None
    claimed_by: uuid.UUID | None
    claimed_at: datetime | None
    claim_expires_at: datetime | None
    claimed_by_name: str | None


@dataclass(frozen=True)
class SystemCoreRecord:
    unassigned_users: int
    expiring_files: int
    expired_files: int
    logical_file_count: int
    logical_total_bytes: int
    active_sync_tasks: int
    failed_sync_tasks: int
    stale_running_candidates: int


@dataclass(frozen=True)
class OutboxRecord:
    pending: int
    oldest_pending_at: datetime | None


@dataclass(frozen=True)
class DeadLetterRecord:
    pending: int
    requeued: int
    resolved: int


class DashboardRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_schema_capabilities(self) -> SchemaCapabilities:
        result = await self._session.execute(
            text(
                """
                SELECT
                  (
                    SELECT count(*) = 5
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = 'files'
                      AND column_name IN (
                        'submitted_at', 'review_due_at', 'claimed_by',
                        'claimed_at', 'claim_expires_at'
                      )
                  ) AS review_claim_sla,
                  to_regclass(current_schema() || '.outbox_dead_letters') IS NOT NULL
                    AS outbox_dead_letters
                """
            )
        )
        row = result.mappings().one()
        return SchemaCapabilities(
            review_claim_sla=bool(row["review_claim_sla"]),
            outbox_dead_letters=bool(row["outbox_dead_letters"]),
        )

    async def list_managed_department_ids(self, user_id: uuid.UUID) -> frozenset[uuid.UUID]:
        result = await self._session.execute(
            select(USER_MANAGED_DEPARTMENTS.c.department_id)
            .join(
                DEPARTMENTS,
                DEPARTMENTS.c.id == USER_MANAGED_DEPARTMENTS.c.department_id,
            )
            .where(
                USER_MANAGED_DEPARTMENTS.c.user_id == user_id,
                DEPARTMENTS.c.status == "active",
            )
            .order_by(USER_MANAGED_DEPARTMENTS.c.department_id.asc())
        )
        return frozenset(cast(uuid.UUID, value) for value in result.scalars())

    async def get_employee_counts(self, user_id: uuid.UUID) -> EmployeeCountsRecord:
        predicate = and_(
            FILES.c.uploader_id == user_id,
            FILES.c.status.not_in(HIDDEN_FILE_STATUSES),
        )
        statement = select(
            func.count().label("total"),
            func.count().filter(FILES.c.status.in_(DRAFT_STATUSES)).label("draft"),
            func.count().filter(FILES.c.status.in_(AI_PROCESSING_STATUSES)).label("ai_processing"),
            func.count().filter(FILES.c.status == "analysis_failed").label("analysis_failed"),
            func.count()
            .filter(FILES.c.status == "sensitive_review_required")
            .label("sensitive_review"),
            func.count().filter(FILES.c.status == "pending_review").label("pending_review"),
            func.count().filter(FILES.c.status == "approved").label("approved"),
            func.count().filter(FILES.c.status == "rejected").label("rejected"),
            func.count()
            .filter(FILES.c.status.in_(SYNC_PROCESSING_STATUSES))
            .label("sync_processing"),
            func.count().filter(FILES.c.status == "parsed").label("parsed"),
            func.count().filter(FILES.c.status.in_(SYNC_FAILED_STATUSES)).label("sync_failed"),
            func.count().filter(FILES.c.status == "disabled").label("archived"),
        ).where(predicate)
        row = (await self._session.execute(statement)).mappings().one()
        return EmployeeCountsRecord(
            **{key: int(row[key] or 0) for key in EmployeeCountsRecord.__dataclass_fields__}
        )

    async def list_recent_documents(
        self,
        *,
        user_id: uuid.UUID,
        limit: int,
    ) -> list[RecentDocumentRecord]:
        result = await self._session.execute(
            select(
                FILES.c.id,
                FILES.c.original_name,
                FILES.c.extension,
                FILES.c.status,
                FILES.c.review_status,
                FILES.c.updated_at,
            )
            .where(
                FILES.c.uploader_id == user_id,
                FILES.c.status.not_in(HIDDEN_FILE_STATUSES),
            )
            .order_by(FILES.c.updated_at.desc(), FILES.c.id.asc())
            .limit(limit)
        )
        return [
            RecentDocumentRecord(
                id=cast(uuid.UUID, row.id),
                original_name=cast(str, row.original_name),
                extension=cast(str, row.extension),
                status=cast(str, row.status),
                review_status=cast(str, row.review_status),
                updated_at=cast(datetime, row.updated_at),
            )
            for row in result
        ]

    async def count_unread_notifications(self, user_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(NOTIFICATIONS)
            .where(
                NOTIFICATIONS.c.user_id == user_id,
                NOTIFICATIONS.c.channel == "in_app",
                NOTIFICATIONS.c.read_at.is_(None),
            )
        )
        return int(result.scalar_one())

    async def list_recent_notifications(
        self,
        *,
        user_id: uuid.UUID,
        limit: int,
    ) -> list[RecentNotificationRecord]:
        metadata = NOTIFICATIONS.c.metadata_json
        result = await self._session.execute(
            select(
                NOTIFICATIONS.c.id,
                NOTIFICATIONS.c.type,
                NOTIFICATIONS.c.title,
                NOTIFICATIONS.c.body,
                NOTIFICATIONS.c.read_at,
                NOTIFICATIONS.c.created_at,
                metadata.op("->>")("resource_type").label("resource_type"),
                metadata.op("->>")("resource_id").label("resource_id"),
                metadata.op("->>")("file_id").label("file_id"),
                metadata.op("->>")("task_id").label("task_id"),
            )
            .where(
                NOTIFICATIONS.c.user_id == user_id,
                NOTIFICATIONS.c.channel == "in_app",
            )
            .order_by(NOTIFICATIONS.c.created_at.desc(), NOTIFICATIONS.c.id.asc())
            .limit(limit)
        )
        return [
            RecentNotificationRecord(
                id=cast(uuid.UUID, row.id),
                type=cast(str, row.type),
                title=cast(str, row.title),
                body=cast(str, row.body),
                read_at=cast(datetime | None, row.read_at),
                created_at=cast(datetime, row.created_at),
                resource_type=cast(str | None, row.resource_type),
                resource_id=cast(str | None, row.resource_id),
                file_id=cast(str | None, row.file_id),
                task_id=cast(str | None, row.task_id),
            )
            for row in result
        ]

    async def get_review_queue_counts(
        self,
        *,
        department_ids: frozenset[uuid.UUID] | None,
        current_user_id: uuid.UUID,
        now: datetime,
        review_claim_sla: bool,
        due_soon_hours: int,
    ) -> ReviewQueueCountsRecord:
        scope = _department_scope_predicate(department_ids)
        pending = FILES.c.status == "pending_review"
        sync_failed = FILES.c.status.in_(SYNC_FAILED_STATUSES)
        if not review_claim_sla:
            row = (
                (
                    await self._session.execute(
                        select(
                            func.count().filter(pending).label("scope_total_pending"),
                            func.count().filter(sync_failed).label("sync_failed"),
                        ).where(scope)
                    )
                )
                .mappings()
                .one()
            )
            return ReviewQueueCountsRecord(
                scope_total_pending=int(row["scope_total_pending"] or 0),
                unclaimed=None,
                mine=None,
                due_soon=None,
                overdue=None,
                sync_failed=int(row["sync_failed"] or 0),
            )

        active_claim = and_(
            FILES.c.claimed_by.is_not(None),
            FILES.c.claimed_at.is_not(None),
            FILES.c.claim_expires_at.is_not(None),
            FILES.c.claim_expires_at > now,
        )
        unclaimed = or_(
            FILES.c.claimed_by.is_(None),
            FILES.c.claimed_at.is_(None),
            FILES.c.claim_expires_at.is_(None),
            FILES.c.claim_expires_at <= now,
        )
        due_soon_at = now + timedelta(hours=due_soon_hours)
        row = (
            (
                await self._session.execute(
                    select(
                        func.count().filter(pending).label("scope_total_pending"),
                        func.count().filter(and_(pending, unclaimed)).label("unclaimed"),
                        func.count()
                        .filter(
                            and_(
                                pending,
                                active_claim,
                                FILES.c.claimed_by == current_user_id,
                            )
                        )
                        .label("mine"),
                        func.count()
                        .filter(
                            and_(
                                pending,
                                FILES.c.review_due_at > now,
                                FILES.c.review_due_at <= due_soon_at,
                            )
                        )
                        .label("due_soon"),
                        func.count()
                        .filter(and_(pending, FILES.c.review_due_at <= now))
                        .label("overdue"),
                        func.count().filter(sync_failed).label("sync_failed"),
                    ).where(scope)
                )
            )
            .mappings()
            .one()
        )
        return ReviewQueueCountsRecord(
            scope_total_pending=int(row["scope_total_pending"] or 0),
            unclaimed=int(row["unclaimed"] or 0),
            mine=int(row["mine"] or 0),
            due_soon=int(row["due_soon"] or 0),
            overdue=int(row["overdue"] or 0),
            sync_failed=int(row["sync_failed"] or 0),
        )

    async def list_priority_review_queue(
        self,
        *,
        department_ids: frozenset[uuid.UUID] | None,
        now: datetime,
        review_claim_sla: bool,
        page: int,
        page_size: int,
        q: str | None,
    ) -> tuple[list[ReviewQueueRecord], int]:
        if department_ids is not None and not department_ids:
            return [], 0

        uploader = USERS.alias("dashboard_uploader")
        claimant = USERS.alias("dashboard_claimant")
        from_clause = (
            FILES.join(uploader, uploader.c.id == FILES.c.uploader_id)
            .join(DEPARTMENTS, DEPARTMENTS.c.id == FILES.c.department_id)
            .outerjoin(DOCUMENT_ANALYSIS, DOCUMENT_ANALYSIS.c.file_id == FILES.c.id)
        )
        selected = [
            FILES.c.id,
            FILES.c.original_name,
            FILES.c.extension,
            uploader.c.name.label("uploader_name"),
            FILES.c.department_id,
            DEPARTMENTS.c.name.label("department_name"),
            DOCUMENT_ANALYSIS.c.sensitive_risk_level,
            FILES.c.uploaded_at,
        ]
        if review_claim_sla:
            from_clause = from_clause.outerjoin(claimant, claimant.c.id == FILES.c.claimed_by)
            selected.extend(
                (
                    FILES.c.submitted_at,
                    FILES.c.review_due_at,
                    FILES.c.claimed_by,
                    FILES.c.claimed_at,
                    FILES.c.claim_expires_at,
                    claimant.c.name.label("claimed_by_name"),
                )
            )
        else:
            selected.extend(
                (
                    literal(None).cast(DateTime(timezone=True)).label("submitted_at"),
                    literal(None).cast(DateTime(timezone=True)).label("review_due_at"),
                    literal(None).cast(UUID(as_uuid=True)).label("claimed_by"),
                    literal(None).cast(DateTime(timezone=True)).label("claimed_at"),
                    literal(None).cast(DateTime(timezone=True)).label("claim_expires_at"),
                    literal(None).cast(String(100)).label("claimed_by_name"),
                )
            )

        predicates = [
            FILES.c.status == "pending_review",
            _department_scope_predicate(department_ids),
        ]
        if q is not None:
            pattern = f"%{_escape_like(q)}%"
            predicates.append(
                or_(
                    FILES.c.original_name.ilike(pattern, escape="\\"),
                    uploader.c.name.ilike(pattern, escape="\\"),
                    DEPARTMENTS.c.name.ilike(pattern, escape="\\"),
                )
            )

        risk_rank = case(
            (DOCUMENT_ANALYSIS.c.sensitive_risk_level == "critical", 0),
            (DOCUMENT_ANALYSIS.c.sensitive_risk_level == "high", 1),
            (DOCUMENT_ANALYSIS.c.sensitive_risk_level == "medium", 2),
            (DOCUMENT_ANALYSIS.c.sensitive_risk_level == "low", 3),
            (DOCUMENT_ANALYSIS.c.sensitive_risk_level == "none", 4),
            else_=5,
        )
        statement = select(*selected).select_from(from_clause).where(*predicates)
        if review_claim_sla:
            statement = statement.order_by(
                case((FILES.c.review_due_at <= now, 0), else_=1).asc(),
                risk_rank.asc(),
                FILES.c.review_due_at.asc().nullslast(),
                FILES.c.submitted_at.asc().nullslast(),
                FILES.c.id.asc(),
            )
        else:
            statement = statement.order_by(
                risk_rank.asc(),
                FILES.c.uploaded_at.asc(),
                FILES.c.id.asc(),
            )

        count_statement = select(func.count()).select_from(from_clause).where(*predicates)
        total = int((await self._session.execute(count_statement)).scalar_one())
        result = await self._session.execute(
            statement.offset((page - 1) * page_size).limit(page_size)
        )
        return [
            ReviewQueueRecord(
                id=cast(uuid.UUID, row.id),
                original_name=cast(str, row.original_name),
                extension=cast(str, row.extension),
                uploader_name=cast(str, row.uploader_name),
                department_id=cast(uuid.UUID, row.department_id),
                department_name=cast(str, row.department_name),
                sensitive_risk_level=cast(str | None, row.sensitive_risk_level),
                uploaded_at=cast(datetime, row.uploaded_at),
                submitted_at=cast(datetime | None, row.submitted_at),
                review_due_at=cast(datetime | None, row.review_due_at),
                claimed_by=cast(uuid.UUID | None, row.claimed_by),
                claimed_at=cast(datetime | None, row.claimed_at),
                claim_expires_at=cast(datetime | None, row.claim_expires_at),
                claimed_by_name=cast(str | None, row.claimed_by_name),
            )
            for row in result
        ], total

    async def get_system_core_snapshot(
        self,
        *,
        unassigned_department_id: uuid.UUID,
        now: datetime,
    ) -> SystemCoreRecord:
        active_file = FILES.c.status.not_in(HIDDEN_FILE_STATUSES)
        stale_before = now - timedelta(minutes=STALE_RUNNING_MINUTES)
        statement = select(
            select(func.count())
            .select_from(USERS)
            .where(
                USERS.c.department_id == unassigned_department_id,
                USERS.c.status == "active",
            )
            .scalar_subquery()
            .label("unassigned_users"),
            select(func.count())
            .select_from(FILES)
            .where(active_file, FILES.c.expiry_status == "expiring")
            .scalar_subquery()
            .label("expiring_files"),
            select(func.count())
            .select_from(FILES)
            .where(active_file, FILES.c.expiry_status == "expired")
            .scalar_subquery()
            .label("expired_files"),
            select(func.count())
            .select_from(FILES)
            .where(active_file)
            .scalar_subquery()
            .label("logical_file_count"),
            select(func.coalesce(func.sum(FILES.c.size), 0))
            .select_from(FILES)
            .where(active_file)
            .scalar_subquery()
            .label("logical_total_bytes"),
            select(func.count())
            .select_from(SYNC_TASKS)
            .where(SYNC_TASKS.c.status.in_(("queued", "running")))
            .scalar_subquery()
            .label("active_sync_tasks"),
            select(func.count())
            .select_from(SYNC_TASKS)
            .where(SYNC_TASKS.c.status == "failed")
            .scalar_subquery()
            .label("failed_sync_tasks"),
            select(func.count())
            .select_from(SYNC_TASKS)
            .where(
                SYNC_TASKS.c.status == "running",
                or_(SYNC_TASKS.c.started_at.is_(None), SYNC_TASKS.c.started_at <= stale_before),
            )
            .scalar_subquery()
            .label("stale_running_candidates"),
        )
        row = (await self._session.execute(statement)).mappings().one()
        return SystemCoreRecord(
            **{key: int(row[key] or 0) for key in SystemCoreRecord.__dataclass_fields__}
        )

    async def get_outbox_snapshot(self) -> OutboxRecord:
        row = (
            (
                await self._session.execute(
                    select(
                        func.count().filter(EVENT_OUTBOX.c.published_at.is_(None)).label("pending"),
                        func.min(EVENT_OUTBOX.c.occurred_at)
                        .filter(EVENT_OUTBOX.c.published_at.is_(None))
                        .label("oldest_pending_at"),
                    )
                )
            )
            .mappings()
            .one()
        )
        return OutboxRecord(
            pending=int(row["pending"] or 0),
            oldest_pending_at=cast(datetime | None, row["oldest_pending_at"]),
        )

    async def get_dead_letter_snapshot(self) -> DeadLetterRecord:
        row = (
            (
                await self._session.execute(
                    select(
                        func.count()
                        .filter(OUTBOX_DEAD_LETTERS.c.status == "pending")
                        .label("pending"),
                        func.count()
                        .filter(OUTBOX_DEAD_LETTERS.c.status == "requeued")
                        .label("requeued"),
                        func.count()
                        .filter(OUTBOX_DEAD_LETTERS.c.status == "resolved")
                        .label("resolved"),
                    )
                )
            )
            .mappings()
            .one()
        )
        return DeadLetterRecord(
            pending=int(row["pending"] or 0),
            requeued=int(row["requeued"] or 0),
            resolved=int(row["resolved"] or 0),
        )


def _department_scope_predicate(
    department_ids: frozenset[uuid.UUID] | None,
) -> ColumnElement[bool]:
    if department_ids is None:
        return FILES.c.id.is_not(None)
    if not department_ids:
        return false()
    return FILES.c.department_id.in_(department_ids)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
