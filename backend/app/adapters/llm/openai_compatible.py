from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass

import httpx

from .base import LLMCompletion, LLMFailureCategory, LLMProviderError, LLMUsage
from .safe_transport import (
    AsyncHostResolver,
    LLMEndpointSecurityError,
    SystemHostResolver,
    build_pinned_transport,
    resolve_and_authorize_llm_endpoint,
)

MODEL_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}\Z")
MAX_USAGE_TOKENS = 1_000_000_000
MAX_PROVIDER_RESPONSE_BYTES = 1_048_576
UNSAFE_MODEL_NAME_RE = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{8,}|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{8,255})"
)


class ResponseBodyTooLargeError(ValueError):
    pass


async def _read_bounded_response(response: httpx.Response) -> bytes:
    body = bytearray()
    async for chunk in response.aiter_bytes():
        if len(body) + len(chunk) > MAX_PROVIDER_RESPONSE_BYTES:
            raise ResponseBodyTooLargeError
        body.extend(chunk)
    return bytes(body)


def _http_error(status_code: int, *, latency_ms: int) -> LLMProviderError:
    if status_code in {401, 403}:
        return LLMProviderError(
            "authentication_failed",
            retryable=False,
            latency_ms=latency_ms,
            status_code=status_code,
        )
    category: LLMFailureCategory
    retryable: bool
    if status_code == 408:
        category, retryable = "timeout", True
    elif status_code == 429:
        category, retryable = "rate_limited", True
    elif status_code >= 500:
        category, retryable = "provider_unavailable", True
    else:
        category, retryable = "request_rejected", False
    return LLMProviderError(
        category,
        retryable=retryable,
        latency_ms=latency_ms,
        status_code=status_code,
    )


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def validate_model_name(value: object) -> str:
    if (
        not isinstance(value, str)
        or MODEL_NAME_RE.fullmatch(value) is None
        or UNSAFE_MODEL_NAME_RE.search(value) is not None
    ):
        raise ValueError("invalid model")
    return value


def _nonnegative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_USAGE_TOKENS:
        msg = "invalid usage value"
        raise ValueError(msg)
    return value


@dataclass(frozen=True)
class LLMTestResult:
    status: str
    latency_ms: int | None
    message: str | None


