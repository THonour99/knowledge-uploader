from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from .base import LLMProviderError


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
        headers = {"Content-Type": "application/json"}
        if self._api_key is not None:
            headers["Authorization"] = f"Bearer {self._api_key}"

        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )
        except httpx.HTTPError as exc:
            msg = type(exc).__name__
            raise LLMProviderError(msg) from exc

        if response.status_code >= 400:
            msg = f"provider returned HTTP {response.status_code}"
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

    async def test_connection(self) -> LLMTestResult:
        started = time.perf_counter()
        try:
            await self.complete("Respond with ok.")
        except LLMProviderError as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            return LLMTestResult(status="failed", latency_ms=latency_ms, message=str(exc))
        latency_ms = int((time.perf_counter() - started) * 1000)
        return LLMTestResult(status="success", latency_ms=latency_ms, message="ok")
