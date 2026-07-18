from __future__ import annotations

import json

import httpx
import pytest

from app.adapters.llm.base import LLMProviderError
from app.adapters.llm.openai_compatible import MAX_PROVIDER_RESPONSE_BYTES, OpenAICompatibleProvider
from app.adapters.llm.safe_transport import TLSSPKIPinningError

pytestmark = pytest.mark.asyncio


class StaticResolver:
    async def resolve(self, hostname: str, port: int) -> list[str]:
        assert hostname == "llm.invalid"
        assert port == 443
        return ["8.8.8.8"]


def _provider(transport: httpx.AsyncBaseTransport) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        base_url="https://llm.invalid/v1",
        api_key="sk-local-test-secret",
        model="requested-model",
        timeout_seconds=2,
        transport=transport,
        resolver=StaticResolver(),
        raw_allowed_base_urls="https://llm.invalid/v1",
        allow_external=True,
    )


def _assert_sanitized_exception(
    exception: LLMProviderError,
    *forbidden_values: str,
) -> None:
    assert exception.__cause__ is None
    assert exception.__context__ is None
    serialized = repr((exception.args, vars(exception)))
    for forbidden in forbidden_values:
        assert forbidden not in serialized


def _success_payload(*, model: str = "actual-model") -> dict[str, object]:
    return {
        "model": model,
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "summary": "摘要",
                            "category_id": None,
                            "tags": [],
                            "sensitive_risk_level": "none",
                        },
                        ensure_ascii=False,
                    )
                }
            }
        ],
        "usage": {"prompt_tokens": 21, "completion_tokens": 7},
    }


async def test_complete_returns_actual_model_usage_and_json_request() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer sk-local-test-secret"
        return httpx.Response(200, json=_success_payload())

    completion = await _provider(httpx.MockTransport(handler)).complete(
        "bounded input",
        system_prompt="system contract",
        temperature=0,
        top_p=0.1,
        max_output_tokens=512,
        json_mode=True,
    )

    assert completion.model == "actual-model"
    assert completion.usage.prompt_tokens == 21
    assert completion.usage.completion_tokens == 7
    assert captured["response_format"] == {"type": "json_object"}
    assert captured["max_tokens"] == 512
    assert captured["messages"] == [
        {"role": "system", "content": "system contract"},
        {"role": "user", "content": "bounded input"},
    ]


@pytest.mark.parametrize(
    ("status_code", "category", "retryable"),
    [
        (408, "timeout", True),
        (429, "rate_limited", True),
        (500, "provider_unavailable", True),
        (401, "authentication_failed", False),
        (403, "authentication_failed", False),
        (400, "request_rejected", False),
    ],
)
async def test_http_statuses_map_to_sanitized_retry_policy(
    status_code: int,
    category: str,
    retryable: bool,
) -> None:
    secret_body = "vendor body contains sk-never-persist-this"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text=secret_body)

    with pytest.raises(LLMProviderError) as raised:
        await _provider(httpx.MockTransport(handler)).complete("document secret")

    assert raised.value.category == category
    assert raised.value.retryable is retryable
    assert str(raised.value) == category
    assert secret_body not in str(raised.value)
    assert "document secret" not in str(raised.value)
    assert "sk-local-test-secret" not in str(raised.value)
    _assert_sanitized_exception(
        raised.value,
        secret_body,
        "document secret",
        "sk-local-test-secret",
    )


class _PinFailureTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        del request
        try:
            raise TLSSPKIPinningError("tls pin verification failed")
        except TLSSPKIPinningError as exc:
            raise httpx.ConnectError("tls pin verification failed") from exc


async def test_spki_pin_failure_is_permanent_and_sanitized() -> None:
    with pytest.raises(LLMProviderError) as raised:
        await _provider(_PinFailureTransport()).complete("document-secret")

    assert raised.value.category == "request_rejected"
    assert raised.value.retryable is False
    assert str(raised.value) == "request_rejected"
    _assert_sanitized_exception(
        raised.value,
        "tls pin verification failed",
        "llm.invalid",
        "sk-local-test-secret",
        "document-secret",
    )


