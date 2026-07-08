from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AiModuleStatus(BaseModel):
    name: str = "ai"


class AiGlobalConfigResponse(BaseModel):
    ai_analysis_enabled: bool
    allow_external_llm: bool
    allow_sync_when_analysis_failed: bool


class AiFeatureResponse(BaseModel):
    key: str
    name: str
    description: str | None = None
    enabled: bool


class AiFeatureUpdateRequest(BaseModel):
    enabled: bool


class AiProviderCreateRequest(BaseModel):
    name: str
    provider_type: str = "openai_compatible"
    base_url: str | None = None
    api_key: str | None = None
    chat_model: str | None = None
    embedding_model: str | None = None
    vision_model: str | None = None
    is_internal: bool = False
    enabled: bool = True
    priority: int = 100
    timeout_seconds: int = 60
    max_retry_count: int = 2
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    temperature: float = 0.2
    top_p: float | None = None


class AiProviderUpdateRequest(BaseModel):
    name: str | None = None
    provider_type: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    clear_api_key: bool = False
    chat_model: str | None = None
    embedding_model: str | None = None
    vision_model: str | None = None
    is_internal: bool | None = None
    enabled: bool | None = None
    priority: int | None = None
    timeout_seconds: int | None = None
    max_retry_count: int | None = None
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None


class AiProviderResponse(BaseModel):
    id: UUID
    name: str
    provider_type: str
    base_url: str | None
    chat_model: str | None
    embedding_model: str | None
    vision_model: str | None
    is_internal: bool
    enabled: bool
    priority: int
    timeout_seconds: int
    max_retry_count: int
    max_input_tokens: int | None
    max_output_tokens: int | None
    temperature: float
    top_p: float | None
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
