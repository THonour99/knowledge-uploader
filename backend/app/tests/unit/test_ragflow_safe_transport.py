# ruff: noqa: ASYNC109 - httpcore test doubles implement the protocol exactly

from __future__ import annotations

import datetime as dt
import ssl
from collections.abc import Iterable, Sequence
from typing import Any

import httpcore
import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import NameOID

from app.adapters.ragflow.base import RagflowClientError
from app.adapters.ragflow.http import HttpRagflowClient
from app.adapters.ragflow.safe_transport import (
    PinnedNetworkBackend,
    RagflowEndpointSecurityError,
    ResolvedRagflowEndpoint,
    SpkiPinningNetworkStream,
    build_pinned_ragflow_transport,
    resolve_and_authorize_ragflow_endpoint,
    spki_sha256_digest_from_der_certificate,
)

pytestmark = pytest.mark.asyncio


class StaticResolver:
    def __init__(self, addresses: Sequence[str]) -> None:
        self.addresses = addresses
        self.calls: list[tuple[str, int]] = []

    async def resolve(self, hostname: str, port: int) -> Sequence[str]:
        self.calls.append((hostname, port))
        return self.addresses


class FakeSSLObject:
    def __init__(self, certificate_der: bytes) -> None:
        self._certificate_der = certificate_der

    def getpeercert(self, binary_form: bool = False) -> object:
        return self._certificate_der if binary_form else {}


class ExplodingSSLObject:
    def getpeercert(self, binary_form: bool = False) -> object:
        raise RuntimeError("certificate=https://secret.invalid key=secret")


class FakeStream(httpcore.AsyncNetworkStream):
    def __init__(
        self,
        *,
        ssl_object: object | None = None,
        tls_stream: FakeStream | None = None,
        handshake_error: Exception | None = None,
    ) -> None:
        self.ssl_object = ssl_object
        self.tls_stream = tls_stream
        self.handshake_error = handshake_error
        self.closed = False
        self.writes: list[bytes] = []
        self.start_tls_calls: list[tuple[ssl.SSLContext, str | None]] = []

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return b""

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self.writes.append(buffer)

    async def aclose(self) -> None:
        self.closed = True

    async def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.AsyncNetworkStream:
        self.start_tls_calls.append((ssl_context, server_hostname))
        if self.handshake_error is not None:
            raise self.handshake_error
        return self.tls_stream or self

    def get_extra_info(self, info: str) -> object:
        if info == "ssl_object":
            return self.ssl_object
        return None


class FakeBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, stream: FakeStream) -> None:
        self.stream = stream
        self.connect_calls: list[tuple[str, int]] = []

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        self.connect_calls.append((host, port))
        return self.stream

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise AssertionError("unix socket must not be used")

    async def sleep(self, seconds: float) -> None:
        return None


