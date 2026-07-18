from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .llm_analysis import estimate_cost_microunits

CostStatus = Literal["known", "unknown_pricing", "unknown_usage", "legacy_unverifiable"]


@dataclass(frozen=True)
class CostObservation:
    status: CostStatus
    estimated_cost_microunits: int | None


def pricing_confirmation_is_effective(
    *,
    declared: bool,
    input_price_microunits_per_million_tokens: int,
    output_price_microunits_per_million_tokens: int,
    pricing_currency: str,
    confirmed_input_microunits_per_million: int | None,
    confirmed_output_microunits_per_million: int | None,
    confirmed_currency: str | None,
) -> bool:
    return bool(
        declared
        and confirmed_input_microunits_per_million is not None
        and confirmed_output_microunits_per_million is not None
        and confirmed_currency is not None
        and confirmed_input_microunits_per_million == input_price_microunits_per_million_tokens
        and confirmed_output_microunits_per_million == output_price_microunits_per_million_tokens
        and confirmed_currency == pricing_currency
    )


def pricing_confirmation_basis(
    *,
    configured: bool,
    input_price_microunits_per_million_tokens: int,
    output_price_microunits_per_million_tokens: int,
    pricing_currency: str,
) -> tuple[int | None, int | None, str | None]:
    if not configured:
        return None, None, None
    return (
        input_price_microunits_per_million_tokens,
        output_price_microunits_per_million_tokens,
        pricing_currency,
    )


def resolve_create_pricing_configured(
    *,
    explicit: bool | None,
    input_price_microunits_per_million_tokens: int,
    output_price_microunits_per_million_tokens: int,
) -> bool:
    if explicit is not None:
        return explicit
    return bool(
        input_price_microunits_per_million_tokens or output_price_microunits_per_million_tokens
    )


def resolve_update_pricing_configured(
    *,
    explicit: bool | None,
    previous: bool,
    pricing_fields_submitted: bool,
    input_price_microunits_per_million_tokens: int,
    output_price_microunits_per_million_tokens: int,
) -> bool:
    if explicit is not None:
        return explicit
    if pricing_fields_submitted and (
        input_price_microunits_per_million_tokens > 0
        or output_price_microunits_per_million_tokens > 0
    ):
        return True
    return previous


def observe_llm_cost(
    *,
    pricing_configured: bool,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    input_price_microunits_per_million_tokens: int,
    output_price_microunits_per_million_tokens: int,
) -> CostObservation:
    """Return an explicit unknown status instead of turning unavailable cost into zero."""
    if prompt_tokens is None or completion_tokens is None:
        return CostObservation(status="unknown_usage", estimated_cost_microunits=None)
    if prompt_tokens < 0 or completion_tokens < 0:
        return CostObservation(status="unknown_usage", estimated_cost_microunits=None)
    if not pricing_configured:
        return CostObservation(status="unknown_pricing", estimated_cost_microunits=None)
    try:
        estimated_cost = estimate_cost_microunits(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            input_price_microunits_per_million_tokens=input_price_microunits_per_million_tokens,
            output_price_microunits_per_million_tokens=output_price_microunits_per_million_tokens,
        )
    except OverflowError:
        return CostObservation(status="unknown_usage", estimated_cost_microunits=None)
    except ValueError:
        return CostObservation(status="unknown_pricing", estimated_cost_microunits=None)
    return CostObservation(
        status="known",
        estimated_cost_microunits=estimated_cost,
    )


def aggregate_cost_observation(
    *,
    call_observation: CostObservation,
    aggregate_currency: str,
    call_currency: str,
) -> CostObservation:
    if aggregate_currency != call_currency:
        return CostObservation(status="unknown_pricing", estimated_cost_microunits=None)
    return call_observation


def merge_cost_status(current: CostStatus, observed: CostStatus) -> CostStatus:
    """Conservatively preserve the strongest reason an aggregate cost is unknown."""
    priority: dict[CostStatus, int] = {
        "known": 0,
        "legacy_unverifiable": 1,
        "unknown_pricing": 2,
        "unknown_usage": 3,
    }
    return current if priority[current] >= priority[observed] else observed
