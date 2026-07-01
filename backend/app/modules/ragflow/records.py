from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass
class RagflowSyncFileRecord:
    id: UUID
    original_name: str
    stored_name: str
    extension: str
    mime_type: str
    size: int
    bucket: str
    object_key: str
    uploader_id: UUID
    department_id: UUID
    department_name: str | None
    department_code: str | None
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
    ragflow_error_message: str | None
    uploaded_at: datetime
    last_sync_at: datetime | None


@dataclass(frozen=True)
class RagflowDatasetMappingRecord:
    id: UUID
    ragflow_dataset_id: str
    enabled: bool
