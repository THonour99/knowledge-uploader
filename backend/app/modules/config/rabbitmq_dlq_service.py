from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Literal, Protocol, cast

from redis.asyncio import Redis, from_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_admin_audit_log
from app.core.config import get_settings
from app.modules.user.schemas import AuthUserRecord
from app.workers.celery_app import celery_app
from app.workers.rabbitmq_replay import (
    RabbitDeadLetterChanged,
    RabbitDeadLetterEmpty,
    RabbitDeadLetterError,
    RabbitDeadLetterUnavailable,
    RabbitDeadLetterUnsafe,
    SafeRabbitDeadLetter,
    inspect_next_dead_letter,
    replay_next_dead_letter,
)
from app.workers.rabbitmq_topology import TASK_QUEUE_NAMES

from . import exceptions
from .permissions import SYSTEM_ADMIN_ROLE
from .schemas import RabbitDeadLetterReplayResponse

RabbitQueueName = Literal[
    "document_queue",
    "ai_queue",
    "ragflow_queue",
    "notification_queue",
]
_LOCK_SECONDS = 120
_redis_from_url = cast(Callable[..., Redis], from_url)
_RELEASE_LOCK_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""


class RequestContext(Protocol):
    @property
    def ip_address(self) -> str: ...

    @property
    def user_agent(self) -> str: ...


