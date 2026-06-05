from __future__ import annotations

from dataclasses import dataclass

from starlette import status

from app.core.exceptions import ErrorCode


@dataclass(frozen=True)
class DocumentError(Exception):
    error_code: ErrorCode
    message: str
    status_code: int


def file_empty() -> DocumentError:
    return DocumentError(ErrorCode.FILE_EMPTY, "file is empty", status.HTTP_400_BAD_REQUEST)


def file_too_large(max_size: int) -> DocumentError:
    return DocumentError(
        ErrorCode.FILE_TOO_LARGE,
        f"file size exceeds {max_size} bytes",
        status.HTTP_400_BAD_REQUEST,
    )


def extension_not_allowed(extension: str) -> DocumentError:
    return DocumentError(
        ErrorCode.FILE_EXTENSION_NOT_ALLOWED,
        "file extension is not allowed",
        status.HTTP_400_BAD_REQUEST,
    )


def mime_not_allowed(mime_type: str) -> DocumentError:
    return DocumentError(
        ErrorCode.FILE_MIME_NOT_ALLOWED,
        "file mime type is not allowed",
        status.HTTP_400_BAD_REQUEST,
    )


def mime_mismatch(expected: str, actual: str) -> DocumentError:
    return DocumentError(
        ErrorCode.FILE_MIME_MISMATCH,
        "file mime type mismatch",
        status.HTTP_400_BAD_REQUEST,
    )


def invalid_visibility() -> DocumentError:
    return DocumentError(
        ErrorCode.VALIDATION_ERROR,
        "invalid file visibility",
        status.HTTP_400_BAD_REQUEST,
    )


def file_not_found() -> DocumentError:
    return DocumentError(ErrorCode.FILE_NOT_FOUND, "file not found", status.HTTP_404_NOT_FOUND)


def storage_error() -> DocumentError:
    return DocumentError(
        ErrorCode.STORAGE_ERROR,
        "file storage failed",
        status.HTTP_502_BAD_GATEWAY,
    )
