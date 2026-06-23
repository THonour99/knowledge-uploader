from __future__ import annotations

import re
from collections.abc import MutableMapping
from typing import Any

import structlog

API_KEY_PATTERN = re.compile(r"\b(sk-)[A-Za-z0-9_-]+([A-Za-z0-9_-]{4})\b")
RAGFLOW_KEY_PATTERN = re.compile(r"\b(ragflow-)[A-Za-z0-9_-]+([A-Za-z0-9_-]{4})\b")
BEARER_TOKEN_PATTERN = re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/-]+=*", re.IGNORECASE)
SENSITIVE_KEY_PARTS = (
    "api_key",
    "authorization",
    "credential",
    "password",
    "secret",
    "token",
)


def mask_secret(value: str) -> str:
    masked = API_KEY_PATTERN.sub(r"\1****\2", value)
    masked = RAGFLOW_KEY_PATTERN.sub(r"\1****\2", masked)
    return BEARER_TOKEN_PATTERN.sub(r"\1***", masked)


def mask_log_value(key: str, value: Any) -> Any:
    if isinstance(value, dict):
        return {
            nested_key: mask_log_value(str(nested_key), nested_value)
            for nested_key, nested_value in value.items()
        }
    if isinstance(value, list):
        return [mask_log_value(key, item) for item in value]
    if isinstance(value, str):
        if any(part in key.lower() for part in SENSITIVE_KEY_PARTS):
            return "***"
        return mask_secret(value)
    return value


def mask_secrets_processor(
    _logger: Any,
    _method_name: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    return {key: mask_log_value(key, value) for key, value in event_dict.items()}


def configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            mask_secrets_processor,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> Any:
    return structlog.get_logger(name)
