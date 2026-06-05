from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, Text, func, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


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
        result = await self._session.execute(
            select(EventOutbox)
            .where(
                EventOutbox.published_at.is_(None),
                EventOutbox.publish_attempts < max_attempts,
            )
            .order_by(EventOutbox.occurred_at, EventOutbox.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list(result.scalars())

    async def mark_published(self, event: EventOutbox) -> None:
        event.published_at = datetime.now(UTC)
        event.last_error = None

    async def mark_failed(self, event: EventOutbox, error: str) -> None:
        event.publish_attempts += 1
        event.last_error = error[:2000]
