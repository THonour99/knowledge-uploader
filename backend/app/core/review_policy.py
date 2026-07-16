from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.runtime_config import get_config

DEFAULT_REVIEW_SLA_HOURS = 24
MIN_REVIEW_SLA_HOURS = 1
MAX_REVIEW_SLA_HOURS = 720
DEFAULT_CLAIM_TIMEOUT_MINUTES = 30
MIN_CLAIM_TIMEOUT_MINUTES = 5
MAX_CLAIM_TIMEOUT_MINUTES = 1440
REVIEW_DUE_SOON_HOURS = 4


async def resolve_review_sla_hours() -> int:
    return await _bounded_int_config(
        "review.sla_hours",
        default=DEFAULT_REVIEW_SLA_HOURS,
        minimum=MIN_REVIEW_SLA_HOURS,
        maximum=MAX_REVIEW_SLA_HOURS,
    )


async def resolve_claim_timeout_minutes() -> int:
    return await _bounded_int_config(
        "review.claim_timeout_minutes",
        default=DEFAULT_CLAIM_TIMEOUT_MINUTES,
        minimum=MIN_CLAIM_TIMEOUT_MINUTES,
        maximum=MAX_CLAIM_TIMEOUT_MINUTES,
    )


async def review_submission_times(
    *,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    submitted_at = now or datetime.now(UTC)
    if submitted_at.tzinfo is None:
        submitted_at = submitted_at.replace(tzinfo=UTC)
    sla_hours = await resolve_review_sla_hours()
    return submitted_at, submitted_at + timedelta(hours=sla_hours)


async def review_claim_expiry(*, now: datetime | None = None) -> tuple[datetime, datetime]:
    claimed_at = now or datetime.now(UTC)
    if claimed_at.tzinfo is None:
        claimed_at = claimed_at.replace(tzinfo=UTC)
    timeout_minutes = await resolve_claim_timeout_minutes()
    return claimed_at, claimed_at + timedelta(minutes=timeout_minutes)


async def _bounded_int_config(
    key: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = await get_config(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    if value < minimum or value > maximum:
        return default
    return value
