from __future__ import annotations

from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.minio_client import MinioDocumentStorage
from app.core.config import Settings
from app.core.database import get_session
from app.core.deps import get_app_settings, get_current_user
from app.core.exceptions import ErrorCode
from app.core.ratelimit import is_within_rate_limit, upload_rate_limit_key
from app.core.responses import success_response
from app.modules.user.schemas import AuthUserRecord

from .exceptions import DocumentError
from .models import File as DocumentFile
from .repository import DocumentRepository
from .schemas import FileListResponse, FileResponse
from .service import (
    DocumentService,
    DocumentStorage,
    UploadedFileResult,
    resolve_upload_max_size_bytes,
)

router = APIRouter(prefix="/api/files", tags=["files"])
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
        extension=file.extension,
        mime_type=file.mime_type,
        size=file.size,
        uploader_id=file.uploader_id,
        department=file.department,
        category_id=file.category_id,
        dataset_mapping_id=file.dataset_mapping_id,
        visibility=file.visibility,
        description=file.description,
        tags=file.tags,
        status=file.status,
        review_status=file.review_status,
        ragflow_dataset_id=file.ragflow_dataset_id,
        ragflow_document_id=file.ragflow_document_id,
        ragflow_parse_status=file.ragflow_parse_status,
        ai_analysis_enabled_at_upload=file.ai_analysis_enabled_at_upload,
        uploaded_at=file.uploaded_at,
        last_sync_at=file.last_sync_at,
        created_at=file.created_at,
        updated_at=file.updated_at,
        duplicate=duplicate_file_id is not None,
        duplicate_file_id=duplicate_file_id,
    )


def _upload_response(result: UploadedFileResult) -> FileResponse:
    return _file_response(result.file, duplicate_file_id=result.duplicate_file_id)


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
    description: Annotated[str | None, Form(max_length=2000)] = None,
    visibility: Annotated[str, Form()] = "private",
) -> dict[str, object]:
    try:
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
) -> dict[str, object]:
    files = await _service(session=session, settings=settings, storage=storage).list_my_files(
        current_user
    )
    response = FileListResponse(
        items=[_file_response(file) for file in files],
        total=len(files),
    )
    return success_response(response.model_dump(mode="json"), request)


@router.get("/{file_id}")
async def get_my_file(
    file_id: UUID,
    request: Request,
    current_user: CurrentUserDep,
    session: SessionDep,
    settings: SettingsDep,
    storage: DocumentStorageDep,
) -> dict[str, object]:
    try:
        file = await _service(session=session, settings=settings, storage=storage).get_my_file(
            current_user=current_user,
            file_id=file_id,
        )
    except DocumentError as error:
        _raise_document_error(error)
    return success_response(_file_response(file).model_dump(mode="json"), request)
