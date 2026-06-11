from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ReviewModuleStatus(BaseModel):
    name: str = "review"


class CategoryCreateRequest(BaseModel):
    name: str
    code: str
    description: str | None = None
    parent_id: UUID | None = None
    require_review: bool = True
    default_dataset_id: str | None = None
    allow_employee_select: bool = True
    allow_ai_recommend: bool = True
    default_visibility: str = "private"
    keywords: list[str] = Field(default_factory=list)
    classification_prompt: str | None = None
    ai_analysis_enabled: bool = True
    sensitive_detection_enabled: bool = True
    auto_sync_enabled: bool = False


class CategoryUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    parent_id: UUID | None = None
    require_review: bool | None = None
    default_dataset_id: str | None = None
    allow_employee_select: bool | None = None
    allow_ai_recommend: bool | None = None
    default_visibility: str | None = None
    keywords: list[str] | None = None
    classification_prompt: str | None = None
    ai_analysis_enabled: bool | None = None
    sensitive_detection_enabled: bool | None = None
    auto_sync_enabled: bool | None = None


class CategoryResponse(BaseModel):
    id: UUID
    name: str
    code: str
    description: str | None
    parent_id: UUID | None
    require_review: bool
    default_dataset_id: str | None
    allow_employee_select: bool
    allow_ai_recommend: bool
    default_visibility: str
    keywords: list[str]
    classification_prompt: str | None
    ai_analysis_enabled: bool
    sensitive_detection_enabled: bool
    auto_sync_enabled: bool
    created_at: datetime
    updated_at: datetime


class CategoryListResponse(BaseModel):
    items: list[CategoryResponse]
    total: int


class DatasetMappingCreateRequest(BaseModel):
    name: str
    category_id: UUID
    ragflow_dataset_id: str
    ragflow_dataset_name: str
    enabled: bool = True


class DatasetMappingUpdateRequest(BaseModel):
    name: str | None = None
    category_id: UUID | None = None
    ragflow_dataset_id: str | None = None
    ragflow_dataset_name: str | None = None
    enabled: bool | None = None


class DatasetMappingResponse(BaseModel):
    id: UUID
    name: str
    category_id: UUID
    ragflow_dataset_id: str
    ragflow_dataset_name: str
    enabled: bool
    created_at: datetime
    updated_at: datetime


class DatasetMappingListResponse(BaseModel):
    items: list[DatasetMappingResponse]
    total: int


class TagCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None


class TagUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    enabled: bool | None = None


class TagMergeRequest(BaseModel):
    target_tag_id: UUID


class TagResponse(BaseModel):
    id: UUID
    name: str
    description: str | None
    usage_count: int
    is_system_generated: bool
    enabled: bool
    created_at: datetime
    updated_at: datetime


class TagListResponse(BaseModel):
    items: list[TagResponse]
    total: int
    page: int
    page_size: int


class ReviewDecisionRequest(BaseModel):
    category_id: UUID | None = None
    dataset_mapping_id: UUID | None = None
    reason: str | None = None


class RejectFileRequest(BaseModel):
    reason: str


class UpdateFileClassificationRequest(BaseModel):
    category_id: UUID | None = None
    dataset_mapping_id: UUID | None = None
