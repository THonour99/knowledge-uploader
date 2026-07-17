"""Shared fail-closed checks for Alertmanager HTTP header secret material."""

from __future__ import annotations

import re
from collections.abc import Mapping

PUBLIC_INLINE_HTTP_HEADER_NAMES = frozenset({"accept", "contenttype", "useragent"})


def _canonical_header_name(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().casefold())


def _configured(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        return any(_configured(child) for child in value.values())
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_configured(child) for child in value)
    return True


def sensitive_http_header_paths(value: object) -> tuple[str, ...]:
    """Return config paths that contain inline HTTP header secret material.

    Alertmanager permits header values to be sourced from ``files``. Those file
    references remain valid. Inline ``secrets`` are always forbidden, while
    inline ``values`` are allowed only for a small public-header allowlist.
    """

    errors: list[str] = []

    def inspect_http_config(config: object, path: tuple[str, ...]) -> None:
        if not isinstance(config, Mapping):
            return
        raw_headers = next(
            (
                child
                for raw_key, child in config.items()
                if str(raw_key).strip().casefold() == "http_headers"
            ),
            None,
        )
        if not isinstance(raw_headers, Mapping):
            return
        for raw_name, raw_definition in raw_headers.items():
            header_name = str(raw_name).strip()
            header_path = (*path, "http_headers", header_name)
            if not isinstance(raw_definition, Mapping):
                continue
            fields = {
                str(raw_key).strip().casefold(): child for raw_key, child in raw_definition.items()
            }
            if _configured(fields.get("secrets")):
                errors.append(".".join((*header_path, "secrets")))
            if _configured(fields.get("values")) and (
                _canonical_header_name(header_name) not in PUBLIC_INLINE_HTTP_HEADER_NAMES
            ):
                errors.append(".".join((*header_path, "values")))

    def walk(node: object, path: tuple[str, ...]) -> None:
        if isinstance(node, Mapping):
            for raw_key, child in node.items():
                key = str(raw_key).strip()
                child_path = (*path, key)
                if key.casefold() == "http_config":
                    inspect_http_config(child, child_path)
                walk(child, child_path)
        elif isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, (*path, str(index)))

    walk(value, ())
    return tuple(errors)
