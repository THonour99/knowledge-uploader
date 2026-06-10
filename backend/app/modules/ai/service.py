from __future__ import annotations

import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from ipaddress import ip_address
from typing import Protocol
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.llm.openai_compatible import LLMTestResult, OpenAICompatibleProvider
from app.adapters.minio_client import STORAGE_TRANSIENT_ERRORS, is_transient_storage_error
from app.core.audit import record_admin_audit_log
from app.core.config import Settings
from app.core.document_state import DocumentStateError, DocumentStateMachine
from app.core.runtime_config import get_config as get_runtime_config
from app.core.security import decrypt_api_key, encrypt_api_key
from app.modules.user.schemas import AuthUserRecord

from . import exceptions
from .models import AiFeatureConfig, AiProvider, DocumentAnalysis, PromptTemplate, SensitiveRule
from .parsers import MAX_EXTRACTED_TEXT_LENGTH, MAX_PDF_PAGES, extract_text_from_bytes
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
    PromptTemplateResponse,
    SensitiveRuleResponse,
)

ADMIN_ROLES = {"knowledge_admin", "system_admin"}
SYSTEM_ADMIN_ROLE = "system_admin"
MAX_ERROR_MESSAGE_LENGTH = 500
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


class AiObjectStorage(Protocol):
    async def get_object(self, *, bucket: str, object_key: str) -> bytes:
        pass


