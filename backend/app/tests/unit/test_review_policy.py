from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.asyncio


async def test_review_policy_uses_bounded_runtime_values_and_snapshots_absolute_times(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core import review_policy

    values: dict[str, object] = {
        "review.sla_hours": 48,
        "review.claim_timeout_minutes": 90,
    }

    async def get_config(key: str) -> object | None:
        return values.get(key)

    monkeypatch.setattr(review_policy, "get_config", get_config)
    now = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)
    submitted_at, review_due_at = await review_policy.review_submission_times(now=now)
    claimed_at, claim_expires_at = await review_policy.review_claim_expiry(now=now)

    assert submitted_at == now
    assert review_due_at == now + timedelta(hours=48)
    assert claimed_at == now
    assert claim_expires_at == now + timedelta(minutes=90)

    values["review.sla_hours"] = 1
    values["review.claim_timeout_minutes"] = 5
    assert review_due_at == now + timedelta(hours=48)
    assert claim_expires_at == now + timedelta(minutes=90)


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("review.sla_hours", True, 24),
        ("review.sla_hours", 0, 24),
        ("review.sla_hours", 721, 24),
        ("review.claim_timeout_minutes", None, 30),
        ("review.claim_timeout_minutes", 4, 30),
        ("review.claim_timeout_minutes", 1441, 30),
    ],
)
async def test_review_policy_invalid_values_fall_back_safely(
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    value: object,
    expected: int,
) -> None:
    from app.core import review_policy

    async def get_config(config_key: str) -> object | None:
        return value if config_key == key else None

    monkeypatch.setattr(review_policy, "get_config", get_config)

    if key == "review.sla_hours":
        assert await review_policy.resolve_review_sla_hours() == expected
    else:
        assert await review_policy.resolve_claim_timeout_minutes() == expected
