from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_admin_audit_log
from app.core.identity import UNASSIGNED_DEPARTMENT_ID
from app.core.permissions import Role
from app.modules.user.schemas import AuthUserRecord

from . import exceptions
from .permissions import employee_department_is_ready, ensure_supported_role
from .repository import (
    STALE_RUNNING_MINUTES,
    DashboardRepository,
    EmployeeCountsRecord,
    RecentDocumentRecord,
    RecentNotificationRecord,
    ReviewQueueCountsRecord,
    ReviewQueueRecord,
    SchemaCapabilities,
)
from .schemas import (
    AdminWorkbench,
    ComponentHealth,
    DashboardAccess,
    DashboardPayload,
    DeadLetterSnapshot,
    DepartmentAdminDashboard,
    EmployeeActionCounts,
    EmployeeDashboard,
    EmployeeStatusCounts,
    EmployeeWorkbench,
    ExpirySnapshot,
    LogicalStorageSnapshot,
    NextDocumentAction,
    OutboxSnapshot,
    ProcessingSnapshot,
    RecentDocument,
    RecentNotification,
    ReviewQueueCounts,
    ReviewQueueItem,
    ReviewQueuePage,
    RiskLevel,
    SystemAdminDashboard,
    SystemWorkbench,
    UnassignedUsersSnapshot,
)

RECENT_LIMIT = 5
DUE_SOON_HOURS = 4
MAX_NOTIFICATION_EXCERPT = 500
VALID_RISK_LEVELS = frozenset({"none", "low", "medium", "high", "critical"})


@dataclass(frozen=True)
class DashboardQuery:
    page: int = 1
    page_size: int = 10
    q: str | None = None


@dataclass(frozen=True)
class RequestContext:
    ip_address: str
    user_agent: str


