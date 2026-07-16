from __future__ import annotations

import hashlib
import hmac
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    and_,
    func,
    or_,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import get_settings
from app.core.request_ids import normalize_opaque_request_id
from app.db.base import Base

MAX_QUARANTINE_BATCH_SIZE = 1000
OUTBOX_PAYLOAD_SUMMARY_KEY_CONTEXT = b"knowledge-uploader:outbox-payload-summary:v1"


@dataclass(frozen=True)
class OutboxMessage:
    id: int
    event_type: str
    aggregate_type: str
    aggregate_id: str
    payload: dict[str, object]
    occurred_at: datetime


class EventOutbox(Base):
    __tablename__ = "event_outbox"
    __table_args__ = (
        Index(
            "idx_outbox_pending",
            "occurred_at",
            postgresql_where=text("published_at IS NULL"),
        ),
        Index("idx_outbox_event_type", "event_type"),
        Index("idx_outbox_aggregate", "aggregate_type", "aggregate_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(80), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    publish_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    trace_id: Mapped[str | None] = mapped_column(String(64))
    first_publish_failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_publish_failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OutboxDeadLetter(Base):
    __tablename__ = "outbox_dead_letters"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'requeued', 'resolved')",
            name="ck_outbox_dead_letters_status",
        ),
        CheckConstraint("attempts >= 0", name="ck_outbox_dead_letters_attempts_nonnegative"),
        CheckConstraint(
            "replay_count >= 0",
            name="ck_outbox_dead_letters_replay_count_nonnegative",
        ),
        Index("uq_outbox_dead_letters_event_id", "event_id", unique=True),
        Index(
            "idx_outbox_dead_letters_status_last_failed_at",
            "status",
            "last_failed_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    event_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("event_outbox.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        server_default=text("'pending'"),
    )
    first_failed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )
    last_failed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    error_type: Mapped[str] = mapped_column(String(120), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(80), nullable=False)
    trace_id: Mapped[str | None] = mapped_column(String(64))
    payload_summary: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    replay_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    last_replayed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_replayed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    last_replay_reason: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


@dataclass(frozen=True)
class DeadLetterRecord:
    dead_letter: OutboxDeadLetter
    event: EventOutbox


@dataclass(frozen=True)
class DeadLetterReplay:
    dead_letter: OutboxDeadLetter
    event: EventOutbox
    queued: bool


@dataclass(frozen=True)
class OutboxHealth:
    pending: int
    oldest_seconds: float
    dead_letter_pending: int
    dead_letter_requeued: int
    dead_letter_resolved: int


class OutboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(
        self,
        *,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: dict[str, object],
        trace_id: str | None = None,
    ) -> EventOutbox:
        event = EventOutbox(
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            payload=payload,
            trace_id=trace_id,
        )
        self._session.add(event)
        return event

    async def fetch_pending(self, *, limit: int, max_attempts: int) -> list[EventOutbox]:
        await self.quarantine_exhausted(
            max_attempts=max_attempts,
            limit=min(max(limit, 1), MAX_QUARANTINE_BATCH_SIZE),
        )
        result = await self._session.execute(
            select(EventOutbox)
            .outerjoin(
                OutboxDeadLetter,
                OutboxDeadLetter.event_id == EventOutbox.id,
            )
            .where(
                EventOutbox.published_at.is_(None),
                or_(
                    and_(
                        OutboxDeadLetter.id.is_(None),
                        EventOutbox.publish_attempts < max_attempts,
                    ),
                    OutboxDeadLetter.status == "requeued",
                ),
            )
            .order_by(EventOutbox.occurred_at, EventOutbox.id)
            .limit(limit)
            .with_for_update(of=EventOutbox, skip_locked=True)
        )
        return list(result.scalars())

    async def mark_published(self, event: EventOutbox) -> None:
        now = datetime.now(UTC)
        event.published_at = now
        event.last_error = None
        result = await self._session.execute(
            select(OutboxDeadLetter).where(OutboxDeadLetter.event_id == event.id).with_for_update()
        )
        dead_letter = result.scalar_one_or_none()
        if dead_letter is not None and dead_letter.status != "resolved":
            dead_letter.status = "resolved"
            dead_letter.resolved_at = now

    async def health(self, *, max_attempts: int) -> OutboxHealth:
        await self.quarantine_exhausted(
            max_attempts=max_attempts,
            limit=MAX_QUARANTINE_BATCH_SIZE,
        )
        pending_result = await self._session.execute(
            select(func.count(EventOutbox.id), func.min(EventOutbox.occurred_at))
            .outerjoin(
                OutboxDeadLetter,
                OutboxDeadLetter.event_id == EventOutbox.id,
            )
            .where(
                EventOutbox.published_at.is_(None),
                or_(
                    and_(
                        OutboxDeadLetter.id.is_(None),
                        EventOutbox.publish_attempts < max_attempts,
                    ),
                    OutboxDeadLetter.status == "requeued",
                ),
            )
        )
        pending_count, oldest_at = pending_result.one()
        status_result = await self._session.execute(
            select(OutboxDeadLetter.status, func.count(OutboxDeadLetter.id)).group_by(
                OutboxDeadLetter.status
            )
        )
        status_counts = {str(status): int(count) for status, count in status_result}
        oldest_seconds = 0.0
        if isinstance(oldest_at, datetime):
            normalized_oldest = (
                oldest_at if oldest_at.tzinfo is not None else oldest_at.replace(tzinfo=UTC)
            )
            oldest_seconds = max((datetime.now(UTC) - normalized_oldest).total_seconds(), 0.0)
        return OutboxHealth(
            pending=int(pending_count),
            oldest_seconds=oldest_seconds,
            dead_letter_pending=status_counts.get("pending", 0),
            dead_letter_requeued=status_counts.get("requeued", 0),
            dead_letter_resolved=status_counts.get("resolved", 0),
        )

    async def quarantine_exhausted(self, *, max_attempts: int, limit: int) -> int:
        """Backfill one bounded DLQ batch when a runtime retry limit is lowered."""
        if limit < 1 or limit > MAX_QUARANTINE_BATCH_SIZE:
            raise ValueError(f"quarantine limit must be between 1 and {MAX_QUARANTINE_BATCH_SIZE}")
        result = await self._session.execute(
            select(EventOutbox)
            .outerjoin(
                OutboxDeadLetter,
                OutboxDeadLetter.event_id == EventOutbox.id,
            )
            .where(
                EventOutbox.published_at.is_(None),
                EventOutbox.publish_attempts >= max_attempts,
                OutboxDeadLetter.id.is_(None),
            )
            .order_by(EventOutbox.id)
            .limit(limit)
            .with_for_update(of=EventOutbox, skip_locked=True)
        )
        events = list(result.scalars())
        now = datetime.now(UTC)
        for event in events:
            if event.id is None:
                raise RuntimeError("outbox event must be persisted before quarantine")
            first_failed_at = (
                event.first_publish_failed_at
                or event.last_publish_failed_at
                or event.occurred_at
                or now
            )
            last_failed_at = event.last_publish_failed_at or first_failed_at
            self._session.add(
                OutboxDeadLetter(
                    event_id=event.id,
                    status="pending",
                    first_failed_at=first_failed_at,
                    last_failed_at=last_failed_at,
                    attempts=event.publish_attempts,
                    error_type=_safe_error_type(event.last_error or "RetryLimitReducedError"),
                    correlation_id=f"outbox:{event.id}",
                    trace_id=_safe_trace_id(event.trace_id),
                    payload_summary=_safe_payload_summary(event.payload),
                )
            )
        return len(events)

    async def mark_failed(
        self,
        event: EventOutbox,
        error: str,
        *,
        max_attempts: int,
    ) -> None:
        now = datetime.now(UTC)
        event.publish_attempts += 1
        event.last_error = _safe_error_type(error)
        if event.first_publish_failed_at is None:
            event.first_publish_failed_at = now
        event.last_publish_failed_at = now

        result = await self._session.execute(
            select(OutboxDeadLetter).where(OutboxDeadLetter.event_id == event.id).with_for_update()
        )
        dead_letter = result.scalar_one_or_none()
        if dead_letter is not None:
            dead_letter.last_failed_at = now
            dead_letter.attempts = event.publish_attempts
            dead_letter.error_type = event.last_error
            dead_letter.resolved_at = None
        if event.publish_attempts < max_attempts:
            return

        if dead_letter is None:
            if event.id is None:
                raise RuntimeError("outbox event must be persisted before delivery")
            self._session.add(
                OutboxDeadLetter(
                    event_id=event.id,
                    status="pending",
                    first_failed_at=event.first_publish_failed_at or now,
                    last_failed_at=now,
                    attempts=event.publish_attempts,
                    error_type=event.last_error,
                    correlation_id=f"outbox:{event.id}",
                    trace_id=_safe_trace_id(event.trace_id),
                    payload_summary=_safe_payload_summary(event.payload),
                )
            )
            return
        dead_letter.status = "pending"

    async def list_dead_letters(
        self,
        *,
        page: int,
        page_size: int,
        status: str | None,
    ) -> tuple[list[DeadLetterRecord], int]:
        filters = []
        if status is not None:
            filters.append(OutboxDeadLetter.status == status)
        total_result = await self._session.execute(
            select(func.count(OutboxDeadLetter.id)).where(*filters)
        )
        total = int(total_result.scalar_one())
        rows_result = await self._session.execute(
            select(OutboxDeadLetter, EventOutbox)
            .join(EventOutbox, EventOutbox.id == OutboxDeadLetter.event_id)
            .where(*filters)
            .order_by(OutboxDeadLetter.last_failed_at.desc(), OutboxDeadLetter.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return (
            [
                DeadLetterRecord(dead_letter=dead_letter, event=event)
                for dead_letter, event in rows_result.tuples()
            ],
            total,
        )

    async def get_dead_letter(
        self,
        dead_letter_id: uuid.UUID,
    ) -> DeadLetterRecord | None:
        result = await self._session.execute(
            select(OutboxDeadLetter, EventOutbox)
            .join(EventOutbox, EventOutbox.id == OutboxDeadLetter.event_id)
            .where(OutboxDeadLetter.id == dead_letter_id)
        )
        row = result.one_or_none()
        if row is None:
            return None
        dead_letter, event = row
        return DeadLetterRecord(dead_letter=dead_letter, event=event)

    async def replay_dead_letter(
        self,
        *,
        dead_letter_id: uuid.UUID,
        actor_id: uuid.UUID,
        reason: str,
    ) -> DeadLetterReplay | None:
        result = await self._session.execute(
            select(OutboxDeadLetter, EventOutbox)
            .join(EventOutbox, EventOutbox.id == OutboxDeadLetter.event_id)
            .where(OutboxDeadLetter.id == dead_letter_id)
            .with_for_update()
        )
        row = result.one_or_none()
        if row is None:
            return None
        dead_letter, event = row
        if dead_letter.status in {"requeued", "resolved"} or event.published_at is not None:
            if event.published_at is not None and dead_letter.status != "resolved":
                dead_letter.status = "resolved"
                dead_letter.resolved_at = event.published_at
            return DeadLetterReplay(dead_letter=dead_letter, event=event, queued=False)

        now = datetime.now(UTC)
        event.publish_attempts = 0
        event.last_error = None
        event.first_publish_failed_at = None
        event.last_publish_failed_at = None
        # requeued means accepted for another delivery attempt, not published successfully.
        dead_letter.status = "requeued"
        dead_letter.replay_count += 1
        dead_letter.last_replayed_at = now
        dead_letter.last_replayed_by = actor_id
        dead_letter.last_replay_reason = reason
        dead_letter.resolved_at = None
        return DeadLetterReplay(dead_letter=dead_letter, event=event, queued=True)


def _safe_error_type(error: str) -> str:
    candidate = error.strip()
    if (
        re.fullmatch(
            r"(?:[A-Za-z_][A-Za-z0-9_]*\.)*[A-Za-z_][A-Za-z0-9_]*(?:Error|Exception)",
            candidate,
        )
        is not None
        and len(candidate) <= 120
    ):
        return candidate
    # Pre-DLQ releases persisted str(exc) in this column. Never transform that
    # historical free text into API-visible identifiers because alphanumeric
    # fragments could still contain credentials, URLs, email addresses, or PII.
    return "LegacyPublishError"


def _safe_trace_id(trace_id: str | None) -> str | None:
    return normalize_opaque_request_id(trace_id)


def _safe_payload_summary(payload: dict[str, object]) -> dict[str, object]:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    summary_key = hmac.new(
        get_settings().encryption_key.encode("utf-8"),
        OUTBOX_PAYLOAD_SUMMARY_KEY_CONTEXT,
        hashlib.sha256,
    ).digest()
    digest = hmac.new(summary_key, canonical, hashlib.sha256).hexdigest()
    field_names = sorted(
        {
            normalized
            for raw_name in payload
            if (normalized := re.sub(r"[^A-Za-z0-9_.-]", "_", str(raw_name))[:64])
        }
    )[:32]
    return {
        "field_names": field_names,
        "field_count": len(payload),
        "encoded_bytes": len(canonical),
        "hmac_sha256": digest,
    }
