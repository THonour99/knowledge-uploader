from __future__ import annotations

from typing import cast

import httpx
from httpx._types import QueryParamTypes, RequestFiles

from .base import (
    RagflowClientError,
    RagflowDocumentNotFoundError,
    RagflowDocumentStatus,
    RagflowUploadResult,
)

DEFAULT_TIMEOUT_SECONDS = 30.0


class HttpRagflowClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._client = client

    async def ping(self) -> bool:
        try:
            await self.check_connection()
        except RagflowClientError:
            return False
        return True

    async def check_connection(self) -> None:
        """显式连通性探测; 失败时抛出已脱敏的 RagflowClientError, 保留错误详情。"""
        await self._request(
            "GET",
            "/api/v1/datasets",
            params={"page": 1, "page_size": 1},
        )

    async def upload_document(
        self,
        *,
        dataset_id: str,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> RagflowUploadResult:
        payload = await self._request(
            "POST",
            f"/api/v1/datasets/{dataset_id}/documents",
            files={"file": (filename, content, content_type)},
        )
        document = self._extract_first_document(payload)
        document_id = document.get("id")
        if not isinstance(document_id, str) or not document_id:
            raise self._client_error("RAGFlow upload response missing document id")
        return RagflowUploadResult(document_id=document_id, raw=document)

    async def update_document_metadata(
        self,
        *,
        dataset_id: str,
        document_id: str,
        name: str,
        metadata: dict[str, object],
    ) -> None:
        await self._request(
            "PUT",
            f"/api/v1/datasets/{dataset_id}/documents/{document_id}",
            json={"name": name, "meta_fields": metadata},
        )

    async def start_parse(self, *, dataset_id: str, document_id: str) -> None:
        await self._request(
            "POST",
            f"/api/v1/datasets/{dataset_id}/chunks",
            json={"document_ids": [document_id]},
        )

    async def get_document_status(
        self,
        *,
        dataset_id: str,
        document_id: str,
    ) -> RagflowDocumentStatus:
        payload = await self._request(
            "GET",
            f"/api/v1/datasets/{dataset_id}/documents",
            params={"page": 1, "page_size": 1, "id": document_id},
        )
        document = self._extract_document_by_id(payload, document_id)
        returned_id = document.get("id")
        if not isinstance(returned_id, str):
            raise self._client_error("RAGFlow response missing requested document id")
        run = document.get("run")
        if not isinstance(run, str) or not run:
            run = "UNKNOWN"
        return RagflowDocumentStatus(
            document_id=returned_id,
            run=run,
            progress=_optional_float(document.get("progress")),
            raw=document,
        )

    async def delete_document(self, *, dataset_id: str, document_id: str) -> None:
        try:
            await self._request(
                "DELETE",
                f"/api/v1/datasets/{dataset_id}/documents",
                json={"ids": [document_id]},
            )
        except RagflowDocumentNotFoundError:
            raise
        except RagflowClientError as exc:
            if _is_document_not_found(str(exc)):
                raise RagflowDocumentNotFoundError(str(exc)) from None
            raise

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: object | None = None,
        files: RequestFiles | None = None,
        params: QueryParamTypes | None = None,
    ) -> dict[str, object]:
        if not self._api_key.strip():
            raise self._client_error("RAGFlow API key is not configured")
        url = f"{self._base_url}{path}"
        try:
            if self._client is not None:
                response = await self._client.request(
                    method,
                    url,
                    headers=self._headers(),
                    json=json,
                    files=files,
                    params=params,
                )
            else:
                async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                    response = await client.request(
                        method,
                        url,
                        headers=self._headers(),
                        json=json,
                        files=files,
                        params=params,
                    )
        except httpx.HTTPError as exc:
            raise self._client_error(f"RAGFlow request failed: {type(exc).__name__}") from None
        return self._parse_response(response)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    def _parse_response(self, response: httpx.Response) -> dict[str, object]:
        if response.status_code >= 400:
            raise self._client_error(f"RAGFlow request failed: HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError:
            raise self._client_error("RAGFlow response is not JSON") from None
        if not isinstance(payload, dict):
            raise self._client_error("RAGFlow response has invalid shape")

        typed_payload = cast(dict[str, object], payload)
        code = typed_payload.get("code")
        if code not in (0, "0"):
            message = typed_payload.get("message")
            detail = str(message) if message is not None else "unknown error"
            raise self._client_error(f"RAGFlow API returned code {code}: {detail}")
        return typed_payload

    def _extract_first_document(self, payload: dict[str, object]) -> dict[str, object]:
        documents = self._extract_documents(payload)
        if documents:
            return documents[0]
        raise self._client_error("RAGFlow response missing document data")

    def _extract_document_by_id(
        self,
        payload: dict[str, object],
        document_id: str,
    ) -> dict[str, object]:
        for document in self._extract_documents(payload):
            if document.get("id") == document_id:
                return document
        raise self._client_error("RAGFlow response missing requested document id")

    def _extract_documents(self, payload: dict[str, object]) -> list[dict[str, object]]:
        data = payload.get("data")
        documents: list[dict[str, object]] = []
        if isinstance(data, list) and data:
            documents.extend(dict(item) for item in data if isinstance(item, dict))
        if isinstance(data, dict):
            if isinstance(data.get("id"), str):
                documents.append(dict(data))
            docs = data.get("docs")
            if isinstance(docs, list) and docs:
                documents.extend(dict(item) for item in docs if isinstance(item, dict))
        return documents

    def _client_error(self, message: str) -> RagflowClientError:
        return RagflowClientError(redact_secret(message, self._api_key))


def _is_document_not_found(message: str) -> bool:
    """识别远端文档不存在的错误 (HTTP 404 或 RAGFlow 'not found' 业务码消息)。"""
    lowered = message.lower()
    return "http 404" in lowered or "not found" in lowered


def _optional_float(value: object) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def redact_secret(value: str, secret: str) -> str:
    """把消息中出现的 secret 替换为 ****; secret 为空时原样返回。"""
    if not secret:
        return value
    return value.replace(secret, "****")