class DashboardService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        repository: DashboardRepository,
    ) -> None:
        self._session = session
        self._repository = repository

    async def get_dashboard(
        self,
        *,
        current_user: AuthUserRecord,
        query: DashboardQuery,
        context: RequestContext,
        now: datetime | None = None,
    ) -> DashboardPayload:
        ensure_supported_role(current_user)
        generated_at = _normalize_datetime(now)
        normalized_query = DashboardQuery(
            page=query.page,
            page_size=query.page_size,
            q=_normalize_query(query.q),
        )
        try:
            if current_user.role == Role.EMPLOYEE.value:
                return await self._employee_dashboard(
                    current_user=current_user,
                    generated_at=generated_at,
                )
            if current_user.role == Role.DEPT_ADMIN.value:
                return await self._department_admin_dashboard(
                    current_user=current_user,
                    query=normalized_query,
                    context=context,
                    generated_at=generated_at,
                )
            return await self._system_admin_dashboard(
                current_user=current_user,
                query=normalized_query,
                context=context,
                generated_at=generated_at,
            )
        except SQLAlchemyError as error:
            await self._session.rollback()
            raise exceptions.unavailable() from error

    async def _employee_dashboard(
        self,
        *,
        current_user: AuthUserRecord,
        generated_at: datetime,
    ) -> EmployeeDashboard:
        access_ready = employee_department_is_ready(current_user)
        access = DashboardAccess(
            scope="self",
            ready=access_ready,
            blocker=None if access_ready else "department_required",
            department_ids=[current_user.department_id] if access_ready else [],
        )
        if not access_ready:
            return EmployeeDashboard(
                generated_at=generated_at,
                access=access,
                employee=None,
            )

        counts = await self._repository.get_employee_counts(current_user.id)
        documents = await self._repository.list_recent_documents(
            user_id=current_user.id,
            limit=RECENT_LIMIT,
        )
        unread_count = await self._repository.count_unread_notifications(current_user.id)
        notifications = await self._repository.list_recent_notifications(
            user_id=current_user.id,
            limit=RECENT_LIMIT,
        )
        return EmployeeDashboard(
            generated_at=generated_at,
            access=access,
            employee=EmployeeWorkbench(
                status_counts=_employee_status_counts(counts),
                action_counts=_employee_action_counts(counts),
                recent_documents=[_recent_document(item) for item in documents],
                recent_notifications=[_recent_notification(item) for item in notifications],
                unread_notification_count=unread_count,
            ),
        )

    async def _department_admin_dashboard(
        self,
        *,
        current_user: AuthUserRecord,
        query: DashboardQuery,
        context: RequestContext,
        generated_at: datetime,
    ) -> DepartmentAdminDashboard:
        department_ids = await self._repository.list_managed_department_ids(current_user.id)
        capabilities = await self._repository.get_schema_capabilities()
        access = DashboardAccess(
            scope="managed_departments",
            ready=bool(department_ids),
            blocker=None if department_ids else "managed_departments_required",
            department_ids=sorted(department_ids),
        )
        admin = await self._admin_workbench(
            current_user=current_user,
            department_ids=department_ids,
            capabilities=capabilities,
            query=query,
            generated_at=generated_at,
        )
        await self._commit_read_then_audit(
            current_user=current_user,
            context=context,
            scope="managed_departments",
            department_ids=department_ids,
            query=query,
            result_count=len(admin.priority_queue.items),
        )
        return DepartmentAdminDashboard(
            generated_at=generated_at,
            access=access,
            admin=admin,
        )

    async def _system_admin_dashboard(
        self,
        *,
        current_user: AuthUserRecord,
        query: DashboardQuery,
        context: RequestContext,
        generated_at: datetime,
    ) -> SystemAdminDashboard:
        capabilities = await self._repository.get_schema_capabilities()
        admin = await self._admin_workbench(
            current_user=current_user,
            department_ids=None,
            capabilities=capabilities,
            query=query,
            generated_at=generated_at,
        )
        core = await self._repository.get_system_core_snapshot(
            unassigned_department_id=UNASSIGNED_DEPARTMENT_ID,
            now=generated_at,
        )
        outbox = await self._repository.get_outbox_snapshot()
        dead_letters = (
            await self._repository.get_dead_letter_snapshot()
            if capabilities.outbox_dead_letters
            else None
        )
        oldest_pending_seconds = (
            max(0, int((generated_at - core_datetime(outbox.oldest_pending_at)).total_seconds()))
            if outbox.oldest_pending_at is not None
            else None
        )
        system = SystemWorkbench(
            database=ComponentHealth(
                status="ok",
                source="dashboard_database_query",
            ),
            worker_heartbeats=ComponentHealth(
                status="unavailable",
                source="not_collected",
            ),
            outbox=OutboxSnapshot(
                pending=outbox.pending,
                oldest_pending_seconds=oldest_pending_seconds,
            ),
            dead_letters=DeadLetterSnapshot(
                available=dead_letters is not None,
                pending=dead_letters.pending if dead_letters is not None else None,
                requeued=dead_letters.requeued if dead_letters is not None else None,
                resolved=dead_letters.resolved if dead_letters is not None else None,
            ),
            unassigned_users=UnassignedUsersSnapshot(count=core.unassigned_users),
            expiry=ExpirySnapshot(
                expiring=core.expiring_files,
                expired=core.expired_files,
            ),
            logical_storage=LogicalStorageSnapshot(
                file_count=core.logical_file_count,
                total_bytes=core.logical_total_bytes,
            ),
            processing=ProcessingSnapshot(
                active_sync_tasks=core.active_sync_tasks,
                failed_sync_tasks=core.failed_sync_tasks,
                stale_running_candidates=core.stale_running_candidates,
                stale_after_minutes=STALE_RUNNING_MINUTES,
            ),
        )
        await self._commit_read_then_audit(
            current_user=current_user,
            context=context,
            scope="all",
            department_ids=frozenset(),
            query=query,
            result_count=len(admin.priority_queue.items),
        )
        return SystemAdminDashboard(
            generated_at=generated_at,
            access=DashboardAccess(scope="all", ready=True, department_ids=[]),
            admin=admin,
            system=system,
        )

    async def _admin_workbench(
        self,
        *,
        current_user: AuthUserRecord,
        department_ids: frozenset[uuid.UUID] | None,
        capabilities: SchemaCapabilities,
        query: DashboardQuery,
        generated_at: datetime,
    ) -> AdminWorkbench:
        if department_ids is not None and not department_ids:
            counts = ReviewQueueCountsRecord(
                scope_total_pending=0,
                unclaimed=0 if capabilities.review_claim_sla else None,
                mine=0 if capabilities.review_claim_sla else None,
                due_soon=0 if capabilities.review_claim_sla else None,
                overdue=0 if capabilities.review_claim_sla else None,
                sync_failed=0,
            )
            rows: list[ReviewQueueRecord] = []
            total = 0
        else:
            counts = await self._repository.get_review_queue_counts(
                department_ids=department_ids,
                current_user_id=current_user.id,
                now=generated_at,
                review_claim_sla=capabilities.review_claim_sla,
                due_soon_hours=DUE_SOON_HOURS,
            )
            rows, total = await self._repository.list_priority_review_queue(
                department_ids=department_ids,
                now=generated_at,
                review_claim_sla=capabilities.review_claim_sla,
                page=query.page,
                page_size=query.page_size,
                q=query.q,
            )
        return AdminWorkbench(
            counts=ReviewQueueCounts(
                scope_total_pending=counts.scope_total_pending,
                unclaimed=counts.unclaimed,
                mine=counts.mine,
                due_soon=counts.due_soon,
                overdue=counts.overdue,
                sync_failed=counts.sync_failed,
                claim_sla_available=capabilities.review_claim_sla,
            ),
            priority_queue=ReviewQueuePage(
                items=[
                    _review_queue_item(
                        row,
                        current_user_id=current_user.id,
                        now=generated_at,
                        claim_sla_available=capabilities.review_claim_sla,
                    )
                    for row in rows
                ],
                page=query.page,
                page_size=query.page_size,
                total=total,
                total_pages=(total + query.page_size - 1) // query.page_size,
                q_applied=query.q is not None,
                claim_sla_available=capabilities.review_claim_sla,
                sort_policy=(
                    "sla_risk_submitted"
                    if capabilities.review_claim_sla
                    else "risk_uploaded_legacy"
                ),
            ),
        )

    async def _commit_read_then_audit(
        self,
        *,
        current_user: AuthUserRecord,
        context: RequestContext,
        scope: str,
        department_ids: frozenset[uuid.UUID],
        query: DashboardQuery,
        result_count: int,
    ) -> None:
        # End the aggregate read transaction before opening the short audit transaction.
        await self._session.commit()
        sorted_ids = sorted(str(department_id) for department_id in department_ids)
        scope_digest = hashlib.sha256(",".join(sorted_ids).encode("utf-8")).hexdigest()
        await record_admin_audit_log(
            self._session,
            actor_id=current_user.id,
            action="dashboard.view",
            target_type="dashboard",
            target_id=current_user.id,
            ip_address=context.ip_address,
            user_agent=context.user_agent,
            metadata_json={
                "actor_role": current_user.role,
                "scope": scope,
                "department_count": len(department_ids),
                "department_scope_sha256": scope_digest,
                "page": query.page,
                "page_size": query.page_size,
                "q_applied": query.q is not None,
                "result_count": result_count,
            },
        )
        await self._session.commit()