def _certificate_der() -> bytes:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ragflow.internal")])
    now = dt.datetime.now(dt.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    return certificate.public_bytes(Encoding.DER)


@pytest.mark.parametrize(
    ("base_url", "pins", "reason"),
    [
        ("http://ragflow.internal", (b"x" * 32,), "protected_https_required"),
        ("https://ragflow.internal", (), "protected_tls_spki_pin_required"),
    ],
)
async def test_protected_policy_fails_before_dns(
    base_url: str,
    pins: tuple[bytes, ...],
    reason: str,
) -> None:
    resolver = StaticResolver(["10.0.0.10"])

    with pytest.raises(RagflowEndpointSecurityError, match=reason):
        await resolve_and_authorize_ragflow_endpoint(
            base_url=base_url,
            protected_environment=True,
            tls_spki_pins=pins,
            resolver=resolver,
        )

    assert resolver.calls == []


@pytest.mark.parametrize(
    "addresses",
    [
        ["10.0.0.10", "169.254.169.254"],
        ["10.0.0.10", "8.8.8.8"],
        ["127.0.0.1"],
        ["not-an-ip"],
        [],
    ],
)
async def test_dns_rejects_any_forbidden_mixed_or_invalid_answer(
    addresses: list[str],
) -> None:
    with pytest.raises(RagflowEndpointSecurityError):
        await resolve_and_authorize_ragflow_endpoint(
            base_url="https://ragflow.internal",
            protected_environment=True,
            tls_spki_pins=(b"x" * 32,),
            resolver=StaticResolver(addresses),
        )


async def test_dns_all_safe_answers_are_checked_before_first_ip_is_pinned() -> None:
    resolver = StaticResolver(["10.0.0.20", "10.0.0.21"])

    endpoint = await resolve_and_authorize_ragflow_endpoint(
        base_url="https://ragflow.internal/api",
        protected_environment=True,
        tls_spki_pins=(b"x" * 32,),
        resolver=resolver,
    )

    assert endpoint.pinned_ip == "10.0.0.20"
    assert resolver.calls == [("ragflow.internal", 443)]


async def test_pinned_backend_connects_only_to_resolved_ip_for_exact_origin() -> None:
    raw_stream = FakeStream()
    backend = FakeBackend(raw_stream)
    endpoint = ResolvedRagflowEndpoint(
        base_url="https://ragflow.internal",
        scheme="https",
        hostname="ragflow.internal",
        port=443,
        pinned_ip="10.0.0.20",
        tls_spki_pins=frozenset({b"x" * 32}),
    )
    pinned = PinnedNetworkBackend(endpoint=endpoint, backend=backend)

    stream = await pinned.connect_tcp("ragflow.internal", 443)

    assert isinstance(stream, SpkiPinningNetworkStream)
    assert backend.connect_calls == [("10.0.0.20", 443)]
    with pytest.raises(httpcore.ConnectError, match="pinned endpoint mismatch"):
        await pinned.connect_tcp("attacker.internal", 443)


async def test_transport_builder_rejects_pins_on_manual_http_endpoint() -> None:
    endpoint = ResolvedRagflowEndpoint(
        base_url="http://ragflow.internal",
        scheme="http",
        hostname="ragflow.internal",
        port=80,
        pinned_ip="10.0.0.20",
        tls_spki_pins=frozenset({b"x" * 32}),
    )

    with pytest.raises(RagflowEndpointSecurityError, match="tls_spki_pin_requires_https"):
        build_pinned_ragflow_transport(endpoint)


async def test_same_tls_stream_spki_match_is_accepted_with_exact_sni() -> None:
    certificate_der = _certificate_der()
    expected_pin = spki_sha256_digest_from_der_certificate(certificate_der)
    tls_stream = FakeStream(ssl_object=FakeSSLObject(certificate_der))
    raw_stream = FakeStream(tls_stream=tls_stream)
    stream = SpkiPinningNetworkStream(
        raw_stream,
        allowed_pins=frozenset({expected_pin}),
        expected_hostname="ragflow.internal",
    )
    context = ssl.create_default_context()

    result = await stream.start_tls(context, server_hostname="ragflow.internal")

    assert result is tls_stream
    assert raw_stream.start_tls_calls == [(context, "ragflow.internal")]
    assert tls_stream.closed is False


async def test_spki_mismatch_closes_the_actual_tls_stream_without_detail() -> None:
    certificate_der = _certificate_der()
    tls_stream = FakeStream(ssl_object=FakeSSLObject(certificate_der))
    raw_stream = FakeStream(tls_stream=tls_stream)
    stream = SpkiPinningNetworkStream(
        raw_stream,
        allowed_pins=frozenset({b"x" * 32}),
        expected_hostname="ragflow.internal",
    )

    with pytest.raises(httpcore.ConnectError) as error:
        await stream.start_tls(
            ssl.create_default_context(),
            server_hostname="ragflow.internal",
        )

    assert str(error.value) == "RAGFlow TLS peer validation failed"
    assert tls_stream.closed is True
    assert raw_stream.writes == []
    assert tls_stream.writes == []
    assert certificate_der.hex() not in str(error.value)


async def test_certificate_accessor_error_is_sanitized_and_closes_tls_stream() -> None:
    tls_stream = FakeStream(ssl_object=ExplodingSSLObject())
    raw_stream = FakeStream(tls_stream=tls_stream)
    stream = SpkiPinningNetworkStream(
        raw_stream,
        allowed_pins=frozenset({b"x" * 32}),
        expected_hostname="ragflow.internal",
    )

    with pytest.raises(httpcore.ConnectError) as error:
        await stream.start_tls(
            ssl.create_default_context(),
            server_hostname="ragflow.internal",
        )

    assert str(error.value) == "RAGFlow TLS peer validation failed"
    assert "secret.invalid" not in str(error.value)
    assert tls_stream.closed is True
    assert tls_stream.writes == []


async def test_sni_mismatch_closes_raw_stream_before_tls_handshake() -> None:
    raw_stream = FakeStream()
    stream = SpkiPinningNetworkStream(
        raw_stream,
        allowed_pins=frozenset({b"x" * 32}),
        expected_hostname="ragflow.internal",
    )

    with pytest.raises(httpcore.ConnectError, match="TLS peer validation failed"):
        await stream.start_tls(
            ssl.create_default_context(),
            server_hostname="attacker.internal",
        )

    assert raw_stream.closed is True
    assert raw_stream.start_tls_calls == []
    assert raw_stream.writes == []


async def test_tls_handshake_error_closes_raw_stream() -> None:
    raw_stream = FakeStream(handshake_error=httpcore.ConnectError("handshake failed"))
    stream = SpkiPinningNetworkStream(
        raw_stream,
        allowed_pins=frozenset({b"x" * 32}),
        expected_hostname="ragflow.internal",
    )

    with pytest.raises(httpcore.ConnectError, match="handshake failed"):
        await stream.start_tls(
            ssl.create_default_context(),
            server_hostname="ragflow.internal",
        )

    assert raw_stream.closed is True


async def test_protected_http_client_rejects_http_missing_pin_and_custom_client() -> None:
    cases = [
        HttpRagflowClient(
            base_url="http://ragflow.internal",
            api_key="secret",
            protected_environment=True,
            tls_spki_pins=frozenset({b"x" * 32}),
            resolver=StaticResolver(["10.0.0.20"]),
        ),
        HttpRagflowClient(
            base_url="https://ragflow.internal",
            api_key="secret",
            protected_environment=True,
            resolver=StaticResolver(["10.0.0.20"]),
        ),
    ]
    for client in cases:
        with pytest.raises(
            RagflowClientError,
            match=r"^RAGFlow endpoint security check failed$",
        ):
            await client.check_connection()

    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"code": 0})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as injected:
        for protected_environment in (True, False):
            client = HttpRagflowClient(
                base_url="https://ragflow.internal",
                api_key="secret",
                protected_environment=protected_environment,
                tls_spki_pins=frozenset({b"x" * 32}),
                client=injected,
            )
            with pytest.raises(RagflowClientError, match="custom HTTP clients are forbidden"):
                await client.check_connection()

    assert calls == []


async def test_runtime_client_disables_environment_proxy_and_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructed: dict[str, Any] = {}
    requested: dict[str, Any] = {}

    class FakeAsyncClient:
        def __init__(self, **kwargs: Any) -> None:
            constructed.update(kwargs)

        async def __aenter__(self) -> FakeAsyncClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
            requested.update({"method": method, "url": url, **kwargs})
            return httpx.Response(200, json={"code": 0, "data": []})

    monkeypatch.setenv("HTTPS_PROXY", "http://attacker.invalid:8080")
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    client = HttpRagflowClient(
        base_url="https://ragflow.internal",
        api_key="secret",
        protected_environment=True,
        tls_spki_pins=frozenset({b"x" * 32}),
        resolver=StaticResolver(["10.0.0.20"]),
    )

    await client.check_connection()

    assert constructed["trust_env"] is False
    assert constructed["follow_redirects"] is False
    assert requested["follow_redirects"] is False
    transport = constructed["transport"]
    assert transport.__class__.__name__ == "PinnedAsyncHTTPTransport"
