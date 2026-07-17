from __future__ import annotations

import json
import uuid
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.ai import exceptions
from app.modules.ai.llm_analysis import (
    LLM_ANALYSIS_SYSTEM_PROMPT,
    LLM_REPAIR_SUFFIX,
    MAX_LLM_CATEGORIES,
    MAX_POSTGRES_BIGINT,
    MAX_PROVIDER_PRICE_MICROUNITS,
    AnalysisFeatureSelection,
    LLMOutputValidationError,
    build_analysis_prompt,
    build_input_provenance,
    checked_persisted_sum,
    estimate_cost_microunits,
    parse_analysis_output,
)
from app.modules.ai.models import AiProvider
from app.modules.ai.repository import AiCategoryRecord, AiRepository  # noqa: TID251
from app.modules.ai.service import (  # noqa: TID251
    AiAnalysisService,
    AiConfigService,
    LLMAnalysisConfigurationError,
    mask_secret,
    resolve_analysis_engine_type,
)
from app.modules.document.schemas import FileAnalysisDetail

FEATURES = AnalysisFeatureSelection(
    summary=True,
    category=True,
    tags=True,
    sensitive=True,
)


def _category() -> AiCategoryRecord:
    return AiCategoryRecord(
        id=uuid.uuid4(),
        name="员工制度",
        code="handbook",
        keywords=["handbook"],
        allow_ai_recommend=True,
        ai_analysis_enabled=True,
        sensitive_detection_enabled=True,
    )


def _payload(*, category_id: str | None = None, summary: str = "合规摘要") -> str:
    return json.dumps(
        {
            "summary": summary,
            "category_id": category_id,
            "tags": ["制度", "员工"],
            "sensitive_risk_level": "low",
        },
        ensure_ascii=False,
    )


@pytest.mark.parametrize(
    ("secret", "expected"),
    [
        ("a", "****"),
        ("abc", "****"),
        ("abcd", "****"),
        ("abcde", "****"),
        ("sk-a", "sk-****"),
        ("sk-abcde", "sk-****"),
        ("abcdefgh", "****efgh"),
        ("sk-abcdefgh", "sk-****efgh"),
    ],
)
def test_provider_secret_mask_never_exposes_short_credentials(
    secret: str,
    expected: str,
) -> None:
    masked = mask_secret(secret)

    assert masked == expected
    assert secret not in masked
    assert masked.count("sk-") <= 1


def test_parse_analysis_output_accepts_only_allowed_category() -> None:
    category = _category()

    result = parse_analysis_output(
        _payload(category_id=str(category.id)),
        allowed_category_ids={category.id},
        features=FEATURES,
    )

    assert result.category_id == category.id
    assert result.summary == "合规摘要"
    assert result.tags == ["制度", "员工"]
    assert result.sensitive_risk_level == "low"


@pytest.mark.parametrize(
    "raw_output",
    [
        '{"summary":"a","summary":"b","category_id":null,"tags":[],"sensitive_risk_level":"none"}',
        '{"summary":NaN,"category_id":null,"tags":[],"sensitive_risk_level":"none"}',
        '{"summary":Infinity,"category_id":null,"tags":[],"sensitive_risk_level":"none"}',
        '{"summary":"ok","category_id":null,"tags":[],"sensitive_risk_level":"none","extra":1}',
        '{"summary":"ok","category_id":null,"tags":[]}',
        "[]",
    ],
)
def test_parse_analysis_output_rejects_non_strict_json(raw_output: str) -> None:
    with pytest.raises(LLMOutputValidationError, match="invalid_output") as raised:
        parse_analysis_output(
            raw_output,
            allowed_category_ids=set(),
            features=FEATURES,
        )
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert raw_output not in repr((raised.value.args, vars(raised.value)))


def test_parse_analysis_output_rejects_oversized_and_unknown_category() -> None:
    with pytest.raises(LLMOutputValidationError, match="invalid_output"):
        parse_analysis_output(
            "x" * 8_193,
            allowed_category_ids=set(),
            features=FEATURES,
        )
    with pytest.raises(LLMOutputValidationError, match="invalid_output"):
        parse_analysis_output(
            _payload(category_id=str(uuid.uuid4())),
            allowed_category_ids=set(),
            features=FEATURES,
        )


@pytest.mark.parametrize(
    "secret",
    [
        "Bearer abcdefghijklmnop",
        "sk-abcdefghijklmnop",
        "AKIA1234567890ABCDEF",
        "ghp_1234567890abcdefghijklmnop",
        "-----BEGIN RSA PRIVATE KEY-----",
        "password=super-secret-value",
        "client_secret: secret-secret",
        "access_token=abcdefghijk",
        "alice@example.com",
    ],
)
def test_parse_analysis_output_rejects_secret_like_summary(secret: str) -> None:
    with pytest.raises(LLMOutputValidationError) as raised:
        parse_analysis_output(
            _payload(summary=secret),
            allowed_category_ids=set(),
            features=FEATURES,
        )

    assert str(raised.value) == "invalid_output"
    assert secret not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_prompt_budget_accounts_for_system_and_repair_overhead() -> None:
    category = _category()
    max_input_tokens = 220

    built = build_analysis_prompt(
        template_text="按契约分析输入。",
        text="正文" * 10_000,
        categories=[category],
        max_input_tokens=max_input_tokens,
    )
    provenance = build_input_provenance(
        user_prompt=built.text + LLM_REPAIR_SUFFIX,
        category_count=built.category_count,
        input_truncated=built.input_truncated,
    )

    assert len(LLM_ANALYSIS_SYSTEM_PROMPT) + len(built.text) + len(LLM_REPAIR_SUFFIX) <= (
        max_input_tokens * 3
    )
    assert built.input_truncated is True
    assert provenance.input_char_count <= max_input_tokens * 3
    assert len(provenance.input_sha256) == 64
    assert provenance.category_count == 1


