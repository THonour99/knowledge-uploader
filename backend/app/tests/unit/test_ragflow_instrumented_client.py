from __future__ import annotations

import uuid

import pytest

from app.adapters.ragflow import instrumented
from app.adapters.ragflow.base import (
    RagflowClientError,
    RagflowDocumentNotFoundError,
    RagflowDocumentStatus,
    RagflowUploadResult,
)
from app.adapters.ragflow.instrumented import InstrumentedRagflowClient
from app.modules.ragflow.service import (  # noqa: TID251 - production wiring regression
    _instrument_ragflow_client,
)


class FakeRagflowClient:
    def __init__(self, *, error: Exception | None = None, ping_result: bool = True) -> None:
        self.error = error
        self.ping_result = ping_result

    async def ping(self) -> bool:
        self._raise_if_needed()
        return self.ping_result

    async def check_connection(self) -> None:
        self._raise_if_needed()

    async def upload_document(
        self,
        *,
        dataset_id: str,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> RagflowUploadResult:
        del dataset_id, filename, content, content_type
        self._raise_if_needed()
        return RagflowUploadResult(document_id="remote-1", raw={})

    async def find_document_by_name(
        self,
        *,
        dataset_id: str,
        name: str,
    ) -> RagflowUploadResult | None:
        del dataset_id, name
        self._raise_if_needed()
        return None

    async def update_document_metadata(
        self,
        *,
        dataset_id: str,
        document_id: str,
        name: str,
        metadata: dict[str, object],
    ) -> None:
        del dataset_id, document_id, name, metadata
        self._raise_if_needed()

    async def start_parse(self, *, dataset_id: str, document_id: str) -> None:
        del dataset_id, document_id
        self._raise_if_needed()

    async def get_document_status(
        self,
        *,
        dataset_id: str,
        document_id: str,
    ) -> RagflowDocumentStatus:
        del dataset_id, document_id
        self._raise_if_needed()
        return RagflowDocumentStatus(document_id="remote-1", run="DONE", progress=1, raw={})

    async def delete_document(self, *, dataset_id: str, document_id: str) -> None:
        del dataset_id, document_id
        self._raise_if_needed()

    def _raise_if_needed(self) -> None:
        if self.error is not None:
            raise self.error


def test_service_wiring_does_not_double_wrap_clients() -> None:
    base_client = FakeRagflowClient()
    department_id = uuid.uuid4()
    wrapped = _instrument_ragflow_client(base_client, department_id=department_id)

    assert isinstance(wrapped, InstrumentedRagflowClient)
    assert _instrument_ragflow_client(wrapped, department_id=uuid.uuid4()) is wrapped


class UnstringableRagflowError(Exception):
    def __str__(self) -> str:
        raise RuntimeError("broken exception rendering")


@pytest.fixture
def telemetry_spy(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[str, str, str | None, uuid.UUID | None]]:
    events: list[tuple[str, str, str | None, uuid.UUID | None]] = []

    async def fake_start(*, operation: str, department_id: uuid.UUID | None = None) -> uuid.UUID:
        events.append((operation, "started", None, department_id))
        return uuid.UUID(int=1)

    async def fake_finish(
        *,
        call_id: uuid.UUID | None,
        operation: str,
        result: str,
        failure_category: str | None = None,
    ) -> None:
        del call_id
        events.append((operation, result, failure_category, None))

    monkeypatch.setattr(instrumented, "best_effort_start_ragflow_api_call", fake_start)
    monkeypatch.setattr(instrumented, "best_effort_finish_ragflow_api_call", fake_finish)
    return events


@pytest.mark.asyncio
async def test_instrumented_client_records_success_without_payload(
    telemetry_spy: list[tuple[str, str, str | None, uuid.UUID | None]],
) -> None:
    department_id = uuid.uuid4()
    client = InstrumentedRagflowClient(FakeRagflowClient(), department_id=department_id)

    result = await client.upload_document(
        dataset_id="private-dataset",
        filename="private-name.pdf",
        content=b"private-content",
        content_type="application/pdf",
    )

    assert result.document_id == "remote-1"
    assert telemetry_spy == [
        ("upload_document", "started", None, department_id),
        ("upload_document", "success", None, None),
    ]


@pytest.mark.asyncio
async def test_instrumented_client_preserves_business_exception(
    telemetry_spy: list[tuple[str, str, str | None, uuid.UUID | None]],
) -> None:
    original = RagflowClientError("RAGFlow request failed: HTTP 503 secret-body")
    client = InstrumentedRagflowClient(FakeRagflowClient(error=original))

    with pytest.raises(RagflowClientError) as captured:
        await client.start_parse(dataset_id="dataset", document_id="document")

    assert captured.value is original
    assert telemetry_spy[-1][0:3] == ("start_parse", "failure", "upstream_5xx")


@pytest.mark.asyncio
async def test_failure_classification_cannot_replace_business_exception(
    telemetry_spy: list[tuple[str, str, str | None, uuid.UUID | None]],
) -> None:
    original = UnstringableRagflowError()
    client = InstrumentedRagflowClient(FakeRagflowClient(error=original))

    with pytest.raises(UnstringableRagflowError) as captured:
        await client.start_parse(dataset_id="dataset", document_id="document")

    assert captured.value is original
    assert telemetry_spy[-1][0:3] == ("start_parse", "failure", "unknown")


@pytest.mark.asyncio
async def test_telemetry_failure_does_not_change_business_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failed_start(*, operation: str, department_id: uuid.UUID | None = None) -> None:
        del operation, department_id
        return None

    async def failed_finish(**_: object) -> None:
        raise RuntimeError("telemetry unavailable")

    monkeypatch.setattr(instrumented, "best_effort_start_ragflow_api_call", failed_start)
    monkeypatch.setattr(instrumented, "best_effort_finish_ragflow_api_call", failed_finish)
    client = InstrumentedRagflowClient(FakeRagflowClient())

    result = await client.find_document_by_name(dataset_id="dataset", name="name")

    assert result is None


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (RagflowDocumentNotFoundError("missing"), "not_found"),
        (RagflowClientError("RAGFlow API key is not configured"), "configuration"),
        (RagflowClientError("HTTP 401"), "authentication"),
        (RagflowClientError("HTTP 403"), "authorization"),
        (RagflowClientError("HTTP 409"), "conflict"),
        (RagflowClientError("HTTP 429"), "rate_limited"),
        (RagflowClientError("ConnectError"), "network"),
        (RagflowClientError("ReadTimeout"), "timeout"),
        (RagflowClientError("response is not JSON"), "protocol"),
        (RagflowClientError("opaque"), "unknown"),
    ],
)
def test_failure_classification_is_bounded(error: Exception, expected: str) -> None:
    assert instrumented.classify_ragflow_failure(error) == expected
