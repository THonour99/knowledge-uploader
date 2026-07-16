from __future__ import annotations

from typing import ClassVar

from app.core.events import DomainEvent

NOTIFICATION_EMAIL_VERIFICATION = "email_verification"
NOTIFICATION_PASSWORD_RESET = "password_reset"
NOTIFICATION_REVIEW_APPROVED = "review_approved"
NOTIFICATION_REVIEW_REJECTED = "review_rejected"
NOTIFICATION_REVIEW_SUBMITTED = "review_submitted"
NOTIFICATION_RAGFLOW_SYNC_SUCCEEDED = "ragflow_sync_succeeded"
NOTIFICATION_RAGFLOW_SYNC_FAILED = "ragflow_sync_failed"
NOTIFICATION_AI_ANALYSIS_SUCCEEDED = "ai_analysis_succeeded"
NOTIFICATION_AI_ANALYSIS_FAILED = "ai_analysis_failed"
NOTIFICATION_DOCUMENT_EXPIRING = "document_expiring"
NOTIFICATION_DOCUMENT_EXPIRED = "document_expired"

NOTIFICATION_EMAIL_REQUESTED = "notification.email.requested"
DOCUMENT_FILE_EXPIRING = "document.file.expiring"
DOCUMENT_FILE_EXPIRED = "document.file.expired"
AI_FILE_ANALYZED = "ai.file.analyzed"


class NotificationEmailRequested(DomainEvent):
    ROUTING_KEY: ClassVar[str] = NOTIFICATION_EMAIL_REQUESTED


class DocumentFileExpiring(DomainEvent):
    ROUTING_KEY: ClassVar[str] = DOCUMENT_FILE_EXPIRING


class DocumentFileExpired(DomainEvent):
    ROUTING_KEY: ClassVar[str] = DOCUMENT_FILE_EXPIRED


class AiFileAnalyzed(DomainEvent):
    ROUTING_KEY: ClassVar[str] = AI_FILE_ANALYZED
