from __future__ import annotations


class MockLLMProvider:
    async def complete(self, prompt: str) -> str:
        return prompt
