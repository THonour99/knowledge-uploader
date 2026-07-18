# ruff: noqa: ASYNC109 - httpcore protocol requires timeout-named parameters

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import socket
import ssl
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, cast

import httpcore
import httpx
from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from app.core.ragflow_endpoint import (
    normalize_ragflow_hostname,
    ragflow_endpoint_identity,
    validate_ragflow_spki_digests,
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
PRIVATE_RAGFLOW_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
)


class AsyncHostResolver(Protocol):
    async def resolve(self, hostname: str, port: int) -> Sequence[str]: ...


class _PeerCertificateProvider(Protocol):
    def getpeercert(self, binary_form: bool = False) -> object: ...


class RagflowEndpointSecurityError(Exception):
    """A detail-free RAGFlow endpoint failure safe to report without endpoint data."""

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
class ResolvedRagflowEndpoint:
    base_url: str
    scheme: str
    hostname: str
    port: int
    pinned_ip: str
    tls_spki_pins: frozenset[bytes]


class SystemHostResolver:
    async def resolve(self, hostname: str, port: int) -> Sequence[str]:
        loop = asyncio.get_running_loop()
        try:
            results = await loop.getaddrinfo(
                hostname,
                port,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
                proto=socket.IPPROTO_TCP,
            )
        except (OSError, UnicodeError):
            raise RagflowEndpointSecurityError(
                "dns_resolution_failed",
                kind="resolution",
                retryable=True,
            ) from None
        return [str(sockaddr[0]) for _family, _type, _proto, _canonname, sockaddr in results]


