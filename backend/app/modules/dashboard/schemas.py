from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field

DashboardRole = Literal["employee", "dept_admin", "system_admin"]
ScopeKind = Literal["self", "managed_departments", "all"]
AccessBlocker = Literal["department_required", "managed_departments_required"]
RiskLevel = Literal["none", "low", "medium", "high", "critical"]
DeepLinkResource = Literal["file", "sync_task"]
ClaimState = Literal["unclaimed", "mine", "claimed_by_other", "expired", "unavailable"]
NextDocumentAction = Literal[
    "submit_review",
    "revise_rejected",
    "confirm_sensitive",
    "view_progress",
    "view_detail",
]


class DashboardAccess(BaseModel):
    scope: ScopeKind
    ready: bool
    blocker: AccessBlocker | None = None
    department_ids: list[UUID] = Field(default_factory=list)


class EmployeeStatusCounts(BaseModel):
    total: int = Field(ge=0)
    draft: int = Field(ge=0)
    ai_processing: int = Field(ge=0)
    analysis_failed: int = Field(ge=0)
    sensitive_review: int = Field(ge=0)
    pending_review: int = Field(ge=0)
    approved: int = Field(ge=0)
    rejected: int = Field(ge=0)
    sync_processing: int = Field(ge=0)
    parsed: int = Field(ge=0)
    sync_failed: int = Field(ge=0)
    archived: int = Field(ge=0)


class EmployeeActionCounts(BaseModel):
    total: int = Field(ge=0)
    submit_draft: int = Field(ge=0)
    revise_rejected: int = Field(ge=0)
    confirm_sensitive: int = Field(ge=0)
    analysis_failed: int = Field(ge=0)


class RecentDocument(BaseModel):
    id: UUID
    original_name: str
    extension: str
    status: str
    review_status: str
    updated_at: datetime
    next_action: NextDocumentAction


class RecentNotification(BaseModel):
    id: UUID
    type: str
    title: str
    body_excerpt: str
    is_read: bool
    created_at: datetime
    resource_type: DeepLinkResource | None = None
    resource_id: UUID | None = None


class EmployeeWorkbench(BaseModel):
    status_counts: EmployeeStatusCounts
    action_counts: EmployeeActionCounts
    recent_documents: list[RecentDocument] = Field(max_length=5)
    recent_notifications: list[RecentNotification] = Field(max_length=5)
    unread_notification_count: int = Field(ge=0)


class ReviewQueueCounts(BaseModel):
    scope_total_pending: int = Field(ge=0)
    unclaimed: int | None = Field(default=None, ge=0)
    mine: int | None = Field(default=None, ge=0)
    due_soon: int | None = Field(default=None, ge=0)
    overdue: int | None = Field(default=None, ge=0)
    sync_failed: int = Field(ge=0)
    claim_sla_available: bool


class ReviewQueueItem(BaseModel):
    id: UUID
    original_name: str
    extension: str
    uploader_name: str
    department_id: UUID
    department_name: str
    sensitive_risk_level: RiskLevel | None = None
    submitted_at: datetime | None = None
    review_due_at: datetime | None = None
    wait_seconds: int | None = Field(default=None, ge=0)
    claim_state: ClaimState
    claimed_by_name: str | None = None


class ReviewQueuePage(BaseModel):
    items: list[ReviewQueueItem]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=50)
    total: int = Field(ge=0)
    total_pages: int = Field(ge=0)
    q_applied: bool
    claim_sla_available: bool
    sort_policy: Literal["sla_risk_submitted", "risk_uploaded_legacy"]


class AdminWorkbench(BaseModel):
    counts: ReviewQueueCounts
    priority_queue: ReviewQueuePage


class OutboxSnapshot(BaseModel):
    pending: int = Field(ge=0)
    oldest_pending_seconds: int | None = Field(default=None, ge=0)


class DeadLetterSnapshot(BaseModel):
    metric_scope: Literal["outbox_event_dead_letters"] = "outbox_event_dead_letters"
    rabbitmq_queue_depth_available: bool = False
    available: bool
    pending: int | None = Field(default=None, ge=0)
    requeued: int | None = Field(default=None, ge=0)
    resolved: int | None = Field(default=None, ge=0)


class ExpirySnapshot(BaseModel):
    expiring: int = Field(ge=0)
    expired: int = Field(ge=0)


class LogicalStorageSnapshot(BaseModel):
    file_count: int = Field(ge=0)
    total_bytes: int = Field(ge=0)
    metric_scope: Literal["active_files_database_logical_size"] = (
        "active_files_database_logical_size"
    )
    physical_capacity_available: bool = False


class ProcessingSnapshot(BaseModel):
    active_sync_tasks: int = Field(ge=0)
    failed_sync_tasks: int = Field(ge=0)
    stale_running_candidates: int = Field(ge=0)
    stale_after_minutes: int = Field(ge=1)


class ComponentHealth(BaseModel):
    status: Literal["ok", "unavailable"]
    source: Literal["dashboard_database_query", "not_collected"]


class UnassignedUsersSnapshot(BaseModel):
    count: int = Field(ge=0)
    metric_scope: Literal["active_users"] = "active_users"


class SystemWorkbench(BaseModel):
    database: ComponentHealth
    worker_heartbeats: ComponentHealth
    outbox: OutboxSnapshot
    dead_letters: DeadLetterSnapshot
    unassigned_users: UnassignedUsersSnapshot
    expiry: ExpirySnapshot
    logical_storage: LogicalStorageSnapshot
    processing: ProcessingSnapshot


class EmployeeDashboard(BaseModel):
    role: Literal["employee"] = "employee"
    generated_at: datetime
    access: DashboardAccess
    employee: EmployeeWorkbench | None
    admin: None = None
    system: None = None


class DepartmentAdminDashboard(BaseModel):
    role: Literal["dept_admin"] = "dept_admin"
    generated_at: datetime
    access: DashboardAccess
    employee: None = None
    admin: AdminWorkbench
    system: None = None


class SystemAdminDashboard(BaseModel):
    role: Literal["system_admin"] = "system_admin"
    generated_at: datetime
    access: DashboardAccess
    employee: None = None
    admin: AdminWorkbench
    system: SystemWorkbench


DashboardPayload = Annotated[
    EmployeeDashboard | DepartmentAdminDashboard | SystemAdminDashboard,
    Field(discriminator="role"),
]


class DashboardEnvelope(BaseModel):
    success: Literal[True]
    data: DashboardPayload
    message: str
    request_id: str
