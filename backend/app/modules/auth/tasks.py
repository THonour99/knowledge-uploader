"""Auth Celery tasks.

auth.trigger_password_reset — triggered by user.password_reset.requested outbox event
(admin-initiated reset).  Reuses AuthService.forgot_password so token creation and email
dispatch are handled identically to the self-service flow.
"""

from __future__ import annotations

import asyncio
import uuid
from importlib import import_module

import structlog
from celery import Task

from app.core.config import get_settings
from app.core.database import AsyncSessionFactory, engine
from app.core.identity import get_user_identity_store
from app.modules.auth.repository import AuthRepository  # noqa: TID251 - same-module repository
from app.modules.auth.schemas import ForgotPasswordRequest
from app.modules.auth.service import AuthService  # noqa: TID251 - same-module service
from app.workers.celery_app import celery_app

import_module("app.db.models")

logger = structlog.get_logger(__name__)


@celery_app.task(  # type: ignore[misc]
    name="auth.trigger_password_reset",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    queue="notification_queue",
)
def trigger_password_reset(self: Task, user_id: str) -> None:
    """Generate a password-reset token and dispatch the email event for the given user."""
    asyncio.run(_run_trigger_password_reset(user_id))


async def _run_trigger_password_reset(user_id: str) -> None:
    settings = get_settings()
    try:
        parsed_id = uuid.UUID(user_id)
    except ValueError:
        logger.error("auth.trigger_password_reset.invalid_user_id", user_id=user_id)
        return

    async with AsyncSessionFactory() as session:
        user_store = get_user_identity_store(session)
        user = await user_store.get_by_id(parsed_id)
        if user is None:
            logger.warning("auth.trigger_password_reset.user_not_found", user_id=user_id)
            return
        if user.status == "disabled":
            logger.info(
                "auth.trigger_password_reset.skipped_disabled",
                user_id=user_id,
            )
            return

        service = AuthService(
            session=session,
            repository=AuthRepository(session),
            user_store=user_store,
            settings=settings,
        )
        # Reuse the self-service forgot_password logic which creates the DB token
        # and publishes the email event in the same transaction.
        await service.forgot_password(
            ForgotPasswordRequest(email=user.email),
            trace_id=f"admin-reset:{user_id}",
        )

    await engine.dispose()
