from __future__ import annotations

import re
import uuid
import zipfile
from contextlib import suppress
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import PurePosixPath, PureWindowsPath
from typing import Protocol

import filetype
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_admin_audit_log, record_audit_log
from app.core.config import Settings
from app.core.outbox import OutboxRepository
from app.core.runtime_config import get_config
from app.modules.user.schemas import AuthUserRecord

from . import events, exceptions
from .models import File
from .repository import DocumentRepository
from .schemas import FileAnalysisDetail

WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
FILENAME_UNSAFE_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
EXTENSION_MIME_MAP = {
    "pdf": {"application/pdf"},
    "docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    "xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
    "pptx": {"application/vnd.openxmlformats-officedocument.presentationml.presentation"},
    "txt": {"text/plain"},
    "md": {"text/markdown", "text/plain"},
    "csv": {"text/csv", "text/plain"},
}
VALID_VISIBILITIES = {"private", "department", "company"}
PDF_MAGIC = b"%PDF-"
PDF_EOF_MARKER = b"%%EOF"
PDF_STARTXREF_MARKER = b"startxref"
OOXML_REQUIRED_ENTRIES = {
    "docx": "word/document.xml",
    "xlsx": "xl/workbook.xml",
    "pptx": "ppt/presentation.xml",
}
UNSUPPORTED_LEGACY_EXTENSIONS = {"doc", "xls", "ppt"}
TEXT_EXTENSIONS = {"txt", "md", "csv"}
ADMIN_ROLES = {"knowledge_admin", "system_admin"}
EXTRACTED_TEXT_PREVIEW_CHARS = 500


class DocumentStorage(Protocol):
    async def put_object(
        self,
        *,
        bucket: str,
        object_key: str,
        data: bytes,
        content_type: str,
    ) -> None:
        pass

    async def delete_object(self, *, bucket: str, object_key: str) -> None:
        pass


@dataclass(frozen=True)
class UploadedFileResult:
    file: File
    duplicate_file_id: uuid.UUID | None


@dataclass(frozen=True)
class FileDetailResult:
    file: File
    category_name: str | None
    analysis: FileAnalysisDetail | None
    sync_error: str | None


