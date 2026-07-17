from __future__ import annotations

from dataclasses import dataclass

from starlette import status

from app.core.exceptions import ErrorCode


@dataclass(frozen=True)
class RagflowTaskError(Exception):
    error_code: ErrorCode
    message: str
    status_code: int


class RagflowTaskAlreadyRunningError(Exception):
    """A redelivered worker message observed an execution that may still be alive."""


class RagflowTaskLeaseLostError(Exception):
    """A stale worker no longer owns the persisted execution fencing token."""


class RagflowUploadOutcomeUnknownError(Exception):
    """Remote upload may have committed, but no durable document id was observed."""


def permission_denied() -> RagflowTaskError:
    return RagflowTaskError(
        ErrorCode.PERMISSION_DENIED,
        "permission denied",
        status.HTTP_403_FORBIDDEN,
    )


def task_not_found() -> RagflowTaskError:
    return RagflowTaskError(
        ErrorCode.VALIDATION_ERROR,
        "task not found",
        status.HTTP_404_NOT_FOUND,
    )


def task_not_retryable() -> RagflowTaskError:
    return RagflowTaskError(
        ErrorCode.VALIDATION_ERROR,
        "task cannot be retried",
        status.HTTP_400_BAD_REQUEST,
    )


def task_conflict() -> RagflowTaskError:
    return RagflowTaskError(
        ErrorCode.VALIDATION_ERROR,
        "another active ragflow synchronization task exists",
        status.HTTP_409_CONFLICT,
    )


def task_lock_busy() -> RagflowTaskError:
    return RagflowTaskError(
        ErrorCode.VALIDATION_ERROR,
        "ragflow sync task is busy",
        status.HTTP_409_CONFLICT,
    )


def file_not_found() -> RagflowTaskError:
    return RagflowTaskError(
        ErrorCode.VALIDATION_ERROR,
        "file not found",
        status.HTTP_404_NOT_FOUND,
    )


def file_not_syncable() -> RagflowTaskError:
    return RagflowTaskError(
        ErrorCode.VALIDATION_ERROR,
        "file cannot be manually synced in its current state",
        status.HTTP_409_CONFLICT,
    )


def dataset_not_allowed() -> RagflowTaskError:
    return RagflowTaskError(
        ErrorCode.VALIDATION_ERROR,
        "ragflow dataset id is not allowed",
        status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


def dataset_mapping_not_found() -> RagflowTaskError:
    return RagflowTaskError(
        ErrorCode.VALIDATION_ERROR,
        "dataset mapping not found or disabled",
        status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


def dataset_mapping_category_mismatch() -> RagflowTaskError:
    return RagflowTaskError(
        ErrorCode.VALIDATION_ERROR,
        "dataset mapping does not match file category",
        status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


def remote_document_dataset_change_not_allowed() -> RagflowTaskError:
    return RagflowTaskError(
        ErrorCode.VALIDATION_ERROR,
        "an existing ragflow document cannot change dataset target",
        status.HTTP_409_CONFLICT,
    )


def high_risk_sync_not_allowed() -> RagflowTaskError:
    return RagflowTaskError(
        ErrorCode.VALIDATION_ERROR,
        "high risk file sync is disabled",
        status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


def high_risk_reason_required() -> RagflowTaskError:
    return RagflowTaskError(
        ErrorCode.VALIDATION_ERROR,
        "reason is required to sync a high risk file",
        status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


def sync_blocked_by_sensitive_policy() -> RagflowTaskError:
    return RagflowTaskError(
        ErrorCode.VALIDATION_ERROR,
        "file sync is blocked by sensitive content policy",
        status.HTTP_409_CONFLICT,
    )


def incomplete_version_switch_task_not_cancelable() -> RagflowTaskError:
    return RagflowTaskError(
        ErrorCode.VALIDATION_ERROR,
        "an incomplete version switch task cannot be canceled",
        status.HTTP_409_CONFLICT,
    )


def task_not_version_switch_reconcilable() -> RagflowTaskError:
    return RagflowTaskError(
        ErrorCode.VALIDATION_ERROR,
        "task is not eligible for version switch reconciliation",
        status.HTTP_409_CONFLICT,
    )


def version_switch_reconcile_reason_required() -> RagflowTaskError:
    return RagflowTaskError(
        ErrorCode.VALIDATION_ERROR,
        "a reason is required for version switch reconciliation",
        status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


def task_not_cancelable() -> RagflowTaskError:
    return RagflowTaskError(
        ErrorCode.VALIDATION_ERROR,
        "task cannot be canceled",
        status.HTTP_400_BAD_REQUEST,
    )
