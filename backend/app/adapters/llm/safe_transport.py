# ruff: noqa: ASYNC109 - httpcore protocol requires timeout-named parameters

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import socket
import ssl
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, cast

import httpcore
import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization

from app.core.llm_endpoint import (
    llm_endpoint_parts,
    normalize_llm_base_url,
    normalize_llm_hostname,
    normalized_llm_allowed_base_urls,
    normalized_llm_tls_spki_pins,
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
    tls_spki_sha256_pins: frozenset[bytes] = frozenset()


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
    raw_tls_spki_pins: str = "",
    require_tls_spki_pin: bool = False,
) -> ResolvedLLMEndpoint:
    normalized = normalize_llm_base_url(base_url)
    if normalized not in normalized_llm_allowed_base_urls(raw_allowed_base_urls):
        raise LLMEndpointSecurityError(
            "base_url_not_allowed",
            kind="policy",
            retryable=False,
        )
    scheme, hostname, port = llm_endpoint_parts(normalized)
    endpoint_pins = normalized_llm_tls_spki_pins(raw_tls_spki_pins).get(
        normalized,
        frozenset(),
    )
    if require_tls_spki_pin and (scheme != "https" or not endpoint_pins):
        raise LLMEndpointSecurityError(
            "tls_spki_pin_required",
            kind="policy",
            retryable=False,
        )
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
        tls_spki_sha256_pins=endpoint_pins,
    )


class TLSSPKIPinningError(httpcore.ConnectError):
    """A detail-free, permanent TLS identity policy failure."""


def spki_sha256_digest_from_der_certificate(certificate_der: bytes) -> bytes:
    certificate = x509.load_der_x509_certificate(certificate_der)
    public_key_der = certificate.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    digest = hashes.Hash(hashes.SHA256())
    digest.update(public_key_der)
    return digest.finalize()


async def _close_stream_quietly(stream: httpcore.AsyncNetworkStream) -> None:
    try:
        await stream.aclose()
    except Exception:
        pass


class TLSSPKIPinningStream(httpcore.AsyncNetworkStream):
    def __init__(
        self,
        stream: httpcore.AsyncNetworkStream,
        *,
        expected_hostname: str,
        allowed_pins: frozenset[bytes],
    ) -> None:
        self._stream = stream
        self._expected_hostname = normalize_llm_hostname(expected_hostname)
        self._allowed_pins = allowed_pins

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return await self._stream.read(max_bytes=max_bytes, timeout=timeout)

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        await self._stream.write(buffer=buffer, timeout=timeout)

    async def aclose(self) -> None:
        await self._stream.aclose()

    async def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.AsyncNetworkStream:
        valid_hostname = False
        try:
            valid_hostname = (
                server_hostname is not None
                and normalize_llm_hostname(server_hostname) == self._expected_hostname
            )
        except ValueError:
            valid_hostname = False
        if not valid_hostname:
            await _close_stream_quietly(self._stream)
            raise TLSSPKIPinningError("tls pin verification failed")

        try:
            tls_stream = await self._stream.start_tls(
                ssl_context=ssl_context,
                server_hostname=server_hostname,
                timeout=timeout,
            )
        except BaseException:
            await _close_stream_quietly(self._stream)
            raise
        verified = False
        try:
            ssl_object = tls_stream.get_extra_info("ssl_object")
            get_peer_certificate = getattr(ssl_object, "getpeercert", None)
            if callable(get_peer_certificate):
                certificate_der = cast(Callable[..., object], get_peer_certificate)(
                    binary_form=True
                )
                if isinstance(certificate_der, bytes):
                    actual_pin = spki_sha256_digest_from_der_certificate(certificate_der)
                    verified = any(
                        hmac.compare_digest(actual_pin, expected_pin)
                        for expected_pin in self._allowed_pins
                    )
        except Exception:
            verified = False
        if not verified:
            await _close_stream_quietly(tls_stream)
            raise TLSSPKIPinningError("tls pin verification failed")
        return tls_stream

    def get_extra_info(self, info: str) -> object:
        return self._stream.get_extra_info(info)


class PinnedNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(
        self,
        *,
        hostname: str,
        port: int,
        pinned_ip: str,
        tls_spki_sha256_pins: frozenset[bytes] = frozenset(),
        backend: httpcore.AsyncNetworkBackend | None = None,
    ) -> None:
        self._hostname = normalize_llm_hostname(hostname)
        self._port = port
        self._pinned_ip = str(ipaddress.ip_address(pinned_ip))
        self._tls_spki_sha256_pins = tls_spki_sha256_pins
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
        stream = await self._backend.connect_tcp(
            host=self._pinned_ip,
            port=port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )
        if not self._tls_spki_sha256_pins:
            return stream
        return TLSSPKIPinningStream(
            stream,
            expected_hostname=self._hostname,
            allowed_pins=self._tls_spki_sha256_pins,
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
    if endpoint.tls_spki_sha256_pins and endpoint.scheme != "https":
        raise ValueError("TLS SPKI pins require HTTPS")
    pinned_backend = PinnedNetworkBackend(
        hostname=endpoint.hostname,
        port=endpoint.port,
        pinned_ip=endpoint.pinned_ip,
        tls_spki_sha256_pins=endpoint.tls_spki_sha256_pins,
        backend=network_backend,
    )
    return PinnedAsyncHTTPTransport(pinned_backend)
