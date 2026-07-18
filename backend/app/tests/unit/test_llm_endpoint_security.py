# ruff: noqa: ASYNC109 - httpcore test doubles implement the protocol exactly

from __future__ import annotations

import asyncio
import base64
import ssl
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import httpcore
import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app.adapters.llm.safe_transport import (
    LLMEndpointSecurityError,
    ResolvedLLMEndpoint,
    TLSSPKIPinningStream,
    build_pinned_transport,
    resolve_and_authorize_llm_endpoint,
)
from app.core.llm_endpoint import (
    llm_base_url_is_allowed,
    normalize_llm_base_url,
    normalize_llm_hostname,
    normalized_llm_tls_spki_pins,
)


class StaticResolver:
    def __init__(self, addresses: Sequence[str]) -> None:
        self.addresses = addresses
        self.calls: list[tuple[str, int]] = []

    async def resolve(self, hostname: str, port: int) -> Sequence[str]:
        self.calls.append((hostname, port))
        return self.addresses


def test_llm_url_identity_canonicalizes_idna_ports_and_ipv6() -> None:
    assert (
        normalize_llm_base_url(" HTTPS://BÜCHER.Example.:443/v1/ ")
        == "https://xn--bcher-kva.example/v1"
    )
    assert normalize_llm_base_url("http://[FD00::1]:80/v1/") == "http://[fd00::1]/v1"
    assert normalize_llm_hostname("BÜCHER.Example.") == "xn--bcher-kva.example"


def test_llm_allowlist_is_exact_after_canonicalization() -> None:
    allowed = "https://LLM.Example.:443/v1/, https://llm.example/v2"
    assert llm_base_url_is_allowed("https://llm.example/v1", allowed)
    assert not llm_base_url_is_allowed("https://llm.example/v1/extra", allowed)
    assert not llm_base_url_is_allowed("https://llm.example.evil/v1", allowed)


def _pin_text(byte: int) -> str:
    return "sha256/" + base64.b64encode(bytes([byte]) * 32).decode("ascii")


def test_llm_spki_pin_mapping_is_exact_and_allows_rotation_per_endpoint() -> None:
    first_pin = _pin_text(1)
    second_pin = _pin_text(2)
    mapping = normalized_llm_tls_spki_pins(
        '{"HTTPS://LLM.Example.:443/v1/":["' + first_pin + '","' + second_pin + '"]}'
    )

    assert mapping == {"https://llm.example/v1": frozenset({bytes([1]) * 32, bytes([2]) * 32})}


@pytest.mark.parametrize(
    "raw_value",
    [
        "[]",
        '{"http://llm.example/v1":["' + _pin_text(1) + '"]}',
        '{"https://llm.example/v1":[]}',
        '{"https://llm.example/v1":["sha256/not-base64"]}',
        '{"https://llm.example/v1":["'
        + _pin_text(1)
        + '"],"HTTPS://LLM.EXAMPLE:443/v1/":["'
        + _pin_text(2)
        + '"]}',
        '{"https://llm.example/v1":["' + _pin_text(1) + '","' + _pin_text(1) + '"]}',
        '{"https://first.example/v1":["'
        + _pin_text(1)
        + '"],"https://second.example/v1":["'
        + _pin_text(1)
        + '"]}',
    ],
)
def test_llm_spki_pin_mapping_rejects_ambiguous_or_cross_hostname_bindings(
    raw_value: str,
) -> None:
    with pytest.raises(ValueError, match="LLM_TLS_SPKI_PINS"):
        normalized_llm_tls_spki_pins(raw_value)


def test_llm_spki_pin_mapping_allows_same_certificate_on_same_hostname_paths() -> None:
    pin = _pin_text(1)
    mapping = normalized_llm_tls_spki_pins(
        '{"https://llm.example/v1":["' + pin + '"],"https://llm.example/v2":["' + pin + '"]}'
    )

    assert set(mapping) == {"https://llm.example/v1", "https://llm.example/v2"}


