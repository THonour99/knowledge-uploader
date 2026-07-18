"""Strict, non-logging validation for internal bearer JWT files."""

from __future__ import annotations

import base64
import binascii
import math
import re
import time

from app.core.strict_json import StrictJsonError, strict_json_object

_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
_STRING_IDENTITY_CLAIMS = frozenset({"iss", "sub", "jti", "accessKey"})


def _decode_base64url(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.b64decode(
        segment + padding,
        altchars=b"-_",
        validate=True,
    )


def _strict_object(segment: str) -> dict[str, object]:
    return strict_json_object(_decode_base64url(segment))


def _numeric_date(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError
    return numeric


def _nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _has_nonempty_identity_claim(claims: dict[str, object]) -> bool:
    if any(_nonempty_string(claims.get(name)) for name in _STRING_IDENTITY_CLAIMS):
        return True
    audience = claims.get("aud")
    if _nonempty_string(audience):
        return True
    return (
        isinstance(audience, list)
        and bool(audience)
        and all(_nonempty_string(item) for item in audience)
    )


def is_semantic_time_bound_jwt(
    token: str,
    *,
    now_seconds: float | None = None,
    future_skew_seconds: float = 30.0,
) -> bool:
    """Return False for any lexical, JSON, signature-shape, or time-bound failure."""

    try:
        if (
            not token
            or len(token.encode("ascii", errors="strict")) != len(token)
            or _TOKEN_PATTERN.fullmatch(token) is None
        ):
            return False
        header_segment, claims_segment, signature_segment = token.split(".")
        header = _strict_object(header_segment)
        claims = _strict_object(claims_segment)
        signature = _decode_base64url(signature_segment)
        algorithm = header.get("alg")
        if (
            not isinstance(algorithm, str)
            or not algorithm.strip()
            or algorithm.strip().lower() == "none"
            or not _has_nonempty_identity_claim(claims)
            or not signature
        ):
            return False
        current = time.time() if now_seconds is None else now_seconds
        if _numeric_date(claims.get("exp")) <= current:
            return False
        for claim in ("nbf", "iat"):
            value = claims.get(claim)
            if value is not None and _numeric_date(value) > current + future_skew_seconds:
                return False
        return True
    except (
        UnicodeError,
        binascii.Error,
        StrictJsonError,
        TypeError,
        ValueError,
        OverflowError,
    ):
        return False
