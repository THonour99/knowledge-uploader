from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

LLMFailureCategory = Literal[
    "timeout",
    "connection_error",
    "rate_limited",
    "provider_unavailable",
    "authentication_failed",
    "request_rejected",
    "invalid_response",
]


@dataclass(frozen=True)
class LLMUsage:
    prompt_tokens: int
    completion_tokens: int


@dataclass(frozen=True)
class LLMCompletion:
    content: str
    model: str
    usage: LLMUsage
    latency_ms: int


class BaseLLMProvider(Protocol):
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
    ) -> LLMCompletion: ...


class LLMProviderError(Exception):
    """Sanitized provider failure safe for retry decisions and persistence.

    Provider response bodies, request payloads, API keys, and document text must never be
    attached to this exception. Callers may persist ``category`` but not ``str(exc)``.
    """

    def __init__(
        self,
        category: LLMFailureCategory,
        *,
        retryable: bool,
        latency_ms: int | None = None,
        status_code: int | None = None,
    ) -> None:
        self.category = category
        self.retryable = retryable
        self.latency_ms = latency_ms
        self.status_code = status_code
        super().__init__(category)
