from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

PricingCurrency = Annotated[
    str,
    StringConstraints(strip_whitespace=True, to_upper=True, pattern=r"^[A-Z]{3}$"),
]


class AiModuleStatus(BaseModel):
    name: str = "ai"


class AiGlobalConfigResponse(BaseModel):
    ai_analysis_enabled: bool
    ai_analysis_environment_enabled: bool
    ai_analysis_db_enabled: bool
    allow_external_llm: bool
    allow_external_llm_environment_enabled: bool
    allow_external_llm_db_enabled: bool
    allow_sync_when_analysis_failed: bool


class AiFeatureResponse(BaseModel):
    key: str
    name: str
    description: str | None = None
    enabled: bool


class AiFeatureUpdateRequest(BaseModel):
    enabled: bool


class AiProviderCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    provider_type: str = Field(default="openai_compatible", min_length=1, max_length=40)
    base_url: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=8_192)
    chat_model: str | None = Field(default=None, max_length=120)
    is_internal: bool = False
    enabled: bool = True
    priority: int = Field(default=100, ge=0, le=2_147_483_647)
    timeout_seconds: int = Field(default=60, ge=1, le=240)
    max_retry_count: int = Field(default=2, ge=0, le=10)
    max_input_tokens: int | None = Field(default=None, ge=1, le=1_000_000_000)
    max_output_tokens: int | None = Field(default=None, ge=1, le=4_096)
    temperature: float = Field(default=0.2, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    input_price_microunits_per_million_tokens: int = Field(default=0, ge=0, le=1_000_000_000_000)
    output_price_microunits_per_million_tokens: int = Field(default=0, ge=0, le=1_000_000_000_000)
    pricing_currency: PricingCurrency = "USD"


class AiProviderUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    provider_type: str | None = Field(default=None, min_length=1, max_length=40)
    base_url: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=8_192)
    clear_api_key: bool = False
    chat_model: str | None = Field(default=None, max_length=120)
    is_internal: bool | None = None
    enabled: bool | None = None
    priority: int | None = Field(default=None, ge=0, le=2_147_483_647)
    timeout_seconds: int | None = Field(default=None, ge=1, le=240)
    max_retry_count: int | None = Field(default=None, ge=0, le=10)
    max_input_tokens: int | None = Field(default=None, ge=1, le=1_000_000_000)
    max_output_tokens: int | None = Field(default=None, ge=1, le=4_096)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    input_price_microunits_per_million_tokens: int | None = Field(
        default=None, ge=0, le=1_000_000_000_000
    )
    output_price_microunits_per_million_tokens: int | None = Field(
        default=None, ge=0, le=1_000_000_000_000
    )
    pricing_currency: PricingCurrency | None = None


class AiProviderResponse(BaseModel):
    id: UUID
    name: str
    provider_type: str
    base_url: str | None
    chat_model: str | None
    is_internal: bool
    enabled: bool
    priority: int
    timeout_seconds: int
    max_retry_count: int
    max_input_tokens: int | None
    max_output_tokens: int | None
    temperature: float
    top_p: float | None
    input_price_microunits_per_million_tokens: int
    output_price_microunits_per_million_tokens: int
    pricing_currency: PricingCurrency
    has_api_key: bool
    api_key_masked: str | None
    last_test_status: str | None
    last_test_latency_ms: int | None
    last_tested_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AiProviderTestResponse(BaseModel):
    provider_id: UUID
    status: str
    latency_ms: int | None = None
    message: str | None = None


class PromptTemplateResponse(BaseModel):
    id: UUID
    template_key: str
    name: str
    description: str | None
    prompt_text: str
    variables: list[str]
    enabled: bool
    is_default: bool
    version: int
    updated_at: datetime


class PromptTemplateCreateRequest(BaseModel):
    template_key: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    prompt_text: str = Field(min_length=1)
    variables: list[str] = Field(default_factory=list)
    enabled: bool = True

    @field_validator("template_key")
    @classmethod
    def validate_template_key(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("template_key is required")
        return cleaned


class PromptTemplateUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    prompt_text: str | None = Field(default=None, min_length=1)
    variables: list[str] | None = None
    enabled: bool | None = None


class SensitiveRuleResponse(BaseModel):
    id: UUID
    name: str
    rule_type: str
    pattern: str | None
    keywords: list[str]
    risk_level: str
    action: str
    enabled: bool
    hit_count: int
    updated_at: datetime


SensitiveRuleType = Literal["keyword", "regex"]
SensitiveRiskLevel = Literal["low", "medium", "high", "critical"]
SensitiveRuleAction = Literal["flag", "require_review", "block_sync"]


class SensitiveRuleCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    rule_type: SensitiveRuleType
    pattern: str | None = None
    keywords: list[str] = Field(default_factory=list)
    risk_level: SensitiveRiskLevel
    action: SensitiveRuleAction
    enabled: bool = True


class SensitiveRuleUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    rule_type: SensitiveRuleType | None = None
    pattern: str | None = None
    keywords: list[str] | None = None
    risk_level: SensitiveRiskLevel | None = None
    action: SensitiveRuleAction | None = None
    enabled: bool | None = None


class SensitiveRuleTestRequest(BaseModel):
    text: str = Field(min_length=1)


class SensitiveRuleHitResponse(BaseModel):
    rule_id: UUID
    rule_name: str
    risk_level: str
    action: str
    match: str


class SensitiveRuleTestResponse(BaseModel):
    hits: list[SensitiveRuleHitResponse]


class AiConfigResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    global_config: AiGlobalConfigResponse = Field(alias="global")
    features: list[AiFeatureResponse]
    providers: list[AiProviderResponse]
    prompt_templates: list[PromptTemplateResponse]
    sensitive_rules: list[SensitiveRuleResponse]
