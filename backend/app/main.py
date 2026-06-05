from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from starlette import status
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.exceptions import ErrorCode
from app.core.middlewares import request_id_middleware
from app.core.responses import error_response
from app.modules.auth.api import router as auth_router
from app.modules.user.api import router as user_router

app = FastAPI(title="Knowledge Uploader", version="0.1.0")
app.middleware("http")(request_id_middleware)
app.include_router(auth_router)
app.include_router(user_router)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict):
        error_code = str(detail.get("error_code", ErrorCode.INTERNAL_ERROR))
        message = str(detail.get("message", "request failed"))
    else:
        error_code = ErrorCode.INTERNAL_ERROR
        message = str(detail)
    return JSONResponse(
        status_code=exc.status_code,
        content=error_response(str(error_code), message, request),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=error_response(
            str(ErrorCode.VALIDATION_ERROR),
            "request validation failed",
            request,
        ),
    )


@app.get("/api/system/health", tags=["system"])
async def health_check() -> dict[str, str]:
    return {"status": "ok"}
