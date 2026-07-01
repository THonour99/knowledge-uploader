from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal

import httpx

from .base import LLMProviderError

LLMTestKind = Literal["chat", "embedding", "vision"]


def _http_error_message(status_code: int) -> str:
    if status_code in {401, 403}:
        return (
            f"provider returned HTTP {status_code}: API key or model permission was rejected; "
            "check API key, model name, and Base URL"
        )
    if status_code == 404:
        return "provider returned HTTP 404: check Base URL and model endpoint path"
    return f"provider returned HTTP {status_code}"


@dataclass(frozen=True)
class LLMTestResult:
    status: str
    latency_ms: int | None
    message: str | None


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model: str,
        timeout_seconds: float,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key or None
        self._model = model
        self._timeout_seconds = timeout_seconds

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
    ) -> str:
        payload = {
            "model": model or self._model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2 if temperature is None else temperature,
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                )
        except httpx.HTTPError as exc:
            msg = type(exc).__name__
            raise LLMProviderError(msg) from exc

        if response.status_code >= 400:
            msg = _http_error_message(response.status_code)
            raise LLMProviderError(msg)

        try:
            data = response.json()
            choices = data["choices"]
            message = choices[0]["message"]
            content = message["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            msg = "provider returned invalid chat completion payload"
            raise LLMProviderError(msg) from exc
        if not isinstance(content, str) or not content.strip():
            msg = "provider returned empty completion"
            raise LLMProviderError(msg)
        return content

    async def embed(self, *, model: str | None = None) -> None:
        payload = {
            "model": model or self._model,
            "input": "connection test",
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(
                    f"{self._base_url}/embeddings",
                    headers=self._headers(),
                    json=payload,
                )
        except httpx.HTTPError as exc:
            msg = type(exc).__name__
            raise LLMProviderError(msg) from exc

        if response.status_code >= 400:
            msg = _http_error_message(response.status_code)
            raise LLMProviderError(msg)

        try:
            data = response.json()
            embedding = data["data"][0]["embedding"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            msg = "provider returned invalid embedding payload"
            raise LLMProviderError(msg) from exc
        if not isinstance(embedding, list) or not embedding:
            msg = "provider returned invalid embedding payload"
            raise LLMProviderError(msg)

    async def test_connection(self, *, model_kind: LLMTestKind = "chat") -> LLMTestResult:
        started = time.perf_counter()
        try:
            if model_kind == "embedding":
                await self.embed()
            else:
                await self.complete("Respond with ok.")
        except LLMProviderError as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            return LLMTestResult(status="failed", latency_ms=latency_ms, message=str(exc))
        latency_ms = int((time.perf_counter() - started) * 1000)
        return LLMTestResult(status="success", latency_ms=latency_ms, message="ok")