class DocumentService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        repository: DocumentRepository,
        settings: Settings,
        storage: DocumentStorage,
    ) -> None:
        self._session = session
        self._repository = repository
        self._settings = settings
        self._storage = storage

    async def upload_file(
        self,
        *,
        current_user: AuthUserRecord,
        original_filename: str,
        content_type: str | None,
        data: bytes,
        description: str | None,
        visibility: str,
        client_ip: str,
        user_agent: str,
    ) -> UploadedFileResult:
        await self._validate_size(len(data))
        sanitized_name, extension = sanitize_filename(original_filename)
        await self._validate_extension(extension)
        mime_type = self._validate_mime_type(
            data=data,
            extension=extension,
            declared_mime_type=content_type,
        )
        if visibility not in VALID_VISIBILITIES:
            raise exceptions.invalid_visibility()

        file_hash = sha256(data).hexdigest()
        duplicate = await self._repository.find_first_by_hash_for_uploader(
            file_hash=file_hash,
            uploader_id=current_user.id,
        )
        file_id = uuid.uuid4()
        if duplicate is None:
            stored_name = f"{file_id}-{sanitized_name}"
            object_key = f"uploads/{current_user.id}/{file_id}/{stored_name}"
            bucket = self._settings.minio_bucket
            try:
                await self._storage.put_object(
                    bucket=bucket,
                    object_key=object_key,
                    data=data,
                    content_type=mime_type,
                )
            except Exception as exc:
                raise exceptions.storage_error() from exc
            duplicate_file_id = None
        else:
            stored_name = duplicate.stored_name
            object_key = duplicate.object_key
            bucket = duplicate.bucket
            duplicate_file_id = duplicate.id

        file = File(
            id=file_id,
            original_name=sanitized_name,
            stored_name=stored_name,
            extension=extension,
            mime_type=mime_type,
            size=len(data),
            hash=file_hash,
            storage_type="minio",
            bucket=bucket,
            object_key=object_key,
            uploader_id=current_user.id,
            department=current_user.department,
            visibility=visibility,
            description=clean_optional_text(description),
            tags=[],
            status="uploaded",
            review_status="pending",
            ai_analysis_enabled_at_upload=self._settings.ai_analysis_enabled,
            ai_config_snapshot=None,
        )
        try:
            await self._repository.add(file)
            await self._append_file_uploaded_event(file)
            await self._record_upload_audit(
                file=file,
                current_user=current_user,
                duplicate_file_id=duplicate_file_id,
                client_ip=client_ip,
                user_agent=user_agent,
            )
            await self._session.commit()
        except Exception:
            if duplicate is None:
                with suppress(Exception):
                    await self._storage.delete_object(bucket=bucket, object_key=object_key)
            raise
        await self._session.refresh(file)
        return UploadedFileResult(file=file, duplicate_file_id=duplicate_file_id)

    async def _record_upload_audit(
        self,
        *,
        file: File,
        current_user: AuthUserRecord,
        duplicate_file_id: uuid.UUID | None,
        client_ip: str,
        user_agent: str,
    ) -> None:
        await record_audit_log(
            self._session,
            actor_id=current_user.id,
            action="file.upload",
            target_type="file",
            target_id=file.id,
            ip_address=client_ip,
            user_agent=user_agent[:512] or "unknown",
            metadata_json={
                "original_name": file.original_name,
                "extension": file.extension,
                "mime_type": file.mime_type,
                "size": file.size,
                "visibility": file.visibility,
                "duplicate": duplicate_file_id is not None,
                "duplicate_file_id": (
                    str(duplicate_file_id) if duplicate_file_id is not None else None
                ),
                "ai_analysis_enabled_at_upload": file.ai_analysis_enabled_at_upload,
            },
        )

    async def list_my_files(self, current_user: AuthUserRecord) -> list[File]:
        return await self._repository.list_for_uploader(current_user.id)

    async def get_my_file(self, *, current_user: AuthUserRecord, file_id: uuid.UUID) -> File:
        file = await self._repository.get_for_uploader(
            file_id=file_id,
            uploader_id=current_user.id,
        )
        if file is None:
            raise exceptions.file_not_found()
        return file

    async def get_file_detail(
        self,
        *,
        current_user: AuthUserRecord,
        file_id: uuid.UUID,
        client_ip: str,
        user_agent: str,
    ) -> FileDetailResult:
        is_admin_view = current_user.role in ADMIN_ROLES
        if is_admin_view:
            file = await self._repository.get_by_id(file_id)
            if file is None:
                raise exceptions.file_not_found()
        else:
            file = await self.get_my_file(current_user=current_user, file_id=file_id)

        analysis = await self._repository.get_analysis_for_file(file.id)
        result = FileDetailResult(
            file=file,
            category_name=await self._repository.get_category_name(file.id),
            analysis=(
                FileAnalysisDetail(
                    status=analysis.status,
                    summary=analysis.summary,
                    sensitive_risk_level=analysis.sensitive_risk_level,
                    quality_score=None,
                    extracted_text_preview=(
                        analysis.extracted_text[:EXTRACTED_TEXT_PREVIEW_CHARS]
                        if analysis.extracted_text is not None
                        else None
                    ),
                    error_message=analysis.error_message,
                    finished_at=analysis.finished_at,
                )
                if analysis is not None
                else None
            ),
            sync_error=await self._repository.get_latest_failed_sync_error(file.id),
        )
        # 审计在全部查询成功后写入并与之同事务提交, 避免 admin/员工两条路径事务边界不一致,
        # 也保证只记录实际成功返回的查看操作。
        if is_admin_view:
            await record_admin_audit_log(
                self._session,
                actor_id=current_user.id,
                action="file.view_detail",
                target_type="file",
                target_id=file.id,
                ip_address=client_ip,
                user_agent=user_agent[:512] or "unknown",
                metadata_json={
                    "original_name": file.original_name,
                    "uploader_id": str(file.uploader_id),
                },
            )
            await self._session.commit()
        return result

    async def _validate_size(self, size: int) -> None:
        if size <= 0:
            raise exceptions.file_empty()
        max_size_bytes = await resolve_upload_max_size_bytes(self._settings)
        if size > max_size_bytes:
            raise exceptions.file_too_large(max_size_bytes)

    async def _validate_extension(self, extension: str) -> None:
        if extension in UNSUPPORTED_LEGACY_EXTENSIONS:
            raise exceptions.extension_not_allowed(extension)
        if extension not in await resolve_allowed_extensions(self._settings):
            raise exceptions.extension_not_allowed(extension)

    def _validate_mime_type(
        self,
        *,
        data: bytes,
        extension: str,
        declared_mime_type: str | None,
    ) -> str:
        allowed_mimes = normalized_csv(self._settings.upload_allowed_mime_types)
        declared = normalize_mime_type(declared_mime_type)
        if declared not in allowed_mimes:
            raise exceptions.mime_not_allowed(declared)

        expected_mimes = EXTENSION_MIME_MAP.get(extension, set())
        if not expected_mimes or declared not in expected_mimes:
            expected = ", ".join(sorted(expected_mimes))
            raise exceptions.mime_mismatch(expected, declared)

        if extension in TEXT_EXTENSIONS and is_text_content(data):
            return declared

        detected = detect_mime_type(data, extension)
        if detected != declared:
            raise exceptions.mime_mismatch(declared, detected)
        return declared

    async def _append_file_uploaded_event(self, file: File) -> None:
        await OutboxRepository(self._session).append(
            event_type=events.DOCUMENT_FILE_UPLOADED,
            aggregate_type="file",
            aggregate_id=str(file.id),
            payload={
                "file_id": str(file.id),
                "uploader_id": str(file.uploader_id),
                "hash": file.hash,
                "bucket": file.bucket,
                "object_key": file.object_key,
                "status": file.status,
                "ai_analysis_enabled_at_upload": file.ai_analysis_enabled_at_upload,
            },
        )


