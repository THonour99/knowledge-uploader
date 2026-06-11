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
        status.HTTP_400_BAD_REQUEST,
    )


def dataset_not_allowed() -> ReviewError:
    return ReviewError(
        ErrorCode.VALIDATION_ERROR,
        "ragflow dataset id is not allowed",
        status.HTTP_400_BAD_REQUEST,
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
