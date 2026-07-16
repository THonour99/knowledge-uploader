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


def immutable_config_key(key: str) -> ConfigError:
    return ConfigError(
        ErrorCode.VALIDATION_ERROR,
        f"config key is immutable: {key}",
        status.HTTP_400_BAD_REQUEST,
    )


def empty_update() -> ConfigError:
    return ConfigError(
        ErrorCode.VALIDATION_ERROR,
        "no config items provided",
        status.HTTP_400_BAD_REQUEST,
    )


def dead_letter_not_found() -> ConfigError:
    return ConfigError(
        ErrorCode.VALIDATION_ERROR,
        "outbox dead letter not found",
        status.HTTP_404_NOT_FOUND,
    )


def rabbit_dead_letter_not_found() -> ConfigError:
    return ConfigError(
        ErrorCode.VALIDATION_ERROR,
        "RabbitMQ dead-letter queue is empty",
        status.HTTP_404_NOT_FOUND,
    )


def rabbit_dead_letter_unsafe() -> ConfigError:
    return ConfigError(
        ErrorCode.VALIDATION_ERROR,
        "RabbitMQ dead-letter message is investigation-only and cannot be clean-room replayed",
        status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


def rabbit_dead_letter_changed() -> ConfigError:
    return ConfigError(
        ErrorCode.VALIDATION_ERROR,
        "RabbitMQ dead-letter queue changed; retry the operation",
        status.HTTP_409_CONFLICT,
    )


def rabbit_dead_letter_busy() -> ConfigError:
    return ConfigError(
        ErrorCode.VALIDATION_ERROR,
        "RabbitMQ dead-letter replay is already in progress",
        status.HTTP_409_CONFLICT,
    )


def rabbit_dead_letter_unavailable() -> ConfigError:
    return ConfigError(
        ErrorCode.INTERNAL_ERROR,
        "RabbitMQ dead-letter replay is temporarily unavailable",
        status.HTTP_503_SERVICE_UNAVAILABLE,
    )
