from __future__ import annotations

import ipaddress
import re
from urllib.parse import unquote, urlsplit

MAX_LLM_BASE_URL_LENGTH = 500
DNS_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
NUMERIC_HOST_RE = re.compile(r"[0-9.]+\Z")


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
