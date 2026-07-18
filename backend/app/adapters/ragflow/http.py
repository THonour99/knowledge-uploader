from __future__ import annotations

from typing import cast

import httpx
from httpx._types import QueryParamTypes, RequestFiles

from .base import (
    RagflowClientError,
    RagflowDocumentNotFoundError,
    RagflowDocumentStatus,
    RagflowSubmissionOutcomeUnknownError,
    RagflowUploadResult,
)
from .safe_transport import (
    AsyncHostResolver,
    RagflowEndpointSecurityError,
    SystemHostResolver,
    build_pinned_ragflow_transport,
    resolve_and_authorize_ragflow_endpoint,
)

DEFAULT_TIMEOUT_SECONDS = 30.0
RAGFLOW_RECONCILIATION_PAGE_SIZE = 100
RAGFLOW_RECONCILIATION_MAX_PAGES = 1000


class HttpRagflowClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        protected_environment: bool = False,
        tls_spki_pins: frozenset[bytes] = frozenset(),
        resolver: AsyncHostResolver | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._protected_environment = protected_environment
        self._tls_spki_pins = tls_spki_pins
        self._resolver = resolver or SystemHostResolver()
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
            submission_outcome_unknown=True,
        )
        document = self._extract_first_document(payload)
        document_id = document.get("id")
        if not isinstance(document_id, str) or not document_id:
            raise self._submission_outcome_unknown("RAGFlow upload response missing document id")
        return RagflowUploadResult(document_id=document_id, raw=document)

    async def find_document_by_name(
        self,
        *,
        dataset_id: str,
        name: str,
    ) -> RagflowUploadResult | None:
        """Use RAGFlow's keyword filter, then enforce exact-name uniqueness client-side."""
        matches_by_id: dict[str, dict[str, object]] = {}
        seen_pages: set[tuple[tuple[object, object], ...]] = set()
        documents_seen = 0
        exhausted = False
        for page in range(1, RAGFLOW_RECONCILIATION_MAX_PAGES + 1):
            payload = await self._request(
                "GET",
                f"/api/v1/datasets/{dataset_id}/documents",
                params={
                    "page": page,
                    "page_size": RAGFLOW_RECONCILIATION_PAGE_SIZE,
                    "keywords": name,
                },
            )
            documents = self._extract_documents(payload)
            if not documents:
                exhausted = True
                break
            page_signature = tuple(
                (document.get("id"), document.get("name")) for document in documents
            )
            if page_signature in seen_pages:
                raise self._client_error("RAGFlow reconciliation pagination did not advance")
            seen_pages.add(page_signature)
            documents_seen += len(documents)
            for document in documents:
                if document.get("name") != name:
                    continue
                document_id = document.get("id")
                if not isinstance(document_id, str) or not document_id:
                    raise self._client_error("RAGFlow reconciliation response missing document id")
                matches_by_id[document_id] = document
            total = self._extract_total(payload)
            if total is not None and documents_seen >= total:
                exhausted = True
                break
        if not exhausted:
            raise self._client_error("RAGFlow reconciliation pagination limit exceeded")
        if not matches_by_id:
            return None
        if len(matches_by_id) > 1:
            raise self._client_error("RAGFlow reconciliation found duplicate document names")
        document_id, document = next(iter(matches_by_id.items()))
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
            submission_outcome_unknown=True,
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
                submission_outcome_unknown=True,
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
        submission_outcome_unknown: bool = False,
    ) -> dict[str, object]:
        if not self._api_key.strip():
            raise self._client_error("RAGFlow API key is not configured")
        url = f"{self._base_url}{path}"
        if self._client is not None and (self._protected_environment or self._tls_spki_pins):
            raise self._client_error(
                "RAGFlow custom HTTP clients are forbidden when endpoint pinning is required"
            )
        try:
            if self._client is not None:
                response = await self._client.request(
                    method,
                    url,
                    headers=self._headers(),
                    json=json,
                    files=files,
                    params=params,
                    follow_redirects=False,
                )
            else:
                endpoint = await resolve_and_authorize_ragflow_endpoint(
                    base_url=self._base_url,
                    protected_environment=self._protected_environment,
                    tls_spki_pins=self._tls_spki_pins,
                    resolver=self._resolver,
                )
                transport = build_pinned_ragflow_transport(endpoint)
                async with httpx.AsyncClient(
                    timeout=self._timeout_seconds,
                    transport=transport,
                    trust_env=False,
                    follow_redirects=False,
                ) as client:
                    response = await client.request(
                        method,
                        url,
                        headers=self._headers(),
                        json=json,
                        files=files,
                        params=params,
                        follow_redirects=False,
                    )
        except RagflowEndpointSecurityError:
            raise self._client_error("RAGFlow endpoint security check failed") from None
        except httpx.HTTPError as exc:
            message = f"RAGFlow request failed: {type(exc).__name__}"
            if submission_outcome_unknown:
                raise self._submission_outcome_unknown(message) from None
            raise self._client_error(message) from None
        return self._parse_response(
            response,
            submission_outcome_unknown=submission_outcome_unknown,
        )

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    def _parse_response(
        self,
        response: httpx.Response,
        *,
        submission_outcome_unknown: bool = False,
    ) -> dict[str, object]:
        if 300 <= response.status_code < 400:
            raise self._client_error("RAGFlow request refused an HTTP redirect")
        if response.status_code >= 400:
            if submission_outcome_unknown and response.status_code >= 500:
                raise self._submission_outcome_unknown(
                    f"RAGFlow request failed: HTTP {response.status_code}"
                )
            raise self._client_error(f"RAGFlow request failed: HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError:
            if submission_outcome_unknown:
                raise self._submission_outcome_unknown(
                    "RAGFlow upload response is not JSON"
                ) from None
            raise self._client_error("RAGFlow response is not JSON") from None
        if not isinstance(payload, dict):
            if submission_outcome_unknown:
                raise self._submission_outcome_unknown("RAGFlow upload response has invalid shape")
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
        raise self._submission_outcome_unknown("RAGFlow upload response missing document data")

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

    def _extract_total(self, payload: dict[str, object]) -> int | None:
        data = payload.get("data")
        if not isinstance(data, dict):
            return None
        total = data.get("total")
        if isinstance(total, int) and total >= 0:
            return total
        if isinstance(total, str) and total.isdigit():
            return int(total)
        return None

    def _client_error(self, message: str) -> RagflowClientError:
        return RagflowClientError(redact_secret(message, self._api_key))

    def _submission_outcome_unknown(
        self,
        message: str,
    ) -> RagflowSubmissionOutcomeUnknownError:
        return RagflowSubmissionOutcomeUnknownError(redact_secret(message, self._api_key))


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
