from __future__ import annotations

from dataclasses import dataclass

from starlette import status

from app.core.exceptions import ErrorCode


@dataclass(frozen=True)
class ReviewError(Exception):
    error_code: ErrorCode
    message: str
    status_code: int


def permission_denied() -> ReviewError:
    return ReviewError(
        ErrorCode.PERMISSION_DENIED,
        "permission denied",
        status.HTTP_403_FORBIDDEN,
    )


def department_assignment_required() -> ReviewError:
    return ReviewError(
        ErrorCode.DEPARTMENT_ASSIGNMENT_REQUIRED,
        "department assignment is required",
        status.HTTP_403_FORBIDDEN,
    )


def analysis_failed_submission_disabled() -> ReviewError:
    return ReviewError(
        ErrorCode.ANALYSIS_FAILED_SUBMISSION_DISABLED,
        "submitting an analysis-failed file is disabled by policy",
        status.HTTP_409_CONFLICT,
    )


def sensitive_risk_acknowledgement_required() -> ReviewError:
    return ReviewError(
        ErrorCode.SENSITIVE_RISK_ACKNOWLEDGEMENT_REQUIRED,
        "sensitive risk acknowledgement is required",
        status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


def category_not_found() -> ReviewError:
    return ReviewError(
        ErrorCode.VALIDATION_ERROR,
        "category not found",
        status.HTTP_400_BAD_REQUEST,
    )


def dataset_mapping_not_found() -> ReviewError:
    return ReviewError(
        ErrorCode.VALIDATION_ERROR,
        "dataset mapping not found",
        status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


def dataset_mapping_required() -> ReviewError:
    return ReviewError(
        ErrorCode.VALIDATION_ERROR,
        "dataset mapping is required when sync is selected",
        status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


def approve_only_dataset_forbidden() -> ReviewError:
    return ReviewError(
        ErrorCode.VALIDATION_ERROR,
        "dataset mapping must not be provided when approve_only is selected",
        status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


def high_risk_sync_not_allowed() -> ReviewError:
    return ReviewError(
        ErrorCode.VALIDATION_ERROR,
        "high risk file sync is disabled",
        status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


def high_risk_reason_required() -> ReviewError:
    return ReviewError(
        ErrorCode.VALIDATION_ERROR,
        "reason is required to sync a high risk file",
        status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


def rejection_reason_required() -> ReviewError:
    return ReviewError(
        ErrorCode.VALIDATION_ERROR,
        "rejection reason is required",
        status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


def review_already_decided() -> ReviewError:
    return ReviewError(
        ErrorCode.REVIEW_ALREADY_DECIDED,
        "file review state has changed",
        status.HTTP_409_CONFLICT,
    )


def review_claim_conflict() -> ReviewError:
    return ReviewError(
        ErrorCode.REVIEW_CLAIM_CONFLICT,
        "file is already claimed by another reviewer",
        status.HTTP_409_CONFLICT,
    )


def review_claim_required() -> ReviewError:
    return ReviewError(
        ErrorCode.REVIEW_CLAIM_REQUIRED,
        "an active review claim is required",
        status.HTTP_409_CONFLICT,
    )


def classification_draft_locked() -> ReviewError:
    return ReviewError(
        ErrorCode.VALIDATION_ERROR,
        "file classification can only be changed by the active reviewer before approval",
        status.HTTP_409_CONFLICT,
    )


def classification_patch_empty() -> ReviewError:
    return ReviewError(
        ErrorCode.VALIDATION_ERROR,
        "at least one classification field must be provided",
        status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


def draft_metadata_locked() -> ReviewError:
    return ReviewError(
        ErrorCode.VALIDATION_ERROR,
        "file metadata can only be changed in an editable draft state",
        status.HTTP_409_CONFLICT,
    )


def file_version_conflict() -> ReviewError:
    return ReviewError(
        ErrorCode.FILE_VERSION_CONFLICT,
        "file version has changed",
        status.HTTP_409_CONFLICT,
    )


def force_release_reason_required() -> ReviewError:
    return ReviewError(
        ErrorCode.VALIDATION_ERROR,
        "reason is required to release another reviewer's claim",
        status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


def dataset_not_allowed() -> ReviewError:
    return ReviewError(
        ErrorCode.VALIDATION_ERROR,
        "ragflow dataset id is not allowed",
        status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


def file_not_found() -> ReviewError:
    return ReviewError(ErrorCode.FILE_NOT_FOUND, "file not found", status.HTTP_404_NOT_FOUND)


def invalid_state() -> ReviewError:
    return ReviewError(
        ErrorCode.VALIDATION_ERROR,
        "invalid file status transition",
        status.HTTP_400_BAD_REQUEST,
    )


def invalid_visibility() -> ReviewError:
    return ReviewError(
        ErrorCode.VALIDATION_ERROR,
        "invalid category visibility",
        status.HTTP_400_BAD_REQUEST,
    )


def tag_not_found() -> ReviewError:
    return ReviewError(
        ErrorCode.VALIDATION_ERROR,
        "tag not found",
        status.HTTP_404_NOT_FOUND,
    )


def tag_name_conflict() -> ReviewError:
    return ReviewError(
        ErrorCode.VALIDATION_ERROR,
        "tag name already exists",
        status.HTTP_409_CONFLICT,
    )


def tag_name_empty() -> ReviewError:
    return ReviewError(
        ErrorCode.VALIDATION_ERROR,
        "tag name cannot be empty",
        status.HTTP_400_BAD_REQUEST,
    )


def tag_merge_self() -> ReviewError:
    return ReviewError(
        ErrorCode.VALIDATION_ERROR,
        "cannot merge a tag into itself",
        status.HTTP_400_BAD_REQUEST,
    )


def tag_in_use() -> ReviewError:
    return ReviewError(
        ErrorCode.VALIDATION_ERROR,
        "tag has linked files, merge it into another tag first",
        status.HTTP_409_CONFLICT,
    )
