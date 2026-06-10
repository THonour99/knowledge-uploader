from __future__ import annotations

from dataclasses import dataclass

from starlette import status

from app.core.exceptions import ErrorCode


@dataclass(frozen=True)
class ConfigError(Exception):
    error_code: ErrorCode
    message: str
    status_code: int


def permission_denied() -> ConfigError:
    return ConfigError(
        ErrorCode.PERMISSION_DENIED,
        "permission denied",
        status.HTTP_403_FORBIDDEN,
    )


def group_not_found() -> ConfigError:
    return ConfigError(
        ErrorCode.VALIDATION_ERROR,
        "config group not found",
        status.HTTP_404_NOT_FOUND,
    )


def unknown_config_key(key: str) -> ConfigError:
    return ConfigError(
        ErrorCode.VALIDATION_ERROR,
        f"unknown config key: {key}",
        status.HTTP_400_BAD_REQUEST,
    )


def invalid_config_value(key: str) -> ConfigError:
    return ConfigError(
        ErrorCode.VALIDATION_ERROR,
        f"invalid config value for key: {key}",
        status.HTTP_400_BAD_REQUEST,
    )


def empty_update() -> ConfigError:
    return ConfigError(
        ErrorCode.VALIDATION_ERROR,
        "no config items provided",
        status.HTTP_400_BAD_REQUEST,
    )