async def resolve_upload_max_size_bytes(settings: Settings) -> int:
    """解析单文件大小上限并统一换算为字节。

    配置键 ``upload.max_file_size_mb`` 的单位是 MB (PRD 6.14.1),
    而既有 ``settings.upload_max_file_size_bytes`` 的单位是字节,
    比较前在此统一乘 1024*1024 换算; 非法值回退环境变量字节值。
    """
    value = await get_config("upload.max_file_size_mb")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return settings.upload_max_file_size_bytes
    return value * 1024 * 1024


async def resolve_allowed_extensions(settings: Settings) -> set[str]:
    """解析允许上传的扩展名白名单 (upload.allowed_extensions), 非法值回退环境变量。"""
    value = await get_config("upload.allowed_extensions")
    if isinstance(value, list):
        normalized = {str(item).strip().lower().lstrip(".") for item in value if str(item).strip()}
        if normalized:
            return normalized
    return normalized_csv(settings.upload_allowed_extensions)


def normalized_csv(raw_value: str) -> set[str]:
    return {item.strip().lower().lstrip(".") for item in raw_value.split(",") if item.strip()}


def normalize_mime_type(mime_type: str | None) -> str:
    if not mime_type:
        return "application/octet-stream"
    return mime_type.split(";", 1)[0].strip().lower()


def clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def detect_mime_type(data: bytes, extension: str) -> str:
    if extension == "pdf":
        if is_pdf_document(data):
            return "application/pdf"
        return "application/octet-stream"

    if extension in OOXML_REQUIRED_ENTRIES:
        if is_ooxml_document(data, OOXML_REQUIRED_ENTRIES[extension]):
            return next(iter(EXTENSION_MIME_MAP[extension]))
        return detected_or_unknown(data)

    if extension in TEXT_EXTENSIONS:
        if is_text_content(data):
            return "text/plain" if extension != "md" else "text/markdown"
        return detected_or_unknown(data)

    return detected_or_unknown(data)


def detected_or_unknown(data: bytes) -> str:
    detected_kind = filetype.guess(data)
    if detected_kind is None:
        return "application/octet-stream"
    return normalize_mime_type(detected_kind.mime)


def is_pdf_document(data: bytes) -> bool:
    if not data.startswith(PDF_MAGIC):
        return False
    tail = data[-2048:]
    return PDF_EOF_MARKER in tail and PDF_STARTXREF_MARKER in tail


def is_ooxml_document(data: bytes, required_entry: str) -> bool:
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            names = set(archive.namelist())
    except zipfile.BadZipFile:
        return False
    return "[Content_Types].xml" in names and required_entry in names


def is_text_content(data: bytes) -> bool:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return False
    if "\x00" in text:
        return False
    return True


def sanitize_filename(filename: str) -> tuple[str, str]:
    candidate = PureWindowsPath(filename).name
    candidate = PurePosixPath(candidate).name
    candidate = candidate.strip()
    if not candidate:
        candidate = "upload"
    if "." not in candidate:
        raise exceptions.extension_not_allowed("")

    stem, extension = candidate.rsplit(".", 1)
    extension = extension.lower().strip()
    cleaned_stem = sanitize_filename_part(stem)
    cleaned_name = f"{cleaned_stem}.{extension}"
    return cleaned_name, extension


def sanitize_filename_part(value: str) -> str:
    cleaned = FILENAME_UNSAFE_RE.sub("_", value).strip(" ._")
    if not cleaned:
        cleaned = "file"
    if cleaned.upper() in WINDOWS_RESERVED_NAMES:
        cleaned = f"{cleaned}_file"
    return cleaned[:180]
