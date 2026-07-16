from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Protocol

import structlog

from app.adapters.email import (
    EmailConfigurationError,
    EmailDeliveryError,
    build_email_adapter_from_env,
)
from app.core.config import get_settings
from app.core.database import AsyncSessionFactory, engine
from app.core.email_delivery_metrics import (
    EMAIL_DELIVERY_RESULTS,
    record_email_delivery_result,
)
from app.core.security import decrypt_secret, encrypt_secret
from app.workers.celery_app import celery_app

logger = structlog.get_logger(__name__)
EMAIL_ENVELOPE_VERSION = 1
EMAIL_PUBLISH_RETRY_POLICY: Mapping[str, int | float] = {
    "max_retries": 3,
    "interval_start": 0,
    "interval_step": 0.5,
    "interval_max": 2,
}


class EmailEnvelopeError(RuntimeError):
    """Raised when a queued email cannot be decrypted and validated safely."""


class EmailTaskSender(Protocol):
    def send_task(
        self,
        name: str,
        args: list[str],
        queue: str,
        delivery_mode: int,
        task_id: str,
        retry: bool,
        retry_policy: Mapping[str, int | float],
        expires: datetime | None,
    ) -> object:
        pass


@celery_app.task(  # type: ignore[misc]
    name="notification.send_email",
    queue="notification_queue",
    acks_late=False,
    acks_on_failure_or_timeout=True,
    reject_on_worker_lost=False,
)
def send_email_task(encrypted_envelope: str) -> str:
    try:
        recipient, subject, body, expires_at = _decrypt_email_envelope(encrypted_envelope)
    except EmailEnvelopeError:
        _record_email_delivery_result_best_effort("invalid_envelope")
        raise
    if expires_at is not None and expires_at <= datetime.now(UTC):
        _record_email_delivery_result_best_effort("expired")
        return "expired"
    try:
        asyncio.run(_send_email(recipient=recipient, subject=subject, body=body))
    except EmailConfigurationError:
        _record_email_delivery_result_best_effort("configuration_failure")
        logger.error("notification.email.configuration_failed")
        raise
    except EmailDeliveryError:
        _record_email_delivery_result_best_effort("failure")
        logger.warning("notification.email.delivery_failed")
        raise
    _record_email_delivery_result_best_effort("success")
    return "sent"


@celery_app.task(name="notification.review_approved")  # type: ignore[misc]
def review_approved_notification_task(file_id: str) -> str:
    asyncio.run(_handle_review_approved(file_id))
    return file_id


@celery_app.task(name="notification.review_rejected")  # type: ignore[misc]
def review_rejected_notification_task(file_id: str, reason: str) -> str:
    asyncio.run(_handle_review_rejected(file_id=file_id, reason=reason))
    return file_id


@celery_app.task(name="notification.ragflow_sync_failed")  # type: ignore[misc]
def ragflow_sync_failed_notification_task(sync_task_id: str, error_message: str) -> str:
    asyncio.run(
        _handle_ragflow_sync_failed(
            sync_task_id=sync_task_id,
            error_message=error_message,
        )
    )
    return sync_task_id


@celery_app.task(name="notification.document_expiry")  # type: ignore[misc]
def document_expiry_notification_task(
    file_id: str,
    recipient_user_id: str,
    recipient_email: str,
    file_name: str,
    expires_at: str,
    expiry_status: str,
) -> str:
    asyncio.run(
        _handle_document_expiry(
            file_id=file_id,
            recipient_user_id=recipient_user_id,
            recipient_email=recipient_email,
            file_name=file_name,
            expires_at=expires_at,
            expiry_status=expiry_status,
        )
    )
    return file_id


async def _send_email(*, recipient: str, subject: str, body: str) -> None:
    adapter = build_email_adapter_from_env()
    await adapter.send(recipient, subject, body)


async def _handle_review_approved(file_id: str) -> None:
    from . import handlers

    try:
        async with AsyncSessionFactory() as session:
            await handlers.handle_review_file_approved({"file_id": file_id}, session=session)
    finally:
        await engine.dispose()


async def _handle_review_rejected(*, file_id: str, reason: str) -> None:
    from . import handlers

    try:
        async with AsyncSessionFactory() as session:
            await handlers.handle_review_file_rejected(
                {"file_id": file_id, "reason": reason},
                session=session,
            )
    finally:
        await engine.dispose()