@pytest.mark.parametrize(
    "value",
    [
        "https://llm.example/v1%2Fchat",
        "https://llm.example/v1%5cchat",
        "https://llm.example/v1/../admin",
        "https://user:secret@llm.example/v1",
        "https://llm.example/v1?key=secret",
        "https://llm.example/v1#fragment",
        "https://999.999.999.999/v1",
    ],
)
def test_llm_url_identity_rejects_ambiguous_values(value: str) -> None:
    with pytest.raises(ValueError, match="invalid LLM"):
        normalize_llm_base_url(value)


@pytest.mark.asyncio
async def test_exact_allowlist_is_checked_before_dns() -> None:
    resolver = StaticResolver(["8.8.8.8"])
    with pytest.raises(LLMEndpointSecurityError, match="base_url_not_allowed") as raised:
        await resolve_and_authorize_llm_endpoint(
            base_url="https://llm.example/v1",
            raw_allowed_base_urls="https://llm.example/v2",
            allow_external=True,
            is_internal=False,
            resolver=resolver,
        )

    assert raised.value.retryable is False
    assert resolver.calls == []


@pytest.mark.asyncio
async def test_private_http_endpoint_requires_internal_provider() -> None:
    resolver = StaticResolver(["10.2.3.4"])
    endpoint = await resolve_and_authorize_llm_endpoint(
        base_url="http://vllm.internal:8000/v1",
        raw_allowed_base_urls="http://vllm.internal:8000/v1",
        allow_external=False,
        is_internal=True,
        resolver=resolver,
    )
    assert endpoint.pinned_ip == "10.2.3.4"
    assert endpoint.is_external is False

    with pytest.raises(
        LLMEndpointSecurityError,
        match="private_address_requires_internal_provider",
    ):
        await resolve_and_authorize_llm_endpoint(
            base_url="http://vllm.internal:8000/v1",
            raw_allowed_base_urls="http://vllm.internal:8000/v1",
            allow_external=False,
            is_internal=False,
            resolver=resolver,
        )


@pytest.mark.asyncio
async def test_public_endpoint_requires_https_and_both_external_gates() -> None:
    resolver = StaticResolver(["8.8.8.8"])
    endpoint = await resolve_and_authorize_llm_endpoint(
        base_url="https://llm.example/v1",
        raw_allowed_base_urls="https://llm.example/v1",
        allow_external=True,
        is_internal=False,
        resolver=resolver,
    )
    assert endpoint.is_external is True

    for base_url, allow_external in (
        ("http://llm.example/v1", True),
        ("https://llm.example/v1", False),
    ):
        with pytest.raises(LLMEndpointSecurityError, match="external_provider_not_permitted"):
            await resolve_and_authorize_llm_endpoint(
                base_url=base_url,
                raw_allowed_base_urls=base_url,
                allow_external=allow_external,
                is_internal=False,
                resolver=resolver,
            )


@pytest.mark.asyncio
async def test_mixed_private_and_public_dns_answers_are_rejected() -> None:
    with pytest.raises(LLMEndpointSecurityError, match="mixed_address_scopes_forbidden"):
        await resolve_and_authorize_llm_endpoint(
            base_url="https://llm.example/v1",
            raw_allowed_base_urls="https://llm.example/v1",
            allow_external=True,
            is_internal=True,
            resolver=StaticResolver(["10.0.0.8", "8.8.8.8"]),
        )


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "::1",
        "169.254.1.1",
        "fe80::1",
        "224.0.0.1",
        "ff02::1",
        "0.0.0.0",
        "::",
        "240.0.0.1",
        "100.64.0.1",
        "192.0.2.1",
        "198.51.100.1",
        "203.0.113.1",
        "2001:db8::1",
        "169.254.169.254",
        "169.254.170.2",
        "168.63.129.16",
        "100.100.100.200",
        "fd00:ec2::254",
    ],
)
@pytest.mark.asyncio
async def test_forbidden_address_classes_never_reach_transport(address: str) -> None:
    with pytest.raises(LLMEndpointSecurityError, match="resolved_address_forbidden"):
        await resolve_and_authorize_llm_endpoint(
            base_url="https://llm.example/v1",
            raw_allowed_base_urls="https://llm.example/v1",
            allow_external=True,
            is_internal=True,
            resolver=StaticResolver([address]),
        )


