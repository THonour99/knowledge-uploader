from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RagflowUploadResult:
    document_id: str
    raw: dict[str, object]


@dataclass(frozen=True)
class RagflowDocumentStatus:
    document_id: str
    run: str
    progress: float | None
    raw: dict[str, object]


class RagflowClientError(Exception):
    pass


class RagflowClient(Protocol):
    async def ping(self) -> bool: ...

    async def upload_document(
        self,
        *,
        dataset_id: str,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> RagflowUploadResult: ...

    async def update_document_metadata(
        self,
        *,
        dataset_id: str,
        document_id: str,
        name: str,
        metadata: dict[str, object],
    ) -> None: ...

    async def start_parse(self, *, dataset_id: str, document_id: str) -> None: ...

    async def get_document_status(
        self,
        *,
        dataset_id: str,
        document_id: str,
    ) -> RagflowDocumentStatus: ...

    async def delete_document(self, *, dataset_id: str, document_id: str) -> None: ...
