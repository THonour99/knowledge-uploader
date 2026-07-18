from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast

from cryptography.fernet import InvalidToken
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.llm.base import BaseLLMProvider, LLMCompletion, LLMProviderError
from app.adapters.llm.mock import MockLLMProvider
from app.adapters.llm.openai_compatible import (
    LLMTestResult,
    OpenAICompatibleProvider,
    validate_model_name,
)
from app.adapters.minio_client import STORAGE_TRANSIENT_ERRORS, is_transient_storage_error
from app.core.audit import record_admin_audit_log
from app.core.config import PROTECTED_ENVS, Settings
from app.core.document_state import DocumentStateError, DocumentStateMachine
from app.core.llm_endpoint import (
    llm_base_url_is_allowed,
    normalize_llm_base_url,
    normalized_llm_allowed_base_urls,
)
from app.core.outbox import OutboxRepository
from app.core.review_policy import review_submission_times
from app.core.runtime_config import get_config as get_runtime_config
from app.core.security import decrypt_api_key, encrypt_api_key
from app.modules.user.schemas import AuthUserRecord

from . import events, exceptions
from .cost_governance import (
    CostObservation,
    CostStatus,
    aggregate_cost_observation,
    merge_cost_status,
    observe_llm_cost,
    pricing_confirmation_basis,
    pricing_confirmation_is_effective,
    resolve_create_pricing_configured,
    resolve_update_pricing_configured,
)
from .llm_analysis import (
    ANALYSIS_PROMPT_KEY,
    LLM_ANALYSIS_SYSTEM_PROMPT,
    LLM_REPAIR_SUFFIX,
    MAX_POSTGRES_BIGINT,
    MAX_POSTGRES_INTEGER,
    AnalysisFeatureSelection,
    LLMInputProvenance,
    LLMOutputValidationError,
    ValidatedLLMAnalysis,
    build_analysis_prompt,
    build_input_provenance,
    checked_persisted_sum,
    parse_analysis_output,
)
from .models import (
    AiFeatureConfig,
    AiProvider,
    AiUsageLog,
    DocumentAnalysis,
    PromptTemplate,
    SensitiveRule,
)
from .parsers import (
    MAX_EXTRACTED_TEXT_LENGTH,
    MAX_PDF_PAGES,
    append_tables_markdown,
    extract_tables_from_bytes,
    extract_text_from_bytes,
)
from .quality import normalize_quality_weights, score_document_quality
from .repository import (  # noqa: TID251 - same-module repository dependency
    AiCategoryRecord,
    AiFileRecord,
    AiRepository,
)
from .schemas import (
    AiConfigResponse,
    AiFeatureResponse,
    AiFeatureUpdateRequest,
    AiGlobalConfigResponse,
    AiProviderCreateRequest,
    AiProviderResponse,
    AiProviderTestResponse,
    AiProviderUpdateRequest,
    PromptTemplateCreateRequest,
    PromptTemplateResponse,
    PromptTemplateUpdateRequest,
    SensitiveRuleCreateRequest,
    SensitiveRuleHitResponse,
    SensitiveRuleResponse,
    SensitiveRuleTestRequest,
    SensitiveRuleTestResponse,
    SensitiveRuleUpdateRequest,
)
from .simhash import compute_simhash, hamming_distance, simhash_bands

ADMIN_ROLES = {"system_admin"}
SYSTEM_ADMIN_ROLE = "system_admin"
MAX_ERROR_MESSAGE_LENGTH = 500
AI_HARD_DISABLED_MESSAGE = "AI analysis disabled by environment"
GLOBAL_FEATURE_KEYS = {
    "ai_analysis",
    "allow_external_llm",
    "allow_sync_when_analysis_failed",
}
RISK_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
AI_ANALYSIS_IN_PROGRESS_FILE_STATUSES = frozenset(
    {
        "extracting_text",
        "analysis_queued",
        "analyzing",
    }
)
AI_ANALYSIS_SUCCEEDED_FILE_STATUSES = frozenset(
    {
        "analyzed",
        "sensitive_review_required",
        "pending_review",
        "approved",
        "rejected",
        "queued",
        "syncing",
        "uploaded_to_ragflow",
        "parsing",
        "parsed",
        "failed",
    }
)
PROMPT_TEMPLATE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
SENSITIVE_RISK_LEVELS = {"low", "medium", "high", "critical"}
SENSITIVE_RULE_ACTIONS = {"flag", "require_review", "block_sync"}


class LLMAnalysisConfigurationError(Exception):
    pass


class AnalysisLeaseLostError(Exception):
    pass


def _persistable_usage_counter(value: int | None) -> int | None:
    if value is None or value < 0 or value > MAX_POSTGRES_INTEGER:
        return None
    return value


class AiObjectStorage(Protocol):
    async def get_object(self, *, bucket: str, object_key: str) -> bytes:
        pass


@dataclass(frozen=True)
class RequestContext:
    ip_address: str
    user_agent: str


@dataclass(frozen=True)
class ProviderTestSnapshot:
    provider_id: uuid.UUID
    updated_at: datetime
    fingerprint: str
    provider_type: str
    base_url: str | None
    api_key: str | None = field(repr=False)
    chat_model: str | None
    is_internal: bool
    timeout_seconds: int
    effective_allow_external: bool


@dataclass(frozen=True)
class FeatureDefinition:
    key: str
    name: str
    description: str
    default_enabled: bool


@dataclass(frozen=True)
class PromptDefinition:
    template_key: str
    name: str
    description: str
    prompt_text: str
    variables: list[str]


@dataclass(frozen=True)
class SensitiveRuleDefinition:
    name: str
    rule_type: str
    risk_level: str
    action: str
    pattern: str | None = None
    keywords: list[str] | None = None


@dataclass(frozen=True)
class CategorySuggestion:
    category_id: uuid.UUID | None
    category_name: str | None


