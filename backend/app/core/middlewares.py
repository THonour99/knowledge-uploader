from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import Response

from app.core.request_ids import new_request_id, normalize_opaque_request_id


async def request_id_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    request_id = normalize_opaque_request_id(request.headers.get("x-request-id"))
    if request_id is None:
        request_id = new_request_id()
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["x-request-id"] = request_id
    return response