async def _handle_ragflow_sync_failed(*, sync_task_id: str, error_message: str) -> None:
    from . import handlers

    try:
        async with AsyncSessionFactory() as session:
            await handlers.handle_ragflow_sync_failed(
                {
                    "sync_task_id": sync_task_id,
                    "error_message": error_message,
                },
                session=session,
            )
    finally:
        await engine.dispose()


async def _handle_document_expiry(
    *,
    file_id: str,
    recipient_user_id: str,
    recipient_email: str,
    file_name: str,
    expires_at: str,
    expiry_status: str,
) -> None:
    from . import handlers

    try:
        async with AsyncSessionFactory() as session:
            await handlers.handle_document_expiry_reminder(
                {
                    "file_id": file_id,
                    "recipient_user_id": recipient_user_id,
                    "recipient_email": recipient_email,
                    "file_name": file_name,
                    "expires_at": expires_at,
                    "expiry_status": expiry_status,
                },
                session=session,
            )
    finally:
        await engine.dispose()


def enqueue_email(
    *,
    recipient: str,
    subject: str,
    body: str,
    expires_at: datetime | None = None,
    sender: EmailTaskSender = celery_app,
) -> None:
    if _tasks_disabled_for_tests():
        return
    encrypted_envelope = _encrypt_email_envelope(
        recipient=recipient,
        subject=subject,
        body=body,
        expires_at=expires_at,
    )
    sender.send_task(
        "notification.send_email",
        args=[encrypted_envelope],
        queue="notification_queue",
        delivery_mode=2,
        task_id=f"email-{uuid.uuid4()}",
        retry=True,
        retry_policy=EMAIL_PUBLISH_RETRY_POLICY,
        expires=expires_at,
    )


def _encrypt_email_envelope(
    *,
    recipient: str,
    subject: str,
    body: str,
    expires_at: datetime | None = None,
) -> str:
    if expires_at is not None:
        if expires_at.tzinfo is None:
            raise ValueError("email expiry must include a timezone")
        expires_at = expires_at.astimezone(UTC)
    payload = json.dumps(
        {
            "version": EMAIL_ENVELOPE_VERSION,
            "recipient": recipient,
            "subject": subject,
            "body": body,
            "expires_at": expires_at.isoformat() if expires_at is not None else None,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return encrypt_secret(payload, get_settings().encryption_key)


def _decrypt_email_envelope(
    encrypted_envelope: str,
) -> tuple[str, str, str, datetime | None]:
    try:
        raw_payload = decrypt_secret(encrypted_envelope, get_settings().encryption_key)
        payload = json.loads(raw_payload)
    except Exception:
        raise EmailEnvelopeError("queued email envelope is invalid") from None
    if not isinstance(payload, dict) or set(payload) != {
        "version",
        "recipient",
        "subject",
        "body",
        "expires_at",
    }:
        raise EmailEnvelopeError("queued email envelope is invalid")
    recipient = payload.get("recipient")
    subject = payload.get("subject")
    body = payload.get("body")
    raw_expires_at = payload.get("expires_at")
    if (
        payload.get("version") != EMAIL_ENVELOPE_VERSION
        or not isinstance(recipient, str)
        or not recipient
        or len(recipient) > 320
        or not isinstance(subject, str)
        or not subject
        or len(subject) > 200
        or not isinstance(body, str)
        or not body
        or len(body) > 20_000
    ):
        raise EmailEnvelopeError("queued email envelope is invalid")
    expires_at: datetime | None = None
    if raw_expires_at is not None:
        if not isinstance(raw_expires_at, str):
            raise EmailEnvelopeError("queued email envelope is invalid")
        try:
            expires_at = datetime.fromisoformat(raw_expires_at)
        except ValueError:
            raise EmailEnvelopeError("queued email envelope is invalid") from None
        if expires_at.tzinfo is None:
            raise EmailEnvelopeError("queued email envelope is invalid")
        expires_at = expires_at.astimezone(UTC)
    return recipient, subject, body, expires_at


def _record_email_delivery_result_best_effort(result: str) -> None:
    try:
        asyncio.run(_record_email_delivery_result(result))
    except Exception as error:
        logger.error(
            "notification.email.metric_record_failed",
            result=result if result in EMAIL_DELIVERY_RESULTS else "invalid",
            error_type=type(error).__name__,
        )


async def _record_email_delivery_result(result: str) -> None:
    await record_email_delivery_result(
        redis_url=get_settings().cache_redis_url,
        result=result,
    )


def _tasks_disabled_for_tests() -> bool:
    return (
        os.getenv("APP_ENV", "").strip().lower() == "test"
        and os.getenv("NOTIFICATION_TASKS_ENABLED", "").strip().lower() != "true"
    )