class AiConfigService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        repository: AiRepository,
        settings: Settings,
    ) -> None:
        self._session = session
        self._repository = repository
        self._settings = settings

    async def get_config(
        self,
        *,
        current_user: AuthUserRecord,
        context: RequestContext,
    ) -> AiConfigResponse:
        self._require_admin(current_user)
        await self._ensure_defaults()
        features = await self._feature_map()
        response = AiConfigResponse(
            global_config=AiGlobalConfigResponse(
                ai_analysis_enabled=(
                    self._settings.ai_analysis_enabled and features["ai_analysis"].enabled
                ),
                ai_analysis_environment_enabled=self._settings.ai_analysis_enabled,
                ai_analysis_db_enabled=features["ai_analysis"].enabled,
                allow_external_llm=(
                    self._settings.allow_external_llm and features["allow_external_llm"].enabled
                ),
                allow_external_llm_environment_enabled=self._settings.allow_external_llm,
                allow_external_llm_db_enabled=features["allow_external_llm"].enabled,
                allow_sync_when_analysis_failed=features["allow_sync_when_analysis_failed"].enabled,
            ),
            features=[
                self._feature_response(feature)
                for feature in features.values()
                if feature.feature_name not in GLOBAL_FEATURE_KEYS
            ],
            providers=[
                self._provider_response(provider)
                for provider in await self._repository.list_providers()
            ],
            prompt_templates=[
                self._prompt_template_response(template)
                for template in await self._repository.list_prompt_templates()
            ],
            sensitive_rules=[
                self._sensitive_rule_response(rule)
                for rule in await self._repository.list_sensitive_rules()
            ],
        )
        await self._record_admin_audit(
            current_user=current_user,
            action="ai.config.get",
            target_type="ai_config",
            target_id=current_user.id,
            context=context,
        )
        await self._session.commit()
        return response

    async def update_feature(
        self,
        *,
        current_user: AuthUserRecord,
        feature_key: str,
        request: AiFeatureUpdateRequest,
        context: RequestContext,
    ) -> AiFeatureResponse:
        self._require_system_admin(current_user)
        await self._ensure_defaults()
        feature = await self._repository.get_feature_config(feature_key)
        if feature is None:
            raise exceptions.feature_not_found()
        feature.enabled = request.enabled
        await self._record_admin_audit(
            current_user=current_user,
            action="ai.feature.update",
            target_type="ai_feature",
            target_id=feature.id,
            context=context,
            metadata_json={"feature_name": feature.feature_name, "enabled": feature.enabled},
        )
        await self._session.commit()
        await self._session.refresh(feature)
        return self._feature_response(feature)

    async def create_provider(
        self,
        *,
        current_user: AuthUserRecord,
        request: AiProviderCreateRequest,
        context: RequestContext,
    ) -> AiProvider:
        self._require_system_admin(current_user)
        self._validate_provider_type(request.provider_type, is_internal=request.is_internal)
        invalid_base_url = False
        try:
            base_url = normalize_provider_base_url(request.base_url)
        except ValueError:
            base_url = None
            invalid_base_url = True
        if invalid_base_url:
            raise exceptions.invalid_provider_config("invalid provider base URL")
        chat_model = clean_optional_text(request.chat_model)
        self._validate_provider_activation(
            provider_type=request.provider_type,
            base_url=base_url,
            chat_model=chat_model,
            enabled=request.enabled,
        )
        pricing_configured = resolve_create_pricing_configured(
            explicit=request.pricing_configured,
            input_price_microunits_per_million_tokens=(
                request.input_price_microunits_per_million_tokens
            ),
            output_price_microunits_per_million_tokens=(
                request.output_price_microunits_per_million_tokens
            ),
        )
        confirmed_input, confirmed_output, confirmed_currency = pricing_confirmation_basis(
            configured=pricing_configured,
            input_price_microunits_per_million_tokens=(
                request.input_price_microunits_per_million_tokens
            ),
            output_price_microunits_per_million_tokens=(
                request.output_price_microunits_per_million_tokens
            ),
            pricing_currency=request.pricing_currency,
        )
        provider = AiProvider(
            name=request.name.strip(),
            provider_type=request.provider_type,
            base_url=base_url,
            api_key_encrypted=self._encrypt_api_key(request.api_key),
            chat_model=chat_model,
            is_internal=request.is_internal,
            enabled=request.enabled,
            priority=request.priority,
            timeout_seconds=request.timeout_seconds,
            max_retry_count=request.max_retry_count,
            max_input_tokens=request.max_input_tokens,
            max_output_tokens=request.max_output_tokens,
            temperature=request.temperature,
            top_p=request.top_p,
            input_price_microunits_per_million_tokens=request.input_price_microunits_per_million_tokens,
            output_price_microunits_per_million_tokens=request.output_price_microunits_per_million_tokens,
            pricing_currency=request.pricing_currency,
            pricing_configured=pricing_configured,
            pricing_confirmed_input_microunits_per_million=confirmed_input,
            pricing_confirmed_output_microunits_per_million=confirmed_output,
            pricing_confirmed_currency=confirmed_currency,
        )
        await self._repository.add_provider(provider)
        provider_changed_fields = _provider_create_audit_fields(provider)
        await self._record_admin_audit(
            current_user=current_user,
            action="ai.provider.create",
            target_type="ai_provider",
            target_id=provider.id,
            context=context,
            metadata_json={
                "name": provider.name,
                "provider_type": provider.provider_type,
                "enabled": provider.enabled,
                "pricing_currency": provider.pricing_currency,
                "pricing_configured": _provider_effective_pricing_configured(provider),
                "input_price_microunits_per_million_tokens": (
                    provider.input_price_microunits_per_million_tokens
                ),
                "output_price_microunits_per_million_tokens": (
                    provider.output_price_microunits_per_million_tokens
                ),
                "changed_fields": provider_changed_fields,
            },
        )
        await self._session.commit()
        await self._session.refresh(provider)
        return provider

    async def update_provider(
        self,
        *,
        current_user: AuthUserRecord,
        provider_id: uuid.UUID,
        request: AiProviderUpdateRequest,
        context: RequestContext,
    ) -> AiProvider:
        self._require_system_admin(current_user)
        provider = await self._get_provider_or_raise(provider_id)
        next_type = (
            request.provider_type if request.provider_type is not None else provider.provider_type
        )
        next_internal = (
            request.is_internal if request.is_internal is not None else provider.is_internal
        )
        self._validate_provider_type(next_type, is_internal=next_internal)
        fields_set = request.model_fields_set
        previous_pricing_configured = _provider_effective_pricing_configured(provider)
        pricing_fields_submitted = _pricing_fields_submitted(request)
        if request.name is not None:
            provider.name = request.name.strip()
        if request.provider_type is not None:
            provider.provider_type = request.provider_type
        if "base_url" in fields_set:
            try:
                provider.base_url = normalize_provider_base_url(request.base_url)
            except ValueError as exc:
                raise exceptions.invalid_provider_config("invalid provider base URL") from exc
        if request.clear_api_key:
            provider.api_key_encrypted = None
        elif request.api_key is not None:
            provider.api_key_encrypted = self._encrypt_api_key(request.api_key)
        if "chat_model" in fields_set:
            provider.chat_model = clean_optional_text(request.chat_model)
        if request.is_internal is not None:
            provider.is_internal = request.is_internal
        if request.enabled is not None:
            provider.enabled = request.enabled
        if request.priority is not None:
            provider.priority = request.priority
        if request.timeout_seconds is not None:
            provider.timeout_seconds = request.timeout_seconds
        if request.max_retry_count is not None:
            provider.max_retry_count = request.max_retry_count
        if "max_input_tokens" in fields_set:
            provider.max_input_tokens = request.max_input_tokens
        if "max_output_tokens" in fields_set:
            provider.max_output_tokens = request.max_output_tokens
        if request.temperature is not None:
            provider.temperature = request.temperature
        if "top_p" in fields_set:
            provider.top_p = request.top_p
        if request.input_price_microunits_per_million_tokens is not None:
            provider.input_price_microunits_per_million_tokens = (
                request.input_price_microunits_per_million_tokens
            )
        if request.output_price_microunits_per_million_tokens is not None:
            provider.output_price_microunits_per_million_tokens = (
                request.output_price_microunits_per_million_tokens
            )
        if request.pricing_currency is not None:
            provider.pricing_currency = request.pricing_currency
        provider.pricing_configured = resolve_update_pricing_configured(
            explicit=request.pricing_configured,
            previous=previous_pricing_configured,
            pricing_fields_submitted=pricing_fields_submitted,
            input_price_microunits_per_million_tokens=(
                provider.input_price_microunits_per_million_tokens
            ),
            output_price_microunits_per_million_tokens=(
                provider.output_price_microunits_per_million_tokens
            ),
        )
        should_sync_pricing_confirmation = request.pricing_configured is True or (
            request.pricing_configured is None
            and pricing_fields_submitted
            and (
                provider.input_price_microunits_per_million_tokens > 0
                or provider.output_price_microunits_per_million_tokens > 0
            )
        )
        if should_sync_pricing_confirmation:
            confirmed_input, confirmed_output, confirmed_currency = pricing_confirmation_basis(
                configured=True,
                input_price_microunits_per_million_tokens=(
                    provider.input_price_microunits_per_million_tokens
                ),
                output_price_microunits_per_million_tokens=(
                    provider.output_price_microunits_per_million_tokens
                ),
                pricing_currency=provider.pricing_currency,
            )
            provider.pricing_confirmed_input_microunits_per_million = confirmed_input
            provider.pricing_confirmed_output_microunits_per_million = confirmed_output
            provider.pricing_confirmed_currency = confirmed_currency
        elif not provider.pricing_configured:
            provider.pricing_confirmed_input_microunits_per_million = None
            provider.pricing_confirmed_output_microunits_per_million = None
            provider.pricing_confirmed_currency = None
        effective_pricing_configured = _provider_effective_pricing_configured(provider)
        provider_changed_fields = _provider_update_audit_fields(request)
        if effective_pricing_configured != previous_pricing_configured:
            provider_changed_fields = sorted({*provider_changed_fields, "pricing_configured"})
        self._validate_provider_activation(
            provider_type=provider.provider_type,
            base_url=provider.base_url,
            chat_model=provider.chat_model,
            enabled=provider.enabled,
        )
        await self._record_admin_audit(
            current_user=current_user,
            action="ai.provider.update",
            target_type="ai_provider",
            target_id=provider.id,
            context=context,
            metadata_json={
                "name": provider.name,
                "provider_type": provider.provider_type,
                "enabled": provider.enabled,
                "pricing_currency": provider.pricing_currency,
                "pricing_configured": effective_pricing_configured,
                "input_price_microunits_per_million_tokens": (
                    provider.input_price_microunits_per_million_tokens
                ),
                "output_price_microunits_per_million_tokens": (
                    provider.output_price_microunits_per_million_tokens
                ),
                "changed_fields": provider_changed_fields,
            },
        )
        await self._session.commit()
        await self._session.refresh(provider)
        return provider

    async def test_provider(
        self,
        *,
        current_user: AuthUserRecord,
        provider_id: uuid.UUID,
        context: RequestContext,
    ) -> AiProviderTestResponse:
        self._require_system_admin(current_user)
        provider = await self._get_provider_or_raise(provider_id)
        features = await self._feature_map()
        db_external_enabled = features["allow_external_llm"].enabled
        snapshot = self._provider_test_snapshot(
            provider,
            db_external_enabled=db_external_enabled,
        )
        await self._session.commit()
        if self._session.in_transaction():
            await self._session.rollback()
            raise RuntimeError("provider test transaction was not released")

        try:
            result = await self._test_provider_connectivity(snapshot)
        except Exception:
            result = LLMTestResult(
                status="failed",
                latency_ms=None,
                message="connection_error",
            )

        current_provider = await self._repository.get_provider_for_update(provider_id)
        current_features = await self._feature_map()
        current_db_external = current_features["allow_external_llm"].enabled
        stale_config = current_provider is None
        if current_provider is not None:
            stale_config = bool(
                current_provider.updated_at != snapshot.updated_at
                or self._provider_config_fingerprint(
                    current_provider,
                    db_external_enabled=current_db_external,
                )
                != snapshot.fingerprint
            )
            if not stale_config:
                current_provider.last_test_status = result.status
                current_provider.last_test_latency_ms = result.latency_ms
                current_provider.last_tested_at = datetime.now(UTC)
        audit_status = "discarded" if stale_config else result.status
        await self._record_admin_audit(
            current_user=current_user,
            action="ai.provider.test",
            target_type="ai_provider",
            target_id=snapshot.provider_id,
            context=context,
            metadata_json={
                "status": audit_status,
                "observed_status": result.status,
                "latency_ms": result.latency_ms,
                "stale_config": stale_config,
                "config_fingerprint": snapshot.fingerprint,
            },
        )
        await self._session.commit()
        if stale_config:
            return AiProviderTestResponse(
                provider_id=snapshot.provider_id,
                status="failed",
                latency_ms=result.latency_ms,
                message="provider configuration changed during test",
            )
        return AiProviderTestResponse(
            provider_id=snapshot.provider_id,
            status=result.status,
            latency_ms=result.latency_ms,
            message=result.message,
        )

    async def create_prompt_template(
        self,
        *,
        current_user: AuthUserRecord,
        request: PromptTemplateCreateRequest,
        context: RequestContext,
    ) -> PromptTemplateResponse:
        self._require_system_admin(current_user)
        await self._ensure_defaults()
        template_key = self._normalize_template_key(request.template_key)
        prompt_text = self._required_text(request.prompt_text, "prompt text")
        variables = self._normalize_variables(request.variables)
        self._validate_reserved_prompt_contract(
            template_key=template_key,
            prompt_text=prompt_text,
            variables=variables,
        )
        if await self._repository.get_prompt_template_by_key(template_key) is not None:
            raise exceptions.invalid_ai_config("prompt template key already exists")
        template = PromptTemplate(
            template_key=template_key,
            name=self._required_text(request.name, "prompt template name"),
            description=clean_optional_text(request.description),
            prompt_text=prompt_text,
            variables=variables,
            enabled=request.enabled,
            is_default=False,
            version=1,
        )
        await self._repository.add_prompt_template(template)
        await self._record_ai_config_change(
            current_user=current_user,
            action="ai.prompt.create",
            target_type="ai_prompt_template",
            target_id=template.id,
            context=context,
            metadata_json={
                "template_key": template.template_key,
                "name": template.name,
                "enabled": template.enabled,
                "version": template.version,
                "changed_fields": [
                    "template_key",
                    "name",
                    "description",
                    "prompt_text",
                    "variables",
                    "enabled",
                ],
            },
        )
        await self._session.commit()
        await self._session.refresh(template)
        return self._prompt_template_response(template)

    async def update_prompt_template(
        self,
        *,
        current_user: AuthUserRecord,
        template_id: uuid.UUID,
        request: PromptTemplateUpdateRequest,
        context: RequestContext,
    ) -> PromptTemplateResponse:
        self._require_system_admin(current_user)
        template = await self._get_prompt_template_or_raise(template_id)
        changed_fields: list[str] = []
        next_prompt_text = (
            self._required_text(request.prompt_text, "prompt text")
            if request.prompt_text is not None
            else template.prompt_text
        )
        next_variables = (
            self._normalize_variables(request.variables)
            if request.variables is not None
            else template.variables
        )
        self._validate_reserved_prompt_contract(
            template_key=template.template_key,
            prompt_text=next_prompt_text,
            variables=next_variables,
        )
        if request.name is not None:
            template.name = self._required_text(request.name, "prompt template name")
            changed_fields.append("name")
        if "description" in request.model_fields_set:
            template.description = clean_optional_text(request.description)
            changed_fields.append("description")
        if request.prompt_text is not None:
            template.prompt_text = next_prompt_text
            template.version += 1
            changed_fields.append("prompt_text")
        if request.variables is not None:
            template.variables = next_variables
            if "prompt_text" not in changed_fields:
                template.version += 1
            changed_fields.append("variables")
        if request.enabled is not None:
            template.enabled = request.enabled
            changed_fields.append("enabled")
        if changed_fields:
            await self._record_ai_config_change(
                current_user=current_user,
                action="ai.prompt.update",
                target_type="ai_prompt_template",
                target_id=template.id,
                context=context,
                metadata_json={
                    "template_key": template.template_key,
                    "name": template.name,
                    "enabled": template.enabled,
                    "version": template.version,
                    "changed_fields": changed_fields,
                },
            )
        await self._session.commit()
        await self._session.refresh(template)
        return self._prompt_template_response(template)

    async def restore_prompt_template_default(
        self,
        *,
        current_user: AuthUserRecord,
        template_id: uuid.UUID,
        context: RequestContext,
    ) -> PromptTemplateResponse:
        self._require_system_admin(current_user)
        template = await self._get_prompt_template_or_raise(template_id)
        defaults = {
            definition.template_key: definition for definition in _default_prompt_definitions()
        }
        definition = defaults.get(template.template_key)
        if definition is None:
            raise exceptions.invalid_ai_config("prompt template has no default")
        template.name = definition.name
        template.description = definition.description
        template.prompt_text = definition.prompt_text
        template.variables = definition.variables
        template.enabled = True
        template.is_default = True
        template.version += 1
        await self._record_ai_config_change(
            current_user=current_user,
            action="ai.prompt.restore_default",
            target_type="ai_prompt_template",
            target_id=template.id,
            context=context,
            metadata_json={
                "template_key": template.template_key,
                "name": template.name,
                "enabled": template.enabled,
                "version": template.version,
                "changed_fields": [
                    "name",
                    "description",
                    "prompt_text",
                    "variables",
                    "enabled",
                    "is_default",
                ],
            },
        )
        await self._session.commit()
        await self._session.refresh(template)
        return self._prompt_template_response(template)

    async def delete_prompt_template(
        self,
        *,
        current_user: AuthUserRecord,
        template_id: uuid.UUID,
        context: RequestContext,
    ) -> None:
        self._require_system_admin(current_user)
        template = await self._get_prompt_template_or_raise(template_id)
        metadata = {
            "template_key": template.template_key,
            "name": template.name,
            "enabled": False,
            "version": template.version,
            "changed_fields": ["enabled"] if template.is_default else ["deleted"],
        }
        if template.is_default:
            template.enabled = False
        else:
            await self._repository.delete_prompt_template(template.id)
        await self._record_ai_config_change(
            current_user=current_user,
            action="ai.prompt.delete",
            target_type="ai_prompt_template",
            target_id=template_id,
            context=context,
            metadata_json=metadata,
        )
        await self._session.commit()

    async def create_sensitive_rule(
        self,
        *,
        current_user: AuthUserRecord,
        request: SensitiveRuleCreateRequest,
        context: RequestContext,
    ) -> SensitiveRuleResponse:
        self._require_system_admin(current_user)
        rule_type, pattern, keywords = self._normalize_sensitive_rule_matcher(
            rule_type=request.rule_type,
            pattern=request.pattern,
            keywords=request.keywords,
        )
        rule = SensitiveRule(
            name=self._required_text(request.name, "sensitive rule name"),
            rule_type=rule_type,
            pattern=pattern,
            keywords=keywords,
            risk_level=self._normalize_risk_level(request.risk_level),
            action=self._normalize_rule_action(request.action),
            enabled=request.enabled,
        )
        await self._repository.add_sensitive_rule(rule)
        await self._record_ai_config_change(
            current_user=current_user,
            action="ai.sensitive_rule.create",
            target_type="ai_sensitive_rule",
            target_id=rule.id,
            context=context,
            metadata_json=self._sensitive_rule_audit_metadata(
                rule,
                changed_fields=[
                    "name",
                    "rule_type",
                    "pattern",
                    "keywords",
                    "risk_level",
                    "action",
                    "enabled",
                ],
            ),
        )
        await self._session.commit()
        await self._session.refresh(rule)
        return self._sensitive_rule_response(rule)

    async def update_sensitive_rule(
        self,
        *,
        current_user: AuthUserRecord,
        rule_id: uuid.UUID,
        request: SensitiveRuleUpdateRequest,
        context: RequestContext,
    ) -> SensitiveRuleResponse:
        self._require_system_admin(current_user)
        rule = await self._get_sensitive_rule_or_raise(rule_id)
        next_rule_type = request.rule_type or rule.rule_type
        next_pattern = request.pattern if "pattern" in request.model_fields_set else rule.pattern
        next_keywords = request.keywords if request.keywords is not None else rule.keywords
        rule_type, pattern, keywords = self._normalize_sensitive_rule_matcher(
            rule_type=next_rule_type,
            pattern=next_pattern,
            keywords=next_keywords,
        )
        changed_fields: list[str] = []
        if request.name is not None:
            rule.name = self._required_text(request.name, "sensitive rule name")
            changed_fields.append("name")
        if request.rule_type is not None:
            rule.rule_type = rule_type
            changed_fields.append("rule_type")
        if "pattern" in request.model_fields_set:
            rule.pattern = pattern
            changed_fields.append("pattern")
        if request.keywords is not None:
            rule.keywords = keywords
            changed_fields.append("keywords")
        if request.risk_level is not None:
            rule.risk_level = self._normalize_risk_level(request.risk_level)
            changed_fields.append("risk_level")
        if request.action is not None:
            rule.action = self._normalize_rule_action(request.action)
            changed_fields.append("action")
        if request.enabled is not None:
            rule.enabled = request.enabled
            changed_fields.append("enabled")
        if changed_fields:
            await self._record_ai_config_change(
                current_user=current_user,
                action="ai.sensitive_rule.update",
                target_type="ai_sensitive_rule",
                target_id=rule.id,
                context=context,
                metadata_json=self._sensitive_rule_audit_metadata(
                    rule,
                    changed_fields=changed_fields,
                ),
            )
        await self._session.commit()
        await self._session.refresh(rule)
        return self._sensitive_rule_response(rule)

    async def delete_sensitive_rule(
        self,
        *,
        current_user: AuthUserRecord,
        rule_id: uuid.UUID,
        context: RequestContext,
    ) -> None:
        self._require_system_admin(current_user)
        rule = await self._get_sensitive_rule_or_raise(rule_id)
        metadata = self._sensitive_rule_audit_metadata(rule, changed_fields=["deleted"])
        await self._repository.delete_sensitive_rule(rule.id)
        await self._record_ai_config_change(
            current_user=current_user,
            action="ai.sensitive_rule.delete",
            target_type="ai_sensitive_rule",
            target_id=rule_id,
            context=context,
            metadata_json=metadata,
        )
        await self._session.commit()

    async def test_sensitive_rules(
        self,
        *,
        current_user: AuthUserRecord,
        request: SensitiveRuleTestRequest,
        context: RequestContext,
    ) -> SensitiveRuleTestResponse:
        self._require_system_admin(current_user)
        rules = await self._repository.list_sensitive_rules(enabled_only=True)
        hits = detect_sensitive_hits(request.text, rules)
        await self._record_admin_audit(
            current_user=current_user,
            action="ai.sensitive_rule.test",
            target_type="ai_sensitive_rule",
            target_id=current_user.id,
            context=context,
            metadata_json={"hit_count": len(hits), "enabled_rule_count": len(rules)},
        )
        await self._session.commit()
        return SensitiveRuleTestResponse(
            hits=[
                SensitiveRuleHitResponse(
                    rule_id=uuid.UUID(str(hit["rule_id"])),
                    rule_name=str(hit["rule_name"]),
                    risk_level=str(hit["risk_level"]),
                    action=str(hit["action"]),
                    match=str(hit["match"]),
                )
                for hit in hits
            ]
        )

    def _feature_response(self, feature: AiFeatureConfig) -> AiFeatureResponse:
        metadata = feature.config_json
        return AiFeatureResponse(
            key=feature.feature_name,
            name=str(metadata.get("name", feature.feature_name)),
            description=cast_optional_str(metadata.get("description")),
            enabled=feature.enabled,
        )

    def _provider_response(self, provider: AiProvider) -> AiProviderResponse:
        return AiProviderResponse(
            id=provider.id,
            name=provider.name,
            provider_type=provider.provider_type,
            base_url=safe_provider_base_url(provider.base_url),
            chat_model=provider.chat_model,
            is_internal=provider.is_internal,
            enabled=provider.enabled,
            priority=provider.priority,
            timeout_seconds=provider.timeout_seconds,
            max_retry_count=provider.max_retry_count,
            max_input_tokens=provider.max_input_tokens,
            max_output_tokens=provider.max_output_tokens,
            temperature=provider.temperature,
            top_p=provider.top_p,
            input_price_microunits_per_million_tokens=provider.input_price_microunits_per_million_tokens,
            output_price_microunits_per_million_tokens=provider.output_price_microunits_per_million_tokens,
            pricing_currency=provider.pricing_currency,
            pricing_configured=_provider_effective_pricing_configured(provider),
            has_api_key=bool(provider.api_key_encrypted),
            api_key_masked=self._masked_provider_key(provider),
            last_test_status=provider.last_test_status,
            last_test_latency_ms=provider.last_test_latency_ms,
            last_tested_at=provider.last_tested_at,
            created_at=provider.created_at,
            updated_at=provider.updated_at,
        )

    def _prompt_template_response(self, template: PromptTemplate) -> PromptTemplateResponse:
        return PromptTemplateResponse(
            id=template.id,
            template_key=template.template_key,
            name=template.name,
            description=template.description,
            prompt_text=template.prompt_text,
            variables=template.variables,
            enabled=template.enabled,
            is_default=template.is_default,
            version=template.version,
            updated_at=template.updated_at,
        )

    def _sensitive_rule_response(self, rule: SensitiveRule) -> SensitiveRuleResponse:
        return SensitiveRuleResponse(
            id=rule.id,
            name=rule.name,
            rule_type=rule.rule_type,
            pattern=rule.pattern,
            keywords=rule.keywords,
            risk_level=rule.risk_level,
            action=rule.action,
            enabled=rule.enabled,
            hit_count=rule.hit_count,
            updated_at=rule.updated_at,
        )

    async def _get_provider_or_raise(self, provider_id: uuid.UUID) -> AiProvider:
        provider = await self._repository.get_provider(provider_id)
        if provider is None:
            raise exceptions.provider_not_found()
        return provider

    async def _get_prompt_template_or_raise(self, template_id: uuid.UUID) -> PromptTemplate:
        template = await self._repository.get_prompt_template(template_id)
        if template is None:
            raise exceptions.prompt_template_not_found()
        return template

    async def _get_sensitive_rule_or_raise(self, rule_id: uuid.UUID) -> SensitiveRule:
        rule = await self._repository.get_sensitive_rule(rule_id)
        if rule is None:
            raise exceptions.sensitive_rule_not_found()
        return rule

    def _normalize_template_key(self, value: str) -> str:
        cleaned = value.strip()
        if not PROMPT_TEMPLATE_KEY_RE.fullmatch(cleaned):
            raise exceptions.invalid_ai_config("invalid prompt template key")
        return cleaned

    def _required_text(self, value: str, field_name: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise exceptions.invalid_ai_config(f"{field_name} is required")
        return cleaned

    def _normalize_variables(self, variables: Sequence[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for variable in variables:
            cleaned = variable.strip()
            if not cleaned:
                continue
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", cleaned):
                raise exceptions.invalid_ai_config("invalid prompt variable")
            if cleaned in seen:
                continue
            seen.add(cleaned)
            result.append(cleaned)
        return result

    def _validate_reserved_prompt_contract(
        self,
        *,
        template_key: str,
        prompt_text: str,
        variables: Sequence[str],
    ) -> None:
        if template_key != ANALYSIS_PROMPT_KEY:
            return
        if variables or "{" in prompt_text or "}" in prompt_text:
            raise exceptions.invalid_ai_config("document_analysis prompt must be variable-free")

    def _normalize_sensitive_rule_matcher(
        self,
        *,
        rule_type: str,
        pattern: str | None,
        keywords: Sequence[str],
    ) -> tuple[str, str | None, list[str]]:
        if rule_type == "keyword":
            normalized_keywords = unique_ordered([keyword.strip() for keyword in keywords])
            if not normalized_keywords:
                raise exceptions.invalid_ai_config("keyword rule requires keywords")
            return rule_type, None, normalized_keywords
        if rule_type == "regex":
            cleaned_pattern = clean_optional_text(pattern)
            if cleaned_pattern is None:
                raise exceptions.invalid_ai_config("regex rule requires pattern")
            try:
                re.compile(cleaned_pattern)
            except re.error as exc:
                raise exceptions.invalid_ai_config("invalid regex pattern") from exc
            return rule_type, cleaned_pattern, []
        raise exceptions.invalid_ai_config("invalid sensitive rule type")

    def _normalize_risk_level(self, value: str) -> str:
        if value not in SENSITIVE_RISK_LEVELS:
            raise exceptions.invalid_ai_config("invalid sensitive risk level")
        return value

    def _normalize_rule_action(self, value: str) -> str:
        if value not in SENSITIVE_RULE_ACTIONS:
            raise exceptions.invalid_ai_config("invalid sensitive rule action")
        return value

    def _sensitive_rule_audit_metadata(
        self,
        rule: SensitiveRule,
        *,
        changed_fields: list[str],
    ) -> dict[str, object]:
        return {
            "rule_type": rule.rule_type,
            "risk_level": rule.risk_level,
            "action": rule.action,
            "enabled": rule.enabled,
            "changed_fields": changed_fields,
        }

    async def _record_ai_config_change(
        self,
        *,
        current_user: AuthUserRecord,
        action: str,
        target_type: str,
        target_id: uuid.UUID,
        context: RequestContext,
        metadata_json: dict[str, object],
    ) -> None:
        await self._record_admin_audit(
            current_user=current_user,
            action=action,
            target_type=target_type,
            target_id=target_id,
            context=context,
            metadata_json=metadata_json,
        )
        await OutboxRepository(self._session).append(
            event_type=events.AI_CONFIG_CHANGED,
            aggregate_type=target_type,
            aggregate_id=str(target_id),
            payload={
                "action": action,
                "target_type": target_type,
                "target_id": str(target_id),
                "changed_fields": metadata_json.get("changed_fields", []),
            },
        )

    async def _ensure_defaults(self) -> None:
        existing_features = {
            feature.feature_name for feature in await self._repository.list_feature_configs()
        }
        for feature_definition in self._default_feature_definitions():
            if feature_definition.key in existing_features:
                continue
            await self._repository.add_feature_config(
                AiFeatureConfig(
                    feature_name=feature_definition.key,
                    enabled=feature_definition.default_enabled,
                    config_json={
                        "name": feature_definition.name,
                        "description": feature_definition.description,
                    },
                )
            )

        existing_prompts = {
            template.template_key for template in await self._repository.list_prompt_templates()
        }
        for prompt_definition in _default_prompt_definitions():
            if prompt_definition.template_key in existing_prompts:
                continue
            await self._repository.add_prompt_template(
                PromptTemplate(
                    template_key=prompt_definition.template_key,
                    name=prompt_definition.name,
                    description=prompt_definition.description,
                    prompt_text=prompt_definition.prompt_text,
                    variables=prompt_definition.variables,
                    enabled=True,
                    is_default=True,
                    version=1,
                )
            )

        existing_rule_names = {rule.name for rule in await self._repository.list_sensitive_rules()}
        for rule_definition in _default_sensitive_rule_definitions():
            if rule_definition.name in existing_rule_names:
                continue
            await self._repository.add_sensitive_rule(
                SensitiveRule(
                    name=rule_definition.name,
                    rule_type=rule_definition.rule_type,
                    pattern=rule_definition.pattern,
                    keywords=rule_definition.keywords or [],
                    risk_level=rule_definition.risk_level,
                    action=rule_definition.action,
                    enabled=True,
                )
            )
        if not await self._repository.list_providers():
            provider_type = self._settings.llm_provider.strip().lower() or "disabled"
            is_internal = provider_type in {
                "local_openai_compatible",
                "ollama",
                "vllm",
                "lmstudio",
                "mock",
            }
            self._validate_provider_type(provider_type, is_internal=is_internal)
            chat_model = clean_optional_text(self._settings.llm_model)
            if provider_type == "mock" and chat_model is None:
                chat_model = "mock-analysis-v1"
            base_url: str | None = None
            if provider_type not in {"disabled", "mock"}:
                try:
                    base_url = normalize_provider_base_url(self._settings.llm_base_url)
                except ValueError as exc:
                    raise exceptions.invalid_provider_config() from exc
                if base_url is None or chat_model is None:
                    raise exceptions.invalid_provider_config()
                try:
                    chat_model = validate_model_name(chat_model)
                except ValueError as exc:
                    raise exceptions.invalid_provider_config() from exc

            await self._repository.add_provider(
                AiProvider(
                    name="默认模型供应商",
                    provider_type=provider_type,
                    base_url=base_url,
                    api_key_encrypted=self._encrypt_api_key(self._settings.llm_api_key),
                    chat_model=chat_model,
                    is_internal=is_internal,
                    enabled=provider_type != "disabled",
                    priority=100,
                    timeout_seconds=self._settings.ai_request_timeout,
                    max_retry_count=self._settings.ai_max_retry_count,
                    input_price_microunits_per_million_tokens=0,
                    output_price_microunits_per_million_tokens=0,
                    pricing_currency="USD",
                    pricing_configured=False,
                )
            )
        await self._session.flush()

    async def _feature_map(self) -> dict[str, AiFeatureConfig]:
        await self._ensure_defaults()
        return {
            feature.feature_name: feature
            for feature in await self._repository.list_feature_configs()
        }

    def _default_feature_definitions(self) -> list[FeatureDefinition]:
        return [
            FeatureDefinition(
                "ai_analysis",
                "AI总开关",
                "控制上传后是否创建 AI 分析任务",
                self._settings.ai_analysis_enabled,
            ),
            FeatureDefinition(
                "allow_external_llm",
                "是否允许外部模型",
                "控制是否允许调用企业外部模型服务",
                self._settings.allow_external_llm,
            ),
            FeatureDefinition(
                "allow_sync_when_analysis_failed",
                "分析失败后是否允许同步",
                "AI 分析失败时是否允许继续审核与同步",
                self._settings.ai_allow_sync_when_analysis_failed,
            ),
            FeatureDefinition(
                "summary", "文档摘要", "生成文档内容摘要", self._settings.enable_summary
            ),
            FeatureDefinition(
                "auto_category",
                "自动分类",
                "基于分类关键词生成分类建议",
                self._settings.enable_auto_category,
            ),
            FeatureDefinition(
                "tag_generation",
                "自动标签",
                "提取可用于检索的标签建议",
                self._settings.enable_tag_generation,
            ),
            FeatureDefinition(
                "sensitive_detection",
                "敏感检测",
                "检测密钥、证件号等敏感信息",
                self._settings.enable_sensitive_detection,
            ),
            FeatureDefinition(
                "quality_score",
                "质量评分",
                "为文档质量评分预留的功能开关",
                self._settings.enable_quality_score,
            ),
            FeatureDefinition(
                "table_extraction",
                "表格结构识别",
                "提取 Excel、Word、PDF 中的表格结构",
                bool(getattr(self._settings, "enable_table_extraction", False)),
            ),
            FeatureDefinition(
                "similarity_detection",
                "相似检测",
                "为近重复文档检测预留的功能开关",
                self._settings.enable_similarity_detection,
            ),
        ]

    def _provider_config_fingerprint(
        self,
        provider: AiProvider,
        *,
        db_external_enabled: bool,
    ) -> str:
        payload = {
            "provider_id": str(provider.id),
            "provider_type": provider.provider_type,
            "base_url": provider.base_url,
            "api_key_encrypted": provider.api_key_encrypted,
            "chat_model": provider.chat_model,
            "is_internal": provider.is_internal,
            "enabled": provider.enabled,
            "timeout_seconds": provider.timeout_seconds,
            "db_external_enabled": db_external_enabled,
            "environment_external_enabled": self._settings.allow_external_llm,
            "allowed_base_urls": sorted(
                normalized_llm_allowed_base_urls(self._settings.llm_allowed_base_urls)
            ),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _provider_test_snapshot(
        self,
        provider: AiProvider,
        *,
        db_external_enabled: bool,
    ) -> ProviderTestSnapshot:
        credential_invalid = False
        try:
            api_key = self._decrypt_provider_key(provider)
        except (InvalidToken, ValueError):
            credential_invalid = True
            api_key = None
        if credential_invalid:
            raise exceptions.invalid_provider_config("provider credential is unavailable")
        return ProviderTestSnapshot(
            provider_id=provider.id,
            updated_at=provider.updated_at,
            fingerprint=self._provider_config_fingerprint(
                provider,
                db_external_enabled=db_external_enabled,
            ),
            provider_type=provider.provider_type,
            base_url=provider.base_url,
            api_key=api_key,
            chat_model=provider.chat_model,
            is_internal=provider.is_internal,
            timeout_seconds=provider.timeout_seconds,
            effective_allow_external=(self._settings.allow_external_llm and db_external_enabled),
        )

    async def _test_provider_connectivity(
        self,
        snapshot: ProviderTestSnapshot,
    ) -> LLMTestResult:
        if self._session.in_transaction():
            return LLMTestResult(
                status="failed",
                latency_ms=None,
                message="database transaction was not released",
            )
        if snapshot.provider_type == "mock":
            if _is_protected_app_env(self._settings.app_env):
                return LLMTestResult(
                    status="failed",
                    latency_ms=None,
                    message="mock provider is disabled in protected environments",
                )
            return LLMTestResult(status="success", latency_ms=0, message="ok")
        if snapshot.provider_type == "disabled":
            return LLMTestResult(status="failed", latency_ms=None, message="provider disabled")
        try:
            base_url = normalize_provider_base_url(snapshot.base_url)
        except ValueError:
            return LLMTestResult(
                status="failed",
                latency_ms=None,
                message="invalid provider base URL",
            )
        test_model = clean_optional_text(snapshot.chat_model)
        if base_url is None or test_model is None:
            return LLMTestResult(
                status="failed",
                latency_ms=None,
                message="base_url and model are required",
            )
        client = OpenAICompatibleProvider(
            base_url=base_url,
            api_key=snapshot.api_key,
            model=test_model,
            timeout_seconds=snapshot.timeout_seconds,
            raw_allowed_base_urls=self._settings.llm_allowed_base_urls,
            allow_external=snapshot.effective_allow_external,
            is_internal=snapshot.is_internal,
        )
        return await client.test_connection()

    def _validate_provider_type(self, provider_type: str, *, is_internal: bool) -> None:
        allowed = {
            "openai_compatible",
            "local_openai_compatible",
            "ollama",
            "vllm",
            "lmstudio",
            "custom",
            "mock",
            "disabled",
        }
        if provider_type not in allowed:
            raise exceptions.invalid_provider_config()
        if provider_type == "mock" and _is_protected_app_env(self._settings.app_env):
            raise exceptions.invalid_provider_config(
                "mock provider is disabled in protected environments"
            )
        if provider_type == "openai_compatible" and not is_internal:
            return

    def _validate_provider_activation(
        self,
        *,
        provider_type: str,
        base_url: str | None,
        chat_model: str | None,
        enabled: bool,
    ) -> None:
        invalid_model = False
        if chat_model is not None:
            try:
                validate_model_name(chat_model)
            except ValueError:
                invalid_model = True
        if invalid_model:
            raise exceptions.invalid_provider_config("invalid chat model")
        if base_url is not None and not llm_base_url_is_allowed(
            base_url,
            self._settings.llm_allowed_base_urls,
        ):
            raise exceptions.invalid_provider_config(
                "provider base URL is not in LLM_ALLOWED_BASE_URLS"
            )
        if not enabled or provider_type == "disabled":
            return
        if chat_model is None:
            raise exceptions.invalid_provider_config("enabled provider requires chat_model")
        if provider_type != "mock" and base_url is None:
            raise exceptions.invalid_provider_config("enabled provider requires base_url")

    def _encrypt_api_key(self, api_key: str | None) -> str | None:
        cleaned = clean_optional_text(api_key)
        if cleaned is None:
            return None
        return encrypt_api_key(cleaned, self._settings.encryption_key)

    def _decrypt_provider_key(self, provider: AiProvider) -> str | None:
        if provider.api_key_encrypted is None:
            return None
        return decrypt_api_key(provider.api_key_encrypted, self._settings.encryption_key)

    def _masked_provider_key(self, provider: AiProvider) -> str | None:
        secret = self._decrypt_provider_key(provider)
        return mask_secret(secret)

    async def _record_admin_audit(
        self,
        *,
        current_user: AuthUserRecord,
        action: str,
        target_type: str,
        target_id: uuid.UUID,
        context: RequestContext,
        metadata_json: dict[str, object] | None = None,
    ) -> None:
        await record_admin_audit_log(
            self._session,
            actor_id=current_user.id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            ip_address=context.ip_address,
            user_agent=context.user_agent,
            metadata_json=metadata_json,
        )

    def _require_admin(self, current_user: AuthUserRecord) -> None:
        if current_user.role not in ADMIN_ROLES:
            raise exceptions.permission_denied()

    def _require_system_admin(self, current_user: AuthUserRecord) -> None:
        if current_user.role != SYSTEM_ADMIN_ROLE:
            raise exceptions.permission_denied()


class AiAnalysisService:
    ANALYSIS_LEASE_SECONDS = 900

    def __init__(
        self,
        *,
        session: AsyncSession,
        repository: AiRepository,
        settings: Settings,
    ) -> None:
        self._session = session
        self._repository = repository
        self._settings = settings

    async def run_file_analysis(
        self,
        file_id: uuid.UUID,
        *,
        storage: AiObjectStorage,
        delivery_token: str | None = None,
    ) -> uuid.UUID:
        lease_token = (delivery_token or uuid.uuid4().hex)[:64]
        config_service = AiConfigService(
            session=self._session,
            repository=self._repository,
            settings=self._settings,
        )
        file = await self._get_file_or_raise(file_id)
        if not file.ai_analysis_enabled_at_upload:
            raise exceptions.AiAnalysisPreconditionError("AI disabled when file was uploaded")
        if not self._settings.ai_analysis_enabled:
            await self._continue_auto_submit_without_analysis(file)
            raise exceptions.AiAnalysisPreconditionError("AI analysis disabled")
        await config_service._ensure_defaults()
        features = await config_service._feature_map()
        # The DB switch controls new uploads/queueing. Once a task is queued, the
        # upload-time snapshot is authoritative so a hot toggle cannot strand it.
        idempotent_analysis = await self._get_analysis_for_idempotent_delivery(
            file,
            lease_token=lease_token,
        )
        if idempotent_analysis is not None:
            await self._session.commit()
            return idempotent_analysis.id

        llm_features = AnalysisFeatureSelection(
            summary=features["summary"].enabled,
            category=features["auto_category"].enabled,
            tags=features["tag_generation"].enabled,
            sensitive=features["sensitive_detection"].enabled,
        )
        provider = (
            await self._repository.get_enabled_chat_provider()
            if llm_features.requires_llm
            else None
        )
        llm_will_run = llm_features.requires_llm and provider is not None
        deterministic_features_enabled = any(
            features[key].enabled
            for key in (
                "sensitive_detection",
                "quality_score",
                "table_extraction",
                "similarity_detection",
            )
        ) or (llm_features.requires_llm and not llm_will_run)
        engine_type = resolve_analysis_engine_type(
            llm_enabled=llm_will_run,
            deterministic_enabled=deterministic_features_enabled,
        )

        analysis = await self._start_analysis(
            engine_type=engine_type,
            file=file,
            provider=provider,
            lease_token=lease_token,
        )
        lease_analysis_id = analysis.id
        lease_started_at = analysis.started_at
        if lease_started_at is None:
            raise exceptions.AiAnalysisPreconditionError("analysis lease missing")
        # Persist the lease before the state transition and external storage read. If the
        # delivery is stale (for example the file already entered review), the failure path
        # can still fence and record this exact execution instead of rolling the lease away.
        await self._session.commit()
        try:
            file = await self._transition_file(file, "extracting_text")
            await self._session.commit()
            try:
                raw_content = await storage.get_object(
                    bucket=file.bucket, object_key=file.object_key
                )
            except STORAGE_TRANSIENT_ERRORS as exc:
                if not is_transient_storage_error(exc):
                    # 永久性存储错误: 交给外层 except Exception 兜底
                    # (外层负责 rollback 并以异常类型名标记 analysis_failed)。
                    raise
                await self._release_analysis_for_retry(
                    file_id=file_id,
                    analysis_id=lease_analysis_id,
                    lease_token=lease_token,
                    lease_started_at=lease_started_at,
                )
                raise exceptions.AiAnalysisTransientError(
                    "object storage unavailable",
                    failure_category="storage_unavailable",
                    max_retries=3,
                    retry_budget="storage",
                ) from exc
            parse_max_pages, parse_max_chars = await resolve_parse_limits()
            extracted_text = extract_text(
                raw_content,
                extension=file.extension,
                max_pages=parse_max_pages,
                max_chars=parse_max_chars,
            )
            tables: list[dict[str, object]] = []
            if features["table_extraction"].enabled:
                tables = extract_tables_from_bytes(
                    raw_content,
                    file.extension,
                    max_pages=parse_max_pages,
                )
                extracted_text = append_tables_markdown(
                    extracted_text,
                    tables,
                    max_chars=parse_max_chars,
                )
            file = await self._get_file_or_raise(file_id)
            current_analysis = await self._repository.get_document_analysis_for_update(file_id)
            if (
                current_analysis is None
                or current_analysis.status != "running"
                or current_analysis.lease_token != lease_token
                or current_analysis.started_at != lease_started_at
            ):
                await self._session.rollback()
                return lease_analysis_id
            analysis = current_analysis
            file = await self._transition_file(file, "analysis_queued")
            await self._append_analysis_event(
                event_type=events.AI_TEXT_EXTRACTED,
                file=file,
                payload={"text_length": len(extracted_text), "table_count": len(tables)},
            )
            file = await self._transition_file(file, "analyzing")

            categories = await self._repository.list_categories()
            llm_result = ValidatedLLMAnalysis(
                summary=None,
                category_id=None,
                tags=[],
                sensitive_risk_level="none",
            )
            if llm_features.requires_llm and provider is not None:
                llm_result, file, analysis = await self._run_llm_analysis(
                    file=file,
                    analysis=analysis,
                    provider=provider,
                    features=llm_features,
                    extracted_text=extracted_text,
                    categories=categories,
                    allow_external_llm=features["allow_external_llm"].enabled,
                )
            elif llm_features.requires_llm:
                category_suggestion = (
                    suggest_category(extracted_text, categories)
                    if llm_features.category
                    else CategorySuggestion(category_id=None, category_name=None)
                )
                llm_result = ValidatedLLMAnalysis(
                    summary=(
                        generate_summary(extracted_text, file=file)
                        if llm_features.summary
                        else None
                    ),
                    category_id=category_suggestion.category_id,
                    tags=(
                        generate_tags(extracted_text, categories=categories)
                        if llm_features.tags
                        else []
                    ),
                    sensitive_risk_level="none",
                )
            sensitive_hits: list[dict[str, object]] = []
            rule_risk_level = "none"
            if features["sensitive_detection"].enabled:
                rules = await self._repository.list_sensitive_rules(enabled_only=True)
                sensitive_hits = detect_sensitive_hits(extracted_text, rules)
                rule_risk_level = highest_risk_level(hit["risk_level"] for hit in sensitive_hits)
                await self._repository.increment_sensitive_rule_hits(
                    [uuid.UUID(str(hit["rule_id"])) for hit in sensitive_hits]
                )
            risk_level = highest_risk_level([rule_risk_level, llm_result.sensitive_risk_level])
            quality_result = None
            if features["quality_score"].enabled:
                quality_weights = await resolve_quality_weights(
                    features["quality_score"].config_json
                )
                quality_result = score_document_quality(
                    extracted_text,
                    weights=quality_weights,
                )

            similar_file_ids: list[str] = []
            if features["similarity_detection"].enabled:
                if extracted_text.strip():
                    fingerprint = compute_simhash(extracted_text)
                    bands = simhash_bands(fingerprint)
                    file.simhash = fingerprint
                    file.simhash_band_0 = bands[0]
                    file.simhash_band_1 = bands[1]
                    file.simhash_band_2 = bands[2]
                    file.simhash_band_3 = bands[3]
                    threshold = await resolve_similarity_threshold(
                        features["similarity_detection"].config_json
                    )
                    candidates = await self._repository.list_simhash_candidates(
                        file_id=file.id,
                        bands=bands,
                    )
                    similar_file_ids = [
                        str(candidate.id)
                        for candidate in candidates
                        if candidate.simhash is not None
                        and hamming_distance(fingerprint, candidate.simhash) <= threshold
                    ]
                else:
                    file.simhash = None
                    file.simhash_band_0 = None
                    file.simhash_band_1 = None
                    file.simhash_band_2 = None
                    file.simhash_band_3 = None

            analysis_target_status = (
                "sensitive_review_required"
                if risk_level == "critical" or requires_sensitive_review(sensitive_hits)
                else "analyzed"
            )
            category_by_id = {category.id: category for category in categories}
            selected_category = (
                category_by_id.get(llm_result.category_id)
                if llm_result.category_id is not None
                else None
            )
            file.tags = merge_tags(file.tags, llm_result.tags)
            file.category_id = llm_result.category_id or file.category_id
            file = await self._transition_file(file, analysis_target_status)
            analysis.status = "succeeded"
            analysis.engine_type = engine_type
            analysis.extracted_text = truncate_text(extracted_text, parse_max_chars)
            analysis.summary = llm_result.summary
            analysis.suggested_category_id = llm_result.category_id
            analysis.suggested_category_name = (
                selected_category.name if selected_category is not None else None
            )
            analysis.suggested_tags = llm_result.tags
            analysis.sensitive_risk_level = risk_level
            analysis.sensitive_hits = sensitive_hits
            analysis.tables_json = tables
            analysis.table_count = len(tables)
            analysis.quality_score = quality_result.score if quality_result is not None else None
            analysis.quality_detail = quality_result.detail if quality_result is not None else {}
            analysis.similar_file_ids = similar_file_ids
            analysis.error_message = None
            analysis.failure_category = None
            analysis.lease_token = None
            analysis.finished_at = datetime.now(UTC)
            auto_submit_requested = self._auto_submit_requested(file)
            auto_submitted = False
            auto_submit_blocked_reason: str | None = None
            if auto_submit_requested:
                if risk_level == "critical":
                    auto_submit_blocked_reason = "critical_sensitive_content"
                else:
                    file.submitted_at, file.review_due_at = await review_submission_times()
                    file.review_version += 1
                    file = await self._transition_file(file, "pending_review")
                    auto_submitted = True

            await self._append_analysis_event(
                event_type=events.AI_FILE_ANALYZED,
                file=file,
                payload={
                    "analysis_id": str(analysis.id),
                    "analysis_status": analysis.status,
                    "sensitive_risk_level": risk_level,
                    "auto_submit_requested": auto_submit_requested,
                    "auto_submitted": auto_submitted,
                    "auto_submit_blocked_reason": auto_submit_blocked_reason,
                },
            )
            if risk_level != "none":
                await self._append_analysis_event(
                    event_type=events.AI_SENSITIVE_DETECTED,
                    file=file,
                    payload={
                        "analysis_id": str(analysis.id),
                        "sensitive_risk_level": risk_level,
                        "hit_count": len(sensitive_hits),
                    },
                )
            if auto_submitted:
                await self._append_review_submitted_event(
                    file=file,
                    previous_status=analysis_target_status,
                    analysis_failed=False,
                )
            await self._session.commit()
            return analysis.id
        except AnalysisLeaseLostError:
            await self._session.rollback()
            return lease_analysis_id
        except exceptions.AiAnalysisTransientError:
            raise
        except LLMProviderError as exc:
            if exc.retryable:
                await self._release_llm_analysis_for_retry(
                    file_id=file_id,
                    analysis_id=lease_analysis_id,
                    lease_token=lease_token,
                    lease_started_at=lease_started_at,
                    failure_category=exc.category,
                )
                raise exceptions.AiAnalysisTransientError(
                    "llm provider temporarily unavailable",
                    failure_category=exc.category,
                    max_retries=provider.max_retry_count if provider is not None else 0,
                    retry_budget="provider",
                ) from exc
            await self._mark_analysis_failed(
                file_id=file_id,
                error_message=provider_failure_message(exc.category),
                error_code=exc.category,
                failure_category=exc.category,
                expected_delivery_token=lease_token,
                expected_started_at=lease_started_at,
                verify_started_at=True,
            )
            return analysis.id
        except LLMOutputValidationError:
            await self._mark_analysis_failed(
                file_id=file_id,
                error_message="模型输出未通过安全格式校验",
                error_code=events.AiAnalysisFailureCode.INVALID_OUTPUT,
                failure_category="invalid_output",
                expected_delivery_token=lease_token,
                expected_started_at=lease_started_at,
                verify_started_at=True,
            )
            return analysis.id
        except LLMAnalysisConfigurationError:
            await self._mark_analysis_failed(
                file_id=file_id,
                error_message="AI 分析配置不可用",
                error_code=events.AiAnalysisFailureCode.CONFIGURATION_ERROR,
                failure_category="configuration_error",
                expected_delivery_token=lease_token,
                expected_started_at=lease_started_at,
                verify_started_at=True,
            )
            return analysis.id
        except exceptions.DocumentParseError as exc:
            await self._session.rollback()
            await self._mark_analysis_failed(
                file_id=file_id,
                error_message=str(exc),
                error_code=events.AiAnalysisFailureCode.INVALID_OUTPUT,
                expected_delivery_token=lease_token,
                expected_started_at=lease_started_at,
                verify_started_at=True,
            )
            return analysis.id
        except Exception as exc:
            await self._session.rollback()
            error_type = type(exc).__name__
            await self._mark_analysis_failed(
                file_id=file_id,
                error_message=error_type,
                error_code=events.AiAnalysisFailureCode.INTERNAL,
                expected_delivery_token=lease_token,
                expected_started_at=lease_started_at,
                verify_started_at=True,
            )
            return analysis.id

    async def recover_hard_disabled_intermediate_file(self, file_id: uuid.UUID) -> bool:
        if self._settings.ai_analysis_enabled:
            raise exceptions.AiAnalysisPreconditionError("AI environment switch is enabled")
        file = await self._get_file_or_raise(file_id)
        if file.status not in AI_ANALYSIS_IN_PROGRESS_FILE_STATUSES:
            await self._session.rollback()
            return False

        previous_status = file.status
        if self._auto_submit_requested(file):
            file.submitted_at, file.review_due_at = await review_submission_times()
            file.review_version += 1
            file = await self._transition_file(file, "pending_review")
            await self._append_review_submitted_event(
                file=file,
                previous_status=previous_status,
                analysis_failed=False,
                analysis_skipped_reason="environment_disabled_recovery",
            )
        else:
            file.submitted_at = None
            file.review_due_at = None
            file = await self._transition_file(file, "uploaded")

        analysis = await self._repository.get_document_analysis_for_update(file_id)
        if analysis is not None and analysis.status == "running":
            analysis.status = "failed"
            analysis.error_message = AI_HARD_DISABLED_MESSAGE
            analysis.lease_token = None
            analysis.finished_at = datetime.now(UTC)
        await self._session.commit()
        return True

    async def _continue_auto_submit_without_analysis(self, file: AiFileRecord) -> bool:
        if not self._auto_submit_requested(file) or file.status != "uploaded":
            return False
        previous_status = file.status
        file.submitted_at, file.review_due_at = await review_submission_times()
        file.review_version += 1
        file = await self._transition_file(file, "pending_review")
        await self._append_review_submitted_event(
            file=file,
            previous_status=previous_status,
            analysis_failed=False,
            analysis_skipped_reason="environment_disabled",
        )
        await self._session.commit()
        return True

    async def _run_llm_analysis(
        self,
        *,
        file: AiFileRecord,
        analysis: DocumentAnalysis,
        provider: AiProvider,
        features: AnalysisFeatureSelection,
        extracted_text: str,
        categories: list[AiCategoryRecord],
        allow_external_llm: bool,
    ) -> tuple[ValidatedLLMAnalysis, AiFileRecord, DocumentAnalysis]:
        prompt_template = await self._repository.get_prompt_template_by_key(ANALYSIS_PROMPT_KEY)
        if prompt_template is None or not prompt_template.enabled or prompt_template.variables:
            raise LLMAnalysisConfigurationError("analysis prompt is unavailable")
        try:
            built_prompt = build_analysis_prompt(
                template_text=prompt_template.prompt_text,
                text=extracted_text,
                categories=categories if features.category else [],
                max_input_tokens=provider.max_input_tokens,
            )
        except ValueError as exc:
            raise LLMAnalysisConfigurationError("analysis prompt is invalid") from exc
        llm_provider = self._build_llm_provider(
            provider,
            allow_external_llm=allow_external_llm,
        )
        analysis.prompt_template_id = prompt_template.id
        analysis.prompt_template_key = prompt_template.template_key
        analysis.prompt_version = prompt_template.version
        output_limit = provider.max_output_tokens or 1_024
        analysis_id = analysis.id
        lease_token = analysis.lease_token
        lease_started_at = analysis.started_at
        if lease_token is None or lease_started_at is None:
            raise LLMAnalysisConfigurationError("analysis lease is unavailable")
        # Persist the analyzing state and release all DB locks before provider I/O.
        await self._session.commit()

        for call_index in range(2):
            call_prompt = (
                built_prompt.text if call_index == 0 else f"{built_prompt.text}{LLM_REPAIR_SUFFIX}"
            )
            input_provenance = build_input_provenance(
                user_prompt=call_prompt,
                category_count=built_prompt.category_count,
                input_truncated=built_prompt.input_truncated,
            )
            try:
                completion = await llm_provider.complete(
                    call_prompt,
                    model=provider.chat_model,
                    temperature=provider.temperature,
                    top_p=provider.top_p,
                    max_output_tokens=output_limit,
                    system_prompt=LLM_ANALYSIS_SYSTEM_PROMPT,
                    json_mode=True,
                )
            except LLMProviderError as exc:
                file, analysis = await self._reacquire_analysis_lease(
                    file_id=file.id,
                    analysis_id=analysis_id,
                    lease_token=lease_token,
                    lease_started_at=lease_started_at,
                )
                await self._record_llm_usage(
                    file=file,
                    analysis=analysis,
                    provider=provider,
                    prompt_template=prompt_template,
                    completion=None,
                    input_provenance=input_provenance,
                    latency_ms=exc.latency_ms,
                    status="failed",
                    failure_category=exc.category,
                )
                raise
            try:
                result = parse_analysis_output(
                    completion.content,
                    allowed_category_ids=built_prompt.allowed_category_ids,
                    features=features,
                )
            except LLMOutputValidationError:
                file, analysis = await self._reacquire_analysis_lease(
                    file_id=file.id,
                    analysis_id=analysis_id,
                    lease_token=lease_token,
                    lease_started_at=lease_started_at,
                )
                await self._record_llm_usage(
                    file=file,
                    analysis=analysis,
                    provider=provider,
                    prompt_template=prompt_template,
                    completion=completion,
                    input_provenance=input_provenance,
                    latency_ms=completion.latency_ms,
                    status="failed",
                    failure_category="invalid_output",
                )
                if call_index == 0:
                    await self._session.commit()
                    continue
                raise
            file, analysis = await self._reacquire_analysis_lease(
                file_id=file.id,
                analysis_id=analysis_id,
                lease_token=lease_token,
                lease_started_at=lease_started_at,
            )
            await self._record_llm_usage(
                file=file,
                analysis=analysis,
                provider=provider,
                prompt_template=prompt_template,
                completion=completion,
                latency_ms=completion.latency_ms,
                input_provenance=input_provenance,
                status="success",
                failure_category=None,
            )
            return result, file, analysis
        raise LLMOutputValidationError("invalid_output")

    async def _reacquire_analysis_lease(
        self,
        *,
        file_id: uuid.UUID,
        analysis_id: uuid.UUID,
        lease_token: str,
        lease_started_at: datetime,
    ) -> tuple[AiFileRecord, DocumentAnalysis]:
        file = await self._repository.get_file_for_update(file_id)
        analysis = await self._repository.get_document_analysis_for_update(file_id)
        if (
            file is None
            or analysis is None
            or analysis.id != analysis_id
            or analysis.status != "running"
            or analysis.lease_token != lease_token
            or analysis.started_at != lease_started_at
        ):
            await self._session.rollback()
            raise AnalysisLeaseLostError
        return file, analysis

    def _build_llm_provider(
        self,
        provider: AiProvider,
        *,
        allow_external_llm: bool,
    ) -> BaseLLMProvider:
        model = clean_optional_text(provider.chat_model)
        if provider.provider_type == "mock":
            if _is_protected_app_env(self._settings.app_env):
                raise LLMAnalysisConfigurationError(
                    "mock provider is disabled in protected environments"
                )
            invalid_model = False
            try:
                validated_mock_model = validate_model_name(model or "mock-analysis-v1")
            except ValueError:
                validated_mock_model = ""
                invalid_model = True
            if invalid_model:
                raise LLMAnalysisConfigurationError("provider model is invalid")
            return MockLLMProvider(model=validated_mock_model)

        supported_types = {
            "openai_compatible",
            "local_openai_compatible",
            "ollama",
            "vllm",
            "lmstudio",
            "custom",
        }
        invalid_endpoint = False
        try:
            base_url = normalize_provider_base_url(provider.base_url)
        except ValueError:
            base_url = None
            invalid_endpoint = True
        if invalid_endpoint:
            raise LLMAnalysisConfigurationError("provider endpoint is invalid")
        if provider.provider_type not in supported_types or base_url is None or model is None:
            raise LLMAnalysisConfigurationError("provider endpoint and chat model are required")
        if not llm_base_url_is_allowed(base_url, self._settings.llm_allowed_base_urls):
            raise LLMAnalysisConfigurationError("provider endpoint is not in LLM_ALLOWED_BASE_URLS")

        invalid_model = False
        try:
            model = validate_model_name(model)
        except ValueError:
            invalid_model = True
        if invalid_model:
            raise LLMAnalysisConfigurationError("provider model is invalid")

        credential_invalid = False
        try:
            api_key = (
                decrypt_api_key(
                    provider.api_key_encrypted,
                    self._settings.encryption_key,
                )
                if provider.api_key_encrypted is not None
                else None
            )
        except (InvalidToken, ValueError):
            api_key = None
            credential_invalid = True
        if credential_invalid:
            raise LLMAnalysisConfigurationError("provider credential is unavailable")

        return OpenAICompatibleProvider(
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout_seconds=provider.timeout_seconds,
            raw_allowed_base_urls=self._settings.llm_allowed_base_urls,
            allow_external=self._settings.allow_external_llm and allow_external_llm,
            is_internal=provider.is_internal,
        )

    async def _record_llm_usage(
        self,
        *,
        file: AiFileRecord,
        analysis: DocumentAnalysis,
        provider: AiProvider,
        prompt_template: PromptTemplate,
        completion: LLMCompletion | None,
        input_provenance: LLMInputProvenance,
        latency_ms: int | None,
        status: str,
        failure_category: str | None,
    ) -> None:
        prompt_tokens = completion.usage.prompt_tokens if completion is not None else None
        completion_tokens = completion.usage.completion_tokens if completion is not None else None
        persisted_prompt_tokens = _persistable_usage_counter(prompt_tokens)
        persisted_completion_tokens = _persistable_usage_counter(completion_tokens)
        persisted_latency_ms = _persistable_usage_counter(latency_ms)
        token_usage_is_persistable = (
            prompt_tokens is None or persisted_prompt_tokens is not None
        ) and (completion_tokens is None or persisted_completion_tokens is not None)
        try:
            call_cost_observation = observe_llm_cost(
                pricing_configured=_provider_effective_pricing_configured(provider),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                input_price_microunits_per_million_tokens=(
                    provider.input_price_microunits_per_million_tokens
                ),
                output_price_microunits_per_million_tokens=(
                    provider.output_price_microunits_per_million_tokens
                ),
            )
        except (OverflowError, ValueError):
            call_cost_observation = CostObservation(
                status="unknown_usage",
                estimated_cost_microunits=None,
            )
        if not token_usage_is_persistable:
            call_cost_observation = CostObservation(
                status="unknown_usage",
                estimated_cost_microunits=None,
            )
        aggregate_observation = aggregate_cost_observation(
            call_observation=call_cost_observation,
            aggregate_currency=analysis.cost_currency,
            call_currency=provider.pricing_currency,
        )
        call_estimated_cost = call_cost_observation.estimated_cost_microunits
        aggregate_estimated_cost = aggregate_observation.estimated_cost_microunits
        call_sequence = await self._repository.next_usage_call_sequence(
            analysis_id=analysis.id,
            analysis_attempt=analysis.attempt_number,
        )
        model_name = completion.model if completion is not None else provider.chat_model
        try:
            next_prompt_tokens = checked_persisted_sum(
                analysis.prompt_tokens,
                prompt_tokens or 0,
                maximum=MAX_POSTGRES_INTEGER,
            )
            next_completion_tokens = checked_persisted_sum(
                analysis.completion_tokens,
                completion_tokens or 0,
                maximum=MAX_POSTGRES_INTEGER,
            )
            next_latency_ms = checked_persisted_sum(
                analysis.latency_ms,
                latency_ms or 0,
                maximum=MAX_POSTGRES_INTEGER,
            )
            current_cost_status = cast(CostStatus, analysis.cost_status)
            next_cost_status = merge_cost_status(current_cost_status, aggregate_observation.status)
            if next_cost_status == "known":
                if aggregate_estimated_cost is None:
                    raise ValueError("known aggregate cost cannot be null")
                next_cost = checked_persisted_sum(
                    analysis.estimated_cost_microunits,
                    aggregate_estimated_cost,
                    maximum=MAX_POSTGRES_BIGINT,
                )
            else:
                next_cost = 0
            usage_overflow = False
        except (OverflowError, ValueError):
            usage_overflow = True
        await self._repository.add_usage_log(
            AiUsageLog(
                provider_id=provider.id,
                file_id=file.id,
                analysis_id=analysis.id,
                feature_name=ANALYSIS_PROMPT_KEY,
                provider_name=provider.name,
                model_name=model_name[:120] if model_name is not None else None,
                prompt_template_id=prompt_template.id,
                prompt_template_key=prompt_template.template_key,
                prompt_version=prompt_template.version,
                analysis_attempt=analysis.attempt_number,
                call_sequence=call_sequence,
                prompt_tokens=persisted_prompt_tokens,
                input_char_count=input_provenance.input_char_count,
                input_sha256=input_provenance.input_sha256,
                category_count=input_provenance.category_count,
                input_truncated=input_provenance.input_truncated,
                completion_tokens=persisted_completion_tokens,
                latency_ms=persisted_latency_ms,
                status="failed" if usage_overflow else status,
                failure_category="usage_overflow" if usage_overflow else failure_category,
                estimated_cost_microunits=(
                    call_estimated_cost
                    if call_cost_observation.status == "known" and call_estimated_cost is not None
                    else 0
                ),
                cost_status=call_cost_observation.status,
                cost_currency=provider.pricing_currency,
                error_message=None,
            )
        )
        analysis.model_name = model_name[:120] if model_name is not None else None
        analysis.input_char_count = input_provenance.input_char_count
        analysis.input_sha256 = input_provenance.input_sha256
        analysis.category_count = input_provenance.category_count
        analysis.input_truncated = input_provenance.input_truncated
        if usage_overflow:
            analysis.estimated_cost_microunits = 0
            analysis.cost_status = "unknown_usage"
            analysis.failure_category = "usage_overflow"
            raise LLMAnalysisConfigurationError("analysis usage aggregate overflow")
        analysis.prompt_tokens = next_prompt_tokens
        analysis.completion_tokens = next_completion_tokens
        analysis.latency_ms = next_latency_ms
        analysis.estimated_cost_microunits = next_cost
        analysis.cost_status = next_cost_status
        analysis.failure_category = failure_category

    async def _start_analysis(
        self,
        *,
        file: AiFileRecord,
        engine_type: str,
        provider: AiProvider | None,
        lease_token: str,
    ) -> DocumentAnalysis:
        analysis = await self._repository.get_document_analysis(file.id)
        started_at = datetime.now(UTC)
        new_attempt = analysis is None or analysis.status != "running"
        if analysis is None:
            analysis = DocumentAnalysis(
                file_id=file.id, estimated_cost_microunits=0, cost_status="known"
            )
            await self._repository.add_document_analysis(analysis)
        elif new_attempt:
            analysis.attempt_number += 1
        if new_attempt:
            analysis.prompt_template_id = None
            analysis.input_char_count = None
            analysis.input_sha256 = None
            analysis.category_count = None
            analysis.input_truncated = None
            analysis.prompt_template_key = None
            analysis.prompt_version = None
            analysis.prompt_tokens = 0
            analysis.completion_tokens = 0
            analysis.latency_ms = 0
            analysis.estimated_cost_microunits = 0
            analysis.cost_status = "known"
            analysis.summary = None
            analysis.cost_currency = provider.pricing_currency if provider is not None else "USD"
            analysis.suggested_category_id = None
            analysis.suggested_category_name = None
            analysis.suggested_tags = []
            analysis.sensitive_risk_level = "none"
            analysis.sensitive_hits = []
            analysis.tables_json = []
            analysis.table_count = 0
            analysis.quality_score = None
            analysis.quality_detail = {}
            analysis.similar_file_ids = []
        analysis.provider_id = provider.id if provider is not None else None
        analysis.provider_name = provider.name if provider is not None else None
        analysis.model_name = None
        analysis.engine_type = engine_type
        analysis.status = "running"
        analysis.error_message = None
        analysis.failure_category = None
        analysis.lease_token = lease_token
        analysis.started_at = started_at
        analysis.finished_at = None
        file.ai_config_snapshot = {
            **(file.ai_config_snapshot or {}),
            **self._analysis_snapshot(provider=provider),
        }
        await self._repository.update_file_analysis_state(file)
        return analysis

    async def _get_analysis_for_idempotent_delivery(
        self,
        file: AiFileRecord,
        *,
        lease_token: str,
    ) -> DocumentAnalysis | None:
        analysis = await self._repository.get_document_analysis_for_update(file.id)
        if analysis is None:
            return None
        if analysis.status == "running":
            stale_before = datetime.now(UTC) - timedelta(seconds=self.ANALYSIS_LEASE_SECONDS)
            if analysis.started_at is None and analysis.lease_token == lease_token:
                # 同一 Celery task.retry 保留 task id; 仅原投递可从 retry-wait 重新获取租约。
                return None
            lease_freshness = analysis.started_at or analysis.updated_at
            if lease_freshness is not None and lease_freshness > stale_before:
                raise exceptions.AiAnalysisAlreadyRunningError(
                    "analysis delivery is already running"
                )
            if file.status in AI_ANALYSIS_IN_PROGRESS_FILE_STATUSES:
                file.status = DocumentStateMachine.transition(file.status, "analysis_failed")
                await self._repository.update_file_analysis_state(file)
            analysis.status = "failed"
            analysis.failure_category = "stale_lease"
            analysis.finished_at = datetime.now(UTC)
            analysis.lease_token = None
            return None
        if analysis.status == "succeeded" and file.status in AI_ANALYSIS_SUCCEEDED_FILE_STATUSES:
            return analysis
        return None

    async def mark_analysis_failed(
        self,
        *,
        file_id: uuid.UUID,
        error_message: str,
        error_code: events.AiAnalysisFailureCode | str = events.AiAnalysisFailureCode.INTERNAL,
        failure_category: str | None = None,
        expected_delivery_token: str | None = None,
        require_retry_wait: bool = False,
    ) -> bool:
        """供 Celery 重试耗尽后调用的公开失败标记入口。"""
        return await self._mark_analysis_failed(
            file_id=file_id,
            error_message=error_message,
            error_code=error_code,
            failure_category=failure_category,
            expected_delivery_token=expected_delivery_token,
            expected_started_at=None,
            verify_started_at=require_retry_wait,
        )

    async def _mark_analysis_failed(
        self,
        *,
        file_id: uuid.UUID,
        error_message: str,
        error_code: events.AiAnalysisFailureCode | str = events.AiAnalysisFailureCode.INTERNAL,
        failure_category: str | None = None,
        expected_delivery_token: str | None = None,
        expected_started_at: datetime | None = None,
        verify_started_at: bool = False,
    ) -> bool:
        file = await self._get_file_or_raise(file_id)
        analysis = await self._repository.get_document_analysis_for_update(file_id)
        if expected_delivery_token is not None and (
            analysis is None
            or analysis.lease_token != expected_delivery_token
            or (verify_started_at and analysis.started_at != expected_started_at)
        ):
            # 旧 worker 的超时/异常晚于新租约到达时必须静默丢弃, 不能覆盖新执行。
            await self._session.rollback()
            return False
        should_publish_failure = analysis is None or analysis.status != "failed"
        if analysis is None:
            analysis = DocumentAnalysis(
                file_id=file_id, estimated_cost_microunits=0, cost_status="known"
            )
            await self._repository.add_document_analysis(analysis)
        try:
            if file.status != "analysis_failed":
                file.status = DocumentStateMachine.transition(file.status, "analysis_failed")
                await self._repository.update_file_analysis_state(file)
        except DocumentStateError:
            pass
        analysis.status = "failed"
        normalized_failure = events.normalize_analysis_failure_code(error_code)
        analysis.error_message = error_message[:MAX_ERROR_MESSAGE_LENGTH]
        analysis.failure_category = failure_category or normalized_failure.value
        analysis.lease_token = None
        analysis.finished_at = datetime.now(UTC)
        auto_submit_requested = self._auto_submit_requested(file)
        allow_submit = await self._allow_submit_when_analysis_failed()
        if auto_submit_requested and allow_submit and file.status == "analysis_failed":
            previous_status = file.status
            file.submitted_at, file.review_due_at = await review_submission_times()
            file.review_version += 1
            file = await self._transition_file(file, "pending_review")
            await self._append_review_submitted_event(
                file=file,
                previous_status=previous_status,
                analysis_failed=True,
            )
        if should_publish_failure:
            await self._append_analysis_event(
                event_type=events.AI_FILE_ANALYSIS_FAILED,
                file=file,
                payload={
                    "analysis_id": str(analysis.id),
                    "analysis_status": analysis.status,
                    "error_code": normalized_failure.value,
                },
            )
        await self._session.commit()
        return True

    async def _release_analysis_for_retry(
        self,
        *,
        file_id: uuid.UUID,
        analysis_id: uuid.UUID,
        lease_token: str,
        lease_started_at: datetime,
    ) -> bool:
        """把瞬态失败租约原子转换为仅原 Celery task 可恢复的 retry-wait。"""
        await self._session.rollback()
        file = await self._repository.get_file_for_update(file_id)
        analysis = await self._repository.get_document_analysis_for_update(file_id)
        if (
            file is None
            or analysis is None
            or analysis.id != analysis_id
            or analysis.status != "running"
            or analysis.lease_token != lease_token
            or analysis.started_at != lease_started_at
        ):
            await self._session.rollback()
            return False
        analysis.started_at = None
        analysis.finished_at = None
        await self._session.commit()
        return True

    async def _release_llm_analysis_for_retry(
        self,
        *,
        file_id: uuid.UUID,
        analysis_id: uuid.UUID,
        lease_token: str,
        lease_started_at: datetime,
        failure_category: str,
    ) -> bool:
        """Persist safe call usage and fence the same Celery delivery for a bounded retry."""
        file = await self._repository.get_file_for_update(file_id)
        analysis = await self._repository.get_document_analysis_for_update(file_id)
        if (
            file is None
            or analysis is None
            or analysis.id != analysis_id
            or analysis.status != "running"
            or analysis.lease_token != lease_token
            or analysis.started_at != lease_started_at
        ):
            await self._session.rollback()
            return False
        if file.status in AI_ANALYSIS_IN_PROGRESS_FILE_STATUSES:
            file.status = DocumentStateMachine.transition(file.status, "analysis_failed")
            await self._repository.update_file_analysis_state(file)
        analysis.started_at = None
        analysis.finished_at = None
        analysis.failure_category = failure_category
        analysis.error_message = None
        await self._session.commit()
        return True

    async def _get_file_or_raise(self, file_id: uuid.UUID) -> AiFileRecord:
        file = await self._repository.get_file_for_update(file_id)
        if file is None:
            raise exceptions.AiAnalysisPreconditionError("file not found")
        return file

    async def _transition_file(self, file: AiFileRecord, to_status: str) -> AiFileRecord:
        if file.status != to_status:
            file.status = DocumentStateMachine.transition(file.status, to_status)
        return await self._repository.update_file_analysis_state(file)

    def _analysis_snapshot(self, provider: AiProvider | None) -> dict[str, object]:
        return {
            "ai_analysis_enabled": self._settings.ai_analysis_enabled,
            "allow_external_llm": self._settings.allow_external_llm,
            "provider_id": str(provider.id) if provider is not None else None,
            "provider_type": provider.provider_type if provider is not None else None,
            "chat_model": provider.chat_model if provider is not None else None,
        }

    def _auto_submit_requested(self, file: AiFileRecord) -> bool:
        snapshot = file.ai_config_snapshot or {}
        return snapshot.get("submit_after_upload") is True

    async def _allow_submit_when_analysis_failed(self) -> bool:
        feature = await self._repository.get_feature_config("allow_sync_when_analysis_failed")
        if feature is not None:
            return feature.enabled
        return self._settings.ai_allow_sync_when_analysis_failed

    async def _append_analysis_event(
        self,
        *,
        event_type: str,
        file: AiFileRecord,
        payload: dict[str, object],
    ) -> None:
        await OutboxRepository(self._session).append(
            event_type=event_type,
            aggregate_type="file",
            aggregate_id=str(file.id),
            payload={"file_id": str(file.id), "status": file.status, **payload},
        )

    async def _append_review_submitted_event(
        self,
        *,
        file: AiFileRecord,
        previous_status: str,
        analysis_failed: bool,
        analysis_skipped_reason: str | None = None,
    ) -> None:
        await OutboxRepository(self._session).append(
            event_type=events.REVIEW_FILE_SUBMITTED,
            aggregate_type="file",
            aggregate_id=str(file.id),
            payload={
                "file_id": str(file.id),
                "actor_id": None,
                "actor_type": "system",
                "previous_status": previous_status,
                "status": file.status,
                "review_status": "pending",
                "analysis_failed": analysis_failed,
                "analysis_skipped_reason": analysis_skipped_reason,
                "auto_submitted": True,
                "submitted_at": file.submitted_at.isoformat() if file.submitted_at else None,
                "review_due_at": file.review_due_at.isoformat() if file.review_due_at else None,
            },
        )


def provider_failure_message(category: str) -> str:
    messages = {
        "timeout": "模型服务调用超时",
        "connection_error": "模型服务暂时无法连接",
        "rate_limited": "模型服务请求频率受限",
        "provider_unavailable": "模型服务暂不可用",
        "authentication_failed": "模型服务鉴权失败",
        "request_rejected": "模型服务拒绝了分析请求",
        "invalid_response": "模型服务响应不符合协议",
    }
    return messages.get(category, "模型服务调用失败")


def clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def normalize_provider_base_url(value: str | None) -> str | None:
    cleaned = clean_optional_text(value)
    return None if cleaned is None else normalize_llm_base_url(cleaned)


def safe_provider_base_url(value: str | None) -> str | None:
    try:
        return normalize_provider_base_url(value)
    except ValueError:
        return None


def _provider_effective_pricing_configured(provider: AiProvider) -> bool:
    return pricing_confirmation_is_effective(
        declared=provider.pricing_configured,
        input_price_microunits_per_million_tokens=(
            provider.input_price_microunits_per_million_tokens
        ),
        output_price_microunits_per_million_tokens=(
            provider.output_price_microunits_per_million_tokens
        ),
        pricing_currency=provider.pricing_currency,
        confirmed_input_microunits_per_million=(
            provider.pricing_confirmed_input_microunits_per_million
        ),
        confirmed_output_microunits_per_million=(
            provider.pricing_confirmed_output_microunits_per_million
        ),
        confirmed_currency=provider.pricing_confirmed_currency,
    )


def _pricing_fields_submitted(request: AiProviderUpdateRequest) -> bool:
    fields_set = request.model_fields_set
    return (
        (
            "input_price_microunits_per_million_tokens" in fields_set
            and request.input_price_microunits_per_million_tokens is not None
        )
        or (
            "output_price_microunits_per_million_tokens" in fields_set
            and request.output_price_microunits_per_million_tokens is not None
        )
        or ("pricing_currency" in fields_set and request.pricing_currency is not None)
    )


def _provider_create_audit_fields(provider: AiProvider) -> list[str]:
    fields = [
        "name",
        "provider_type",
        "is_internal",
        "enabled",
        "priority",
        "timeout_seconds",
        "max_retry_count",
        "temperature",
        "input_price_microunits_per_million_tokens",
        "output_price_microunits_per_million_tokens",
        "pricing_currency",
        "pricing_configured",
    ]
    optional_fields = (
        "base_url",
        "chat_model",
        "max_input_tokens",
        "max_output_tokens",
        "top_p",
    )
    fields.extend(
        field_name for field_name in optional_fields if getattr(provider, field_name) is not None
    )
    if provider.api_key_encrypted is not None:
        fields.append("api_key_rotated")
    return sorted(fields)


def _provider_update_audit_fields(request: AiProviderUpdateRequest) -> list[str]:
    nullable_clear_fields = {
        "base_url",
        "chat_model",
        "max_input_tokens",
        "max_output_tokens",
        "top_p",
    }
    fields = {
        field_name
        for field_name in request.model_fields_set
        if field_name not in {"api_key", "clear_api_key"}
        and (getattr(request, field_name) is not None or field_name in nullable_clear_fields)
    }
    if request.clear_api_key:
        fields.add("api_key_cleared")
    elif request.api_key is not None:
        fields.add("api_key_rotated")
    return sorted(fields)


def cast_optional_str(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


def mask_secret(secret: str | None) -> str | None:
    if not secret:
        return None
    prefix = "sk-" if secret.startswith("sk-") else ""
    credential = secret[len(prefix) :]
    if len(credential) < 8:
        return f"{prefix}****"
    suffix = credential[-4:]
    return f"{prefix}****{suffix}"


def truncate_text(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return value[:max_length]


async def resolve_parse_limits() -> tuple[int, int]:
    """读取解析截断上限 (processing.parse_max_pages / parse_max_chars)。

    DB 值优先, 环境无值时 runtime_config 回退种子默认; 非法值回退模块常量,
    保证 parsers 始终拿到正整数上限。
    """
    pages_value = await get_runtime_config("processing.parse_max_pages")
    chars_value = await get_runtime_config("processing.parse_max_chars")
    max_pages = (
        pages_value
        if isinstance(pages_value, int) and not isinstance(pages_value, bool) and pages_value > 0
        else MAX_PDF_PAGES
    )
    max_chars = (
        chars_value
        if isinstance(chars_value, int) and not isinstance(chars_value, bool) and chars_value > 0
        else MAX_EXTRACTED_TEXT_LENGTH
    )
    return max_pages, max_chars


async def resolve_quality_weights(config_json: Mapping[str, object]) -> dict[str, float]:
    runtime_value = await get_runtime_config("ai.quality_weights")
    runtime_weights = _string_key_mapping(runtime_value)
    if runtime_weights is not None:
        return normalize_quality_weights(runtime_weights)
    feature_weights = _string_key_mapping(config_json.get("weights"))
    return normalize_quality_weights(feature_weights)


async def resolve_similarity_threshold(config_json: Mapping[str, object]) -> int:
    runtime_value = await get_runtime_config("ai.similarity_hamming_threshold")
    runtime_threshold = _int_from_object(runtime_value)
    if runtime_threshold is not None:
        return runtime_threshold
    feature_threshold = _int_from_object(config_json.get("hamming_threshold"))
    return feature_threshold if feature_threshold is not None else 3


def _string_key_mapping(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    return {str(key): item for key, item in value.items()}


def _int_from_object(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 0 or value > 64:
        return None
    return value


def extract_text(
    content: bytes,
    *,
    extension: str,
    max_pages: int = MAX_PDF_PAGES,
    max_chars: int = MAX_EXTRACTED_TEXT_LENGTH,
) -> str:
    return extract_text_from_bytes(content, extension, max_pages=max_pages, max_chars=max_chars)


def generate_summary(text: str, *, file: AiFileRecord) -> str:
    cleaned = normalize_space(text)
    if not cleaned:
        return f"{file.original_name} 暂无可提取文本。"
    return truncate_text(cleaned, 300)


def suggest_category(
    text: str,
    categories: Sequence[AiCategoryRecord],
) -> CategorySuggestion:
    normalized = normalize_space(text).lower()
    best_category: AiCategoryRecord | None = None
    best_score = 0
    for category in categories:
        if not category.ai_analysis_enabled:
            continue
        candidates = [category.name, category.code, *category.keywords]
        score = sum(1 for candidate in candidates if candidate and candidate.lower() in normalized)
        if score > best_score:
            best_score = score
            best_category = category
    if best_category is None:
        return CategorySuggestion(category_id=None, category_name=None)
    return CategorySuggestion(category_id=best_category.id, category_name=best_category.name)


def generate_tags(text: str, *, categories: Sequence[AiCategoryRecord]) -> list[str]:
    normalized = normalize_space(text).lower()
    tags: list[str] = []
    for category in categories:
        for keyword in category.keywords:
            cleaned = keyword.strip()
            if cleaned and cleaned.lower() in normalized:
                tags.append(cleaned[:40])
    words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", text)
    for word in words:
        cleaned = word.strip().lower()
        if cleaned and cleaned not in tags:
            tags.append(cleaned[:40])
        if len(tags) >= 5:
            break
    return unique_ordered(tags)[:5]


def detect_sensitive_hits(text: str, rules: Sequence[SensitiveRule]) -> list[dict[str, object]]:
    hits: list[dict[str, object]] = []
    lower_text = text.lower()
    for rule in rules:
        matched_value: str | None = None
        if rule.rule_type == "keyword":
            for keyword in rule.keywords:
                if keyword.lower() in lower_text:
                    matched_value = keyword
                    break
        elif rule.rule_type == "regex" and rule.pattern:
            match = re.search(rule.pattern, text, flags=re.IGNORECASE)
            if match is not None:
                matched_value = match.group(0)
        if matched_value is None:
            continue
        hits.append(
            {
                "rule_id": str(rule.id),
                "rule_name": rule.name,
                "risk_level": rule.risk_level,
                "action": rule.action,
                "match": truncate_text(matched_value, 80),
            }
        )
    return hits


def highest_risk_level(levels: Iterable[object]) -> str:
    highest = "none"
    for raw_level in levels:
        level = str(raw_level)
        if RISK_ORDER.get(level, 0) > RISK_ORDER[highest]:
            highest = level
    return highest


def requires_sensitive_review(hits: Sequence[Mapping[str, object]]) -> bool:
    return any(str(hit.get("action")) in {"require_review", "block_sync"} for hit in hits)


def merge_tags(existing: list[str], generated: list[str]) -> list[str]:
    return unique_ordered([*existing, *generated])[:20]


def unique_ordered(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = value.strip()
        key = cleaned.lower()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def resolve_analysis_engine_type(
    *,
    llm_enabled: bool,
    deterministic_enabled: bool,
) -> str:
    if llm_enabled and deterministic_enabled:
        return "hybrid"
    if llm_enabled:
        return "llm"
    return "rule"


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _is_protected_app_env(app_env: str) -> bool:
    return app_env.strip().lower() in PROTECTED_ENVS


def _default_prompt_definitions() -> list[PromptDefinition]:
    return [
        PromptDefinition(
            template_key=ANALYSIS_PROMPT_KEY,
            name="文档组合分析",
            description="一次调用生成严格 JSON 摘要、分类、标签与风险建议",
            prompt_text=("分析下方经过长度限制的文档输入,并严格遵循系统消息中的 JSON 契约。"),
            variables=[],
        ),
        PromptDefinition(
            template_key="summary",
            name="文档摘要",
            description="生成面向审核人员的简短摘要",
            prompt_text="请总结文档的核心内容, 保留事实, 不添加猜测: {text}",
            variables=["text"],
        ),
        PromptDefinition(
            template_key="auto_category",
            name="自动分类",
            description="根据候选分类和文档内容给出分类建议",
            prompt_text="候选分类: {categories}\n文档: {text}\n请选择最合适分类。",
            variables=["categories", "text"],
        ),
        PromptDefinition(
            template_key="tag_generation",
            name="自动标签",
            description="提取用于检索的短标签",
            prompt_text="从文档中提取 3-5 个短标签: {text}",
            variables=["text"],
        ),
        PromptDefinition(
            template_key="sensitive_detection",
            name="敏感检测",
            description="识别密钥、证件号、个人隐私等敏感内容",
            prompt_text="请判断文档是否包含敏感信息, 并返回风险等级: {text}",
            variables=["text"],
        ),
    ]


def _default_sensitive_rule_definitions() -> list[SensitiveRuleDefinition]:
    return [
        SensitiveRuleDefinition(
            name="密钥与访问令牌",
            rule_type="keyword",
            risk_level="high",
            action="require_review",
            keywords=["api key", "apikey", "secret", "token", "密钥", "密码"],
        ),
        SensitiveRuleDefinition(
            name="身份证号",
            rule_type="regex",
            risk_level="high",
            action="require_review",
            pattern=r"\b\d{17}[\dXx]\b",
        ),
        SensitiveRuleDefinition(
            name="生产环境凭据",
            rule_type="keyword",
            risk_level="critical",
            action="block_sync",
            keywords=["prod secret", "production password", "生产环境密码", "root password"],
        ),
    ]
