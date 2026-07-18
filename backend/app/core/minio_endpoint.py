"""Strict MinIO endpoint normalization shared by privileged and runtime clients."""

from __future__ import annotations

from collections.abc import Collection
from urllib.parse import urlsplit


def strict_minio_base_url(
    endpoint: str,
    *,
    secure: bool,
    allowed_hosts: Collection[str] | None = None,
    allowed_ports: Collection[int] | None = None,
) -> str:
    """Return one credential-safe base URL or raise ValueError without echoing input."""

    if not endpoint or endpoint != endpoint.strip():
        raise ValueError("MinIO endpoint is invalid")
    scheme = "https" if secure else "http"
    parsed = urlsplit(f"{scheme}://{endpoint}")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("MinIO endpoint is invalid") from error
    hostname = parsed.hostname
    if (
        parsed.scheme != scheme
        or hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("MinIO endpoint is invalid")
    normalized_hostname = hostname.rstrip(".").lower()
    if (
        not normalized_hostname
        or (allowed_hosts is not None and normalized_hostname not in allowed_hosts)
        or (allowed_ports is not None and port not in allowed_ports)
    ):
        raise ValueError("MinIO endpoint is invalid")
    authority = normalized_hostname
    if ":" in authority and not authority.startswith("["):
        authority = f"[{authority}]"
    if port is not None:
        authority = f"{authority}:{port}"
    return f"{scheme}://{authority}"


def minio_metrics_url(
    endpoint: str,
    *,
    secure: bool,
    allowed_hosts: Collection[str] | None = None,
    allowed_ports: Collection[int] | None = None,
) -> str:
    base_url = strict_minio_base_url(
        endpoint,
        secure=secure,
        allowed_hosts=allowed_hosts,
        allowed_ports=allowed_ports,
    )
    return f"{base_url}/minio/v2/metrics/cluster"
