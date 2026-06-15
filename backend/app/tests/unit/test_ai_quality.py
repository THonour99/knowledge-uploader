from __future__ import annotations

from app.modules.ai.quality import (
    normalize_quality_weights,
    quality_level,
    score_document_quality,
)


def test_quality_score_returns_score_level_and_component_detail() -> None:
    text = "\n".join(
        [
            "# 员工手册",
            "1. 入职流程",
            "新员工需要完成账号开通、权限申请和安全培训后才能进入项目环境。",
            "2. 审核要求",
            "所有知识库文档需要经过管理员审核后识别重复过期或低质量内容进入检索系统。",
            "| 字段 | 要求 |",
            "| --- | --- |",
            "| owner | 必填 |",
        ]
    )

    result = score_document_quality(text)

    assert 50 <= result.score <= 100
    assert result.level in {"一般", "良好", "优秀"}
    assert result.detail["level"] == result.level
    components = result.detail["components"]
    assert isinstance(components, dict)
    assert set(components) == {
        "content_length",
        "garbled_rate",
        "structure",
        "extraction_success",
        "ocr_confidence",
    }


def test_quality_level_mapping() -> None:
    assert quality_level(85) == "优秀"
    assert quality_level(70) == "良好"
    assert quality_level(50) == "一般"
    assert quality_level(49) == "较差"


def test_garbled_text_and_failed_extraction_lower_score() -> None:
    good = score_document_quality("# 标题\n这是一个结构完整且可读的文档正文。" * 20)
    bad = score_document_quality("\ufffd" * 50, extraction_success_rate=0.0, ocr_confidence=0.2)

    assert bad.score < good.score
    assert bad.level == "较差"


def test_quality_weights_are_normalized_from_config() -> None:
    weights = normalize_quality_weights(
        {
            "content_length": 3,
            "garbled_rate": 2,
            "structure": 1,
            "extraction_success": 1,
            "ocr_confidence": 1,
        }
    )

    assert round(sum(weights.values()), 6) == 1.0
    assert weights["content_length"] > weights["structure"]
