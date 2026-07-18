from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_serializer,
    model_validator,
)


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
    title: str
    extension: str
    mime_type: str
    size: int
    uploader_id: UUID
    uploader_name: str | None = None
    owner_id: UUID | None = None
    owner_name: str | None = None
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
    submitted_at: datetime | None = None
    review_due_at: datetime | None = None
    claimed_by: UUID | None = None
    claimed_by_name: str | None = None
    claimed_at: datetime | None = None
    claim_expires_at: datetime | None = None
    review_version: int = 0
    sensitive_risk_level: str | None = None
    ragflow_dataset_id: str | None
    ragflow_document_id: str | None
    ragflow_parse_status: str | None
    ai_analysis_enabled_at_upload: bool
    expires_at: datetime | None
    expiry_status: str
    series_id: UUID
    version_number: int
    replaces_file_id: UUID | None = None
    replacement_remote_action: Literal["delete", "archive"] | None = None
    is_current_version: bool
    remote_visibility: str
    version_switch_status: str
    version_switch_error: str | None = None
    version_switch_attempt_count: int = 0
    predecessor_remote_deactivated_at: datetime | None = None
    local_version_activated_at: datetime | None = None
    remote_version_activated_at: datetime | None = None
    uploaded_at: datetime
    last_sync_at: datetime | None
    created_at: datetime
    updated_at: datetime
    duplicate: bool = False
    duplicate_file_id: UUID | None = None


class FileListResponse(BaseModel):
    items: list[FileResponse]
    total: int
    page: int = 1
    page_size: int = 20
    total_pages: int = 0


DraftTitle = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=255),
]


class FileDraftUpdateRequest(BaseModel):
    """Owner-only draft metadata mutation guarded by the shared file version."""

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(strict=True, ge=0, le=2_147_483_647)
    title: DraftTitle | None = None
    description: str | None = Field(default=None, max_length=2000)
    visibility: Literal["private", "department", "company"] | None = None
    owner_id: UUID | None = None
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def validate_patch(self) -> Self:
        mutable_fields = {"title", "description", "visibility", "owner_id", "expires_at"}
        supplied_fields = self.model_fields_set & mutable_fields
        if not supplied_fields:
            raise ValueError("at least one draft metadata field is required")
        if "title" in supplied_fields and self.title is None:
            raise ValueError("title cannot be null")
        if "visibility" in supplied_fields and self.visibility is None:
            raise ValueError("visibility cannot be null")
        if "owner_id" in supplied_fields and self.owner_id is None:
            raise ValueError("owner_id cannot be null")
        return self


class FileAnalysisDetail(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    status: str
    engine_type: Literal["rule", "llm", "hybrid"] = "rule"
    provider_name: str | None = None
    model_name: str | None = None
    prompt_template_key: str | None = None
    prompt_version: int | None = None
    input_char_count: int | None = None
    input_sha256: str | None = None
    category_count: int | None = None
    input_truncated: bool | None = None
    attempt_number: int = 1
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    failure_category: str | None = None
    cost_status: Literal[
        "known",
        "unknown_pricing",
        "unknown_usage",
        "legacy_unverifiable",
    ] = "legacy_unverifiable"
    estimated_cost_microunits: int | None = Field(default=None, ge=0)
    cost_currency: str = "USD"
    summary: str | None
    sensitive_risk_level: str
    quality_score: float | None = None
    extracted_text_preview: str | None
    tables_json: list[dict[str, object]] = []
    table_count: int = 0
    similar_file_ids: list[str] = []
    error_message: str | None
    finished_at: datetime | None

    @model_validator(mode="after")
    def enforce_cost_contract(self) -> Self:
        if self.cost_status == "known":
            if self.estimated_cost_microunits is None:
                raise ValueError("known cost_status requires a non-negative estimated cost")
            return self
        self.estimated_cost_microunits = None
        return self

    @field_serializer("estimated_cost_microunits")
    def serialize_estimated_cost(self, value: int | None) -> str | None:
        return str(value) if value is not None else None


class VersionChainItem(BaseModel):
    id: UUID
    version_number: int
    replaces_file_id: UUID | None = None
    replacement_remote_action: Literal["delete", "archive"] | None = None
    title: str
    status: str
    is_current_version: bool
    remote_visibility: str
    version_switch_status: str
    version_switch_error: str | None = None
    created_at: datetime


class OwnerOptionResponse(BaseModel):
    id: UUID
    name: str


class OwnerOptionListResponse(BaseModel):
    items: list[OwnerOptionResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class FileDetailResponse(FileResponse):
    category_name: str | None = None
    analysis: FileAnalysisDetail | None = None
    sync_error: str | None = None
    version_chain: list[VersionChainItem] = Field(default_factory=list)


class UploadPolicyResponse(BaseModel):
    allowed_extensions: list[str]
    allow_multi_file: bool
    upload_enabled: bool
    max_file_size_mb: int
    allow_user_delete: bool
