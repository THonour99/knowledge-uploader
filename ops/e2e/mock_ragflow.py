"""Stateful, protocol-level RAGFlow double used only by isolated infrastructure E2E."""

from __future__ import annotations

import json
import os
import re
import ssl
import threading
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

MAX_REQUEST_BYTES = 10 * 1024 * 1024
FILENAME_PATTERN = re.compile(rb'filename="([^"\r\n]{1,255})"')

API_KEY = os.environ.get("E2E_RAGFLOW_API_KEY", "")
DATASET_ID = os.environ.get("E2E_RAGFLOW_DATASET_ID", "")
PROBE_TOKEN = os.environ.get("E2E_PROBE_TOKEN", "")
if not API_KEY or not DATASET_ID or not PROBE_TOKEN:
    raise RuntimeError("mock RAGFlow requires API key, dataset id, and probe token")

_LOCK = threading.Lock()
_DOCUMENTS: dict[str, dict[str, object]] = {}
_UPLOAD_COUNT = 0
_METADATA_UPDATE_COUNT = 0
_PARSE_COUNT = 0
_AUTHORIZATION_FAILURES = 0


def _state_file() -> Path | None:
    value = os.environ.get("E2E_RAGFLOW_STATE_FILE", "").strip()
    return Path(value) if value else None


def _load_persistent_state() -> None:
    path = _state_file()
    if path is None or not path.exists():
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        documents = raw["documents"]
        upload_count = raw["upload_count"]
        metadata_update_count = raw["metadata_update_count"]
        parse_count = raw["parse_count"]
        authorization_failures = raw["authorization_failures"]
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("mock RAGFlow persistent state is invalid") from error
    if (
        not isinstance(documents, list)
        or not all(isinstance(document, dict) for document in documents)
        or not all(
            isinstance(value, int) and not isinstance(value, bool) and value >= 0
            for value in (
                upload_count,
                metadata_update_count,
                parse_count,
                authorization_failures,
            )
        )
    ):
        raise RuntimeError("mock RAGFlow persistent state is invalid")
    global _DOCUMENTS
    global _UPLOAD_COUNT
    global _METADATA_UPDATE_COUNT
    global _PARSE_COUNT
    global _AUTHORIZATION_FAILURES
    loaded_documents: dict[str, dict[str, object]] = {}
    for document in documents:
        document_id = document.get("id")
        if not isinstance(document_id, str) or not document_id:
            raise RuntimeError("mock RAGFlow persistent state is invalid")
        loaded_documents[document_id] = dict(document)
    _DOCUMENTS = loaded_documents
    _UPLOAD_COUNT = upload_count
    _METADATA_UPDATE_COUNT = metadata_update_count
    _PARSE_COUNT = parse_count
    _AUTHORIZATION_FAILURES = authorization_failures


