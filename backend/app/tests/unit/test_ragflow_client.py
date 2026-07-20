from __future__ import annotations

import json

import httpx
import pytest

from app.adapters.ragflow.base import (
    RagflowClientError,
    RagflowSubmissionOutcomeUnknownError,
)
from app.adapters.ragflow.http import HttpRagflowClient

pytestmark = pytest.mark.asyncio


async def test_http_ragflow_client_refuses_redirect_without_forwarding_api_key() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.host == "ragflow.internal":
            return httpx.Response(
                302,
                headers={"location": "https://attacker.invalid/capture"},
            )
        raise AssertionError("redirect target must never receive a request")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    ) as http_client:
        client = HttpRagflowClient(
            base_url="https://ragflow.internal",
            api_key="sk-existing-secret",
            client=http_client,
        )
        with pytest.raises(RagflowClientError, match="refused an HTTP redirect"):
            await client.check_connection()

    assert len(calls) == 1
    assert calls[0].url.host == "ragflow.internal"
    assert calls[0].headers["authorization"] == "Bearer sk-existing-secret"


async def test_http_ragflow_client_calls_document_lifecycle_endpoints() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.headers["authorization"] == "Bearer sk-test-secret"
        if request.method == "POST" and request.url.path == "/api/v1/datasets/dataset-1/documents":
            assert "multipart/form-data" in request.headers["content-type"]
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": [
                        {
                            "id": "doc-1",
                            "name": "manual.txt",
                            "run": "UNSTART",
                        }
                    ],
                },
            )
        if (
            request.method == "PUT"
            and request.url.path == "/api/v1/datasets/dataset-1/documents/doc-1"
        ):
            body = json.loads(request.content)
            assert body == {
                "name": "manual.txt",
                "meta_fields": {"source": "knowledge_uploader"},
            }
            return httpx.Response(200, json={"code": 0, "data": {"id": "doc-1"}})
        if request.method == "POST" and request.url.path == "/api/v1/datasets/dataset-1/chunks":
            assert json.loads(request.content) == {"document_ids": ["doc-1"]}
            return httpx.Response(200, json={"code": 0})
        if request.method == "GET" and request.url.path == "/api/v1/datasets/dataset-1/documents":
            if "keywords" in request.url.params:
                assert "name" not in request.url.params
                assert request.url.params["keywords"] == "manual.txt"
                assert request.url.params["page_size"] == "100"
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {
                            "total": 1,
                            "docs": [{"id": "doc-1", "name": "manual.txt", "run": "UNSTART"}],
                        },
                    },
                )
            assert request.url.params["id"] == "doc-1"
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"docs": [{"id": "doc-1", "run": "DONE", "progress": 1.0}]},
                },
            )
        if (
            request.method == "DELETE"
            and request.url.path == "/api/v1/datasets/dataset-1/documents"
        ):
            assert json.loads(request.content) == {"ids": ["doc-1"]}
            return httpx.Response(200, json={"code": 0})
        return httpx.Response(404, json={"code": 404, "message": "unexpected endpoint"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = HttpRagflowClient(
            base_url="http://ragflow.local",
            api_key="sk-test-secret",
            client=http_client,
        )

        upload = await client.upload_document(
            dataset_id="dataset-1",
            filename="manual.txt",
            content=b"hello",
            content_type="text/plain",
        )
        reconciled = await client.find_document_by_name(
            dataset_id="dataset-1",
            name="manual.txt",
        )
        await client.update_document_metadata(
            dataset_id="dataset-1",
            document_id=upload.document_id,
            name="manual.txt",
            metadata={"source": "knowledge_uploader"},
        )
        await client.start_parse(dataset_id="dataset-1", document_id=upload.document_id)
        status = await client.get_document_status(
            dataset_id="dataset-1",
            document_id=upload.document_id,
        )
        await client.delete_document(dataset_id="dataset-1", document_id=upload.document_id)

    assert upload.document_id == "doc-1"
    assert reconciled is not None
    assert reconciled.document_id == "doc-1"
    assert status.document_id == "doc-1"
    assert status.run == "DONE"
    assert status.progress == 1.0
    assert [(call.method, call.url.path) for call in calls] == [
        ("POST", "/api/v1/datasets/dataset-1/documents"),
        ("GET", "/api/v1/datasets/dataset-1/documents"),
        ("PUT", "/api/v1/datasets/dataset-1/documents/doc-1"),
        ("POST", "/api/v1/datasets/dataset-1/chunks"),
        ("GET", "/api/v1/datasets/dataset-1/documents"),
        ("DELETE", "/api/v1/datasets/dataset-1/documents"),
    ]


async def test_http_ragflow_client_redacts_api_key_from_errors() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 102, "message": "bad key sk-live-secret"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = HttpRagflowClient(
            base_url="http://ragflow.local",
            api_key="sk-live-secret",
            client=http_client,
        )

        with pytest.raises(RagflowClientError) as error:
            await client.start_parse(dataset_id="dataset-1", document_id="doc-1")

    assert "sk-live-secret" not in str(error.value)
    assert "****" in str(error.value)


async def test_upload_http_4xx_is_explicit_rejection_without_response_body() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            413,
            text="private remote detail containing sk-live-secret",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = HttpRagflowClient(
            base_url="http://ragflow.local",
            api_key="sk-live-secret",
            client=http_client,
        )
        with pytest.raises(RagflowClientError) as error:
            await client.upload_document(
                dataset_id="dataset-1",
                filename="manual.txt",
                content=b"hello",
                content_type="text/plain",
            )

    assert not isinstance(error.value, RagflowSubmissionOutcomeUnknownError)
    assert str(error.value) == "RAGFlow request failed: HTTP 413"
    assert "private remote detail" not in str(error.value)
    assert "sk-live-secret" not in str(error.value)


@pytest.mark.parametrize(
    ("status_code", "payload"),
    [
        (503, {"code": 503, "message": "possibly committed"}),
        (200, {"code": 0, "data": [{"name": "manual.txt"}]}),
    ],
)
async def test_upload_ambiguous_response_is_outcome_unknown(
    status_code: int,
    payload: dict[str, object],
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = HttpRagflowClient(
            base_url="http://ragflow.local",
            api_key="sk-test-secret",
            client=http_client,
        )
        with pytest.raises(RagflowSubmissionOutcomeUnknownError):
            await client.upload_document(
                dataset_id="dataset-1",
                filename="manual.txt",
                content=b"hello",
                content_type="text/plain",
            )


async def test_upload_transport_timeout_is_outcome_unknown() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("response lost", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = HttpRagflowClient(
            base_url="http://ragflow.local",
            api_key="sk-test-secret",
            client=http_client,
        )
        with pytest.raises(RagflowSubmissionOutcomeUnknownError):
            await client.upload_document(
                dataset_id="dataset-1",
                filename="manual.txt",
                content=b"hello",
                content_type="text/plain",
            )


async def test_reconciliation_uses_keywords_and_paginates_to_exact_match() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert "name" not in request.url.params
        assert request.url.params["keywords"] == "stable-target.pdf"
        page = int(request.url.params["page"])
        if page == 1:
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "total": 3,
                        "docs": [
                            {"id": "doc-1", "name": "unrelated-1.pdf"},
                            {"id": "doc-2", "name": "unrelated-2.pdf"},
                        ],
                    },
                },
            )
        assert page == 2
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "total": 3,
                    "docs": [{"id": "doc-target", "name": "stable-target.pdf"}],
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = HttpRagflowClient(
            base_url="http://ragflow.local",
            api_key="sk-test-secret",
            client=http_client,
        )
        result = await client.find_document_by_name(
            dataset_id="dataset-1",
            name="stable-target.pdf",
        )

    assert result is not None
    assert result.document_id == "doc-target"
    assert [request.url.params["page"] for request in requests] == ["1", "2"]


async def test_reconciliation_rejects_duplicate_exact_names_across_pages() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        document_id = "doc-1" if page == 1 else "doc-2"
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "total": 2,
                    "docs": [{"id": document_id, "name": "duplicate.pdf"}],
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = HttpRagflowClient(
            base_url="http://ragflow.local",
            api_key="sk-test-secret",
            client=http_client,
        )
        with pytest.raises(RagflowClientError, match="duplicate document names"):
            await client.find_document_by_name(
                dataset_id="dataset-1",
                name="duplicate.pdf",
            )


async def test_http_ragflow_client_rejects_status_document_id_mismatch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["id"] == "doc-expected"
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "docs": [
                        {
                            "id": "doc-other",
                            "run": "DONE",
                        }
                    ]
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = HttpRagflowClient(
            base_url="http://ragflow.local",
            api_key="sk-test-secret",
            client=http_client,
        )

        with pytest.raises(RagflowClientError, match="requested document id"):
            await client.get_document_status(
                dataset_id="dataset-1",
                document_id="doc-expected",
            )


async def test_http_ragflow_client_lists_and_sorts_datasets() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.method == "GET"
        assert request.url.path == "/api/v1/datasets"
        assert request.url.params["page"] == "1"
        assert request.url.params["page_size"] == "100"
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": [
                    {"id": "dataset-b", "name": "业务知识库"},
                    {"id": "dataset-a", "name": "产品资料"},
                ],
                "total_datasets": 2,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = HttpRagflowClient(
            base_url="https://ragflow.internal",
            api_key="sk-test-secret",
            client=http_client,
        )
        datasets = await client.list_datasets()

    assert [(item.dataset_id, item.name) for item in datasets] == [
        ("dataset-b", "业务知识库"),
        ("dataset-a", "产品资料"),
    ]
    assert len(calls) == 1


async def test_http_ragflow_client_rejects_malformed_dataset_items() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"code": 0, "data": [{"id": "", "name": "broken"}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = HttpRagflowClient(
            base_url="https://ragflow.internal",
            api_key="sk-test-secret",
            client=http_client,
        )
        with pytest.raises(RagflowClientError, match="missing id"):
            await client.list_datasets()
