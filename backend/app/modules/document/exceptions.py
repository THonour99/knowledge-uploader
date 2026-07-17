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


def permission_denied() -> DocumentError:
    return DocumentError(
        ErrorCode.PERMISSION_DENIED,
        "permission denied",
        status.HTTP_403_FORBIDDEN,
    )


def department_assignment_required() -> DocumentError:
    return DocumentError(
        ErrorCode.DEPARTMENT_ASSIGNMENT_REQUIRED,
        "department assignment is required",
        status.HTTP_403_FORBIDDEN,
    )


def upload_disabled() -> DocumentError:
    return DocumentError(
        ErrorCode.UPLOAD_DISABLED,
        "file upload is disabled",
        status.HTTP_403_FORBIDDEN,
    )


def invalid_state() -> DocumentError:
    return DocumentError(
        ErrorCode.VALIDATION_ERROR,
        "invalid file status transition",
        status.HTTP_400_BAD_REQUEST,
    )


def invalid_replacement() -> DocumentError:
    return DocumentError(
        ErrorCode.FILE_REPLACEMENT_CONFLICT,
        "replacement must target the current parsed version in the same department",
        status.HTTP_409_CONFLICT,
    )


def replacement_conflict() -> DocumentError:
    return DocumentError(
        ErrorCode.FILE_REPLACEMENT_CONFLICT,
        "the selected version already has a replacement",
        status.HTTP_409_CONFLICT,
    )


def version_switch_in_progress() -> DocumentError:
    return DocumentError(
        ErrorCode.FILE_REPLACEMENT_CONFLICT,
        "file cannot be deleted or archived while a version replacement is in progress",
        status.HTTP_409_CONFLICT,
    )


def invalid_owner() -> DocumentError:
    return DocumentError(
        ErrorCode.INVALID_DOCUMENT_OWNER,
        "document owner must be active, verified, and in the document department",
        status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


def review_in_progress() -> DocumentError:
    return DocumentError(
        ErrorCode.VALIDATION_ERROR,
        "file cannot be deleted or archived while review is pending",
        status.HTTP_409_CONFLICT,
    )


def ragflow_reconciliation_pending() -> DocumentError:
    return DocumentError(
        ErrorCode.VALIDATION_ERROR,
        ("RAGFlow upload outcome is still being reconciled; retry after reconciliation completes"),
        status.HTTP_409_CONFLICT,
    )


def ai_analysis_disabled() -> DocumentError:
    return DocumentError(
        ErrorCode.VALIDATION_ERROR,
        "ai analysis is disabled",
        status.HTTP_409_CONFLICT,
    )


def quota_exceeded(*, used_bytes: int, quota_bytes: int) -> DocumentError:
    used_mb = used_bytes / (1024 * 1024)
    quota_mb = quota_bytes / (1024 * 1024)
    remaining_mb = max(quota_bytes - used_bytes, 0) / (1024 * 1024)
    return DocumentError(
        ErrorCode.FILE_QUOTA_EXCEEDED,
        (
            f"storage quota exceeded: used {used_mb:.2f}MB, "
            f"quota {quota_mb:.2f}MB, remaining {remaining_mb:.2f}MB"
        ),
        status.HTTP_400_BAD_REQUEST,
    )


def storage_error() -> DocumentError:
    return DocumentError(
        ErrorCode.STORAGE_ERROR,
        "file storage failed",
        status.HTTP_502_BAD_GATEWAY,
    )