@dataclass(frozen=True)
class _ProviderFailure:
    category: LLMFailureCategory
    retryable: bool
    latency_ms: int | None = None
    status_code: int | None = None

    def to_error(self) -> LLMProviderError:
        return LLMProviderError(
            self.category,
            retryable=self.retryable,
            latency_ms=self.latency_ms,
            status_code=self.status_code,
        )


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: AsyncHostResolver | None = None,
        raw_allowed_base_urls: str = "",
        allow_external: bool = False,
        is_internal: bool = False,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key or None
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._transport = transport

        self._resolver = resolver or SystemHostResolver()
        self._raw_allowed_base_urls = raw_allowed_base_urls
        self._allow_external = allow_external
        self._is_internal = is_internal

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key is not None:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    async def complete(
        self,
        prompt: str,
        *,
        model: str | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        max_output_tokens: int | None = None,
        system_prompt: str | None = None,
        json_mode: bool = False,
    ) -> LLMCompletion:
        endpoint_failure: _ProviderFailure | None = None
        try:
            endpoint = await resolve_and_authorize_llm_endpoint(
                base_url=self._base_url,
                raw_allowed_base_urls=self._raw_allowed_base_urls,
                allow_external=self._allow_external,
                is_internal=self._is_internal,
                resolver=self._resolver,
            )
        except LLMEndpointSecurityError as exc:
            endpoint_failure = _ProviderFailure(
                category=("connection_error" if exc.kind == "resolution" else "request_rejected"),
                retryable=exc.retryable,
            )
        except ValueError:
            endpoint_failure = _ProviderFailure(
                category="request_rejected",
                retryable=False,
            )
        if endpoint_failure is not None:
            safe_error = endpoint_failure.to_error()
            del self, prompt, system_prompt
            raise safe_error
        transport = self._transport or build_pinned_transport(endpoint)

        requested_model = model or self._model
        messages: list[dict[str, str]] = []
        if system_prompt is not None:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        payload: dict[str, object] = {
            "model": requested_model,
            "messages": messages,
            "temperature": 0.2 if temperature is None else temperature,
        }
        if top_p is not None:
            payload["top_p"] = top_p
        if max_output_tokens is not None:
            payload["max_tokens"] = max_output_tokens
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        started = time.perf_counter()
        response: httpx.Response | None = None
        raw_response = b""
        request_failure: _ProviderFailure | None = None
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                transport=transport,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                async with client.stream(
                    "POST",
                    f"{self._base_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                ) as response:
                    raw_response = (
                        await _read_bounded_response(response)
                        if response.status_code < 400
                        else b""
                    )
        except ResponseBodyTooLargeError:
            request_failure = _ProviderFailure(
                "invalid_response",
                retryable=False,
                latency_ms=_elapsed_ms(started),
            )
        except httpx.TimeoutException:
            request_failure = _ProviderFailure(
                "timeout",
                retryable=True,
                latency_ms=_elapsed_ms(started),
            )
        except Exception:
            request_failure = _ProviderFailure(
                "connection_error",
                retryable=True,
                latency_ms=_elapsed_ms(started),
            )
        if request_failure is not None:
            safe_error = request_failure.to_error()
            payload.clear()
            messages.clear()
            raw_response = b""
            response = None
            del self, prompt, system_prompt, transport
            raise safe_error

        latency_ms = _elapsed_ms(started)
        if response is None:
            safe_error = _ProviderFailure(
                "connection_error",
                retryable=True,
                latency_ms=latency_ms,
            ).to_error()
            del self, prompt, system_prompt, transport
            raise safe_error
        if response.status_code >= 400:
            safe_error = _http_error(response.status_code, latency_ms=latency_ms)
            payload.clear()
            messages.clear()
            raw_response = b""
            response = None
            del self, prompt, system_prompt, transport
            raise safe_error

        invalid_response = False
        data: object = None
        content = ""
        validated_model = ""
        prompt_tokens = 0
        completion_tokens = 0
        try:
            data = json.loads(raw_response)
            if not isinstance(data, Mapping):
                raise ValueError("invalid root")
            choices = data.get("choices")
            if not isinstance(choices, list) or not choices:
                raise ValueError("invalid choices")
            first_choice = choices[0]
            if not isinstance(first_choice, Mapping):
                raise ValueError("invalid choice")
            message = first_choice.get("message")
            if not isinstance(message, Mapping):
                raise ValueError("invalid message")
            content_value = message.get("content")
            usage = data.get("usage")
            response_model = data.get("model")
            if not isinstance(content_value, str) or not content_value.strip():
                raise ValueError("invalid content")
            if not isinstance(usage, Mapping):
                raise ValueError("invalid usage")
            content = content_value
            validated_model = validate_model_name(response_model)
            prompt_tokens = _nonnegative_int(usage.get("prompt_tokens"))
            completion_tokens = _nonnegative_int(usage.get("completion_tokens"))
        except (IndexError, TypeError, ValueError):
            invalid_response = True
        if invalid_response:
            safe_error = _ProviderFailure(
                "invalid_response",
                retryable=False,
                latency_ms=latency_ms,
            ).to_error()
            data = None
            raw_response = b""
            payload.clear()
            messages.clear()
            response = None
            del self, prompt, system_prompt, transport
            raise safe_error
        return LLMCompletion(
            content=content.strip(),
            model=validated_model,
            usage=LLMUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            ),
            latency_ms=latency_ms,
        )

    async def test_connection(self) -> LLMTestResult:
        started = time.perf_counter()
        try:
            await self.complete("Respond with ok.")
        except LLMProviderError as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            return LLMTestResult(status="failed", latency_ms=latency_ms, message=str(exc))
        latency_ms = int((time.perf_counter() - started) * 1000)
        return LLMTestResult(status="success", latency_ms=latency_ms, message="ok")
