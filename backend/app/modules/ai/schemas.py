from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


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
    enabled: bool
    is_default: bool
    version: int
    updated_at: datetime


class SensitiveRuleResponse(BaseModel):
    id: UUID
    name: str
    rule_type: str
    risk_level: str
    action: str
    enabled: bool
    hit_count: int
    updated_at: datetime


class AiConfigResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    global_config: AiGlobalConfigResponse = Field(alias="global")
    features: list[AiFeatureResponse]
    providers: list[AiProviderResponse]
    prompt_templates: list[PromptTemplateResponse]
    sensitive_rules: list[SensitiveRuleResponse]
