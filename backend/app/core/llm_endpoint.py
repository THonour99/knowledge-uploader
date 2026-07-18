from __future__ import annotations

import base64
import binascii
import ipaddress
import json
import re
from urllib.parse import unquote, urlsplit

MAX_LLM_BASE_URL_LENGTH = 500
MAX_LLM_TLS_SPKI_PINS_LENGTH = 16_384
MAX_LLM_TLS_SPKI_ENDPOINTS = 64
MAX_LLM_TLS_SPKI_PINS_PER_ENDPOINT = 8
SPKI_SHA256_DIGEST_BYTES = 32
SPKI_SHA256_PREFIX = "sha256/"
DNS_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
NUMERIC_HOST_RE = re.compile(r"[0-9.]+\Z")


class _JSONObjectPairs(list[tuple[str, object]]):
    pass


def _json_object_pairs(pairs: list[tuple[str, object]]) -> _JSONObjectPairs:
    return _JSONObjectPairs(pairs)


def _invalid_spki_pin_configuration() -> ValueError:
    return ValueError("invalid LLM_TLS_SPKI_PINS configuration")


def _decode_spki_sha256_pin(value: object) -> bytes:
    if not isinstance(value, str) or not value.startswith(SPKI_SHA256_PREFIX):
        raise _invalid_spki_pin_configuration()
    encoded_digest = value.removeprefix(SPKI_SHA256_PREFIX)
    try:
        digest = base64.b64decode(encoded_digest, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise _invalid_spki_pin_configuration() from exc
    if (
        len(digest) != SPKI_SHA256_DIGEST_BYTES
        or base64.b64encode(digest).decode("ascii") != encoded_digest
    ):
        raise _invalid_spki_pin_configuration()
    return digest


def normalize_llm_hostname(value: str) -> str:
    candidate = value.rstrip(".")
    if not candidate:
        raise ValueError("invalid LLM hostname")
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        address = None
    if address is not None:
        return address.compressed
    if NUMERIC_HOST_RE.fullmatch(candidate):
        raise ValueError("invalid LLM hostname")
    try:
        ascii_hostname = candidate.encode("idna").decode("ascii").lower()
    except UnicodeError:
        ascii_hostname = ""
    labels = ascii_hostname.split(".")
    if (
        not ascii_hostname
        or len(ascii_hostname) > 253
        or any(DNS_LABEL_RE.fullmatch(label) is None for label in labels)
    ):
        raise ValueError("invalid LLM hostname")
    return ascii_hostname


def normalize_llm_base_url(value: str) -> str:
    cleaned = value.strip()
    if (
        not cleaned
        or len(cleaned) > MAX_LLM_BASE_URL_LENGTH
        or any(ord(char) < 33 for char in cleaned)
        or "\\" in cleaned
        or "?" in cleaned
        or "#" in cleaned
    ):
        raise ValueError("invalid LLM base URL")
    parsed = urlsplit(cleaned)
    invalid_port = False
    try:
        port = parsed.port
    except ValueError:
        invalid_port = True
        port = None
    if (
        invalid_port
        or parsed.scheme.lower() not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("invalid LLM base URL")
    hostname = normalize_llm_hostname(parsed.hostname)
    scheme = parsed.scheme.lower()
    default_port = 443 if scheme == "https" else 80
    host_for_url = f"[{hostname}]" if ":" in hostname else hostname
    authority = host_for_url if port in {None, default_port} else f"{host_for_url}:{port}"
    path = parsed.path.rstrip("/")
    decoded_path = unquote(path)
    lower_path = path.lower()
    if (
        any(ord(char) < 33 for char in path)
        or "\\" in decoded_path
        or "%2f" in lower_path
        or "%5c" in lower_path
        or any(segment in {".", ".."} for segment in decoded_path.split("/"))
    ):
        raise ValueError("invalid LLM base URL")
    return f"{scheme}://{authority}{path}"


def normalized_llm_allowed_base_urls(raw_value: str) -> frozenset[str]:
    normalized: set[str] = set()
    for item in raw_value.split(","):
        candidate = item.strip()
        if candidate:
            normalized.add(normalize_llm_base_url(candidate))
    return frozenset(normalized)


def normalized_llm_tls_spki_pins(raw_value: str) -> dict[str, frozenset[bytes]]:
    """Parse an exact base-URL to SPKI SHA-256 pin mapping.

    The JSON object form prevents a pin approved for one endpoint from being reused for another.
    Duplicate literal or canonical endpoint keys and duplicate pins are rejected.
    """
    cleaned = raw_value.strip()
    if not cleaned:
        return {}
    if len(cleaned) > MAX_LLM_TLS_SPKI_PINS_LENGTH:
        raise _invalid_spki_pin_configuration()
    try:
        parsed: object = json.loads(cleaned, object_pairs_hook=_json_object_pairs)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise _invalid_spki_pin_configuration() from exc
    if not isinstance(parsed, _JSONObjectPairs) or len(parsed) > MAX_LLM_TLS_SPKI_ENDPOINTS:
        raise _invalid_spki_pin_configuration()

    normalized: dict[str, frozenset[bytes]] = {}
    literal_keys: set[str] = set()
    pin_hostnames: dict[bytes, str] = {}
    for raw_base_url, raw_pins in parsed:
        if raw_base_url in literal_keys or not isinstance(raw_pins, list):
            raise _invalid_spki_pin_configuration()
        literal_keys.add(raw_base_url)
        if (
            isinstance(raw_pins, _JSONObjectPairs)
            or not raw_pins
            or len(raw_pins) > MAX_LLM_TLS_SPKI_PINS_PER_ENDPOINT
        ):
            raise _invalid_spki_pin_configuration()
        try:
            base_url = normalize_llm_base_url(raw_base_url)
        except ValueError as exc:
            raise _invalid_spki_pin_configuration() from exc
        if not base_url.startswith("https://") or base_url in normalized:
            raise _invalid_spki_pin_configuration()
        pins = tuple(_decode_spki_sha256_pin(pin) for pin in raw_pins)
        unique_pins = frozenset(pins)
        if len(unique_pins) != len(pins):
            raise _invalid_spki_pin_configuration()
        hostname = urlsplit(base_url).hostname
        if hostname is None:
            raise _invalid_spki_pin_configuration()
        for pin in unique_pins:
            existing_hostname = pin_hostnames.get(pin)
            if existing_hostname is not None and existing_hostname != hostname:
                raise _invalid_spki_pin_configuration()
            pin_hostnames[pin] = hostname
        normalized[base_url] = unique_pins
    return normalized


def llm_base_url_is_allowed(base_url: str, raw_allowed_base_urls: str) -> bool:
    normalized = normalize_llm_base_url(base_url)
    return normalized in normalized_llm_allowed_base_urls(raw_allowed_base_urls)


def llm_endpoint_parts(base_url: str) -> tuple[str, str, int]:
    normalized = normalize_llm_base_url(base_url)
    parsed = urlsplit(normalized)
    if parsed.hostname is None:
        raise ValueError("invalid LLM base URL")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return parsed.scheme, normalize_llm_hostname(parsed.hostname), port
