from __future__ import annotations

from typing import Any

from starlette.requests import Request


def request_id_from(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None)
    if isinstance(request_id, str):
        return request_id
    return ""


def success_response(data: Any, request: Request, message: str = "ok") -> dict[str, Any]:
    return {
        "success": True,
        "data": data,
        "message": message,
        "request_id": request_id_from(request),
    }


def error_response(error_code: str, message: str, request: Request) -> dict[str, Any]:
    return {
        "success": False,
        "error_code": error_code,
        "message": message,
        "request_id": request_id_from(request),
    }
