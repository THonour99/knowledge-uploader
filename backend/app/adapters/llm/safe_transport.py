# ruff: noqa: ASYNC109 - httpcore protocol requires timeout-named parameters

from __future__ import annotations

import asyncio
import ipaddress
import socket
import ssl
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

import httpcore
import httpx

from app.core.llm_endpoint import (
    llm_endpoint_parts,
    normalize_llm_base_url,
    normalize_llm_hostname,
    normalized_llm_allowed_base_urls,
)

EndpointFailureKind = Literal["policy", "resolution"]
METADATA_ADDRESSES = frozenset(
    {
        ipaddress.ip_address("169.254.169.254"),
        ipaddress.ip_address("169.254.170.2"),
        ipaddress.ip_address("168.63.129.16"),
        ipaddress.ip_address("100.100.100.200"),
        ipaddress.ip_address("fd00:ec2::254"),
    }
)
PRIVATE_LLM_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
)



class AsyncHostResolver(Protocol):
    async def resolve(self, hostname: str, port: int) -> Sequence[str]: ...


class LLMEndpointSecurityError(Exception):
    """A detail-free endpoint failure safe to classify and persist.

    Never attach resolver exceptions, request objects, credentials, or document text.
    Production error reporting must not capture traceback locals for LLM calls.
    """

    def __init__(
        self,
        reason: str,
        *,
        kind: EndpointFailureKind,
        retryable: bool,
    ) -> None:
        self.reason = reason
        self.kind = kind
        self.retryable = retryable
        super().__init__(reason)


@dataclass(frozen=True)
class ResolvedLLMEndpoint:
    base_url: str
    scheme: str
    hostname: str
    port: int
    pinned_ip: str
    is_external: bool


class SystemHostResolver:
    async def resolve(self, hostname: str, port: int) -> Sequence[str]:
        loop = asyncio.get_running_loop()
        resolution_failed = False
        try:
            results = await loop.getaddrinfo(
                hostname,
                port,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
                proto=socket.IPPROTO_TCP,
            )
        except (OSError, UnicodeError):
            resolution_failed = True
            results = []
        if resolution_failed:
            raise LLMEndpointSecurityError(
                "dns_resolution_failed",
                kind="resolution",
                retryable=True,
            )
        return [str(sockaddr[0]) for _family, _type, _proto, _canonname, sockaddr in results]


def _safe_ip_addresses(
    raw_addresses: Sequence[str],
) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]:
    parsed: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    invalid = False
    for raw_address in raw_addresses:
        try:
            address = ipaddress.ip_address(raw_address)
        except ValueError:
            invalid = True
            continue
        if address not in parsed:
            parsed.append(address)
    if invalid or not parsed:
        raise LLMEndpointSecurityError(
            "dns_resolution_invalid",
            kind="resolution",
            retryable=True,
        )
    return tuple(parsed)


def _address_is_private(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    return any(address in network for network in PRIVATE_LLM_NETWORKS)


def _address_is_always_forbidden(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    return bool(
        address in METADATA_ADDRESSES
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or address.is_reserved
        or getattr(address, "is_site_local", False)
        or (not _address_is_private(address) and not address.is_global)
    )


async def resolve_and_authorize_llm_endpoint(
    *,
    base_url: str,
    raw_allowed_base_urls: str,
    allow_external: bool,
    is_internal: bool,
    resolver: AsyncHostResolver,
) -> ResolvedLLMEndpoint:
    normalized = normalize_llm_base_url(base_url)
    if normalized not in normalized_llm_allowed_base_urls(raw_allowed_base_urls):
        raise LLMEndpointSecurityError(
            "base_url_not_allowed",
            kind="policy",
            retryable=False,
        )
    scheme, hostname, port = llm_endpoint_parts(normalized)
    resolution_failed = False
    try:
        raw_addresses = await resolver.resolve(hostname, port)
    except LLMEndpointSecurityError:
        raise
    except Exception:
        resolution_failed = True
        raw_addresses = ()
    if resolution_failed:
        raise LLMEndpointSecurityError(
            "dns_resolution_failed",
            kind="resolution",
            retryable=True,
        )
    addresses = _safe_ip_addresses(raw_addresses)
    if any(_address_is_always_forbidden(address) for address in addresses):
        raise LLMEndpointSecurityError(
            "resolved_address_forbidden",
            kind="policy",
            retryable=False,
        )
    has_private = any(_address_is_private(address) for address in addresses)
    has_public = any(address.is_global for address in addresses)
    if has_private and has_public:
        raise LLMEndpointSecurityError(
            "mixed_address_scopes_forbidden",
            kind="policy",
            retryable=False,
        )
    if has_private and not is_internal:
        raise LLMEndpointSecurityError(
            "private_address_requires_internal_provider",
            kind="policy",
            retryable=False,
        )
    if has_public and (scheme != "https" or not allow_external):
        raise LLMEndpointSecurityError(
            "external_provider_not_permitted",
            kind="policy",
            retryable=False,
        )
    return ResolvedLLMEndpoint(
        base_url=normalized,
        scheme=scheme,
        hostname=hostname,
        port=port,
        pinned_ip=str(addresses[0]),
        is_external=has_public,
    )


class PinnedNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(
        self,
        *,
        hostname: str,
        port: int,
        pinned_ip: str,
        backend: httpcore.AsyncNetworkBackend | None = None,
    ) -> None:
        self._hostname = normalize_llm_hostname(hostname)
        self._port = port
        self._pinned_ip = str(ipaddress.ip_address(pinned_ip))
        self._backend = backend or httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        valid_origin = False
        try:
            valid_origin = normalize_llm_hostname(host) == self._hostname and port == self._port
        except ValueError:
            valid_origin = False
        if not valid_origin:
            raise httpcore.ConnectError("pinned endpoint mismatch")
        return await self._backend.connect_tcp(
            host=self._pinned_ip,
            port=port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise httpcore.ConnectError("unix sockets are forbidden")

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)


class PinnedAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    def __init__(self, network_backend: httpcore.AsyncNetworkBackend) -> None:
        super().__init__(trust_env=False, retries=0)
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl.create_default_context(),
            http1=True,
            http2=False,
            retries=0,
            network_backend=network_backend,
        )


def build_pinned_transport(
    endpoint: ResolvedLLMEndpoint,
    *,
    network_backend: httpcore.AsyncNetworkBackend | None = None,
) -> httpx.AsyncBaseTransport:
    pinned_backend = PinnedNetworkBackend(
        hostname=endpoint.hostname,
        port=endpoint.port,
        pinned_ip=endpoint.pinned_ip,
        backend=network_backend,
    )
    return PinnedAsyncHTTPTransport(pinned_backend)
