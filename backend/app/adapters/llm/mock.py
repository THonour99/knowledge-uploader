from __future__ import annotations

import json
import re
from collections.abc import Mapping

from .base import LLMCompletion, LLMUsage

INPUT_START = "INPUT_JSON_START"
INPUT_END = "INPUT_JSON_END"


class MockLLMProvider:
    """Deterministic protocol fake used only by offline tests and local development."""

    def __init__(self, *, model: str = "mock-analysis-v1") -> None:
        self._model = model

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
        _ = (temperature, top_p, max_output_tokens, system_prompt, json_mode)
        payload = _input_payload(prompt)
        text = str(payload.get("document_text", ""))
        categories = payload.get("categories", [])
        category_id: str | None = None
        tags: list[str] = []
        normalized = _normalize(text).lower()
        if isinstance(categories, list):
            for candidate in categories:
                if not isinstance(candidate, Mapping):
                    continue
                values = [candidate.get("name"), candidate.get("code")]
                keywords = candidate.get("keywords")
                if isinstance(keywords, list):
                    values.extend(keywords)
                matched = [
                    str(value).strip()
                    for value in values
                    if isinstance(value, str) and value.strip().lower() in normalized
                ]
                if matched and category_id is None:
                    raw_id = candidate.get("id")
                    category_id = str(raw_id) if raw_id is not None else None
                tags.extend(matched)
        for word in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", text):
            tags.append(word.strip().lower())
            if len(_unique(tags)) >= 5:
                break
        content = json.dumps(
            {
                "summary": _normalize(text)[:300] or None,
                "category_id": category_id,
                "tags": _unique(tags)[:5],
                "sensitive_risk_level": "none",
            },
            ensure_ascii=False,
        )
        return LLMCompletion(
            content=content,
            model=model or self._model,
            usage=LLMUsage(
                prompt_tokens=max(1, len(prompt) // 4),
                completion_tokens=max(1, len(content) // 4),
            ),
            latency_ms=0,
        )


def _input_payload(prompt: str) -> dict[str, object]:
    _, marker, remainder = prompt.partition(INPUT_START)
    if not marker:
        return {}
    raw, marker, _ = remainder.partition(INPUT_END)
    if not marker:
        return {}
    try:
        value = json.loads(raw.strip())
    except (TypeError, ValueError):
        return {}
    return {str(key): item for key, item in value.items()} if isinstance(value, dict) else {}


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value[:40] for value in values if value))
