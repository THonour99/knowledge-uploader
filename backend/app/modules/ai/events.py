from __future__ import annotations

from enum import StrEnum
from typing import ClassVar

from app.core.events import DomainEvent

AI_TEXT_EXTRACTED = "ai.text.extracted"
AI_FILE_ANALYZED = "ai.file.analyzed"
AI_FILE_ANALYSIS_FAILED = "ai.file.analysis_failed"
AI_SENSITIVE_DETECTED = "ai.sensitive.detected"
AI_CONFIG_CHANGED = "ai.config.changed"

# AI 模块通过 outbox 发布审核提交事件, 不直接依赖 review service/repository。
REVIEW_FILE_SUBMITTED = "review.file.submitted"


class AiAnalysisFailureCode(StrEnum):
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    TIMEOUT = "timeout"
    CONNECTION_ERROR = "connection_error"
    RATE_LIMITED = "rate_limited"
    AUTHENTICATION_FAILED = "authentication_failed"
    REQUEST_REJECTED = "request_rejected"
    INVALID_RESPONSE = "invalid_response"
    CONFIGURATION_ERROR = "configuration_error"
    INVALID_OUTPUT = "invalid_output"
    INTERNAL = "internal"


class AiFileAnalysisFailed(DomainEvent):
    ROUTING_KEY: ClassVar[str] = AI_FILE_ANALYSIS_FAILED


def normalize_analysis_failure_code(value: AiAnalysisFailureCode | str) -> AiAnalysisFailureCode:
    try:
        return AiAnalysisFailureCode(value)
    except ValueError:
        return AiAnalysisFailureCode.INTERNAL