def _address_is_private(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    return any(address in network for network in PRIVATE_RAGFLOW_NETWORKS)


def _address_is_forbidden(
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


def _safe_ip_addresses(
    raw_addresses: Sequence[str],
) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]:
    parsed: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for raw_address in raw_addresses:
        try:
            address = ipaddress.ip_address(raw_address)
        except ValueError:
            raise RagflowEndpointSecurityError(
                "dns_resolution_invalid",
                kind="resolution",
                retryable=True,
            ) from None
        if address not in parsed:
            parsed.append(address)
    if not parsed:
        raise RagflowEndpointSecurityError(
            "dns_resolution_invalid",
            kind="resolution",
            retryable=True,
        )
    if any(_address_is_forbidden(address) for address in parsed):
        raise RagflowEndpointSecurityError(
            "resolved_address_forbidden",
            kind="policy",
            retryable=False,
        )
    has_private = any(_address_is_private(address) for address in parsed)
    has_public = any(address.is_global for address in parsed)
    if has_private and has_public:
        raise RagflowEndpointSecurityError(
            "mixed_address_scopes_forbidden",
            kind="policy",
            retryable=False,
        )
    return tuple(parsed)


async def resolve_and_authorize_ragflow_endpoint(
    *,
    base_url: str,
    protected_environment: bool,
    tls_spki_pins: Iterable[bytes],
    resolver: AsyncHostResolver,
) -> ResolvedRagflowEndpoint:
    try:
        scheme, hostname, port, _path = ragflow_endpoint_identity(base_url)
    except ValueError:
        raise RagflowEndpointSecurityError(
            "endpoint_invalid",
            kind="policy",
            retryable=False,
        ) from None

    raw_pins = tuple(tls_spki_pins)
    try:
        pins = validate_ragflow_spki_digests(raw_pins) if raw_pins else frozenset()
    except ValueError:
        raise RagflowEndpointSecurityError(
            "tls_spki_pin_invalid",
            kind="policy",
            retryable=False,
        ) from None
    if protected_environment and scheme != "https":
        raise RagflowEndpointSecurityError(
            "protected_https_required",
            kind="policy",
            retryable=False,
        )
    if protected_environment and not pins:
        raise RagflowEndpointSecurityError(
            "protected_tls_spki_pin_required",
            kind="policy",
            retryable=False,
        )
    if pins and scheme != "https":
        raise RagflowEndpointSecurityError(
            "tls_spki_pin_requires_https",
            kind="policy",
            retryable=False,
        )

    try:
        raw_addresses = await resolver.resolve(hostname, port)
    except RagflowEndpointSecurityError:
        raise
    except Exception:
        raise RagflowEndpointSecurityError(
            "dns_resolution_failed",
            kind="resolution",
            retryable=True,
        ) from None
    addresses = _safe_ip_addresses(raw_addresses)
    return ResolvedRagflowEndpoint(
        base_url=base_url.strip().rstrip("/"),
        scheme=scheme,
        hostname=hostname,
        port=port,
        pinned_ip=str(addresses[0]),
        tls_spki_pins=pins,
    )


def spki_sha256_digest_from_der_certificate(certificate_der: bytes) -> bytes:
    try:
        certificate = x509.load_der_x509_certificate(certificate_der)
        spki = certificate.public_key().public_bytes(
            Encoding.DER,
            PublicFormat.SubjectPublicKeyInfo,
        )
    except Exception:
        raise RagflowEndpointSecurityError(
            "tls_peer_certificate_invalid",
            kind="policy",
            retryable=False,
        ) from None
    return hashlib.sha256(spki).digest()


def verify_same_connection_spki(
    stream: httpcore.AsyncNetworkStream,
    allowed_pins: frozenset[bytes],
) -> None:
    ssl_object = stream.get_extra_info("ssl_object")
    if ssl_object is None or not hasattr(ssl_object, "getpeercert"):
        raise RagflowEndpointSecurityError(
            "tls_peer_certificate_unavailable",
            kind="policy",
            retryable=False,
        )
    try:
        peer_certificate = cast(_PeerCertificateProvider, ssl_object).getpeercert(binary_form=True)
    except Exception:
        raise RagflowEndpointSecurityError(
            "tls_peer_certificate_unavailable",
            kind="policy",
            retryable=False,
        ) from None
    if not isinstance(peer_certificate, bytes | bytearray):
        raise RagflowEndpointSecurityError(
            "tls_peer_certificate_unavailable",
            kind="policy",
            retryable=False,
        )
    actual_pin = spki_sha256_digest_from_der_certificate(bytes(peer_certificate))
    if actual_pin not in allowed_pins:
        raise RagflowEndpointSecurityError(
            "tls_spki_pin_mismatch",
            kind="policy",
            retryable=False,
        )


class SpkiPinningNetworkStream(httpcore.AsyncNetworkStream):
    def __init__(
        self,
        stream: httpcore.AsyncNetworkStream,
        *,
        allowed_pins: frozenset[bytes],
        expected_hostname: str,
    ) -> None:
        self._stream = stream
        self._allowed_pins = allowed_pins
        self._expected_hostname = expected_hostname

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
        try:
            valid_hostname = (
                server_hostname is not None
                and normalize_ragflow_hostname(server_hostname) == self._expected_hostname
            )
        except ValueError:
            valid_hostname = False
        if not valid_hostname:
            await _best_effort_aclose(self._stream)
            raise httpcore.ConnectError("RAGFlow TLS peer validation failed")
        try:
            tls_stream = await self._stream.start_tls(
                ssl_context=ssl_context,
                server_hostname=server_hostname,
                timeout=timeout,
            )
        except BaseException:
            await _best_effort_aclose(self._stream)
            raise
        try:
            verify_same_connection_spki(tls_stream, self._allowed_pins)
        except RagflowEndpointSecurityError:
            await _best_effort_aclose(tls_stream)
            raise httpcore.ConnectError("RAGFlow TLS peer validation failed") from None
        return tls_stream

    def get_extra_info(self, info: str) -> object:
        return self._stream.get_extra_info(info)


async def _best_effort_aclose(stream: httpcore.AsyncNetworkStream) -> None:
    try:
        await stream.aclose()
    except Exception:
        return


class PinnedNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(
        self,
        *,
        endpoint: ResolvedRagflowEndpoint,
        backend: httpcore.AsyncNetworkBackend | None = None,
    ) -> None:
        self._hostname = endpoint.hostname
        self._port = endpoint.port
        self._pinned_ip = str(ipaddress.ip_address(endpoint.pinned_ip))
        self._tls_spki_pins = endpoint.tls_spki_pins
        self._backend = backend or httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        try:
            matches_endpoint = (
                normalize_ragflow_hostname(host) == self._hostname and port == self._port
            )
        except ValueError:
            matches_endpoint = False
        if not matches_endpoint:
            raise httpcore.ConnectError("RAGFlow pinned endpoint mismatch")
        stream = await self._backend.connect_tcp(
            host=self._pinned_ip,
            port=port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )
        if not self._tls_spki_pins:
            return stream
        return SpkiPinningNetworkStream(
            stream,
            allowed_pins=self._tls_spki_pins,
            expected_hostname=self._hostname,
        )

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise httpcore.ConnectError("RAGFlow unix sockets are forbidden")

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)


class PinnedAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    def __init__(
        self,
        network_backend: httpcore.AsyncNetworkBackend,
        *,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        super().__init__(trust_env=False, retries=0)
        verified_ssl_context = ssl_context or ssl.create_default_context()
        verified_ssl_context.check_hostname = True
        verified_ssl_context.verify_mode = ssl.CERT_REQUIRED
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=verified_ssl_context,
            http1=True,
            http2=False,
            retries=0,
            network_backend=network_backend,
        )


def build_pinned_ragflow_transport(
    endpoint: ResolvedRagflowEndpoint,
    *,
    network_backend: httpcore.AsyncNetworkBackend | None = None,
    ssl_context: ssl.SSLContext | None = None,
) -> httpx.AsyncBaseTransport:
    if endpoint.tls_spki_pins and endpoint.scheme != "https":
        raise RagflowEndpointSecurityError(
            "tls_spki_pin_requires_https",
            kind="policy",
            retryable=False,
        )
    pinned_backend = PinnedNetworkBackend(
        endpoint=endpoint,
        backend=network_backend,
    )
    return PinnedAsyncHTTPTransport(
        pinned_backend,
        ssl_context=ssl_context,
    )
