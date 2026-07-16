from __future__ import annotations

import re
import uuid
from typing import Literal, Protocol, cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_admin_audit_log
from app.core.outbox import DeadLetterRecord, OutboxRepository
from app.core.request_ids import normalize_opaque_request_id
from app.modules.user.schemas import AuthUserRecord

from . import exceptions
from .permissions import SYSTEM_ADMIN_ROLE
from .schemas import (
    DeadLetterItemResponse,
    DeadLetterListResponse,
    DeadLetterReplayResponse,
)


class RequestContext(Protocol):
    @property
    def ip_address(self) -> str: ...

    @property
    def user_agent(self) -> str: ...


class DeadLetterService:
    def __init__(self, *, session: AsyncSession) -> None:
        self._session = session
        self._repository = OutboxRepository(session)

    async def list_dead_letters(
        self,
        *,
        page: int,
        page_size: int,
        status: str | None,
        current_user: AuthUserRecord,
        context: RequestContext,
    ) -> DeadLetterListResponse:
        self._require_system_admin(current_user)
        if status not in {None, "pending", "requeued", "resolved"}:
            raise exceptions.invalid_config_value("status")
        records, total = await self._repository.list_dead_letters(
            page=page,
            page_size=page_size,
            status=status,
        )
        await record_admin_audit_log(
            self._session,
            actor_id=current_user.id,
            action="outbox.dead_letter.list",
            target_type="outbox_dead_letter",
            target_id=uuid.uuid5(uuid.NAMESPACE_URL, "outbox-dead-letter-list"),
            ip_address=context.ip_address,
            user_agent=context.user_agent,
            metadata_json={
                "page": page,
                "page_size": page_size,
                "status": status,
                "result_count": len(records),
            },
        )
        await self._session.commit()
        return DeadLetterListResponse(
            items=[self._item_response(record) for record in records],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def get_dead_letter(
        self,
        *,
        dead_letter_id: uuid.UUID,
        current_user: AuthUserRecord,
        context: RequestContext,
    ) -> DeadLetterItemResponse:
        self._require_system_admin(current_user)
        record = await self._repository.get_dead_letter(dead_letter_id)
        if record is None:
            raise exceptions.dead_letter_not_found()
        await record_admin_audit_log(
            self._session,
            actor_id=current_user.id,
            action="outbox.dead_letter.view",
            target_type="outbox_dead_letter",
            target_id=dead_letter_id,
            ip_address=context.ip_address,
            user_agent=context.user_agent,
            metadata_json={"event_id": record.event.id, "status": record.dead_letter.status},
        )
        await self._session.commit()
        return self._item_response(record)

    async def replay_dead_letter(
        self,
        *,
        dead_letter_id: uuid.UUID,
        reason: str,
        current_user: AuthUserRecord,
        context: RequestContext,
    ) -> DeadLetterReplayResponse:
        self._require_system_admin(current_user)
        result = await self._repository.replay_dead_letter(
            dead_letter_id=dead_letter_id,
            actor_id=current_user.id,
            reason=reason,
        )
        if result is None:
            raise exceptions.dead_letter_not_found()
        response = DeadLetterReplayResponse(
            item=self._item_response(
                DeadLetterRecord(dead_letter=result.dead_letter, event=result.event)
            ),
            replay_queued=result.queued,
        )
        await record_admin_audit_log(
            self._session,
            actor_id=current_user.id,
            action="outbox.dead_letter.replay",
            target_type="outbox_dead_letter",
            target_id=dead_letter_id,
            ip_address=context.ip_address,
            user_agent=context.user_agent,
            metadata_json={
                "event_id": result.event.id,
                "replay_count": result.dead_letter.replay_count,
                "replay_queued": result.queued,
            },
            reason=reason,
        )
        await self._session.commit()
        return response

    def _item_response(self, record: DeadLetterRecord) -> DeadLetterItemResponse:
        dead_letter = record.dead_letter
        event = record.event
        return DeadLetterItemResponse(
            id=dead_letter.id,
            event_id=event.id,
            event_type=event.event_type,
            aggregate_type=event.aggregate_type,
            aggregate_id=event.aggregate_id,
            status=cast(Literal["pending", "requeued", "resolved"], dead_letter.status),
            first_failed_at=dead_letter.first_failed_at,
            last_failed_at=dead_letter.last_failed_at,
            attempts=dead_letter.attempts,
            error_type=dead_letter.error_type,
            correlation_id=dead_letter.correlation_id,
            trace_id=normalize_opaque_request_id(dead_letter.trace_id),
            payload_summary=_safe_response_summary(dead_letter.payload_summary),
            replay_count=dead_letter.replay_count,
            last_replayed_at=dead_letter.last_replayed_at,
            resolved_at=dead_letter.resolved_at,
        )

    def _require_system_admin(self, current_user: AuthUserRecord) -> None:
        if current_user.role != SYSTEM_ADMIN_ROLE:
            raise exceptions.permission_denied()


_SAFE_SUMMARY_KEYS = frozenset({"field_names", "field_count", "encoded_bytes", "hmac_sha256"})
_SUMMARY_NAME_PATTERN = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_SUMMARY_HMAC_PATTERN = re.compile(r"[0-9a-f]{64}")
_MAX_SUMMARY_FIELDS = 32
_MAX_SUMMARY_COUNT = 1_000_000
_MAX_SUMMARY_BYTES = 1_073_741_824


def _safe_response_summary(raw_summary: object) -> dict[str, object]:
    """Rebuild the public summary so dirty JSONB rows cannot expose arbitrary values."""
    if not isinstance(raw_summary, dict):
        raw_summary = {}

    raw_field_names = raw_summary.get("field_names")
    field_names: list[str] = []
    if isinstance(raw_field_names, list):
        valid_names = {
            value
            for value in raw_field_names
            if isinstance(value, str) and _SUMMARY_NAME_PATTERN.fullmatch(value) is not None
        }
        field_names = sorted(valid_names)[:_MAX_SUMMARY_FIELDS]

    raw_hmac = raw_summary.get("hmac_sha256")
    hmac_sha256 = (
        raw_hmac
        if isinstance(raw_hmac, str) and _SUMMARY_HMAC_PATTERN.fullmatch(raw_hmac) is not None
        else "0" * 64
    )
    safe_summary = {
        "field_names": field_names,
        "field_count": _safe_summary_integer(
            raw_summary.get("field_count"),
            maximum=_MAX_SUMMARY_COUNT,
        ),
        "encoded_bytes": _safe_summary_integer(
            raw_summary.get("encoded_bytes"),
            maximum=_MAX_SUMMARY_BYTES,
        ),
        "hmac_sha256": hmac_sha256,
    }
    assert set(safe_summary) == _SAFE_SUMMARY_KEYS
    return safe_summary


def _safe_summary_integer(value: object, *, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value if 0 <= value <= maximum else 0
