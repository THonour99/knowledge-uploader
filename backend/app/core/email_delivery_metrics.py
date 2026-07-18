from __future__ import annotations

import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import cast

from redis.asyncio import Redis, from_url

EMAIL_DELIVERY_METRICS_KEY = "metrics:notification:email-delivery"
EMAIL_DELIVERY_RESULTS = frozenset(
    {
        "success",
        "failure",
        "configuration_failure",
        "expired",
        "invalid_envelope",
        "publish_failure",
    }
)


@dataclass(frozen=True)
class EmailDeliveryMetricsSnapshot:
    totals: dict[str, int]
    last_timestamps: dict[str, float]


async def record_email_delivery_result(*, redis_url: str, result: str) -> None:
    if result not in EMAIL_DELIVERY_RESULTS:
        raise ValueError("email delivery result is invalid")
    client = cast(Callable[..., Redis], from_url)(
        redis_url,
        encoding="utf-8",
        decode_responses=True,
    )
    try:
        pipeline = client.pipeline(transaction=True)
        pipeline.hincrby(EMAIL_DELIVERY_METRICS_KEY, f"{result}_total", 1)
        pipeline.hset(
            EMAIL_DELIVERY_METRICS_KEY,
            mapping={f"last_{result}_timestamp_seconds": f"{time.time():.6f}"},
        )
        await pipeline.execute()
    finally:
        await client.aclose()


async def read_email_delivery_metrics(*, redis_url: str) -> EmailDeliveryMetricsSnapshot:
    client = cast(Callable[..., Redis], from_url)(
        redis_url,
        encoding="utf-8",
        decode_responses=True,
    )
    try:
        raw_values = await cast(
            Awaitable[dict[str, str]],
            client.hgetall(EMAIL_DELIVERY_METRICS_KEY),
        )
    finally:
        await client.aclose()
    if not isinstance(raw_values, dict):
        raise RuntimeError("email delivery metrics state is invalid")
    totals: dict[str, int] = {}
    last_timestamps: dict[str, float] = {}
    for result in EMAIL_DELIVERY_RESULTS:
        totals[result] = _nonnegative_int(raw_values.get(f"{result}_total", "0"))
        last_timestamps[result] = _nonnegative_float(
            raw_values.get(f"last_{result}_timestamp_seconds", "0")
        )
    return EmailDeliveryMetricsSnapshot(
        totals=totals,
        last_timestamps=last_timestamps,
    )


def _nonnegative_int(value: object) -> int:
    try:
        parsed = int(str(value))
    except ValueError as error:
        raise RuntimeError("email delivery metrics counter is invalid") from error
    if parsed < 0:
        raise RuntimeError("email delivery metrics counter is invalid")
    return parsed


def _nonnegative_float(value: object) -> float:
    try:
        parsed = float(str(value))
    except ValueError as error:
        raise RuntimeError("email delivery metrics timestamp is invalid") from error
    if not math.isfinite(parsed) or parsed < 0:
        raise RuntimeError("email delivery metrics timestamp is invalid")
    return parsed
