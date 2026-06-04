from __future__ import annotations

from typing import Protocol


class BaseLLMProvider(Protocol):
    async def complete(self, prompt: str) -> str: ...