class MemoryNetworkStream(httpcore.AsyncNetworkStream):
    def __init__(
        self,
        certificate_der: bytes | None = None,
        tls_failure: BaseException | None = None,
    ) -> None:
        self._response = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok"
        self.server_hostname: str | None = None
        self.certificate_der = certificate_der
        self.tls_failure = tls_failure
        self.writes: list[bytes] = []
        self.closed = False

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        response, self._response = self._response[:max_bytes], self._response[max_bytes:]
        return response

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        del timeout
        self.writes.append(buffer)

    async def aclose(self) -> None:
        self.closed = True

    async def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.AsyncNetworkStream:
        del ssl_context, timeout
        self.server_hostname = server_hostname
        if self.tls_failure is not None:
            raise self.tls_failure
        return self

    def get_extra_info(self, info: str) -> Any:
        if info == "ssl_object" and self.certificate_der is not None:
            return CertificateSSLObject(self.certificate_der)
        return None


class CertificateSSLObject:
    def __init__(self, certificate_der: bytes) -> None:
        self._certificate_der = certificate_der

    def getpeercert(self, *, binary_form: bool = False) -> bytes | dict[str, object]:
        return self._certificate_der if binary_form else {}

    def selected_alpn_protocol(self) -> None:
        return None


class RecordingNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(
        self,
        certificate_der: bytes | None = None,
        tls_failure: BaseException | None = None,
    ) -> None:
        self.stream = MemoryNetworkStream(certificate_der, tls_failure)
        self.connections: list[tuple[str, int]] = []

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        del timeout, local_address, socket_options
        self.connections.append((host, port))
        return self.stream

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        del path, timeout, socket_options
        raise AssertionError("unix socket must not be used")

    async def sleep(self, seconds: float) -> None:
        del seconds


@pytest.mark.asyncio
async def test_pinned_transport_connects_verified_ip_and_preserves_tls_origin() -> None:
    backend = RecordingNetworkBackend()
    endpoint = ResolvedLLMEndpoint(
        base_url="https://llm.example/v1",
        scheme="https",
        hostname="llm.example",
        port=443,
        pinned_ip="8.8.8.8",
        is_external=True,
    )
    transport = build_pinned_transport(endpoint, network_backend=backend)

    async with httpx.AsyncClient(transport=transport, trust_env=False) as client:
        response = await client.get("https://llm.example/v1")

    assert response.text == "ok"
    assert backend.connections == [("8.8.8.8", 443)]
    assert backend.stream.server_hostname == "llm.example"


def _certificate_der_and_pin() -> tuple[bytes, bytes]:
    private_key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "llm.example")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(minutes=5))
        .sign(private_key, hashes.SHA256())
    )
    public_key_der = private_key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    digest = hashes.Hash(hashes.SHA256())
    digest.update(public_key_der)
    return certificate.public_bytes(serialization.Encoding.DER), digest.finalize()


@pytest.mark.asyncio
async def test_spki_pin_is_checked_on_same_tls_stream_before_http_body_write() -> None:
    certificate_der, actual_pin = _certificate_der_and_pin()
    backend = RecordingNetworkBackend(certificate_der)
    endpoint = ResolvedLLMEndpoint(
        base_url="https://llm.example/v1",
        scheme="https",
        hostname="llm.example",
        port=443,
        pinned_ip="8.8.8.8",
        is_external=True,
        tls_spki_sha256_pins=frozenset({bytes(~byte & 0xFF for byte in actual_pin)}),
    )
    transport = build_pinned_transport(endpoint, network_backend=backend)

    with pytest.raises(httpx.ConnectError) as raised:
        async with httpx.AsyncClient(transport=transport, trust_env=False) as client:
            await client.post(
                "https://llm.example/v1/chat/completions",
                content=b"document-secret-must-not-leave",
            )

    assert str(raised.value) == "tls pin verification failed"
    assert backend.stream.writes == []
    assert backend.stream.closed is True
    assert "llm.example" not in str(raised.value)
    assert "document-secret" not in str(raised.value)
    assert certificate_der.hex() not in str(raised.value)