def test_prompt_budget_fails_closed_when_contract_cannot_fit() -> None:
    with pytest.raises(ValueError, match="too small"):
        build_analysis_prompt(
            template_text="按契约分析输入。",
            text="正文",
            categories=[],
            max_input_tokens=1,
        )


def test_cost_rounds_up_and_rejects_unsafe_bounds() -> None:
    assert (
        estimate_cost_microunits(
            prompt_tokens=1,
            completion_tokens=0,
            input_price_microunits_per_million_tokens=1,
            output_price_microunits_per_million_tokens=0,
        )
        == 1
    )
    with pytest.raises(ValueError, match="maximum"):
        estimate_cost_microunits(
            prompt_tokens=1,
            completion_tokens=0,
            input_price_microunits_per_million_tokens=MAX_PROVIDER_PRICE_MICROUNITS + 1,
            output_price_microunits_per_million_tokens=0,
        )
    with pytest.raises(OverflowError, match="storage limit"):
        checked_persisted_sum(MAX_POSTGRES_BIGINT, 1, maximum=MAX_POSTGRES_BIGINT)


def test_file_analysis_cost_serializes_losslessly() -> None:
    value = 9_007_199_254_740_993_123_456
    detail = FileAnalysisDetail(
        status="succeeded",
        summary=None,
        sensitive_risk_level="none",
        extracted_text_preview=None,
        error_message=None,
        finished_at=None,
        estimated_cost_microunits=value,
    )

    assert detail.model_dump(mode="json")["estimated_cost_microunits"] == str(value)


@pytest.mark.parametrize(
    ("llm_enabled", "deterministic_enabled", "expected"),
    [
        (False, True, "rule"),
        (False, False, "rule"),
        (True, False, "llm"),
        (True, True, "hybrid"),
    ],
)
def test_analysis_engine_type_reflects_executed_features(
    llm_enabled: bool,
    deterministic_enabled: bool,
    expected: str,
) -> None:
    assert (
        resolve_analysis_engine_type(
            llm_enabled=llm_enabled,
            deterministic_enabled=deterministic_enabled,
        )
        == expected
    )


def test_mock_provider_is_rejected_in_protected_environment() -> None:
    settings = Settings(
        allowed_email_domains="company.com",
        jwt_secret="test-jwt-secret-with-more-than-32-bytes",
        app_env="test",
    ).model_copy(update={"app_env": "production"})
    session = cast(AsyncSession, object())
    repository = cast(AiRepository, object())
    config_service = AiConfigService(
        session=session,
        repository=repository,
        settings=settings,
    )
    analysis_service = AiAnalysisService(
        session=session,
        repository=repository,
        settings=settings,
    )
    provider = AiProvider(
        name="forbidden mock",
        provider_type="mock",
        chat_model="mock-model",
    )

    with pytest.raises(exceptions.AiError, match="mock provider"):
        config_service._validate_provider_type("mock", is_internal=True)
    with pytest.raises(LLMAnalysisConfigurationError, match="mock provider"):
        analysis_service._build_llm_provider(provider, allow_external_llm=False)


def test_provider_model_runtime_limit_constraints_match_api_contract() -> None:
    constraint_names = {
        constraint.name
        for constraint in AiProvider.__table__.constraints
        if constraint.name is not None
    }
    assert {
        "ck_ai_providers_timeout_max",
        "ck_ai_providers_enabled_chat_model",
        "ck_ai_providers_retry_max",
        "ck_ai_providers_max_input_tokens_range",
        "ck_ai_providers_max_output_tokens_range",
        "ck_ai_providers_temperature_range",
        "ck_ai_providers_top_p_range",
    }.issubset(constraint_names)


def test_category_candidates_filter_before_cap_and_are_stably_ordered() -> None:
    enabled_categories = [
        AiCategoryRecord(
            id=uuid.UUID(int=index + 1),
            name=f"分类 {index}",
            code=f"code-{index:03d}",
            keywords=[f"k{index}"],
            allow_ai_recommend=True,
            ai_analysis_enabled=True,
            sensitive_detection_enabled=True,
        )
        for index in range(MAX_LLM_CATEGORIES + 5)
    ]
    disabled_categories = [
        AiCategoryRecord(
            id=uuid.UUID(int=10_000 + index),
            name=f"停用分类 {index}",
            code=f"disabled-{index:03d}",
            keywords=["disabled"],
            allow_ai_recommend=True,
            ai_analysis_enabled=False,
            sensitive_detection_enabled=True,
        )
        for index in range(3)
    ]
    categories = disabled_categories + list(reversed(enabled_categories))

    built = build_analysis_prompt(
        template_text="按契约分析输入。",
        text="正文",
        categories=categories,
        max_input_tokens=None,
    )
    reordered = build_analysis_prompt(
        template_text="按契约分析输入。",
        text="正文",
        categories=list(reversed(categories)),
        max_input_tokens=None,
    )

    assert built.category_count == MAX_LLM_CATEGORIES
    assert built.input_truncated is True
    assert built.allowed_category_ids == {
        category.id for category in enabled_categories[:MAX_LLM_CATEGORIES]
    }
    assert built.text == reordered.text
    assert built.text.find("code-000") < built.text.find("code-099")