class RabbitDeadLetterService:
    def __init__(self, *, session: AsyncSession) -> None:
        self._session = session

    async def replay_next(
        self,
        *,
        queue_name: str,
        reason: str,
        current_user: AuthUserRecord,
        context: RequestContext,
    ) -> RabbitDeadLetterReplayResponse:
        self._require_system_admin(current_user)
        normalized_queue = self._normalize_queue(queue_name)
        settings = get_settings()
        lock_key = f"lock:rabbitmq-dlq-replay:{normalized_queue}"
        lock_token = uuid.uuid4().hex
        redis_client = None
        lock_acquired = False
        try:
            try:
                redis_client = _redis_from_url(
                    settings.cache_redis_url,
                    encoding="utf-8",
                    decode_responses=True,
                )
                lock_result = redis_client.set(
                    lock_key,
                    lock_token,
                    nx=True,
                    ex=_LOCK_SECONDS,
                )
                lock_acquired = bool(await cast(Awaitable[object], lock_result))
            except Exception:
                await self._audit_denied(
                    queue_name=normalized_queue,
                    current_user=current_user,
                    context=context,
                    error_type="ReplayInfrastructureUnavailable",
                    reason=reason,
                )
                raise exceptions.rabbit_dead_letter_unavailable() from None
            if not lock_acquired:
                await self._audit_denied(
                    queue_name=normalized_queue,
                    current_user=current_user,
                    context=context,
                    error_type="ReplayBusy",
                    reason=reason,
                )
                raise exceptions.rabbit_dead_letter_busy()

            preview = await self._inspect_and_audit_plan(
                broker_url=settings.celery_broker_url,
                queue_name=normalized_queue,
                reason=reason,
                current_user=current_user,
                context=context,
            )
            try:
                result = await asyncio.to_thread(
                    replay_next_dead_letter,
                    broker_url=settings.celery_broker_url,
                    queue_name=normalized_queue,
                    expected_original_task_id=preview.original_task_id,
                    sender=celery_app,
                )
            except RabbitDeadLetterError as error:
                await self._audit_replay_failure(
                    dead_letter=preview,
                    current_user=current_user,
                    context=context,
                    reason=reason,
                    error_type=type(error).__name__,
                )
                raise self._public_error(error) from None

            audit_log_id = await record_admin_audit_log(
                self._session,
                actor_id=current_user.id,
                action="rabbitmq.dead_letter.replay_completed",
                target_type="rabbitmq_dead_letter",
                target_id=result.dead_letter.target_id,
                ip_address=context.ip_address,
                user_agent=context.user_agent,
                metadata_json={
                    "queue_name": normalized_queue,
                    "task_name": result.dead_letter.task_name,
                    "original_task_id": str(result.dead_letter.original_task_id),
                    "replay_task_id": str(result.replay_task_id),
                    "raw_payload_copied": False,
                },
                reason=reason,
            )
            await self._session.commit()
            return RabbitDeadLetterReplayResponse(
                queue_name=normalized_queue,
                task_name=result.dead_letter.task_name,
                original_task_id=result.dead_letter.original_task_id,
                replay_task_id=result.replay_task_id,
                target_id=result.dead_letter.target_id,
                audit_log_id=audit_log_id,
                replay_queued=True,
                raw_payload_copied=False,
            )
        except RabbitDeadLetterError as error:
            await self._audit_denied(
                queue_name=normalized_queue,
                current_user=current_user,
                context=context,
                error_type=type(error).__name__,
                reason=reason,
            )
            raise self._public_error(error) from None
        finally:
            if redis_client is not None:
                if lock_acquired:
                    with suppress(Exception):
                        release_result = redis_client.eval(
                            _RELEASE_LOCK_SCRIPT,
                            1,
                            lock_key,
                            lock_token,
                        )
                        if isinstance(release_result, Awaitable):
                            await release_result
                with suppress(Exception):
                    await redis_client.aclose()

    async def _inspect_and_audit_plan(
        self,
        *,
        broker_url: str,
        queue_name: RabbitQueueName,
        reason: str,
        current_user: AuthUserRecord,
        context: RequestContext,
    ) -> SafeRabbitDeadLetter:
        preview = await asyncio.to_thread(
            inspect_next_dead_letter,
            broker_url=broker_url,
            queue_name=queue_name,
        )
        await record_admin_audit_log(
            self._session,
            actor_id=current_user.id,
            action="rabbitmq.dead_letter.replay_planned",
            target_type="rabbitmq_dead_letter",
            target_id=preview.target_id,
            ip_address=context.ip_address,
            user_agent=context.user_agent,
            metadata_json={
                "queue_name": queue_name,
                "task_name": preview.task_name,
                "original_task_id": str(preview.original_task_id),
            },
            reason=reason,
        )
        await self._session.commit()
        return preview

    async def _audit_replay_failure(
        self,
        *,
        dead_letter: SafeRabbitDeadLetter,
        current_user: AuthUserRecord,
        context: RequestContext,
        reason: str,
        error_type: str,
    ) -> None:
        await record_admin_audit_log(
            self._session,
            actor_id=current_user.id,
            action="rabbitmq.dead_letter.replay_failed",
            target_type="rabbitmq_dead_letter",
            target_id=dead_letter.target_id,
            ip_address=context.ip_address,
            user_agent=context.user_agent,
            metadata_json={
                "queue_name": dead_letter.queue_name,
                "task_name": dead_letter.task_name,
                "original_task_id": str(dead_letter.original_task_id),
                "error_type": error_type,
            },
            reason=reason,
        )
        await self._session.commit()

    async def _audit_denied(
        self,
        *,
        queue_name: RabbitQueueName,
        current_user: AuthUserRecord,
        context: RequestContext,
        error_type: str,
        reason: str,
    ) -> None:
        await record_admin_audit_log(
            self._session,
            actor_id=current_user.id,
            action="rabbitmq.dead_letter.replay_denied",
            target_type="rabbitmq_dead_letter_queue",
            target_id=uuid.uuid5(uuid.NAMESPACE_URL, f"rabbitmq-dlq:{queue_name}"),
            ip_address=context.ip_address,
            user_agent=context.user_agent,
            metadata_json={"queue_name": queue_name, "error_type": error_type},
            reason=reason,
        )
        await self._session.commit()

    def _public_error(self, error: RabbitDeadLetterError) -> exceptions.ConfigError:
        if isinstance(error, RabbitDeadLetterEmpty):
            return exceptions.rabbit_dead_letter_not_found()
        if isinstance(error, RabbitDeadLetterUnsafe):
            return exceptions.rabbit_dead_letter_unsafe()
        if isinstance(error, RabbitDeadLetterChanged):
            return exceptions.rabbit_dead_letter_changed()
        if isinstance(error, RabbitDeadLetterUnavailable):
            return exceptions.rabbit_dead_letter_unavailable()
        return exceptions.rabbit_dead_letter_unavailable()

    def _normalize_queue(self, queue_name: str) -> RabbitQueueName:
        if queue_name not in TASK_QUEUE_NAMES:
            raise exceptions.invalid_config_value("queue_name")
        return cast(RabbitQueueName, queue_name)

    def _require_system_admin(self, current_user: AuthUserRecord) -> None:
        if current_user.role != SYSTEM_ADMIN_ROLE:
            raise exceptions.permission_denied()