@dataclass(frozen=True)
class RequestContext:
    ip_address: str
    user_agent: str


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
                ai_analysis_enabled=features["ai_analysis"].enabled,
                allow_external_llm=features["allow_external_llm"].enabled,
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
        provider = AiProvider(
            name=request.name.strip(),
            provider_type=request.provider_type,
            base_url=clean_optional_text(request.base_url),
            api_key_encrypted=self._encrypt_api_key(request.api_key),
            chat_model=clean_optional_text(request.chat_model),
            embedding_model=clean_optional_text(request.embedding_model),
            vision_model=clean_optional_text(request.vision_model),
            is_internal=request.is_internal,
            enabled=request.enabled,
            priority=max(0, request.priority),
            timeout_seconds=max(1, request.timeout_seconds),
            max_retry_count=max(0, request.max_retry_count),
            max_input_tokens=request.max_input_tokens,
            max_output_tokens=request.max_output_tokens,
            temperature=request.temperature,
            top_p=request.top_p,
        )
        await self._repository.add_provider(provider)
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
        if request.name is not None:
            provider.name = request.name.strip()
        if request.provider_type is not None:
            provider.provider_type = request.provider_type
        if "base_url" in fields_set:
            provider.base_url = clean_optional_text(request.base_url)
        if request.clear_api_key:
            provider.api_key_encrypted = None
        elif request.api_key is not None:
            provider.api_key_encrypted = self._encrypt_api_key(request.api_key)
        if "chat_model" in fields_set:
            provider.chat_model = clean_optional_text(request.chat_model)
        if "embedding_model" in fields_set:
            provider.embedding_model = clean_optional_text(request.embedding_model)
        if "vision_model" in fields_set:
            provider.vision_model = clean_optional_text(request.vision_model)
        if request.is_internal is not None:
            provider.is_internal = request.is_internal
        if request.enabled is not None:
            provider.enabled = request.enabled
        if request.priority is not None:
            provider.priority = max(0, request.priority)
        if request.timeout_seconds is not None:
            provider.timeout_seconds = max(1, request.timeout_seconds)
        if request.max_retry_count is not None:
            provider.max_retry_count = max(0, request.max_retry_count)
        if "max_input_tokens" in fields_set:
            provider.max_input_tokens = request.max_input_tokens
        if "max_output_tokens" in fields_set:
            provider.max_output_tokens = request.max_output_tokens
        if request.temperature is not None:
            provider.temperature = request.temperature
        if "top_p" in fields_set:
            provider.top_p = request.top_p
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
        result = await self._test_provider_connectivity(provider)
        provider.last_test_status = result.status
        provider.last_test_latency_ms = result.latency_ms
        provider.last_tested_at = datetime.now(UTC)
        await self._record_admin_audit(
            current_user=current_user,
            action="ai.provider.test",
            target_type="ai_provider",
            target_id=provider.id,
            context=context,
            metadata_json={"status": result.status, "latency_ms": result.latency_ms},
        )
        await self._session.commit()
        return AiProviderTestResponse(
            provider_id=provider.id,
            status=result.status,
            latency_ms=result.latency_ms,
            message=result.message,
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
            base_url=provider.base_url,
            chat_model=provider.chat_model,
            embedding_model=provider.embedding_model,
            vision_model=provider.vision_model,
            is_internal=provider.is_internal,
            enabled=provider.enabled,
            priority=provider.priority,
            timeout_seconds=provider.timeout_seconds,
            max_retry_count=provider.max_retry_count,
            max_input_tokens=provider.max_input_tokens,
            max_output_tokens=provider.max_output_tokens,
            temperature=provider.temperature,
            top_p=provider.top_p,
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
            provider_type = self._settings.llm_provider.strip() or "disabled"
            await self._repository.add_provider(
                AiProvider(
                    name="默认模型供应商",
                    provider_type=provider_type,
                    base_url=clean_optional_text(self._settings.llm_base_url),
                    api_key_encrypted=self._encrypt_api_key(self._settings.llm_api_key),
                    chat_model=clean_optional_text(self._settings.llm_model),
                    embedding_model=clean_optional_text(self._settings.embedding_model),
                    is_internal=provider_type
                    in {"local_openai_compatible", "ollama", "vllm", "lmstudio", "mock"},
                    enabled=provider_type != "disabled",
                    priority=100,
                    timeout_seconds=max(1, int(self._settings.ai_request_timeout)),
                    max_retry_count=max(0, self._settings.ai_max_retry_count),
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
                "ocr", "OCR识别", "为图片/PDF OCR 预留的功能开关", self._settings.enable_ocr
            ),
            FeatureDefinition(
                "similarity_detection",
                "相似检测",
                "为近重复文档检测预留的功能开关",
                self._settings.enable_similarity_detection,
            ),
        ]

    async def _test_provider_connectivity(self, provider: AiProvider) -> LLMTestResult:
        if provider.provider_type == "mock":
            return LLMTestResult(status="success", latency_ms=0, message="ok")
        if provider.provider_type == "disabled":
            return LLMTestResult(status="failed", latency_ms=None, message="provider disabled")
        if not provider.base_url or not provider.chat_model:
            return LLMTestResult(
                status="failed",
                latency_ms=None,
                message="base_url and chat_model are required",
            )
        features = await self._feature_map()
        allow_external_llm = (
            self._settings.allow_external_llm and features["allow_external_llm"].enabled
        )
        if _is_external_url(provider.base_url) and not allow_external_llm:
            return LLMTestResult(
                status="failed",
                latency_ms=None,
                message="external model provider is disabled",
            )
        client = OpenAICompatibleProvider(
            base_url=provider.base_url,
            api_key=self._decrypt_provider_key(provider),
            model=provider.chat_model,
            timeout_seconds=provider.timeout_seconds,
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
        if provider_type == "openai_compatible" and not is_internal:
            return

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
    ) -> uuid.UUID:
        config_service = AiConfigService(
            session=self._session,
            repository=self._repository,
            settings=self._settings,
        )
        if not self._settings.ai_analysis_enabled:
            raise exceptions.AiAnalysisPreconditionError("AI analysis disabled")
        await config_service._ensure_defaults()
        features = await config_service._feature_map()
        if not features["ai_analysis"].enabled:
            raise exceptions.AiAnalysisPreconditionError("AI analysis disabled")

        file = await self._get_file_or_raise(file_id)
        if not file.ai_analysis_enabled_at_upload:
            raise exceptions.AiAnalysisPreconditionError("AI disabled when file was uploaded")
        idempotent_analysis = await self._get_analysis_for_idempotent_delivery(file)
        if idempotent_analysis is not None:
            await self._session.commit()
            return idempotent_analysis.id

        provider = await self._repository.get_enabled_provider()
        analysis = await self._start_analysis(file=file, provider=provider)
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
                await self._session.rollback()
                raise exceptions.AiAnalysisTransientError("object storage unavailable") from exc
            parse_max_pages, parse_max_chars = await resolve_parse_limits()
            extracted_text = extract_text(
                raw_content,
                extension=file.extension,
                max_pages=parse_max_pages,
                max_chars=parse_max_chars,
            )
            file = await self._get_file_or_raise(file_id)
            file = await self._transition_file(file, "analysis_queued")
            file = await self._transition_file(file, "analyzing")

            categories = await self._repository.list_categories()
            summary = (
                generate_summary(extracted_text, file=file) if features["summary"].enabled else None
            )
            category = (
                suggest_category(extracted_text, categories)
                if features["auto_category"].enabled
                else CategorySuggestion(category_id=None, category_name=None)
            )
            tags = (
                generate_tags(extracted_text, categories=categories)
                if features["tag_generation"].enabled
                else []
            )
            sensitive_hits: list[dict[str, object]] = []
            risk_level = "none"
            if features["sensitive_detection"].enabled:
                rules = await self._repository.list_sensitive_rules(enabled_only=True)
                sensitive_hits = detect_sensitive_hits(extracted_text, rules)
                risk_level = highest_risk_level(hit["risk_level"] for hit in sensitive_hits)
                await self._repository.increment_sensitive_rule_hits(
                    [uuid.UUID(str(hit["rule_id"])) for hit in sensitive_hits]
                )

            target_status = (
                "sensitive_review_required"
                if RISK_ORDER[risk_level] >= RISK_ORDER["high"]
                else "analyzed"
            )
            file.tags = merge_tags(file.tags, tags)
            file.category_id = category.category_id or file.category_id
            file = await self._transition_file(file, target_status)
            analysis.status = "succeeded"
            analysis.extracted_text = truncate_text(extracted_text, parse_max_chars)
            analysis.summary = summary
            analysis.suggested_category_id = category.category_id
            analysis.suggested_category_name = category.category_name
            analysis.suggested_tags = tags
            analysis.sensitive_risk_level = risk_level
            analysis.sensitive_hits = sensitive_hits
            analysis.error_message = None
            analysis.finished_at = datetime.now(UTC)
            await self._session.commit()
            return analysis.id
        except exceptions.AiAnalysisTransientError:
            raise
        except exceptions.DocumentParseError as exc:
            await self._session.rollback()
            await self._mark_analysis_failed(file_id=file_id, error_message=str(exc))
            return analysis.id
        except Exception as exc:
            await self._session.rollback()
            error_type = type(exc).__name__
            await self._mark_analysis_failed(file_id=file_id, error_message=error_type)
            return analysis.id

    async def _start_analysis(
        self,
        *,
        file: AiFileRecord,
        provider: AiProvider | None,
    ) -> DocumentAnalysis:
        analysis = await self._repository.get_document_analysis(file.id)
        started_at = datetime.now(UTC)
        if analysis is None:
            analysis = DocumentAnalysis(file_id=file.id)
            await self._repository.add_document_analysis(analysis)
        analysis.provider_id = provider.id if provider is not None else None
        analysis.status = "running"
        analysis.error_message = None
        analysis.started_at = started_at
        analysis.finished_at = None
        file.ai_config_snapshot = self._analysis_snapshot(provider=provider)
        await self._repository.update_file_analysis_state(file)
        await self._session.commit()
        return analysis

    async def _get_analysis_for_idempotent_delivery(
        self,
        file: AiFileRecord,
    ) -> DocumentAnalysis | None:
        analysis = await self._repository.get_document_analysis(file.id)
        if analysis is None:
            return None
        if analysis.status == "running" and file.status in AI_ANALYSIS_IN_PROGRESS_FILE_STATUSES:
            return analysis
        if analysis.status == "succeeded" and file.status in AI_ANALYSIS_SUCCEEDED_FILE_STATUSES:
            return analysis
        return None

    async def mark_analysis_failed(self, *, file_id: uuid.UUID, error_message: str) -> None:
        """供 Celery 重试耗尽后调用的公开失败标记入口。"""
        await self._mark_analysis_failed(file_id=file_id, error_message=error_message)

    async def _mark_analysis_failed(self, *, file_id: uuid.UUID, error_message: str) -> None:
        file = await self._get_file_or_raise(file_id)
        analysis = await self._repository.get_document_analysis(file_id)
        if analysis is None:
            analysis = DocumentAnalysis(file_id=file_id)
            await self._repository.add_document_analysis(analysis)
        try:
            if file.status != "analysis_failed":
                file.status = DocumentStateMachine.transition(file.status, "analysis_failed")
                await self._repository.update_file_analysis_state(file)
        except DocumentStateError:
            pass
        analysis.status = "failed"
        analysis.error_message = error_message[:MAX_ERROR_MESSAGE_LENGTH]
        analysis.finished_at = datetime.now(UTC)
        await self._session.commit()

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


def clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def cast_optional_str(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


def mask_secret(secret: str | None) -> str | None:
    if not secret:
        return None
    suffix = secret[-4:] if len(secret) >= 4 else secret
    prefix = "sk-" if secret.startswith("sk-") else ""
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


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _is_external_url(url: str) -> bool:
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if not host:
        return True
    if host in {
        "localhost",
        "host.docker.internal",
        "ollama",
        "vllm",
        "lmstudio",
    }:
        return False
    try:
        address = ip_address(host)
    except ValueError:
        return True
    return not (address.is_loopback or address.is_private or address.is_link_local)


def _default_prompt_definitions() -> list[PromptDefinition]:
    return [
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
