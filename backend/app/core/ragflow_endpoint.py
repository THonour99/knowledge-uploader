from __future__ import annotations

import base64
import binascii
import ipaddress
import json
import re
from collections.abc import Iterable
from urllib.parse import unquote, urlsplit

MAX_RAGFLOW_BASE_URL_LENGTH = 500
MAX_RAGFLOW_TLS_SPKI_PINS_LENGTH = 16_384
MAX_RAGFLOW_TLS_SPKI_ENDPOINTS = 64
MAX_RAGFLOW_TLS_SPKI_PINS_PER_ENDPOINT = 8
SPKI_SHA256_DIGEST_BYTES = 32
SPKI_SHA256_PREFIX = "sha256/"
DNS_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
NUMERIC_HOST_RE = re.compile(r"[0-9.]+\Z")
RagflowEndpointIdentity = tuple[str, str, int, str]


class _JSONObjectPairs(list[tuple[str, object]]):
    pass


def _json_object_pairs(pairs: list[tuple[str, object]]) -> _JSONObjectPairs:
    return _JSONObjectPairs(pairs)


def _invalid_spki_pin_configuration() -> ValueError:
    return ValueError("invalid RAGFLOW_TLS_SPKI_PINS configuration")


def normalize_ragflow_hostname(raw_value: str) -> str:
    candidate = raw_value.strip().rstrip(".")
    if not candidate:
        raise ValueError("invalid RAGFlow hostname")
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        address = None
    if address is not None:
        return address.compressed.lower()
    if NUMERIC_HOST_RE.fullmatch(candidate):
        raise ValueError("invalid RAGFlow hostname")
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
        raise ValueError("invalid RAGFlow hostname")
    return ascii_hostname


def normalize_ragflow_base_url(raw_value: str) -> str:
    cleaned = raw_value.strip()
    if (
        not cleaned
        or len(cleaned) > MAX_RAGFLOW_BASE_URL_LENGTH
        or any(ord(char) < 33 for char in cleaned)
        or "\\" in cleaned
        or "?" in cleaned
        or "#" in cleaned
    ):
        raise ValueError("RAGFlow base URL must be an absolute HTTP(S) endpoint")
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
        raise ValueError("RAGFlow base URL must be an absolute HTTP(S) endpoint")

    hostname = normalize_ragflow_hostname(parsed.hostname)
    if hostname == "metadata.google.internal":
        raise ValueError("RAGFlow base URL must not target an instance metadata endpoint")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and (address.is_link_local or address.is_unspecified):
        raise ValueError("RAGFlow base URL must not target a link-local endpoint")

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
        raise ValueError("RAGFlow base URL path is invalid")
    return f"{scheme}://{authority}{path}"


def ragflow_endpoint_identity(raw_value: str) -> RagflowEndpointIdentity:
    normalized = normalize_ragflow_base_url(raw_value)
    parsed = urlsplit(normalized)
    hostname = parsed.hostname
    if hostname is None:
        raise ValueError("RAGFlow base URL must include a hostname")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return parsed.scheme, normalize_ragflow_hostname(hostname), port, parsed.path


def normalize_ragflow_spki_pin(raw_value: object) -> bytes:
    if not isinstance(raw_value, str) or not raw_value.startswith(SPKI_SHA256_PREFIX):
        raise _invalid_spki_pin_configuration()
    encoded_digest = raw_value.removeprefix(SPKI_SHA256_PREFIX)
    try:
        digest = base64.b64decode(encoded_digest, validate=True)
    except (binascii.Error, ValueError) as error:
        raise _invalid_spki_pin_configuration() from error
    if (
        len(digest) != SPKI_SHA256_DIGEST_BYTES
        or base64.b64encode(digest).decode("ascii") != encoded_digest
    ):
        raise _invalid_spki_pin_configuration()
    return digest


def validate_ragflow_spki_digests(raw_values: Iterable[bytes]) -> frozenset[bytes]:
    values = tuple(raw_values)
    if (
        not values
        or len(values) > MAX_RAGFLOW_TLS_SPKI_PINS_PER_ENDPOINT
        or any(len(value) != SPKI_SHA256_DIGEST_BYTES for value in values)
    ):
        raise _invalid_spki_pin_configuration()
    pins = frozenset(values)
    if len(pins) != len(values):
        raise _invalid_spki_pin_configuration()
    return pins


def normalized_ragflow_tls_spki_pins(
    raw_value: str,
) -> dict[RagflowEndpointIdentity, frozenset[bytes]]:
    if len(raw_value.encode("utf-8")) > MAX_RAGFLOW_TLS_SPKI_PINS_LENGTH:
        raise _invalid_spki_pin_configuration()
    cleaned = raw_value.strip()
    if not cleaned:
        return {}
    try:
        parsed: object = json.loads(cleaned, object_pairs_hook=_json_object_pairs)
    except (json.JSONDecodeError, RecursionError) as error:
        raise _invalid_spki_pin_configuration() from error
    if not isinstance(parsed, _JSONObjectPairs) or len(parsed) > MAX_RAGFLOW_TLS_SPKI_ENDPOINTS:
        raise _invalid_spki_pin_configuration()

    result: dict[RagflowEndpointIdentity, frozenset[bytes]] = {}
    literal_keys: set[str] = set()
    pin_hostnames: dict[bytes, str] = {}
    for raw_endpoint, raw_pins in parsed:
        if raw_endpoint in literal_keys or not isinstance(raw_pins, list):
            raise _invalid_spki_pin_configuration()
        literal_keys.add(raw_endpoint)
        if (
            isinstance(raw_pins, _JSONObjectPairs)
            or not raw_pins
            or len(raw_pins) > MAX_RAGFLOW_TLS_SPKI_PINS_PER_ENDPOINT
        ):
            raise _invalid_spki_pin_configuration()
        try:
            identity = ragflow_endpoint_identity(raw_endpoint)
        except ValueError as error:
            raise _invalid_spki_pin_configuration() from error
        if identity[0] != "https" or identity in result:
            raise _invalid_spki_pin_configuration()
        pin_values = tuple(normalize_ragflow_spki_pin(pin) for pin in raw_pins)
        pins = frozenset(pin_values)
        if len(pins) != len(pin_values):
            raise _invalid_spki_pin_configuration()
        hostname = identity[1]
        for pin in pins:
            existing_hostname = pin_hostnames.get(pin)
            if existing_hostname is not None and existing_hostname != hostname:
                raise _invalid_spki_pin_configuration()
            pin_hostnames[pin] = hostname
        result[identity] = pins
    return result


def ragflow_tls_spki_pins_for_endpoint(
    base_url: str,
    raw_mapping: str,
) -> frozenset[bytes]:
    return normalized_ragflow_tls_spki_pins(raw_mapping).get(
        ragflow_endpoint_identity(base_url),
        frozenset(),
    )
