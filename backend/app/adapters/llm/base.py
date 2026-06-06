from __future__ import annotations

from typing import Protocol


class BaseLLMProvider(Protocol):
    async def complete(
        self,
        prompt: str,
        *,
        model: str | None = None,
        temperature: float | None = None,
    ) -> str: ...


class LLMProviderError(Exception):
    pass
