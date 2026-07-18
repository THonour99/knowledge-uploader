from __future__ import annotations

import hmac
import json
import os
import threading
import time
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

MAX_REQUEST_BYTES = 1_048_576


class MockProtocolError(ValueError):
    def __init__(self, status: HTTPStatus) -> None:
        super().__init__(status.phrase)
        self.status = status


@dataclass
class ProbeState:
    request_count: int = 0
    authorization_failures: int = 0
    protocol_failures: int = 0
    last_model: str | None = None


_STATE = ProbeState()
_STATE_LOCK = threading.Lock()


def validate_completion_request(
    headers: Mapping[str, str],
    body: bytes,
    *,
    api_key: str,
    expected_model: str,
) -> str:
    authorization = headers.get("Authorization", "")
    if not hmac.compare_digest(authorization, f"Bearer {api_key}"):
        raise MockProtocolError(HTTPStatus.UNAUTHORIZED)
    try:
        payload: object = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MockProtocolError(HTTPStatus.BAD_REQUEST) from exc
    if not isinstance(payload, dict):
        raise MockProtocolError(HTTPStatus.BAD_REQUEST)
    model = payload.get("model")
    if model != expected_model:
        raise MockProtocolError(HTTPStatus.BAD_REQUEST)
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise MockProtocolError(HTTPStatus.BAD_REQUEST)
    last_message = messages[-1]
    if (
        not isinstance(last_message, dict)
        or last_message.get("role") != "user"
        or not isinstance(last_message.get("content"), str)
        or not str(last_message["content"]).strip()
    ):
        raise MockProtocolError(HTTPStatus.BAD_REQUEST)
    response_format = payload.get("response_format")
    if not isinstance(response_format, dict) or response_format.get("type") != "json_object":
        raise MockProtocolError(HTTPStatus.BAD_REQUEST)
    return expected_model


def completion_payload(model: str) -> dict[str, object]:
    content = json.dumps(
        {
            "summary": "协议级模拟分析完成",
            "category_id": None,
            "tags": ["ai-mainchain-probe"],
            "sensitive_risk_level": "none",
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 37,
            "completion_tokens": 13,
            "total_tokens": 50,
        },
    }


def _bounded_delay_seconds() -> float:
    raw_value = os.getenv("AI_PROBE_LLM_DELAY_MS", "500")
    try:
        milliseconds = int(raw_value)
    except ValueError:
        milliseconds = 500
    return max(0, min(milliseconds, 5_000)) / 1_000


class MockLLMHandler(BaseHTTPRequestHandler):
    server_version = "KnowledgeUploaderAiProbe/1"

    def do_GET(self) -> None:
        if self.path == "/health":
            self._write_json(HTTPStatus.OK, {"status": "ok"})
            return
        if self.path == "/__probe/state":
            expected_token = os.environ["AI_PROBE_STATE_TOKEN"]
            provided_token = self.headers.get("X-AI-Probe-Token", "")
            if not hmac.compare_digest(provided_token, expected_token):
                self._write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return
            with _STATE_LOCK:
                state = asdict(_STATE)
            self._write_json(HTTPStatus.OK, state)
            return
        self._write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_POST(self) -> None:
        if self.path != "/v1/chat/completions":
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        content_length = self._content_length()
        if content_length is None:
            self._record_protocol_failure()
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_request"})
            return
        body = self.rfile.read(content_length)
        try:
            model = validate_completion_request(
                self.headers,
                body,
                api_key=os.environ["AI_PROBE_LLM_API_KEY"],
                expected_model=os.environ["AI_PROBE_LLM_MODEL"],
            )
        except MockProtocolError as exc:
            with _STATE_LOCK:
                if exc.status == HTTPStatus.UNAUTHORIZED:
                    _STATE.authorization_failures += 1
                else:
                    _STATE.protocol_failures += 1
            self._write_json(exc.status, {"error": "invalid_request"})
            return

        time.sleep(_bounded_delay_seconds())
        with _STATE_LOCK:
            _STATE.request_count += 1
            _STATE.last_model = model
        self._write_json(HTTPStatus.OK, completion_payload(model))

    def _content_length(self) -> int | None:
        raw_value = self.headers.get("Content-Length")
        if raw_value is None:
            return None
        try:
            value = int(raw_value)
        except ValueError:
            return None
        if value < 1 or value > MAX_REQUEST_BYTES:
            return None
        return value

    def _record_protocol_failure(self) -> None:
        with _STATE_LOCK:
            _STATE.protocol_failures += 1

    def _write_json(self, status: HTTPStatus, payload: Mapping[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def main() -> int:
    server = ThreadingHTTPServer(("0.0.0.0", 8081), MockLLMHandler)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
