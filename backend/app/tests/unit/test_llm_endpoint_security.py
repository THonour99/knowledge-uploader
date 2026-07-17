# ruff: noqa: ASYNC109 - httpcore test doubles implement the protocol exactly

from __future__ import annotations

import ssl
from collections.abc import Iterable, Sequence
from typing import Any

import httpcore
import httpx
import pytest

from app.adapters.llm.safe_transport import (
    LLMEndpointSecurityError,
    ResolvedLLMEndpoint,
    build_pinned_transport,
    resolve_and_authorize_llm_endpoint,
)
from app.core.llm_endpoint import (
    llm_base_url_is_allowed,
    normalize_llm_base_url,
    normalize_llm_hostname,
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
    def __init__(self) -> None:
        self._response = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok"
        self.server_hostname: str | None = None

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        response, self._response = self._response[:max_bytes], self._response[max_bytes:]
        return response

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        del buffer, timeout

    async def aclose(self) -> None:
        return None

    async def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.AsyncNetworkStream:
        del ssl_context, timeout
        self.server_hostname = server_hostname
        return self

    def get_extra_info(self, info: str) -> Any:
        del info
        return None


class RecordingNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(self) -> None:
        self.stream = MemoryNetworkStream()
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
