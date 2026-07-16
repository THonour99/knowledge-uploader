from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Annotated, Literal, NoReturn
from urllib.parse import quote
from uuid import UUID

import structlog
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from app.adapters.minio_client import MinioDocumentStorage
from app.core.access_scope import ScopedAdminDep
from app.core.config import Settings
from app.core.database import get_session
from app.core.deps import get_app_settings, get_current_user
from app.core.exceptions import ErrorCode
from app.core.ratelimit import is_within_rate_limit, upload_rate_limit_key
from app.core.responses import success_response
from app.core.runtime_config import get_config
from app.modules.user.schemas import AuthUserRecord

from . import exceptions
from .exceptions import DocumentError
from .models import File as DocumentFile
from .repository import DocumentRepository
from .schemas import FileDetailResponse, FileListResponse, FileResponse, effective_expiry_status
from .service import (
    DocumentContentStream,
    DocumentService,
    DocumentStorage,
    FileContentResult,
    FileDetailResult,
    UploadedFileResult,
    ensure_upload_allowed,
    resolve_allowed_extensions,
    resolve_upload_enabled,
    resolve_upload_max_size_bytes,
)

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/files", tags=["files"])
admin_router = APIRouter(prefix="/api/admin/files", tags=["files-admin"])
policy_router = APIRouter(prefix="/api", tags=["files"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]
CurrentUserDep = Annotated[AuthUserRecord, Depends(get_current_user)]


def get_document_storage(settings: SettingsDep) -> DocumentStorage:
    return MinioDocumentStorage(settings)


DocumentStorageDep = Annotated[DocumentStorage, Depends(get_document_storage)]


def _service(
    *,
    session: AsyncSession,
    settings: Settings,
    storage: DocumentStorage,
) -> DocumentService:
    return DocumentService(
        session=session,
        repository=DocumentRepository(session),
        settings=settings,
        storage=storage,
    )


def _raise_document_error(error: DocumentError) -> NoReturn:
    raise HTTPException(
        status_code=error.status_code,
        detail={"error_code": error.error_code, "message": error.message},
    )


def _file_response(
    file: DocumentFile,
    *,
    duplicate_file_id: UUID | None = None,
) -> FileResponse:
    return FileResponse(
        id=file.id,
        original_name=file.original_name,
        title=file.title,
        extension=file.extension,
        mime_type=file.mime_type,
        size=file.size,
        uploader_id=file.uploader_id,
        department_id=file.department_id,
        department_name=getattr(file, "department_name", None) or file.department,
        department_code=getattr(file, "department_code", None),
        department=file.department,
        category_id=file.category_id,
        dataset_mapping_id=file.dataset_mapping_id,
        visibility=file.visibility,
        description=file.description,
        tags=file.tags,
        status=file.status,
        review_status=file.review_status,
        submitted_at=file.submitted_at,
        review_due_at=file.review_due_at,
        claimed_by=file.claimed_by,
        claimed_at=file.claimed_at,
        claim_expires_at=file.claim_expires_at,
        review_version=file.review_version,
        ragflow_dataset_id=file.ragflow_dataset_id,
        ragflow_document_id=file.ragflow_document_id,
        ragflow_parse_status=file.ragflow_parse_status,
        ai_analysis_enabled_at_upload=file.ai_analysis_enabled_at_upload,
        expires_at=file.expires_at,
        expiry_status=effective_expiry_status(
            expires_at=file.expires_at,
            stored_status=file.expiry_status,
        ),
        uploaded_at=file.uploaded_at,
        last_sync_at=file.last_sync_at,
        created_at=file.created_at,
        updated_at=file.updated_at,
        duplicate=duplicate_file_id is not None,
        duplicate_file_id=duplicate_file_id,
    )


def _upload_response(result: UploadedFileResult) -> FileResponse:
    return _file_response(result.file, duplicate_file_id=result.duplicate_file_id)


def _file_detail_response(detail: FileDetailResult) -> FileDetailResponse:
    return FileDetailResponse(
        **_file_response(detail.file).model_dump(),
        category_name=detail.category_name,
        analysis=detail.analysis,
        sync_error=detail.sync_error,
    )


def _content_disposition_header(
    *,
    disposition: Literal["inline", "attachment"],
    filename: str,
    extension: str,
) -> str:
    fallback = f"file.{extension}"
    encoded = quote(filename, safe="")
    return f"{disposition}; filename=\"{fallback}\"; filename*=UTF-8''{encoded}"


def _single_byte_range(range_header: str | None, *, total: int) -> tuple[int, int] | None:
    if range_header is None:
        return None
    unit, separator, value = range_header.partition("=")
    if unit.strip().lower() != "bytes" or separator != "=" or "," in value:
        raise _range_not_satisfiable(total)
    start_text, dash, end_text = value.strip().partition("-")
    if dash != "-" or (not start_text and not end_text):
        raise _range_not_satisfiable(total)
    try:
        if not start_text:
            suffix_length = int(end_text)
            if suffix_length <= 0:
                raise ValueError
            start = max(total - suffix_length, 0)
            end = total - 1
        else:
            start = int(start_text)
            end = int(end_text) if end_text else total - 1
    except ValueError as exc:
        raise _range_not_satisfiable(total) from exc
    if start < 0 or start >= total or end < start:
        raise _range_not_satisfiable(total)
    return start, min(end, total - 1)


def _range_not_satisfiable(total: int) -> HTTPException:
    return HTTPException(
        status_code=416,
        detail={
            "error_code": ErrorCode.VALIDATION_ERROR,
            "message": "requested byte range is not satisfiable",
        },
        headers={"Content-Range": f"bytes */{total}"},
    )


SAFE_INLINE_CONTENT_TYPES: dict[str, frozenset[str]] = {
    "application/pdf": frozenset({"pdf"}),
    "image/gif": frozenset({"gif"}),
    "image/jpeg": frozenset({"jpeg", "jpg"}),
    "image/png": frozenset({"png"}),
    "image/webp": frozenset({"webp"}),
    "text/csv": frozenset({"csv"}),
    "text/markdown": frozenset({"md"}),
    "text/plain": frozenset({"csv", "md", "txt"}),
}


def _effective_content_disposition(
    result: FileContentResult,
    requested: Literal["inline", "attachment"],
) -> Literal["inline", "attachment"]:
    if requested == "attachment":
        return "attachment"
    allowed_extensions = SAFE_INLINE_CONTENT_TYPES.get(result.file.mime_type.lower())
    if allowed_extensions is None or result.file.extension.lower() not in allowed_extensions:
        return "attachment"
    return "inline"


class _ManagedDocumentContentStream:
    def __init__(self, stream: DocumentContentStream) -> None:
        self.stream = stream
        self._closed = False

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self.stream.aclose()
        except Exception as exc:
            # Cleanup is best-effort and must never replace the transport error
            # that caused cancellation. Persist only the exception class.
            logger.warning(
                "document_content_stream_close_failed",
                error_type=type(exc).__name__,
            )


async def _stream_and_close(stream: _ManagedDocumentContentStream) -> AsyncIterator[bytes]:
    try:
        async for chunk in stream.stream:
            yield chunk
    finally:
        await stream.aclose()


class _ClosingStreamingResponse(StreamingResponse):
    def __init__(
        self,
        stream: DocumentContentStream,
        *,
        status_code: int,
        media_type: str,
        headers: Mapping[str, str],
    ) -> None:
        self._managed_document_stream = _ManagedDocumentContentStream(stream)
        super().__init__(
            _stream_and_close(self._managed_document_stream),
            status_code=status_code,
            media_type=media_type,
            headers=headers,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            # The body iterator may never receive its first item when the client
            # disconnects immediately. Response-scope cleanup is the outer
            # guarantee; the generator finally remains the inner guarantee.
            await self._managed_document_stream.aclose()


def _content_response(
    result: FileContentResult,
    stream: DocumentContentStream,
    *,
    disposition: Literal["inline", "attachment"],
    requested_range: tuple[int, int] | None,
) -> StreamingResponse:
    total = result.file.size
    status_code = 200
    content_length = total
    effective_disposition = _effective_content_disposition(result, disposition)
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, no-store",
        "Content-Disposition": _content_disposition_header(
            disposition=effective_disposition,
            filename=result.file.original_name,
            extension=result.file.extension,
        ),
        "Content-Security-Policy": "sandbox",
        "ETag": f'"{result.file.hash}"',
        "X-Content-Type-Options": "nosniff",
    }
    if requested_range is not None:
        start, end = requested_range
        content_length = end - start + 1
        status_code = 206
        headers["Content-Range"] = f"bytes {start}-{end}/{total}"
    headers["Content-Length"] = str(content_length)
    return _ClosingStreamingResponse(
        stream,
        status_code=status_code,
        media_type=result.file.mime_type,
        headers=headers,
    )


@policy_router.get("/upload-policy", deprecated=True)
@router.get("/policy")
async def get_upload_policy(
    request: Request,
    current_user: CurrentUserDep,
    settings: SettingsDep,
) -> dict[str, object]:
    allowed_extensions = await resolve_allowed_extensions(settings)
    allow_multi_file = await get_config("upload.allow_multi_file")
    allow_user_delete = await get_config("upload.allow_user_delete")
    from .schemas import UploadPolicyResponse

    max_file_size_bytes = await resolve_upload_max_size_bytes(settings)
    response = UploadPolicyResponse(
        allowed_extensions=sorted(allowed_extensions),
        allow_multi_file=allow_multi_file is True,
        upload_enabled=await resolve_upload_enabled(),
        max_file_size_mb=max(1, (max_file_size_bytes + 1024 * 1024 - 1) // (1024 * 1024)),
        allow_user_delete=allow_user_delete is True,
    )
    return success_response(response.model_dump(mode="json"), request)


def _raise_rate_limited() -> NoReturn:
    raise HTTPException(
        status_code=429,
        detail={"error_code": ErrorCode.RATE_LIMITED, "message": "too many requests"},
    )


def _client_ip(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"


def _user_agent(request: Request) -> str:
    return request.headers.get("user-agent", "unknown")[:512] or "unknown"


async def _enforce_upload_rate_limit(user: AuthUserRecord, settings: Settings) -> None:
    allowed = await is_within_rate_limit(
        redis_url=settings.cache_redis_url,
        key=upload_rate_limit_key(str(user.id)),
        limit=settings.upload_rate_limit_per_minute,
        window_seconds=60,
    )
    if not allowed:
        _raise_rate_limited()


async def _read_upload_file(
    *,
    upload: UploadFile,
    request: Request,
    max_size: int,
) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            request_size = int(content_length)
        except ValueError:
            request_size = 0
        if request_size > max_size + 1024 * 1024:
            raise DocumentError(
                error_code=ErrorCode.FILE_TOO_LARGE,
                message=f"file size exceeds {max_size} bytes",
                status_code=400,
            )

    data = bytearray()
    while chunk := await upload.read(1024 * 1024):
        if len(data) + len(chunk) > max_size:
            raise DocumentError(
                error_code=ErrorCode.FILE_TOO_LARGE,
                message=f"file size exceeds {max_size} bytes",
                status_code=400,
            )
        data.extend(chunk)
    return bytes(data)


@router.post("/upload", status_code=201)
async def upload_file(
    request: Request,
    current_user: CurrentUserDep,
    session: SessionDep,
    settings: SettingsDep,
    storage: DocumentStorageDep,
    file: Annotated[UploadFile, File(...)],
    submit_after_upload: Annotated[bool, Form()],
    description: Annotated[str | None, Form(max_length=2000)] = None,
    visibility: Annotated[str, Form()] = "private",
    ai_analysis_enabled: Annotated[bool | None, Form()] = None,
) -> dict[str, object]:
    try:
        await ensure_upload_allowed(current_user)
        await _enforce_upload_rate_limit(current_user, settings)
        # 与 service 层共用 runtime_config 的 upload.max_file_size_mb, 避免半切换状态
        data = await _read_upload_file(
            upload=file,
            request=request,
            max_size=await resolve_upload_max_size_bytes(settings),
        )
        result = await _service(session=session, settings=settings, storage=storage).upload_file(
            current_user=current_user,
            original_filename=file.filename or "upload",
            content_type=file.content_type,
            data=data,
            description=description,
            visibility=visibility,
            submit_after_upload=submit_after_upload,
            ai_analysis_enabled=ai_analysis_enabled,
            client_ip=_client_ip(request),
            user_agent=_user_agent(request),
        )
    except DocumentError as error:
        _raise_document_error(error)
    return success_response(_upload_response(result).model_dump(mode="json"), request)


@router.get("")
async def list_my_files(
    request: Request,
    current_user: CurrentUserDep,
    session: SessionDep,
    settings: SettingsDep,
    storage: DocumentStorageDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    q: Annotated[str | None, Query(max_length=200)] = None,
    status: Annotated[str | None, Query(max_length=40)] = None,
    extension: Annotated[str | None, Query()] = None,
    tag_id: Annotated[UUID | None, Query()] = None,
    expiry_status: Annotated[
        Literal["never", "active", "expiring", "expired"] | None,
        Query(),
    ] = None,
    sort: Annotated[
        Literal["uploaded_at", "updated_at", "original_name", "title", "size", "status"],
        Query(),
    ] = "uploaded_at",
    order: Annotated[Literal["asc", "desc"], Query()] = "desc",
) -> dict[str, object]:
    result = await _service(session=session, settings=settings, storage=storage).list_my_files(
        current_user,
        page=page,
        page_size=page_size,
        search=q,
        status=status,
        extension=extension,
        tag_id=tag_id,
        expiry_status=expiry_status,
        sort=sort,
        order=order,
    )
    response = FileListResponse(
        items=[_file_response(file) for file in result.items],
        total=result.total,
        page=result.page,
        page_size=result.page_size,
        total_pages=(result.total + result.page_size - 1) // result.page_size,
    )
    return success_response(response.model_dump(mode="json"), request)


@router.get("/{file_id}")
async def get_file_detail(
    file_id: UUID,
    request: Request,
    current_user: CurrentUserDep,
    session: SessionDep,
    settings: SettingsDep,
    storage: DocumentStorageDep,
) -> dict[str, object]:
    try:
        detail = await _service(
            session=session,
            settings=settings,
            storage=storage,
        ).get_file_detail(
            current_user=current_user,
            file_id=file_id,
            client_ip=_client_ip(request),
            user_agent=_user_agent(request),
        )
    except DocumentError as error:
        _raise_document_error(error)
    return success_response(_file_detail_response(detail).model_dump(mode="json"), request)


@router.get("/{file_id}/content")
async def get_file_content(
    file_id: UUID,
    request: Request,
    current_user: CurrentUserDep,
    session: SessionDep,
    settings: SettingsDep,
    storage: DocumentStorageDep,
    disposition: Annotated[Literal["inline", "attachment"], Query()] = "inline",
) -> Response:
    try:
        result = await _service(
            session=session,
            settings=settings,
            storage=storage,
        ).get_file_content(
            current_user=current_user,
            file_id=file_id,
            disposition=disposition,
            client_ip=_client_ip(request),
            user_agent=_user_agent(request),
        )
    except DocumentError as error:
        _raise_document_error(error)
    requested_range = _single_byte_range(
        request.headers.get("range"),
        total=result.file.size,
    )
    offset = requested_range[0] if requested_range is not None else 0
    # 始终把已校验的数据库大小作为 MinIO 读取上限, 避免对象被外部替换后响应体
    # 超出 Content-Length, 或无界读取占用 worker。
    length = (
        requested_range[1] - requested_range[0] + 1
        if requested_range is not None
        else result.file.size
    )
    try:
        stream = await storage.open_object(
            bucket=result.file.bucket,
            object_key=result.file.object_key,
            offset=offset,
            length=length,
        )
    except Exception:
        _raise_document_error(exceptions.storage_error())
    return _content_response(
        result,
        stream,
        disposition=disposition,
        requested_range=requested_range,
    )


@router.delete("/{file_id}")
async def delete_file(
    file_id: UUID,
    request: Request,
    current_user: CurrentUserDep,
    session: SessionDep,
    settings: SettingsDep,
    storage: DocumentStorageDep,
) -> dict[str, object]:
    try:
        await _service(session=session, settings=settings, storage=storage).delete_file(
            current_user=current_user,
            file_id=file_id,
            client_ip=_client_ip(request),
            user_agent=_user_agent(request),
        )
    except DocumentError as error:
        _raise_document_error(error)
    return success_response({}, request)


@admin_router.post("/{file_id}/archive")
async def archive_file(
    file_id: UUID,
    request: Request,
    current_user: CurrentUserDep,
    scope: ScopedAdminDep,
    session: SessionDep,
    settings: SettingsDep,
    storage: DocumentStorageDep,
) -> dict[str, object]:
    try:
        file = await _service(session=session, settings=settings, storage=storage).archive_file(
            current_user=current_user,
            scope=scope,
            file_id=file_id,
            client_ip=_client_ip(request),
            user_agent=_user_agent(request),
        )
    except DocumentError as error:
        _raise_document_error(error)
    return success_response(_file_response(file).model_dump(mode="json"), request)


@admin_router.post("/{file_id}/reanalyze")
async def reanalyze_file(
    file_id: UUID,
    request: Request,
    current_user: CurrentUserDep,
    scope: ScopedAdminDep,
    session: SessionDep,
    settings: SettingsDep,
    storage: DocumentStorageDep,
) -> dict[str, object]:
    try:
        await _service(session=session, settings=settings, storage=storage).reanalyze_file(
            current_user=current_user,
            scope=scope,
            file_id=file_id,
            client_ip=_client_ip(request),
            user_agent=_user_agent(request),
        )
    except DocumentError as error:
        _raise_document_error(error)
    return success_response({}, request)


@admin_router.post("/{file_id}/reparse")
async def reparse_file(
    file_id: UUID,
    request: Request,
    current_user: CurrentUserDep,
    scope: ScopedAdminDep,
    session: SessionDep,
    settings: SettingsDep,
    storage: DocumentStorageDep,
) -> dict[str, object]:
    # 当前文本解析在 AI 分析流水线内执行, reparse 复用 reanalyze 路径重新入队
    try:
        await _service(session=session, settings=settings, storage=storage).reanalyze_file(
            current_user=current_user,
            scope=scope,
            file_id=file_id,
            client_ip=_client_ip(request),
            user_agent=_user_agent(request),
            audit_action="file.reparse",
        )
    except DocumentError as error:
        _raise_document_error(error)
    return success_response({}, request)
