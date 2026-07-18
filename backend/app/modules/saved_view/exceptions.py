from __future__ import annotations

from dataclasses import dataclass

from starlette import status


@dataclass(frozen=True, slots=True)
class SavedViewError(Exception):
    status_code: int
    error_code: str
    message: str


def invalid_definition(message: str) -> SavedViewError:
    return SavedViewError(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        error_code="SAVED_VIEW_INVALID_DEFINITION",
        message=message,
    )


def invalid_scope(message: str = "saved view scope is not available") -> SavedViewError:
    return SavedViewError(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        error_code="SAVED_VIEW_INVALID_SCOPE",
        message=message,
    )


def not_found() -> SavedViewError:
    return SavedViewError(
        status_code=status.HTTP_404_NOT_FOUND,
        error_code="SAVED_VIEW_NOT_FOUND",
        message="saved view not found",
    )


def conflict(message: str) -> SavedViewError:
    return SavedViewError(
        status_code=status.HTTP_409_CONFLICT,
        error_code="SAVED_VIEW_CONFLICT",
        message=message,
    )


def quota_exceeded(limit: int) -> SavedViewError:
    return SavedViewError(
        status_code=status.HTTP_409_CONFLICT,
        error_code="SAVED_VIEW_QUOTA_EXCEEDED",
        message=f"saved view namespace quota of {limit} has been reached",
    )


def unsupported_schema() -> SavedViewError:
    return SavedViewError(
        status_code=status.HTTP_409_CONFLICT,
        error_code="SAVED_VIEW_SCHEMA_UNSUPPORTED",
        message="saved view uses a newer schema and is read-only",
    )
