from __future__ import annotations

import json

import httpx
import pytest

from app.adapters.ragflow.base import RagflowClientError
from app.adapters.ragflow.http import HttpRagflowClient

pytestmark = pytest.mark.asyncio


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
    assert status.document_id == "doc-1"
    assert status.run == "DONE"
    assert status.progress == 1.0
    assert [(call.method, call.url.path) for call in calls] == [
        ("POST", "/api/v1/datasets/dataset-1/documents"),
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
