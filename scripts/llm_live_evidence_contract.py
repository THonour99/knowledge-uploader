"""Pure validation contract for protected live LLM evidence.

This module deliberately has no ``app.*`` dependency so release authorization and
readiness checks can validate evidence without loading the runtime LLM adapter.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final, NoReturn

PROBE_SCHEMA: Final = "knowledge-uploader.llm-live-probe-receipt.v1"
CONTRACT_VERSION: Final = 1
WORKFLOW_PATH: Final = ".github/workflows/protected-llm-evidence.yml"
PROVIDER_IDENTITY: Final = "openai_compatible"
SYSTEM_PROMPT: Final = (
    "This is a synthetic availability probe. Follow the user instruction exactly and emit no "
    "additional text."
)
USER_PROMPT: Final = "Return exactly the ASCII token KU_LLM_LIVE_OK and nothing else."
EXPECTED_RESPONSE: Final = "KU_LLM_LIVE_OK"
TIMEOUT_SECONDS: Final = 20
MAX_OUTPUT_TOKENS: Final = 16
MAX_PROMPT_TOKENS: Final = 512
MAX_JSON_BYTES: Final = 128 * 1024
EVIDENCE_TTL: Final = timedelta(minutes=15)
ENV_BASE_URL: Final = "PROTECTED_LLM_BASE_URL"
ENV_API_KEY: Final = "PROTECTED_LLM_API_KEY"
ENV_MODEL: Final = "PROTECTED_LLM_MODEL"
ENV_SPKI_PIN: Final = "PROTECTED_LLM_TLS_SPKI_PIN"

HASH_PATTERN: Final = re.compile(r"[0-9a-f]{64}")
GIT_SHA_PATTERN: Final = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
REPOSITORY_PATTERN: Final = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
ENVIRONMENT_PATTERN: Final = re.compile(r"[a-z][a-z0-9-]{1,31}")
NONCE_PATTERN: Final = re.compile(r"[A-Za-z0-9_-]{32,128}")
TIMESTAMP_PATTERN: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")

TOP_LEVEL_FIELDS: Final = frozenset(
    {
        "schema",
        "version",
        "status",
        "environment",
        "repository",
        "git_sha",
        "workflow",
        "main_ci",
        "owner_attestation",
        "identities",
        "probe",
        "usage",
        "cost",
        "started_at",
        "succeeded_at",
        "expires_at",
    }
)
WORKFLOW_FIELDS: Final = frozenset({"path", "run_id", "run_attempt", "trust_summary_sha256"})
MAIN_CI_FIELDS: Final = frozenset({"run_id", "run_attempt"})
ATTESTATION_FIELDS: Final = frozenset({"algorithm", "attestation_sha256", "policy_sha256", "nonce"})
IDENTITY_FIELDS: Final = frozenset(
    {
        "endpoint_sha256",
        "tls_spki_sha256",
        "provider_sha256",
        "requested_model_sha256",
        "response_model_sha256",
    }
)
PROBE_FIELDS: Final = frozenset(
    {
        "prompt_contract_sha256",
        "response_contract_sha256",
        "request_count",
        "retry_count",
        "timeout_seconds",
        "max_output_tokens",
        "latency_ms",
    }
)
USAGE_FIELDS: Final = frozenset({"prompt_tokens", "completion_tokens", "total_tokens"})
COST_FIELDS: Final = frozenset(
    {
        "currency",
        "input_price_microusd_per_million_tokens",
        "output_price_microusd_per_million_tokens",
        "estimated_cost_microusd",
    }
)


class LLMLiveProbeError(RuntimeError):
    """A bounded failure code that is safe to print in protected workflow logs."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _DuplicateJsonKey(ValueError):
    pass


@dataclass(frozen=True)
class ProbeContext:
    environment: str
    repository: str
    git_sha: str
    nonce: str
    workflow_run_id: int
    workflow_run_attempt: int
    main_ci_run_id: int
    main_ci_run_attempt: int