def _employee_status_counts(record: EmployeeCountsRecord) -> EmployeeStatusCounts:
    return EmployeeStatusCounts(
        total=record.total,
        draft=record.draft,
        ai_processing=record.ai_processing,
        analysis_failed=record.analysis_failed,
        sensitive_review=record.sensitive_review,
        pending_review=record.pending_review,
        approved=record.approved,
        rejected=record.rejected,
        sync_processing=record.sync_processing,
        parsed=record.parsed,
        sync_failed=record.sync_failed,
        archived=record.archived,
    )


def _employee_action_counts(record: EmployeeCountsRecord) -> EmployeeActionCounts:
    total = record.draft + record.rejected + record.sensitive_review + record.analysis_failed
    return EmployeeActionCounts(
        total=total,
        submit_draft=record.draft,
        revise_rejected=record.rejected,
        confirm_sensitive=record.sensitive_review,
        analysis_failed=record.analysis_failed,
    )


def _recent_document(record: RecentDocumentRecord) -> RecentDocument:
    action_by_status: dict[str, NextDocumentAction] = {
        "uploaded": "submit_review",
        "analyzed": "submit_review",
        "rejected": "revise_rejected",
        "sensitive_review_required": "confirm_sensitive",
        "extracting_text": "view_progress",
        "analysis_queued": "view_progress",
        "analyzing": "view_progress",
        "pending_review": "view_progress",
        "queued": "view_progress",
        "syncing": "view_progress",
        "uploaded_to_ragflow": "view_progress",
        "parsing": "view_progress",
    }
    return RecentDocument(
        id=record.id,
        original_name=record.original_name,
        extension=record.extension,
        status=record.status,
        review_status=record.review_status,
        updated_at=record.updated_at,
        next_action=action_by_status.get(record.status, "view_detail"),
    )


