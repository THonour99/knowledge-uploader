"""Stateful, protocol-level RAGFlow double used only by isolated infrastructure E2E."""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

MAX_REQUEST_BYTES = 10 * 1024 * 1024
FILENAME_PATTERN = re.compile(br'filename="([^"\r\n]{1,255})"')

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
            self._ragflow([document])
            return
        if parsed.path == f"/api/v1/datasets/{DATASET_ID}/chunks":
            payload = self._json_body()
            document_ids = payload.get("document_ids")
            if not isinstance(document_ids, list) or not all(
                isinstance(item, str) for item in document_ids
            ):
                self._json(HTTPStatus.BAD_REQUEST, {"code": 400, "message": "invalid ids"})
                return
            global _PARSE_COUNT
            with _LOCK:
                for document_id in document_ids:
                    document = _DOCUMENTS.get(document_id)
                    if document is not None:
                        document["run"] = "DONE"
                        document["progress"] = 1.0
                _PARSE_COUNT += 1
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
            self._ragflow({})
            return
        self._json(HTTPStatus.NOT_FOUND, {"code": 404, "message": "not found"})

    def _authorized(self) -> bool:
        global _AUTHORIZATION_FAILURES
        if self.headers.get("Authorization") == f"Bearer {API_KEY}":
            return True
        with _LOCK:
            _AUTHORIZATION_FAILURES += 1
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
    server = ThreadingHTTPServer(("0.0.0.0", 9380), MockRagflowHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
