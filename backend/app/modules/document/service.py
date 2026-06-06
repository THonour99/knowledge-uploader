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

from app.core.config import Settings
from app.core.outbox import OutboxRepository
from app.modules.user.schemas import AuthUserRecord

from . import events, exceptions
from .models import File
from .repository import DocumentRepository

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
    ) -> UploadedFileResult:
        self._validate_size(len(data))
        sanitized_name, extension = sanitize_filename(original_filename)
        self._validate_extension(extension)
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
            await self._session.commit()
        except Exception:
            if duplicate is None:
                with suppress(Exception):
                    await self._storage.delete_object(bucket=bucket, object_key=object_key)
            raise
        await self._session.refresh(file)
        return UploadedFileResult(file=file, duplicate_file_id=duplicate_file_id)

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

    def _validate_size(self, size: int) -> None:
        if size <= 0:
            raise exceptions.file_empty()
        if size > self._settings.upload_max_file_size_bytes:
            raise exceptions.file_too_large(self._settings.upload_max_file_size_bytes)

    def _validate_extension(self, extension: str) -> None:
        if extension in UNSUPPORTED_LEGACY_EXTENSIONS:
            raise exceptions.extension_not_allowed(extension)
        if extension not in normalized_csv(self._settings.upload_allowed_extensions):
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