def _recent_notification(record: RecentNotificationRecord) -> RecentNotification:
    resource_type, resource_id = _safe_notification_resource(record)
    return RecentNotification(
        id=record.id,
        type=record.type,
        title=record.title,
        body_excerpt=record.body.strip()[:MAX_NOTIFICATION_EXCERPT],
        is_read=record.read_at is not None,
        created_at=record.created_at,
        resource_type=resource_type,
        resource_id=resource_id,
    )


def _safe_notification_resource(
    record: RecentNotificationRecord,
) -> tuple[str | None, uuid.UUID | None]:
    candidates = (
        (record.resource_type, record.resource_id),
        ("file", record.file_id),
        ("sync_task", record.task_id),
    )
    for resource_type, raw_id in candidates:
        if resource_type not in {"file", "sync_task"} or raw_id is None:
            continue
        try:
            return resource_type, uuid.UUID(raw_id)
        except (AttributeError, TypeError, ValueError):
            continue
    return None, None


def _review_queue_item(
    record: ReviewQueueRecord,
    *,
    current_user_id: uuid.UUID,
    now: datetime,
    claim_sla_available: bool,
) -> ReviewQueueItem:
    claim_state = _claim_state(
        record,
        current_user_id=current_user_id,
        now=now,
        claim_sla_available=claim_sla_available,
    )
    submitted_at = core_datetime(record.submitted_at) if record.submitted_at is not None else None
    wait_seconds = (
        max(0, int((now - submitted_at).total_seconds())) if submitted_at is not None else None
    )
    risk = record.sensitive_risk_level
    return ReviewQueueItem(
        id=record.id,
        original_name=record.original_name,
        extension=record.extension,
        uploader_name=record.uploader_name,
        department_id=record.department_id,
        department_name=record.department_name,
        sensitive_risk_level=_safe_risk_level(risk),
        submitted_at=submitted_at,
        review_due_at=(
            core_datetime(record.review_due_at) if record.review_due_at is not None else None
        ),
        wait_seconds=wait_seconds,
        claim_state=claim_state,
        claimed_by_name=(
            record.claimed_by_name if claim_state in {"mine", "claimed_by_other"} else None
        ),
    )


def _claim_state(
    record: ReviewQueueRecord,
    *,
    current_user_id: uuid.UUID,
    now: datetime,
    claim_sla_available: bool,
) -> str:
    if not claim_sla_available:
        return "unavailable"
    if record.claimed_by is None or record.claimed_at is None or record.claim_expires_at is None:
        return "unclaimed"
    claimed_at = core_datetime(record.claimed_at)
    claim_expires_at = core_datetime(record.claim_expires_at)
    if claim_expires_at <= claimed_at or claim_expires_at <= now:
        return "expired"
    if record.claimed_by == current_user_id:
        return "mine"
    return "claimed_by_other"


def _safe_risk_level(value: str | None) -> RiskLevel | None:
    if value not in VALID_RISK_LEVELS:
        return None
    return cast(RiskLevel, value)


def _normalize_query(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_datetime(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    return core_datetime(value)


def core_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
