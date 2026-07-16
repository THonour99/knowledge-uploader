from __future__ import annotations

import uuid

import pytest
from starlette.requests import Request
from starlette.responses import Response

from app.core.middlewares import request_id_middleware

pytestmark = pytest.mark.asyncio


def _request(request_id: str | None) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if request_id is not None:
        headers.append((b"x-request-id", request_id.encode("latin-1")))
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/probe",
            "raw_path": b"/probe",
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 12345),
            "server": ("test", 80),
        }
    )


async def _response(_request: Request) -> Response:
    return Response("ok")


@pytest.mark.parametrize(
    "untrusted",
    (
        "skAbc123",
        "BearerToken",
        "employee@example.com",
        "value\r\nInjected: true",
        "a" * 10_000,
        "00000000000000000000000000000000",
    ),
)
async def test_untrusted_request_id_is_not_reflected(untrusted: str) -> None:
    request = _request(untrusted)

    response = await request_id_middleware(request, _response)

    reflected = response.headers["x-request-id"]
    assert reflected != untrusted
    assert str(uuid.UUID(reflected)) == reflected
    assert request.state.request_id == reflected


async def test_canonical_uuid_request_id_is_preserved() -> None:
    request_id = str(uuid.uuid4())
    request = _request(request_id)

    response = await request_id_middleware(request, _response)

    assert response.headers["x-request-id"] == request_id
    assert request.state.request_id == request_id


async def test_nonzero_hex_trace_id_is_normalized() -> None:
    request = _request("ABCDEF1234567890ABCDEF1234567890")

    response = await request_id_middleware(request, _response)

    assert response.headers["x-request-id"] == "abcdef1234567890abcdef1234567890"
