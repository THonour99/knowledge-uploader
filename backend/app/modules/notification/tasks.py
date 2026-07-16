from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import NoReturn, Protocol

import structlog
from celery import Task
from celery.exceptions import MaxRetriesExceededError, Reject

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

from . import exceptions
from .repository import NotificationRepository  # noqa: TID251 - same-module repository dependency

logger = structlog.get_logger(__name__)
EMAIL_ENVELOPE_VERSION = 1
EMAIL_PUBLISH_RETRY_POLICY: Mapping[str, int | float] = {
    "max_retries": 3,
    "interval_start": 0,
    "interval_step": 0.5,
    "interval_max": 2,
}
NOTIFICATION_TASK_MAX_RETRIES = 5
NOTIFICATION_TASK_BASE_COUNTDOWN_SECONDS = 15
NOTIFICATION_TASK_MAX_COUNTDOWN_SECONDS = 120


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
        argsrepr: str,
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


@celery_app.task(  # type: ignore[misc]
    name="notification.process_domain_event",
    bind=True,
    queue="notification_queue",
    acks_late=True,
    acks_on_failure_or_timeout=False,
    reject_on_worker_lost=True,
    max_retries=NOTIFICATION_TASK_MAX_RETRIES,
)
def process_domain_event_task(self: Task, event_id: str) -> str:
    try:
        return run_process_domain_event_task(event_id)
    except (
        exceptions.NotificationSourceEventNotFoundError,
        exceptions.UnsupportedNotificationSourceEventError,
        ValueError,
    ) as error:
        raise Reject(reason=type(error).__name__, requeue=False) from None
    except Exception as error:
        logger.error(
            "notification.event.processing_failed",
            event_id=event_id,
            error_type=type(error).__name__,
            retries=int(self.request.retries or 0),
        )
        _retry_or_reject(self, error)


@celery_app.task(  # type: ignore[misc]
    name="notification.send_persisted_email",
    bind=True,
    queue="notification_queue",
    acks_late=True,
    acks_on_failure_or_timeout=False,
    reject_on_worker_lost=True,
    max_retries=NOTIFICATION_TASK_MAX_RETRIES,
)
def send_persisted_email_task(self: Task, notification_id: str) -> str:
    try:
        result = run_send_persisted_email_task(notification_id)
    except (exceptions.NotificationEmailNotFoundError, ValueError) as error:
        raise Reject(reason=type(error).__name__, requeue=False) from None
    except EmailConfigurationError as error:
        _record_email_delivery_result_best_effort("configuration_failure")
        logger.error(
            "notification.persisted_email.configuration_failed",
            notification_id=notification_id,
            retries=int(self.request.retries or 0),
        )
        _retry_or_reject(self, error)
    except EmailDeliveryError as error:
        _record_email_delivery_result_best_effort("failure")
        logger.warning(
            "notification.persisted_email.delivery_failed",
            notification_id=notification_id,
            retries=int(self.request.retries or 0),
        )
        _retry_or_reject(self, error)
    except Exception as error:
        logger.error(
            "notification.persisted_email.infrastructure_failed",
            notification_id=notification_id,
            error_type=type(error).__name__,
            retries=int(self.request.retries or 0),
        )
        _retry_or_reject(self, error)
    _record_email_delivery_result_best_effort("success")
    return result


async def _send_email(*, recipient: str, subject: str, body: str) -> None:
    adapter = build_email_adapter_from_env()
    await adapter.send(recipient, subject, body)


def run_process_domain_event_task(event_id: str) -> str:
    parsed_event_id = _positive_event_id(event_id)
    asyncio.run(_process_domain_event(parsed_event_id))
    return event_id


async def _process_domain_event(event_id: int) -> None:
    from . import handlers

    try:
        async with AsyncSessionFactory() as session:
            await handlers.handle_source_event_id(event_id, session=session)
    finally:
        await engine.dispose()


def run_send_persisted_email_task(notification_id: str) -> str:
    parsed_notification_id = uuid.UUID(notification_id)
    result = asyncio.run(_send_persisted_email(parsed_notification_id))
    return result


async def _send_persisted_email(notification_id: uuid.UUID) -> str:
    try:
        async with AsyncSessionFactory() as session:
            repository = NotificationRepository(session)
            delivery = await repository.get_email_for_delivery(notification_id)
            if delivery is None:
                raise exceptions.NotificationEmailNotFoundError(
                    "persisted email notification not found"
                )
            notification = delivery.notification
            if notification.delivery_status == "sent":
                return "already_sent"

            notification.delivery_attempts += 1
            try:
                # The row lock serializes duplicate Celery deliveries. SMTP cannot offer
                # exactly-once across a process crash after send and before this commit;
                # that unavoidable boundary is documented and delivery remains at-least-once.
                await _send_email(
                    recipient=delivery.recipient_email,
                    subject=notification.title,
                    body=notification.body,
                )
            except (EmailConfigurationError, EmailDeliveryError) as error:
                notification.delivery_status = "failed"
                notification.last_delivery_error = type(error).__name__[:120]
                await session.commit()
                raise
            notification.delivery_status = "sent"
            notification.last_delivery_error = None
            notification.delivered_at = datetime.now(UTC)
            await session.commit()
            return "sent"
    finally:
        await engine.dispose()


def _retry_or_reject(task: Task, error: BaseException) -> NoReturn:
    retries = int(task.request.retries or 0)
    error_type = type(error).__name__
    max_retries = task.max_retries
    if max_retries is not None and retries >= max_retries:
        raise Reject(reason=error_type, requeue=False) from None
    countdown = min(
        (2**retries) * NOTIFICATION_TASK_BASE_COUNTDOWN_SECONDS,
        NOTIFICATION_TASK_MAX_COUNTDOWN_SECONDS,
    )
    try:
        raise task.retry(exc=RuntimeError(error_type), countdown=countdown)
    except MaxRetriesExceededError:
        raise Reject(reason=error_type, requeue=False) from None


def _positive_event_id(value: str) -> int:
    try:
        event_id = int(value)
    except ValueError:
        raise ValueError("event_id must be a positive integer") from None
    if event_id < 1 or str(event_id) != value:
        raise ValueError("event_id must be a canonical positive integer")
    return event_id


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
        argsrepr="(<encrypted-email-envelope>,)",
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
