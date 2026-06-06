from __future__ import annotations


class MockLLMProvider:
    async def complete(
        self,
        prompt: str,
        *,
        model: str | None = None,
        temperature: float | None = None,
    ) -> str:
        _ = (model, temperature)
        return prompt
