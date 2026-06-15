from __future__ import annotations

import asyncio
import os
from typing import Protocol

import structlog

from app.adapters.email import (
    EmailConfigurationError,
    EmailDeliveryError,
    build_email_adapter_from_env,
)
from app.core.database import AsyncSessionFactory, engine
from app.workers.celery_app import celery_app

logger = structlog.get_logger(__name__)


class EmailTaskSender(Protocol):
    def send_task(
        self,
        name: str,
        args: list[str],
        queue: str,
        delivery_mode: int,
    ) -> object:
        pass


@celery_app.task(  # type: ignore[misc]
    name="notification.send_email",
    autoretry_for=(EmailDeliveryError,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
    queue="notification_queue",
)
def send_email_task(recipient: str, subject: str, body: str) -> str:
    try:
        asyncio.run(_send_email(recipient=recipient, subject=subject, body=body))
    except EmailConfigurationError:
        logger.warning("notification.email.skipped_unconfigured")
        return "skipped"
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


def enqueue_email(
    *,
    recipient: str,
    subject: str,
    body: str,
    sender: EmailTaskSender = celery_app,
) -> None:
    if _tasks_disabled_for_tests():
        return
    sender.send_task(
        "notification.send_email",
        args=[recipient, subject, body],
        queue="notification_queue",
        delivery_mode=1,
    )


def _tasks_disabled_for_tests() -> bool:
    return (
        os.getenv("APP_ENV", "").strip().lower() == "test"
        and os.getenv("NOTIFICATION_TASKS_ENABLED", "").strip().lower() != "true"
    )