def _fail(code: str) -> NoReturn:
    raise LLMLiveProbeError(code)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _identity_hash(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def verify_policy_sha256(payload: bytes, expected_sha256: str) -> None:
    """Reject an artifact-supplied owner policy unless a protected anchor matches."""

    if HASH_PATTERN.fullmatch(expected_sha256) is None or _sha256_bytes(payload) != expected_sha256:
        _fail("owner_policy_anchor_invalid")


def prompt_contract_sha256() -> str:
    return _sha256_bytes(
        _canonical_bytes(
            {
                "json_mode": False,
                "max_output_tokens": MAX_OUTPUT_TOKENS,
                "response": EXPECTED_RESPONSE,
                "system_prompt": SYSTEM_PROMPT,
                "temperature": 0,
                "top_p": 1,
                "user_prompt": USER_PROMPT,
            }
        )
    )


def response_contract_sha256() -> str:
    return _identity_hash(EXPECTED_RESPONSE)


def _utc_second(value: datetime) -> datetime:
    if value.tzinfo is None:
        _fail("clock_invalid")
    return value.astimezone(UTC).replace(microsecond=0)


def _timestamp(value: datetime) -> str:
    return _utc_second(value).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_timestamp(value: object, *, code: str) -> datetime:
    if not isinstance(value, str) or TIMESTAMP_PATTERN.fullmatch(value) is None:
        _fail(code)
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        _fail(code)


def _positive_integer(value: object, *, code: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        _fail(code)
    return value


def _nonnegative_integer(value: object, *, code: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        _fail(code)
    return value


def _hash(value: object, *, code: str) -> str:
    if not isinstance(value, str) or HASH_PATTERN.fullmatch(value) is None:
        _fail(code)
    return value


def _exact_mapping(
    value: object,
    *,
    fields: frozenset[str],
    code: str,
) -> Mapping[str, object]:
    if (
        not isinstance(value, dict)
        or not all(isinstance(key, str) for key in value)
        or set(value) != fields
    ):
        _fail(code)
    return value


def _strict_json_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(key)
        result[key] = value
    return result


def _reject_json_constant(_: str) -> NoReturn:
    raise ValueError("non-finite JSON number")


def read_stable_regular_file(path: Path, *, maximum: int = MAX_JSON_BYTES) -> bytes:
    descriptor = -1
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            _fail("input_file_invalid")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            or opened.st_size > maximum
        ):
            _fail("input_file_invalid")
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = -1
            payload = stream.read(maximum + 1)
            after = os.fstat(stream.fileno())
        current = path.lstat()
    except (OSError, ValueError) as error:
        raise LLMLiveProbeError("input_file_invalid") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    identity = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
    if (
        not payload
        or len(payload) > maximum
        or len(payload) != opened.st_size
        or identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or identity != (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)
        or not stat.S_ISREG(current.st_mode)
    ):
        _fail("input_file_invalid")
    return payload


def load_strict_json_object(payload: bytes) -> Mapping[str, object]:
    if b"\x00" in payload:
        _fail("input_json_invalid")
    try:
        value: object = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_json_pairs,
            parse_constant=_reject_json_constant,
        )
    except (
        UnicodeError,
        json.JSONDecodeError,
        ValueError,
        RecursionError,
        _DuplicateJsonKey,
    ) as error:
        raise LLMLiveProbeError("input_json_invalid") from error
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        _fail("input_json_invalid")
    return value


def _write_new_file(path: Path, payload: bytes) -> None:
    descriptor = -1
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        raise LLMLiveProbeError("output_write_failed") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def write_canonical_json(path: Path, value: object) -> None:
    _write_new_file(path, _canonical_bytes(value) + b"\n")


def _validate_context(context: ProbeContext) -> None:
    if ENVIRONMENT_PATTERN.fullmatch(context.environment) is None:
        _fail("context_invalid")
    if REPOSITORY_PATTERN.fullmatch(context.repository) is None:
        _fail("context_invalid")
    if GIT_SHA_PATTERN.fullmatch(context.git_sha) is None:
        _fail("context_invalid")
    if NONCE_PATTERN.fullmatch(context.nonce) is None:
        _fail("context_invalid")
    for value in (
        context.workflow_run_id,
        context.workflow_run_attempt,
        context.main_ci_run_id,
        context.main_ci_run_attempt,
    ):
        _positive_integer(value, code="context_invalid")


def _verify_owner_attestation(
    *,
    attestation: Mapping[str, object],
    policy: Mapping[str, object],
    context: ProbeContext,
    endpoint_sha256: str,
    tls_spki_sha256: str,
    provider_sha256: str,
    model_sha256: str,
    now: datetime,
) -> None:
    try:
        from scripts.verify_endpoint_owner_attestation import (
            AttestationVerificationError,
            ExpectedContext,
            verify_attestation,
        )
    except ModuleNotFoundError as error:  # pragma: no cover - direct script execution
        if error.name != "scripts":
            raise
        from verify_endpoint_owner_attestation import (  # type: ignore[import-not-found,no-redef]
            AttestationVerificationError,
            ExpectedContext,
            verify_attestation,
        )
    try:
        verify_attestation(
            attestation,
            policy,
            expected=ExpectedContext(
                service_kind="llm",
                environment=context.environment,
                repository=context.repository,
                git_sha=context.git_sha,
                workflow_run_id=context.workflow_run_id,
                workflow_run_attempt=context.workflow_run_attempt,
                endpoint_identity_sha256=endpoint_sha256,
                tls_spki_sha256=tls_spki_sha256,
                nonce=context.nonce,
                provider_identity_sha256=provider_sha256,
                model_identity_sha256=model_sha256,
            ),
            now=now,
        )
    except AttestationVerificationError as error:
        raise LLMLiveProbeError("owner_attestation_invalid") from error


def validate_probe_receipt(
    value: object,
    *,
    expected_context: ProbeContext,
    now: datetime,
) -> Mapping[str, object]:
    """Validate the exact, hash-only receipt schema and its replay/TTL bindings."""

    _validate_context(expected_context)
    receipt = _exact_mapping(value, fields=TOP_LEVEL_FIELDS, code="probe_receipt_schema_invalid")
    if (
        receipt.get("schema") != PROBE_SCHEMA
        or type(receipt.get("version")) is not int
        or receipt.get("version") != CONTRACT_VERSION
        or receipt.get("status") != "passed"
        or receipt.get("environment") != expected_context.environment
        or receipt.get("repository") != expected_context.repository
        or receipt.get("git_sha") != expected_context.git_sha
    ):
        _fail("probe_receipt_context_invalid")

    workflow = _exact_mapping(
        receipt.get("workflow"), fields=WORKFLOW_FIELDS, code="probe_receipt_schema_invalid"
    )
    if (
        workflow.get("path") != WORKFLOW_PATH
        or workflow.get("run_id") != expected_context.workflow_run_id
        or workflow.get("run_attempt") != expected_context.workflow_run_attempt
    ):
        _fail("probe_receipt_context_invalid")
    _hash(workflow.get("trust_summary_sha256"), code="probe_receipt_schema_invalid")

    main_ci = _exact_mapping(
        receipt.get("main_ci"), fields=MAIN_CI_FIELDS, code="probe_receipt_schema_invalid"
    )
    if (
        main_ci.get("run_id") != expected_context.main_ci_run_id
        or main_ci.get("run_attempt") != expected_context.main_ci_run_attempt
    ):
        _fail("probe_receipt_context_invalid")

    owner = _exact_mapping(
        receipt.get("owner_attestation"),
        fields=ATTESTATION_FIELDS,
        code="probe_receipt_schema_invalid",
    )
    if owner.get("algorithm") != "Ed25519" or owner.get("nonce") != expected_context.nonce:
        _fail("probe_receipt_context_invalid")
    _hash(owner.get("attestation_sha256"), code="probe_receipt_schema_invalid")
    _hash(owner.get("policy_sha256"), code="probe_receipt_schema_invalid")

    identities = _exact_mapping(
        receipt.get("identities"),
        fields=IDENTITY_FIELDS,
        code="probe_receipt_schema_invalid",
    )
    for field in IDENTITY_FIELDS:
        _hash(identities.get(field), code="probe_receipt_schema_invalid")
    if identities.get("requested_model_sha256") != identities.get("response_model_sha256"):
        _fail("probe_receipt_contract_invalid")

    probe = _exact_mapping(
        receipt.get("probe"), fields=PROBE_FIELDS, code="probe_receipt_schema_invalid"
    )
    if (
        probe.get("prompt_contract_sha256") != prompt_contract_sha256()
        or probe.get("response_contract_sha256") != response_contract_sha256()
        or probe.get("request_count") != 1
        or probe.get("retry_count") != 0
        or probe.get("timeout_seconds") != TIMEOUT_SECONDS
        or probe.get("max_output_tokens") != MAX_OUTPUT_TOKENS
    ):
        _fail("probe_receipt_contract_invalid")
    latency_ms = _nonnegative_integer(probe.get("latency_ms"), code="probe_receipt_schema_invalid")
    if latency_ms > (TIMEOUT_SECONDS + 5) * 1000:
        _fail("probe_receipt_contract_invalid")

    usage = _exact_mapping(
        receipt.get("usage"), fields=USAGE_FIELDS, code="probe_receipt_schema_invalid"
    )
    prompt_tokens = _positive_integer(
        usage.get("prompt_tokens"), code="probe_receipt_schema_invalid"
    )
    completion_tokens = _positive_integer(
        usage.get("completion_tokens"), code="probe_receipt_schema_invalid"
    )
    total_tokens = _positive_integer(usage.get("total_tokens"), code="probe_receipt_schema_invalid")
    if (
        prompt_tokens > MAX_PROMPT_TOKENS
        or completion_tokens > MAX_OUTPUT_TOKENS
        or total_tokens != prompt_tokens + completion_tokens
    ):
        _fail("probe_receipt_contract_invalid")

    cost = _exact_mapping(
        receipt.get("cost"), fields=COST_FIELDS, code="probe_receipt_schema_invalid"
    )
    if (
        cost.get("currency") != "USD"
        or type(cost.get("input_price_microusd_per_million_tokens")) is not int
        or cost.get("input_price_microusd_per_million_tokens") != 0
        or type(cost.get("output_price_microusd_per_million_tokens")) is not int
        or cost.get("output_price_microusd_per_million_tokens") != 0
        or type(cost.get("estimated_cost_microusd")) is not int
        or cost.get("estimated_cost_microusd") != 0
    ):
        _fail("probe_receipt_cost_invalid")

    started_at = _parse_timestamp(receipt.get("started_at"), code="probe_receipt_time_invalid")
    succeeded_at = _parse_timestamp(receipt.get("succeeded_at"), code="probe_receipt_time_invalid")
    expires_at = _parse_timestamp(receipt.get("expires_at"), code="probe_receipt_time_invalid")
    instant = _utc_second(now)
    if (
        started_at > succeeded_at
        or succeeded_at - started_at > timedelta(seconds=TIMEOUT_SECONDS + 5)
        or succeeded_at > instant + timedelta(minutes=5)
        or instant >= expires_at
        or expires_at - succeeded_at > EVIDENCE_TTL
    ):
        _fail("probe_receipt_time_invalid")
    return receipt
