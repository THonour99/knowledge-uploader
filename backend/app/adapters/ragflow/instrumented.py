from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Protocol, TypeVar, cast

from app.core.ragflow_call_telemetry import (
    best_effort_finish_ragflow_api_call,
    best_effort_start_ragflow_api_call,
)

from .base import (
    RagflowClient,
    RagflowClientError,
    RagflowDataset,
    RagflowDocumentNotFoundError,
    RagflowDocumentStatus,
    RagflowUploadResult,
)

T = TypeVar("T")


class _ConnectionCheckClient(Protocol):
    async def check_connection(self) -> None: ...


class _DatasetListingClient(Protocol):
    async def list_datasets(self) -> list[RagflowDataset]: ...


class InstrumentedRagflowClient:
    """Record bounded, payload-free telemetry without changing client outcomes."""

    def __init__(
        self,
        client: RagflowClient,
        *,
        department_id: uuid.UUID | None = None,
    ) -> None:
        self._client = client
        self._department_id = department_id

    async def ping(self) -> bool:
        return await self._call("ping", self._client.ping, failure_when=lambda value: not value)

    async def check_connection(self) -> None:
        """Preserve the diagnostic check exposed by the concrete HTTP client."""
        checker = getattr(self._client, "check_connection", None)
        if checker is None or not callable(checker):

            async def check_by_ping() -> None:
                if not await self._client.ping():
                    raise RagflowClientError("RAGFlow connection check failed")

            await self._call("ping", check_by_ping)
            return
        typed_client = cast(_ConnectionCheckClient, self._client)
        await self._call("ping", typed_client.check_connection)

    async def list_datasets(self) -> list[RagflowDataset]:
        listing_client = cast(_DatasetListingClient, self._client)
        return await self._call(
            "list_datasets",
            listing_client.list_datasets,
        )

    async def upload_document(
        self,
        *,
        dataset_id: str,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> RagflowUploadResult:
        return await self._call(
            "upload_document",
            lambda: self._client.upload_document(
                dataset_id=dataset_id,
                filename=filename,
                content=content,
                content_type=content_type,
            ),
        )

    async def find_document_by_name(
        self,
        *,
        dataset_id: str,
        name: str,
    ) -> RagflowUploadResult | None:
        return await self._call(
            "find_document_by_name",
            lambda: self._client.find_document_by_name(dataset_id=dataset_id, name=name),
        )

    async def update_document_metadata(
        self,
        *,
        dataset_id: str,
        document_id: str,
        name: str,
        metadata: dict[str, object],
    ) -> None:
        await self._call(
            "update_document_metadata",
            lambda: self._client.update_document_metadata(
                dataset_id=dataset_id,
                document_id=document_id,
                name=name,
                metadata=metadata,
            ),
        )

    async def start_parse(self, *, dataset_id: str, document_id: str) -> None:
        await self._call(
            "start_parse",
            lambda: self._client.start_parse(dataset_id=dataset_id, document_id=document_id),
        )

    async def get_document_status(
        self,
        *,
        dataset_id: str,
        document_id: str,
    ) -> RagflowDocumentStatus:
        return await self._call(
            "get_document_status",
            lambda: self._client.get_document_status(
                dataset_id=dataset_id,
                document_id=document_id,
            ),
        )

    async def delete_document(self, *, dataset_id: str, document_id: str) -> None:
        await self._call(
            "delete_document",
            lambda: self._client.delete_document(
                dataset_id=dataset_id,
                document_id=document_id,
            ),
        )

    async def _call(
        self,
        operation: str,
        action: Callable[[], Awaitable[T]],
        *,
        failure_when: Callable[[T], bool] | None = None,
    ) -> T:
        call_id = await self._start_telemetry(operation)
        try:
            value = await action()
        except Exception as error:
            await self._finish_telemetry(
                call_id=call_id,
                operation=operation,
                result="failure",
                failure_category=classify_ragflow_failure(error),
            )
            raise
        is_failure = failure_when is not None and failure_when(value)
        await self._finish_telemetry(
            call_id=call_id,
            operation=operation,
            result="failure" if is_failure else "success",
            failure_category="unknown" if is_failure else None,
        )
        return value

    async def _start_telemetry(self, operation: str) -> uuid.UUID | None:
        try:
            return await best_effort_start_ragflow_api_call(
                operation=operation,
                department_id=self._department_id,
            )
        except Exception:
            return None

    async def _finish_telemetry(
        self,
        *,
        call_id: uuid.UUID | None,
        operation: str,
        result: str,
        failure_category: str | None = None,
    ) -> None:
        try:
            await best_effort_finish_ragflow_api_call(
                call_id=call_id,
                operation=operation,
                result=result,
                failure_category=failure_category,
            )
        except Exception:
            return


def classify_ragflow_failure(error: Exception) -> str:
    """Map arbitrary client failures to the fixed metrics contract without retaining detail."""
    if isinstance(error, RagflowDocumentNotFoundError):
        return "not_found"
    name = type(error).__name__.lower()
    try:
        detail = str(error).lower()
    except Exception:
        detail = ""
    combined = f"{name} {detail}"
    if "api key" in combined and ("not configured" in combined or "missing" in combined):
        return "configuration"
    if "http 401" in combined or "unauthenticated" in combined:
        return "authentication"
    if "http 403" in combined or "forbidden" in combined:
        return "authorization"
    if "http 404" in combined or "not found" in combined:
        return "not_found"
    if "http 409" in combined or "conflict" in combined or "duplicate" in combined:
        return "conflict"
    if "http 429" in combined or "rate limit" in combined:
        return "rate_limited"
    if any(f"http 5{digit}" in combined for digit in range(10)):
        return "upstream_5xx"
    if "timeout" in combined or "timed out" in combined:
        return "timeout"
    if any(
        marker in combined for marker in ("connecterror", "networkerror", "readerror", "writeerror")
    ):
        return "network"
    if any(
        marker in combined
        for marker in (
            "redirect",
            "not json",
            "invalid shape",
            "missing document",
            "protocol",
        )
    ):
        return "protocol"
    return "unknown"
