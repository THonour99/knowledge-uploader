"""Strict JSON object parsing for security-sensitive internal contracts."""

from __future__ import annotations

import json
import math


class StrictJsonError(ValueError):
    """Raised without payload details when a strict JSON contract is violated."""


def _reject_constant(_value: str) -> None:
    raise StrictJsonError


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise StrictJsonError
        result[key] = value
    return result


def _validate_numbers(value: object) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise StrictJsonError
    if isinstance(value, dict):
        for nested in value.values():
            _validate_numbers(nested)
    elif isinstance(value, list):
        for nested in value:
            _validate_numbers(nested)


def strict_json_object(payload: str | bytes) -> dict[str, object]:
    """Decode one UTF-8 JSON object, rejecting duplicates and non-finite numbers."""

    try:
        source = payload.decode("utf-8", errors="strict") if isinstance(payload, bytes) else payload
        value = json.loads(
            source,
            parse_constant=_reject_constant,
            object_pairs_hook=_reject_duplicate_pairs,
        )
    except (UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise StrictJsonError from error
    if not isinstance(value, dict):
        raise StrictJsonError
    _validate_numbers(value)
    return value