@pytest.mark.asyncio
async def test_tls_handshake_failure_closes_the_pinned_raw_stream() -> None:
    backend = RecordingNetworkBackend(tls_failure=httpcore.ConnectError("handshake failed"))
    endpoint = ResolvedLLMEndpoint(
        base_url="https://llm.example/v1",
        scheme="https",
        hostname="llm.example",
        port=443,
        pinned_ip="8.8.8.8",
        is_external=True,
        tls_spki_sha256_pins=frozenset({bytes([1]) * 32}),
    )
    transport = build_pinned_transport(endpoint, network_backend=backend)

    with pytest.raises(httpx.ConnectError, match="handshake failed"):
        async with httpx.AsyncClient(transport=transport, trust_env=False) as client:
            await client.get("https://llm.example/v1")

    assert backend.stream.closed is True
    assert backend.stream.writes == []


@pytest.mark.asyncio
async def test_tls_handshake_cancellation_closes_the_pinned_raw_stream() -> None:
    raw_stream = MemoryNetworkStream(tls_failure=asyncio.CancelledError())
    stream = TLSSPKIPinningStream(
        raw_stream,
        expected_hostname="llm.example",
        allowed_pins=frozenset({bytes([1]) * 32}),
    )

    with pytest.raises(asyncio.CancelledError):
        await stream.start_tls(
            ssl.create_default_context(),
            server_hostname="llm.example",
        )

    assert raw_stream.closed is True
    assert raw_stream.writes == []


@pytest.mark.asyncio
async def test_matching_spki_pin_allows_http_request_after_tls_verification() -> None:
    certificate_der, actual_pin = _certificate_der_and_pin()
    backend = RecordingNetworkBackend(certificate_der)
    endpoint = ResolvedLLMEndpoint(
        base_url="https://llm.example/v1",
        scheme="https",
        hostname="llm.example",
        port=443,
        pinned_ip="8.8.8.8",
        is_external=True,
        tls_spki_sha256_pins=frozenset({actual_pin}),
    )
    transport = build_pinned_transport(endpoint, network_backend=backend)

    async with httpx.AsyncClient(transport=transport, trust_env=False) as client:
        response = await client.get("https://llm.example/v1")

    assert response.text == "ok"
    assert backend.stream.writes
    assert backend.stream.writes[0].startswith(b"GET /v1 HTTP/1.1")


@pytest.mark.parametrize(
    "raw_tls_spki_pins",
    [
        "{}",
        '{"https://llm.example/v2":["' + _pin_text(1) + '"]}',
    ],
)
@pytest.mark.asyncio
async def test_required_spki_pin_is_exactly_bound_and_rejected_before_dns_when_missing(
    raw_tls_spki_pins: str,
) -> None:
    resolver = StaticResolver(["8.8.8.8"])

    with pytest.raises(LLMEndpointSecurityError, match="tls_spki_pin_required"):
        await resolve_and_authorize_llm_endpoint(
            base_url="https://llm.example/v1",
            raw_allowed_base_urls="https://llm.example/v1",
            allow_external=True,
            is_internal=False,
            resolver=resolver,
            raw_tls_spki_pins=raw_tls_spki_pins,
            require_tls_spki_pin=True,
        )

    assert resolver.calls == []


@pytest.mark.asyncio
async def test_dns_rebinding_is_rechecked_for_each_call() -> None:
    resolver = StaticResolver(["8.8.8.8"])
    first = await resolve_and_authorize_llm_endpoint(
        base_url="https://llm.example/v1",
        raw_allowed_base_urls="https://llm.example/v1",
        allow_external=True,
        is_internal=False,
        resolver=resolver,
    )
    assert first.pinned_ip == "8.8.8.8"

    resolver.addresses = ["127.0.0.1"]
    with pytest.raises(LLMEndpointSecurityError, match="resolved_address_forbidden"):
        await resolve_and_authorize_llm_endpoint(
            base_url="https://llm.example/v1",
            raw_allowed_base_urls="https://llm.example/v1",
            allow_external=True,
            is_internal=False,
            resolver=resolver,
        )
