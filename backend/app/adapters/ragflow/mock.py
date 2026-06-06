from __future__ import annotations

from .base import RagflowDocumentStatus, RagflowUploadResult


class MockRagflowClient:
    async def ping(self) -> bool:
        return True

    async def upload_document(
        self,
        *,
        dataset_id: str,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> RagflowUploadResult:
        return RagflowUploadResult(
            document_id=f"mock-{dataset_id}-{filename}",
            raw={"dataset_id": dataset_id, "name": filename, "size": len(content)},
        )

    async def update_document_metadata(
        self,
        *,
        dataset_id: str,
        document_id: str,
        name: str,
        metadata: dict[str, object],
    ) -> None:
        return None

    async def start_parse(self, *, dataset_id: str, document_id: str) -> None:
        return None

    async def get_document_status(
        self,
        *,
        dataset_id: str,
        document_id: str,
    ) -> RagflowDocumentStatus:
        return RagflowDocumentStatus(
            document_id=document_id,
            run="DONE",
            progress=1.0,
            raw={"dataset_id": dataset_id, "id": document_id, "run": "DONE"},
        )

    async def delete_document(self, *, dataset_id: str, document_id: str) -> None:
        return None
