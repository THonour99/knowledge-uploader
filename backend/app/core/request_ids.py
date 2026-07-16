from __future__ import annotations

import re
import uuid

_TRACE_ID_PATTERN = re.compile(r"[0-9a-fA-F]{32}")


def normalize_opaque_request_id(value: object) -> str | None:
    """Accept only canonical UUIDs or non-zero 128-bit hexadecimal trace IDs."""
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if len(candidate) == 36:
        try:
            parsed = uuid.UUID(candidate)
        except ValueError:
            return None
        canonical = str(parsed)
        return canonical if candidate.lower() == canonical else None
    if _TRACE_ID_PATTERN.fullmatch(candidate) is not None and int(candidate, 16) != 0:
        return candidate.lower()
    return None


def new_request_id() -> str:
    return str(uuid.uuid4())
