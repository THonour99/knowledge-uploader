from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import BaseModel


class DocumentModuleStatus(BaseModel):
    name: str = "document"


def effective_expiry_status(
    *,
    expires_at: datetime | None,
    stored_status: str | None,
    now: datetime | None = None,
    warning_window_days: int = 7,
) -> str:
    if expires_at is None:
        return "never"
    effective_now = now or datetime.now(UTC)
    if effective_now.tzinfo is None:
        effective_now = effective_now.replace(tzinfo=UTC)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= effective_now:
        return "expired"
    if expires_at <= effective_now + timedelta(days=warning_window_days):
        return "expiring"
    if stored_status in {"active", "expiring", "expired"}:
        return stored_status
    return "active"


class FileResponse(BaseModel):
    id: UUID
    original_name: str
    extension: str
    mime_type: str
    size: int
    uploader_id: UUID
    department_id: UUID
    department_name: str | None = None
    department_code: str | None = None
    department: str | None
    category_id: UUID | None
    dataset_mapping_id: UUID | None
    visibility: str
    description: str | None
    # AI 建议标签 (建议值); 正式标签实体见 review 模块 GET /api/tags 与 file_tags 关联。
    tags: list[str]
    status: str
    review_status: str
    ragflow_dataset_id: str | None
    ragflow_document_id: str | None
    ragflow_parse_status: str | None
    ai_analysis_enabled_at_upload: bool
    expires_at: datetime | None
    expiry_status: str
    uploaded_at: datetime
    last_sync_at: datetime | None
    created_at: datetime
    updated_at: datetime
    duplicate: bool = False
    duplicate_file_id: UUID | None = None


class FileListResponse(BaseModel):
    items: list[FileResponse]
    total: int


class FileAnalysisDetail(BaseModel):
    status: str
    summary: str | None
    sensitive_risk_level: str
    quality_score: float | None = None
    extracted_text_preview: str | None
    tables_json: list[dict[str, object]] = []
    table_count: int = 0
    similar_file_ids: list[str] = []
    error_message: str | None
    finished_at: datetime | None


class FileDetailResponse(FileResponse):
    category_name: str | None = None
    analysis: FileAnalysisDetail | None = None
    sync_error: str | None = None


class UploadPolicyResponse(BaseModel):
    allowed_extensions: list[str]
    allow_multi_file: bool
    upload_enabled: bool
    max_file_size_mb: int
    allow_user_delete: bool