async def test_configured_pin_cannot_be_bypassed_by_injected_transport() -> None:
    transport_called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal transport_called
        transport_called = True
        return httpx.Response(200, json=_success_payload())

    provider = OpenAICompatibleProvider(
        base_url="https://llm.invalid/v1",
        api_key="sk-local-test-secret",
        model="requested-model",
        timeout_seconds=2,
        transport=httpx.MockTransport(handler),
        resolver=StaticResolver(),
        raw_allowed_base_urls="https://llm.invalid/v1",
        allow_external=True,
        raw_tls_spki_pins=(
            '{"https://llm.invalid/v1":["sha256/AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE="]}'
        ),
        require_tls_spki_pin=True,
    )

    with pytest.raises(LLMProviderError) as raised:
        await provider.complete("document-secret")

    assert raised.value.category == "request_rejected"
    assert raised.value.retryable is False
    assert transport_called is False


async def test_timeout_is_retryable_without_transport_message_leak() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("transport leaked secret", request=request)

    with pytest.raises(LLMProviderError) as raised:
        await _provider(httpx.MockTransport(handler)).complete("input")

    assert raised.value.category == "timeout"
    assert raised.value.retryable is True
    assert str(raised.value) == "timeout"
    _assert_sanitized_exception(
        raised.value,
        "transport leaked secret",
        "sk-local-test-secret",
        "input",
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"model": "actual-model", "choices": [], "usage": {}},
        {
            "model": "actual-model",
            "choices": [{"message": {"content": "{}"}}],
        },
        {
            "model": "actual-model",
            "choices": [{"message": {"content": "{}"}}],
            "usage": {"prompt_tokens": -1, "completion_tokens": 1},
        },
        _success_payload(model="ghp_1234567890abcdefghijklmnop"),
        _success_payload(model="model\ncontrol"),
    ],
)
async def test_malformed_or_unsafe_response_is_permanent(payload: dict[str, object]) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    with pytest.raises(LLMProviderError) as raised:
        await _provider(httpx.MockTransport(handler)).complete("input")

    assert raised.value.category == "invalid_response"
    assert raised.value.retryable is False
    assert str(raised.value) == "invalid_response"
    _assert_sanitized_exception(raised.value, "input", repr(payload))


async def test_oversized_response_is_sanitized_and_permanent() -> None:
    oversized_body = b"x" * (MAX_PROVIDER_RESPONSE_BYTES + 1)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=oversized_body)

    provider = _provider(httpx.MockTransport(handler))
    with pytest.raises(LLMProviderError) as raised:
        await provider.complete("input")

    assert raised.value.category == "invalid_response"
    assert raised.value.retryable is False
    assert str(raised.value) == "invalid_response"
    _assert_sanitized_exception(raised.value, "input")


async def test_connection_test_uses_the_same_bounded_reader() -> None:
    oversized_body = b"secret-body-" + b"x" * MAX_PROVIDER_RESPONSE_BYTES

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=oversized_body)

    result = await _provider(httpx.MockTransport(handler)).test_connection()

    assert result.status == "failed"
    assert result.message == "invalid_response"
    assert "secret-body" not in (result.message or "")


async def test_resolver_failure_drops_original_exception_graph() -> None:
    class LeakingResolver:
        async def resolve(self, hostname: str, port: int) -> list[str]:
            del hostname, port
            raise RuntimeError("Authorization: Bearer sk-resolver-secret; document=confidential")

    provider = OpenAICompatibleProvider(
        base_url="https://llm.invalid/v1",
        api_key="sk-local-test-secret",
        model="requested-model",
        timeout_seconds=2,
        transport=httpx.MockTransport(lambda _request: httpx.Response(500)),
        resolver=LeakingResolver(),
        raw_allowed_base_urls="https://llm.invalid/v1",
        allow_external=True,
    )
    with pytest.raises(LLMProviderError) as raised:
        await provider.complete("document=confidential")

    assert raised.value.category == "connection_error"
    _assert_sanitized_exception(
        raised.value,
        "sk-resolver-secret",
        "sk-local-test-secret",
        "document=confidential",
    )