def _save_persistent_state_locked() -> None:
    path = _state_file()
    if path is None:
        return
    payload = {
        "documents": [dict(document) for document in _DOCUMENTS.values()],
        "upload_count": _UPLOAD_COUNT,
        "metadata_update_count": _METADATA_UPDATE_COUNT,
        "parse_count": _PARSE_COUNT,
        "authorization_failures": _AUTHORIZATION_FAILURES,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


class MockRagflowHandler(BaseHTTPRequestHandler):
    server_version = "KnowledgeUploaderE2ERagflow/1"

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path == "/health":
            self._json(HTTPStatus.OK, {"status": "ok"})
            return
        if parsed.path == "/__e2e/state":
            if self.headers.get("X-E2E-Probe-Token") != PROBE_TOKEN:
                self._json(HTTPStatus.FORBIDDEN, {"status": "forbidden"})
                return
            with _LOCK:
                state = {
                    "upload_count": _UPLOAD_COUNT,
                    "metadata_update_count": _METADATA_UPDATE_COUNT,
                    "parse_count": _PARSE_COUNT,
                    "authorization_failures": _AUTHORIZATION_FAILURES,
                    "documents": [dict(document) for document in _DOCUMENTS.values()],
                }
            self._json(HTTPStatus.OK, state)
            return
        if not self._authorized():
            return
        if parsed.path == "/api/v1/datasets":
            self._ragflow({"docs": [{"id": DATASET_ID, "name": "E2E Dataset"}], "total": 1})
            return
        if parsed.path == f"/api/v1/datasets/{DATASET_ID}/documents":
            query = parse_qs(parsed.query)
            requested_id = query.get("id", [None])[0]
            keyword = query.get("keywords", [None])[0]
            with _LOCK:
                documents = [
                    dict(document)
                    for document in _DOCUMENTS.values()
                    if (requested_id is None or document["id"] == requested_id)
                    and (keyword is None or document["name"] == keyword)
                ]
            self._ragflow({"docs": documents, "total": len(documents)})
            return
        self._json(HTTPStatus.NOT_FOUND, {"code": 404, "message": "not found"})

    def do_POST(self) -> None:
        parsed = urlsplit(self.path)
        if not self._authorized():
            return
        if parsed.path == f"/api/v1/datasets/{DATASET_ID}/documents":
            body = self._body()
            match = FILENAME_PATTERN.search(body)
            filename = (
                match.group(1).decode("utf-8", errors="strict")
                if match is not None
                else "e2e-document.txt"
            )
            document_id = str(
                uuid.uuid5(uuid.NAMESPACE_URL, f"mock-ragflow:{DATASET_ID}:{filename}")
            )
            document: dict[str, object] = {
                "id": document_id,
                "name": filename,
                "run": "UNSTART",
                "progress": 0.0,
            }
            global _UPLOAD_COUNT
            with _LOCK:
                _DOCUMENTS[document_id] = document
                _UPLOAD_COUNT += 1
                _save_persistent_state_locked()
            self._ragflow([document])
            return
        if parsed.path == f"/api/v1/datasets/{DATASET_ID}/chunks":
            payload = self._json_body()
            raw_document_ids = payload.get("document_ids")
            if not isinstance(raw_document_ids, list):
                self._json(HTTPStatus.BAD_REQUEST, {"code": 400, "message": "invalid ids"})
                return
            document_ids: list[str] = []
            for item in raw_document_ids:
                if not isinstance(item, str):
                    self._json(
                        HTTPStatus.BAD_REQUEST,
                        {"code": 400, "message": "invalid ids"},
                    )
                    return
                document_ids.append(item)
            global _PARSE_COUNT
            with _LOCK:
                for document_id in document_ids:
                    stored_document = _DOCUMENTS.get(document_id)
                    if stored_document is not None:
                        stored_document["run"] = "DONE"
                        stored_document["progress"] = 1.0
                _PARSE_COUNT += 1
                _save_persistent_state_locked()
            self._ragflow({})
            return
        self._json(HTTPStatus.NOT_FOUND, {"code": 404, "message": "not found"})

    def do_PUT(self) -> None:
        parsed = urlsplit(self.path)
        if not self._authorized():
            return
        prefix = f"/api/v1/datasets/{DATASET_ID}/documents/"
        if parsed.path.startswith(prefix):
            document_id = parsed.path.removeprefix(prefix)
            payload = self._json_body()
            global _METADATA_UPDATE_COUNT
            with _LOCK:
                document = _DOCUMENTS.get(document_id)
                if document is None:
                    self._json(HTTPStatus.NOT_FOUND, {"code": 404, "message": "not found"})
                    return
                name = payload.get("name")
                if isinstance(name, str) and name:
                    document["name"] = name
                document["metadata_updated"] = isinstance(payload.get("meta_fields"), dict)
                _METADATA_UPDATE_COUNT += 1
                _save_persistent_state_locked()
            self._ragflow({})
            return
        self._json(HTTPStatus.NOT_FOUND, {"code": 404, "message": "not found"})

    def do_DELETE(self) -> None:
        parsed = urlsplit(self.path)
        if not self._authorized():
            return
        if parsed.path == f"/api/v1/datasets/{DATASET_ID}/documents":
            payload = self._json_body()
            document_ids = payload.get("ids")
            if isinstance(document_ids, list):
                with _LOCK:
                    for document_id in document_ids:
                        if isinstance(document_id, str):
                            _DOCUMENTS.pop(document_id, None)
                    _save_persistent_state_locked()
            self._ragflow({})
            return
        self._json(HTTPStatus.NOT_FOUND, {"code": 404, "message": "not found"})

    def _authorized(self) -> bool:
        global _AUTHORIZATION_FAILURES
        if self.headers.get("Authorization") == f"Bearer {API_KEY}":
            return True
        with _LOCK:
            _AUTHORIZATION_FAILURES += 1
            _save_persistent_state_locked()
        self._json(HTTPStatus.UNAUTHORIZED, {"code": 401, "message": "unauthorized"})
        return False

    def _body(self) -> bytes:
        raw_length = self.headers.get("Content-Length", "")
        try:
            length = int(raw_length)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_REQUEST_BYTES:
            raise ValueError("invalid request size")
        return self.rfile.read(length)

    def _json_body(self) -> dict[str, object]:
        try:
            payload = json.loads(self._body().decode("utf-8"))
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _ragflow(self, data: object) -> None:
        self._json(HTTPStatus.OK, {"code": 0, "data": data})

    def _json(self, status: HTTPStatus, payload: object) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    with _LOCK:
        _load_persistent_state()
    server = ThreadingHTTPServer(("0.0.0.0", 9380), MockRagflowHandler)
    certificate = os.environ.get("E2E_TLS_CERT_FILE", "").strip()
    private_key = os.environ.get("E2E_TLS_KEY_FILE", "").strip()
    if not certificate or not private_key:
        raise RuntimeError("mock RAGFlow TLS certificate and key are required")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certificate, private_key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
