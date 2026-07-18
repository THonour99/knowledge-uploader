"""Run one protected, nonbillable LLM probe and write a hash-only receipt.

The endpoint, credential, model name, prompt and response are deliberately read or
defined inside this process and are never serialized.  A successful call uses the
application's production ``OpenAICompatibleProvider`` so DNS authorization, pinned IP,
CA/hostname verification and SPKI verification all apply to the same TLS connection.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from scripts.llm_live_evidence_contract import (
    CONTRACT_VERSION as CONTRACT_VERSION,
)
from scripts.llm_live_evidence_contract import (
    ENV_API_KEY as ENV_API_KEY,
)
from scripts.llm_live_evidence_contract import (
    ENV_BASE_URL as ENV_BASE_URL,
)
from scripts.llm_live_evidence_contract import (
    ENV_MODEL as ENV_MODEL,
)
from scripts.llm_live_evidence_contract import (
    ENV_SPKI_PIN as ENV_SPKI_PIN,
)
from scripts.llm_live_evidence_contract import (
    EVIDENCE_TTL as EVIDENCE_TTL,
)
from scripts.llm_live_evidence_contract import (
    EXPECTED_RESPONSE as EXPECTED_RESPONSE,
)
from scripts.llm_live_evidence_contract import (
    MAX_OUTPUT_TOKENS as MAX_OUTPUT_TOKENS,
)
from scripts.llm_live_evidence_contract import (
    MAX_PROMPT_TOKENS as MAX_PROMPT_TOKENS,
)
from scripts.llm_live_evidence_contract import (
    PROBE_SCHEMA as PROBE_SCHEMA,
)
from scripts.llm_live_evidence_contract import (
    PROVIDER_IDENTITY as PROVIDER_IDENTITY,
)
from scripts.llm_live_evidence_contract import (
    SYSTEM_PROMPT as SYSTEM_PROMPT,
)
from scripts.llm_live_evidence_contract import (
    TIMEOUT_SECONDS as TIMEOUT_SECONDS,
)
from scripts.llm_live_evidence_contract import (
    USER_PROMPT as USER_PROMPT,
)
from scripts.llm_live_evidence_contract import (
    WORKFLOW_PATH as WORKFLOW_PATH,
)
from scripts.llm_live_evidence_contract import (
    LLMLiveProbeError as LLMLiveProbeError,
)
from scripts.llm_live_evidence_contract import (
    ProbeContext as ProbeContext,
)
from scripts.llm_live_evidence_contract import (
    _fail,
    _identity_hash,
    _parse_timestamp,
    _sha256_bytes,
    _timestamp,
    _utc_second,
    _validate_context,
    _verify_owner_attestation,
    load_strict_json_object,
    prompt_contract_sha256,
    read_stable_regular_file,
    response_contract_sha256,
    validate_probe_receipt,
    verify_policy_sha256,
    write_canonical_json,
)

from app.adapters.llm.base import LLMCompletion, LLMProviderError
from app.adapters.llm.openai_compatible import OpenAICompatibleProvider, validate_model_name
from app.core.llm_endpoint import normalize_llm_base_url, normalized_llm_tls_spki_pins


class LLMCompleter(Protocol):
    async def complete(
        self,
        prompt: str,
        *,
        model: str | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        max_output_tokens: int | None = None,
        system_prompt: str | None = None,
        json_mode: bool = False,
    ) -> LLMCompletion: ...


class ProviderFactory(Protocol):
    def __call__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model: str,
        timeout_seconds: float,
        raw_allowed_base_urls: str,
        allow_external: bool,
        is_internal: bool,
        raw_tls_spki_pins: str,
        require_tls_spki_pin: bool,
    ) -> LLMCompleter: ...


def _required_environment(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name, "").strip()
    if not value:
        _fail("runtime_configuration_invalid")
    return value


def _endpoint_context(
    environment: Mapping[str, str],
) -> tuple[str, str, str, str, str]:
    try:
        base_url = normalize_llm_base_url(_required_environment(environment, ENV_BASE_URL))
        model = validate_model_name(_required_environment(environment, ENV_MODEL))
        raw_pin = _required_environment(environment, ENV_SPKI_PIN)
        raw_pin_mapping = json.dumps(
            {base_url: [raw_pin]}, ensure_ascii=True, separators=(",", ":")
        )
        parsed_pins = normalized_llm_tls_spki_pins(raw_pin_mapping)
    except ValueError as error:
        raise LLMLiveProbeError("runtime_configuration_invalid") from error
    pins = parsed_pins.get(base_url, frozenset())
    if not base_url.startswith("https://") or len(pins) != 1:
        _fail("runtime_configuration_invalid")
    tls_spki_sha256 = next(iter(pins)).hex()
    return (
        base_url,
        model,
        raw_pin_mapping,
        _identity_hash(base_url),
        tls_spki_sha256,
    )


def _default_provider_factory(
    *,
    base_url: str,
    api_key: str | None,
    model: str,
    timeout_seconds: float,
    raw_allowed_base_urls: str,
    allow_external: bool,
    is_internal: bool,
    raw_tls_spki_pins: str,
    require_tls_spki_pin: bool,
) -> LLMCompleter:
    return OpenAICompatibleProvider(
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout_seconds=timeout_seconds,
        raw_allowed_base_urls=raw_allowed_base_urls,
        allow_external=allow_external,
        is_internal=is_internal,
        raw_tls_spki_pins=raw_tls_spki_pins,
        require_tls_spki_pin=require_tls_spki_pin,
    )


def _owner_documents(
    *,
    attestation_path: Path,
    policy_path: Path,
) -> tuple[bytes, Mapping[str, object], bytes, Mapping[str, object]]:
    attestation_bytes = read_stable_regular_file(attestation_path)
    policy_bytes = read_stable_regular_file(policy_path)
    return (
        attestation_bytes,
        load_strict_json_object(attestation_bytes),
        policy_bytes,
        load_strict_json_object(policy_bytes),
    )


def _attestation_expiry(attestation: Mapping[str, object]) -> datetime:
    payload = attestation.get("payload")
    if not isinstance(payload, dict):
        _fail("owner_attestation_invalid")
    return _parse_timestamp(payload.get("expires_at"), code="owner_attestation_invalid")


def _validate_completion(completion: LLMCompletion, *, model: str) -> None:
    if completion.content != EXPECTED_RESPONSE or completion.model != model:
        _fail("provider_contract_mismatch")
    prompt_tokens = completion.usage.prompt_tokens
    completion_tokens = completion.usage.completion_tokens
    if (
        isinstance(prompt_tokens, bool)
        or isinstance(completion_tokens, bool)
        or not isinstance(prompt_tokens, int)
        or not isinstance(completion_tokens, int)
        or not 1 <= prompt_tokens <= MAX_PROMPT_TOKENS
        or not 1 <= completion_tokens <= MAX_OUTPUT_TOKENS
        or not isinstance(completion.latency_ms, int)
        or isinstance(completion.latency_ms, bool)
        or not 0 <= completion.latency_ms <= (TIMEOUT_SECONDS + 5) * 1000
    ):
        _fail("provider_contract_mismatch")


def _receipt(
    *,
    context: ProbeContext,
    attestation_sha256: str,
    policy_sha256: str,
    workflow_trust_sha256: str,
    endpoint_sha256: str,
    tls_spki_sha256: str,
    provider_sha256: str,
    model_sha256: str,
    completion: LLMCompletion,
    started_at: datetime,
    succeeded_at: datetime,
    expires_at: datetime,
) -> dict[str, object]:
    prompt_tokens = completion.usage.prompt_tokens
    completion_tokens = completion.usage.completion_tokens
    return {
        "schema": PROBE_SCHEMA,
        "version": CONTRACT_VERSION,
        "status": "passed",
        "environment": context.environment,
        "repository": context.repository,
        "git_sha": context.git_sha,
        "workflow": {
            "path": WORKFLOW_PATH,
            "run_id": context.workflow_run_id,
            "run_attempt": context.workflow_run_attempt,
            "trust_summary_sha256": workflow_trust_sha256,
        },
        "main_ci": {
            "run_id": context.main_ci_run_id,
            "run_attempt": context.main_ci_run_attempt,
        },
        "owner_attestation": {
            "algorithm": "Ed25519",
            "attestation_sha256": attestation_sha256,
            "policy_sha256": policy_sha256,
            "nonce": context.nonce,
        },
        "identities": {
            "endpoint_sha256": endpoint_sha256,
            "tls_spki_sha256": tls_spki_sha256,
            "provider_sha256": provider_sha256,
            "requested_model_sha256": model_sha256,
            "response_model_sha256": model_sha256,
        },
        "probe": {
            "prompt_contract_sha256": prompt_contract_sha256(),
            "response_contract_sha256": response_contract_sha256(),
            "request_count": 1,
            "retry_count": 0,
            "timeout_seconds": TIMEOUT_SECONDS,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "latency_ms": completion.latency_ms,
        },
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
        "cost": {
            "currency": "USD",
            "input_price_microusd_per_million_tokens": 0,
            "output_price_microusd_per_million_tokens": 0,
            "estimated_cost_microusd": 0,
        },
        "started_at": _timestamp(started_at),
        "succeeded_at": _timestamp(succeeded_at),
        "expires_at": _timestamp(expires_at),
    }


async def run_live_probe(
    *,
    context: ProbeContext,
    attestation_path: Path,
    policy_path: Path,
    expected_owner_policy_sha256: str,
    workflow_trust_path: Path,
    output_path: Path,
    environment: Mapping[str, str],
    provider_factory: ProviderFactory = _default_provider_factory,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Mapping[str, object]:
    """Verify owner authorization, perform exactly one request, then write a receipt."""

    _validate_context(context)
    base_url, model, raw_pin_mapping, endpoint_sha256, tls_spki_sha256 = _endpoint_context(
        environment
    )
    provider_sha256 = _identity_hash(PROVIDER_IDENTITY)
    model_sha256 = _identity_hash(model)
    attestation_bytes, attestation, policy_bytes, policy = _owner_documents(
        attestation_path=attestation_path,
        policy_path=policy_path,
    )
    verify_policy_sha256(policy_bytes, expected_owner_policy_sha256)
    workflow_trust_bytes = read_stable_regular_file(workflow_trust_path)
    started_at = _utc_second(clock())
    _verify_owner_attestation(
        attestation=attestation,
        policy=policy,
        context=context,
        endpoint_sha256=endpoint_sha256,
        tls_spki_sha256=tls_spki_sha256,
        provider_sha256=provider_sha256,
        model_sha256=model_sha256,
        now=started_at,
    )

    api_key = environment.get(ENV_API_KEY, "").strip() or None
    provider = provider_factory(
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout_seconds=float(TIMEOUT_SECONDS),
        raw_allowed_base_urls=base_url,
        allow_external=False,
        is_internal=True,
        raw_tls_spki_pins=raw_pin_mapping,
        require_tls_spki_pin=True,
    )
    try:
        completion = await provider.complete(
            USER_PROMPT,
            model=model,
            temperature=0.0,
            top_p=1.0,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            system_prompt=SYSTEM_PROMPT,
            json_mode=False,
        )
    except LLMProviderError as error:
        raise LLMLiveProbeError("provider_request_failed") from error
    except Exception as error:
        raise LLMLiveProbeError("provider_request_failed") from error
    _validate_completion(completion, model=model)

    succeeded_at = _utc_second(clock())
    if succeeded_at < started_at or succeeded_at - started_at > timedelta(
        seconds=TIMEOUT_SECONDS + 5
    ):
        _fail("probe_time_invalid")
    owner_expiry = _attestation_expiry(attestation)
    expires_at = min(succeeded_at + EVIDENCE_TTL, owner_expiry)
    if expires_at <= succeeded_at:
        _fail("owner_attestation_expired")
    receipt = _receipt(
        context=context,
        attestation_sha256=_sha256_bytes(attestation_bytes),
        policy_sha256=_sha256_bytes(policy_bytes),
        workflow_trust_sha256=_sha256_bytes(workflow_trust_bytes),
        endpoint_sha256=endpoint_sha256,
        tls_spki_sha256=tls_spki_sha256,
        provider_sha256=provider_sha256,
        model_sha256=model_sha256,
        completion=completion,
        started_at=started_at,
        succeeded_at=succeeded_at,
        expires_at=expires_at,
    )
    validate_probe_receipt(receipt, expected_context=context, now=succeeded_at)
    write_canonical_json(output_path, receipt)
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--git-sha", required=True)
    parser.add_argument("--nonce", required=True)
    parser.add_argument("--workflow-run-id", required=True, type=int)
    parser.add_argument("--workflow-run-attempt", required=True, type=int)
    parser.add_argument("--main-ci-run-id", required=True, type=int)
    parser.add_argument("--main-ci-run-attempt", required=True, type=int)
    parser.add_argument("--owner-attestation", required=True, type=Path)
    parser.add_argument("--owner-policy", required=True, type=Path)
    parser.add_argument("--expected-owner-policy-sha256", required=True)
    parser.add_argument("--workflow-trust", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    context = ProbeContext(
        environment=arguments.environment,
        repository=arguments.repository,
        git_sha=arguments.git_sha,
        nonce=arguments.nonce,
        workflow_run_id=arguments.workflow_run_id,
        workflow_run_attempt=arguments.workflow_run_attempt,
        main_ci_run_id=arguments.main_ci_run_id,
        main_ci_run_attempt=arguments.main_ci_run_attempt,
    )
    try:
        asyncio.run(
            run_live_probe(
                context=context,
                attestation_path=arguments.owner_attestation,
                policy_path=arguments.owner_policy,
                expected_owner_policy_sha256=arguments.expected_owner_policy_sha256,
                workflow_trust_path=arguments.workflow_trust,
                output_path=arguments.output,
                environment=os.environ,
            )
        )
    except LLMLiveProbeError as error:
        print(f"LLM live probe failed: {error.code}", file=sys.stderr)
        return 1
    except Exception:
        print("LLM live probe failed: internal_error", file=sys.stderr)
        return 1
    print("LLM live probe verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
