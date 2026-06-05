from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class DocumentModuleStatus(BaseModel):
    name: str = "document"


class FileResponse(BaseModel):
    id: UUID
    original_name: str
    extension: str
    mime_type: str
    size: int
    uploader_id: UUID
    department: str | None
    category_id: UUID | None
    dataset_mapping_id: UUID | None
    visibility: str
    description: str | None
    tags: list[str]
    status: str
    review_status: str
    ragflow_dataset_id: str | None
    ragflow_document_id: str | None
    ragflow_parse_status: str | None
    ai_analysis_enabled_at_upload: bool
    uploaded_at: datetime
    last_sync_at: datetime | None
    created_at: datetime
    updated_at: datetime
    duplicate: bool = False
    duplicate_file_id: UUID | None = None


class FileListResponse(BaseModel):
    items: list[FileResponse]
    total: int
