from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass
class ReviewFileRecord:
    id: UUID
    original_name: str
    title: str
    extension: str
    mime_type: str
    size: int
    uploader_id: UUID
    uploader_name: str | None
    owner_id: UUID | None
    owner_name: str | None
    department_id: UUID
    department: str | None
    category_id: UUID | None
    dataset_mapping_id: UUID | None
    visibility: str
    description: str | None
    tags: list[str]  # AI 建议标签快照; 正式标签关联见 tags/file_tags
    status: str
    review_status: str
    submitted_at: datetime | None
    review_due_at: datetime | None
    claimed_by: UUID | None
    claimed_by_name: str | None
    claimed_at: datetime | None
    claim_expires_at: datetime | None
    review_version: int
    sensitive_risk_level: str | None
    ragflow_dataset_id: str | None
    ragflow_document_id: str | None
    ragflow_parse_status: str | None
    ai_analysis_enabled_at_upload: bool
    expires_at: datetime | None
    expiry_status: str
    series_id: UUID
    version_number: int
    replaces_file_id: UUID | None
    replacement_remote_action: str | None
    is_current_version: bool
    remote_visibility: str
    version_switch_status: str
    version_switch_error: str | None
    version_switch_attempt_count: int
    predecessor_remote_deactivated_at: datetime | None
    local_version_activated_at: datetime | None
    remote_version_activated_at: datetime | None
    uploaded_at: datetime
    last_sync_at: datetime | None
    created_at: datetime
    updated_at: datetime
