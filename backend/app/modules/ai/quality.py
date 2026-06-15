from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_QUALITY_WEIGHTS: dict[str, float] = {
    "content_length": 0.30,
    "garbled_rate": 0.25,
    "structure": 0.20,
    "extraction_success": 0.15,
    "ocr_confidence": 0.10,
}
QUALITY_LEVELS = (
    (85, "优秀"),
    (70, "良好"),
    (50, "一般"),
    (0, "较差"),
)


@dataclass(frozen=True)
class QualityScoreResult:
    score: int
    level: str
    detail: dict[str, object]


def score_document_quality(
    text: str,
    *,
    weights: Mapping[str, float] | None = None,
    extraction_success_rate: float | None = None,
    ocr_confidence: float | None = None,
) -> QualityScoreResult:
    normalized_weights = normalize_quality_weights(weights)
    component_scores = {
        "content_length": _content_length_score(text),
        "garbled_rate": _garbled_text_score(text),
        "structure": _structure_score(text),
        "extraction_success": _rate_score(
            extraction_success_rate if extraction_success_rate is not None else bool(text.strip())
        ),
        "ocr_confidence": _rate_score(1.0 if ocr_confidence is None else ocr_confidence),
    }
    score = round(
        sum(component_scores[key] * normalized_weights[key] for key in DEFAULT_QUALITY_WEIGHTS)
    )
    score = max(0, min(100, score))
    level = quality_level(score)
    detail: dict[str, object] = {
        "level": level,
        "components": {
            key: {
                "score": round(component_scores[key]),
                "weight": round(normalized_weights[key], 4),
            }
            for key in DEFAULT_QUALITY_WEIGHTS
        },
        "metrics": {
            "content_length": len(text.strip()),
            "garbled_rate": round(_garbled_rate(text), 4),
            "ocr_confidence": None
            if ocr_confidence is None
            else round(_normalize_rate(ocr_confidence), 4),
        },
    }
    return QualityScoreResult(score=score, level=level, detail=detail)


def normalize_quality_weights(raw_weights: Mapping[str, object] | None) -> dict[str, float]:
    if raw_weights is None:
        return DEFAULT_QUALITY_WEIGHTS.copy()
    weights: dict[str, float] = {}
    for key, default_value in DEFAULT_QUALITY_WEIGHTS.items():
        candidate = raw_weights.get(key)
        number = _number_from_object(candidate)
        weights[key] = number if number is not None and number >= 0 else default_value
    total = sum(weights.values())
    if total <= 0:
        return DEFAULT_QUALITY_WEIGHTS.copy()
    return {key: value / total for key, value in weights.items()}


def quality_level(score: int) -> str:
    for minimum, level in QUALITY_LEVELS:
        if score >= minimum:
            return level
    return "较差"


def _content_length_score(text: str) -> float:
    length = len(text.strip())
    if length <= 0:
        return 0.0
    if length >= 2000:
        return 100.0
    if length >= 500:
        return 70.0 + ((length - 500) / 1500 * 30.0)
    return min(70.0, length / 500 * 70.0)


def _garbled_text_score(text: str) -> float:
    return max(0.0, 100.0 - (_garbled_rate(text) * 500.0))


def _garbled_rate(text: str) -> float:
    if not text:
        return 1.0
    suspicious = 0
    for char in text:
        if char == "\ufffd":
            suspicious += 1
        elif ord(char) < 32 and char not in "\n\r\t":
            suspicious += 1
        elif char in {"□", "�"}:
            suspicious += 1
    return suspicious / len(text)


def _structure_score(text: str) -> float:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return 0.0
    heading_count = sum(1 for line in lines if _looks_like_heading(line))
    bullet_count = sum(1 for line in lines if re.match(r"^([-*+]|\d+[.)、])\s+", line))
    paragraph_count = sum(1 for line in lines if len(line) >= 40)
    table_count = sum(1 for line in lines if line.startswith("|") and line.endswith("|"))
    score = 20.0
    score += min(heading_count, 4) * 15.0
    score += min(bullet_count, 6) * 4.0
    score += min(paragraph_count, 6) * 4.0
    score += min(table_count, 4) * 6.0
    if len(lines) >= 5:
        score += 10.0
    return min(100.0, score)


def _looks_like_heading(line: str) -> bool:
    return bool(
        re.match(
            r"^(#{1,6}\s+|\d+(\.\d+)*[.)、]\s*|[一二三四五六七八九十]+[、.]\s*|第.+[章节])",
            line,
        )
    )


def _rate_score(value: float | bool) -> float:
    return _normalize_rate(value) * 100.0


def _normalize_rate(value: float | bool) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if value > 1.0:
        return max(0.0, min(100.0, value)) / 100.0
    return max(0.0, min(1.0, value))


def _number_from_object(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None
