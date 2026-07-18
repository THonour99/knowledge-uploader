"""Verify an endpoint owner's short-lived Ed25519 attestation offline.

The contract intentionally accepts only hashed endpoint identities and fixed
operation names.  It never accepts URLs, credentials, prompts, documents or
raw provider responses, so the verifier can be used in protected evidence jobs
without turning their artifacts or logs into a secret-bearing channel.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import re
import stat
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final, NoReturn

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

ATTESTATION_SCHEMA: Final = "knowledge-uploader.endpoint-owner-attestation.v1"
POLICY_SCHEMA: Final = "knowledge-uploader.endpoint-owner-trust-policy.v1"
CONTRACT_VERSION: Final = 1
ALGORITHM: Final = "Ed25519"

MAX_ATTESTATION_BYTES: Final = 64 * 1024
MAX_POLICY_BYTES: Final = 128 * 1024
MAX_POLICY_KEYS: Final = 32
MAX_HARD_LIFETIME_SECONDS: Final = 3600

HASH_PATTERN: Final = re.compile(r"[0-9a-f]{64}")
GIT_SHA_PATTERN: Final = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
REPOSITORY_PATTERN: Final = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
ENVIRONMENT_PATTERN: Final = re.compile(r"[a-z][a-z0-9-]{1,31}")
KEY_ID_PATTERN: Final = re.compile(r"[a-z0-9][a-z0-9._-]{2,63}")
POLICY_ID_PATTERN: Final = re.compile(r"[a-z0-9][a-z0-9._-]{2,127}")
NONCE_PATTERN: Final = re.compile(r"[A-Za-z0-9_-]{32,128}")
TIMESTAMP_PATTERN: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
BASE64URL_PATTERN: Final = re.compile(r"[A-Za-z0-9_-]+")

LLM_OPERATIONS: Final = ("chat.completions.create",)
RAGFLOW_OPERATIONS: Final = (
    "documents.list",
    "documents.upload",
    "documents.update",
    "documents.parse",
    "documents.delete",
)

SIGNED_FIELDS: Final = frozenset({"schema", "version", "algorithm", "key_id", "payload"})
ATTESTATION_FIELDS: Final = SIGNED_FIELDS | {"signature"}
COMMON_PAYLOAD_FIELDS: Final = frozenset(
    {
        "service_kind",
        "environment",
        "repository",
        "git_sha",
        "workflow_run_id",
        "workflow_run_attempt",
        "endpoint_identity_sha256",
        "tls_spki_sha256",
        "nonce",
        "issued_at",
        "not_before",
        "expires_at",
        "allowed_operations",
    }
)
LLM_PAYLOAD_FIELDS: Final = COMMON_PAYLOAD_FIELDS | {
    "internal",
    "private",
    "nonbillable",
    "provider_identity_sha256",
    "model_identity_sha256",
    "zero_pricing_policy",
}
RAGFLOW_PAYLOAD_FIELDS: Final = COMMON_PAYLOAD_FIELDS | {
    "dataset_identity_sha256",
    "dataset_isolated",
    "dataset_initially_empty",
}
ZERO_PRICING_FIELDS: Final = frozenset({"currency", "unit", "input_price", "output_price"})
POLICY_FIELDS: Final = frozenset(
    {
        "schema",
        "version",
        "policy_id",
        "max_attestation_lifetime_seconds",
        "max_attestation_age_seconds",
        "keys",
    }
)
POLICY_KEY_FIELDS: Final = frozenset(
    {
        "key_id",
        "algorithm",
        "public_key_base64url",
        "service_kind",
        "environment",
        "repository",
        "allowed_operations",
        "not_before",
        "expires_at",
    }
)


class AttestationVerificationError(RuntimeError):
    """A fail-closed verification error carrying only a non-sensitive code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _DuplicateJsonKey(ValueError):
    pass


@dataclass(frozen=True)
class ExpectedContext:
    """Values independently observed or supplied by the protected workflow."""

    service_kind: str
    environment: str
    repository: str
    git_sha: str
    workflow_run_id: int
    workflow_run_attempt: int
    endpoint_identity_sha256: str
    tls_spki_sha256: str
    nonce: str
    provider_identity_sha256: str | None = None
    model_identity_sha256: str | None = None
    dataset_identity_sha256: str | None = None


@dataclass(frozen=True)
class _PolicyKey:
    key_id: str
    public_key_bytes: bytes
    service_kind: str
    environment: str
    repository: str
    allowed_operations: tuple[str, ...]
    not_before: datetime
    expires_at: datetime


@dataclass(frozen=True)
class _TrustPolicy:
    max_lifetime: timedelta
    max_age: timedelta
    keys: Mapping[str, _PolicyKey]


def _raise(code: str) -> NoReturn:
    raise AttestationVerificationError(code)


def _mapping(value: object, code: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        _raise(code)
    return value


def _sequence(value: object, code: str) -> Sequence[object]:
    if not isinstance(value, list):
        _raise(code)
    return value


def _exact_keys(value: Mapping[str, object], expected: frozenset[str], code: str) -> None:
    if set(value) != expected:
        _raise(code)


def _text(value: object, pattern: re.Pattern[str], code: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        _raise(code)
    return value


def _positive_integer(value: object, code: str) -> int:
    if type(value) is not int or value < 1:
        _raise(code)
    return value


def _positive_bounded_integer(value: object, code: str) -> int:
    parsed = _positive_integer(value, code)
    if parsed > MAX_HARD_LIFETIME_SECONDS:
        _raise(code)
    return parsed


def _hash(value: object, code: str) -> str:
    return _text(value, HASH_PATTERN, code)


def _timestamp(value: object, code: str) -> datetime:
    text = _text(value, TIMESTAMP_PATTERN, code)
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        _raise(code)


def _service_kind(value: object, code: str) -> str:
    if not isinstance(value, str) or value not in {"llm", "ragflow"}:
        _raise(code)
    return value


def _operations(value: object, service_kind: str, code: str) -> tuple[str, ...]:
    raw = _sequence(value, code)
    operations = tuple(item for item in raw if isinstance(item, str))
    if len(operations) != len(raw):
        _raise(code)
    expected = LLM_OPERATIONS if service_kind == "llm" else RAGFLOW_OPERATIONS
    if operations != expected:
        _raise(code)
    return operations


def _decode_base64url(value: object, *, expected_size: int, code: str) -> bytes:
    text = _text(value, BASE64URL_PATTERN, code)
    try:
        decoded = base64.b64decode(
            text + "=" * (-len(text) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, binascii.Error):
        _raise(code)
    canonical = base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=")
    if len(decoded) != expected_size or canonical != text:
        _raise(code)
    return decoded


def _validate_zero_pricing(value: object) -> None:
    pricing = _mapping(value, "llm_constraints_invalid")
    _exact_keys(pricing, ZERO_PRICING_FIELDS, "llm_constraints_invalid")
    input_price = pricing.get("input_price")
    output_price = pricing.get("output_price")
    if (
        pricing.get("currency") != "USD"
        or pricing.get("unit") != "million_tokens"
        # Exact built-in integers reject JSON 0.0 and booleans.
        or type(input_price) is not int
        or input_price != 0
        or type(output_price) is not int
        or output_price != 0
    ):
        _raise("llm_constraints_invalid")


def _validate_payload(value: object) -> tuple[Mapping[str, object], str]:
    payload = _mapping(value, "attestation_schema_invalid")
    service_kind = _service_kind(payload.get("service_kind"), "payload_invalid")
    expected_fields = LLM_PAYLOAD_FIELDS if service_kind == "llm" else RAGFLOW_PAYLOAD_FIELDS
    _exact_keys(payload, expected_fields, "attestation_schema_invalid")

    _text(payload.get("environment"), ENVIRONMENT_PATTERN, "payload_invalid")
    _text(payload.get("repository"), REPOSITORY_PATTERN, "payload_invalid")
    _text(payload.get("git_sha"), GIT_SHA_PATTERN, "payload_invalid")
    _positive_integer(payload.get("workflow_run_id"), "workflow_binding_invalid")
    _positive_integer(payload.get("workflow_run_attempt"), "workflow_binding_invalid")
    _hash(payload.get("endpoint_identity_sha256"), "payload_invalid")
    _hash(payload.get("tls_spki_sha256"), "payload_invalid")
    _text(payload.get("nonce"), NONCE_PATTERN, "payload_invalid")
    _timestamp(payload.get("issued_at"), "time_invalid")
    _timestamp(payload.get("not_before"), "time_invalid")
    _timestamp(payload.get("expires_at"), "time_invalid")
    _operations(payload.get("allowed_operations"), service_kind, "operations_invalid")

    if service_kind == "llm":
        if (
            payload.get("internal") is not True
            or payload.get("private") is not True
            or payload.get("nonbillable") is not True
        ):
            _raise("llm_constraints_invalid")
        _hash(payload.get("provider_identity_sha256"), "llm_constraints_invalid")
        _hash(payload.get("model_identity_sha256"), "llm_constraints_invalid")
        _validate_zero_pricing(payload.get("zero_pricing_policy"))
    else:
        if (
            payload.get("dataset_isolated") is not True
            or payload.get("dataset_initially_empty") is not True
        ):
            _raise("ragflow_constraints_invalid")
        _hash(payload.get("dataset_identity_sha256"), "ragflow_constraints_invalid")
    return payload, service_kind


def _validate_signed_document(value: object) -> tuple[Mapping[str, object], str]:
    signed = _mapping(value, "attestation_schema_invalid")
    _exact_keys(signed, SIGNED_FIELDS, "attestation_schema_invalid")
    if (
        signed.get("schema") != ATTESTATION_SCHEMA
        or type(signed.get("version")) is not int
        or signed.get("version") != CONTRACT_VERSION
    ):
        _raise("attestation_schema_invalid")
    if signed.get("algorithm") != ALGORITHM:
        _raise("algorithm_invalid")
    _text(signed.get("key_id"), KEY_ID_PATTERN, "key_id_invalid")
    _, service_kind = _validate_payload(signed.get("payload"))
    return signed, service_kind


def canonical_signed_bytes(signed_document: Mapping[str, object]) -> bytes:
    """Return the only byte representation accepted for Ed25519 signatures."""

    signed, _ = _validate_signed_document(signed_document)
    canonical = {key: signed[key] for key in sorted(SIGNED_FIELDS)}
    return json.dumps(
        canonical,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _parse_policy(value: object) -> _TrustPolicy:
    policy = _mapping(value, "policy_invalid")
    _exact_keys(policy, POLICY_FIELDS, "policy_invalid")
    if (
        policy.get("schema") != POLICY_SCHEMA
        or type(policy.get("version")) is not int
        or policy.get("version") != CONTRACT_VERSION
    ):
        _raise("policy_invalid")
    _text(policy.get("policy_id"), POLICY_ID_PATTERN, "policy_invalid")
    max_lifetime = _positive_bounded_integer(
        policy.get("max_attestation_lifetime_seconds"), "policy_invalid"
    )
    max_age = _positive_bounded_integer(policy.get("max_attestation_age_seconds"), "policy_invalid")
    raw_keys = _sequence(policy.get("keys"), "policy_invalid")
    if not raw_keys or len(raw_keys) > MAX_POLICY_KEYS:
        _raise("policy_invalid")

    keys: dict[str, _PolicyKey] = {}
    public_keys: set[bytes] = set()
    for raw_key in raw_keys:
        item = _mapping(raw_key, "policy_invalid")
        _exact_keys(item, POLICY_KEY_FIELDS, "policy_invalid")
        key_id = _text(item.get("key_id"), KEY_ID_PATTERN, "policy_invalid")
        if item.get("algorithm") != ALGORITHM:
            _raise("policy_invalid")
        public_key = _decode_base64url(
            item.get("public_key_base64url"), expected_size=32, code="policy_invalid"
        )
        try:
            Ed25519PublicKey.from_public_bytes(public_key)
        except ValueError:
            _raise("policy_invalid")
        service_kind = _service_kind(item.get("service_kind"), "policy_invalid")
        environment = _text(item.get("environment"), ENVIRONMENT_PATTERN, "policy_invalid")
        repository = _text(item.get("repository"), REPOSITORY_PATTERN, "policy_invalid")
        operations = _operations(item.get("allowed_operations"), service_kind, "policy_invalid")
        not_before = _timestamp(item.get("not_before"), "policy_invalid")
        expires_at = _timestamp(item.get("expires_at"), "policy_invalid")
        if not_before >= expires_at or key_id in keys or public_key in public_keys:
            _raise("policy_invalid")
        keys[key_id] = _PolicyKey(
            key_id=key_id,
            public_key_bytes=public_key,
            service_kind=service_kind,
            environment=environment,
            repository=repository,
            allowed_operations=operations,
            not_before=not_before,
            expires_at=expires_at,
        )
        public_keys.add(public_key)
    return _TrustPolicy(
        max_lifetime=timedelta(seconds=max_lifetime),
        max_age=timedelta(seconds=max_age),
        keys=keys,
    )


def _validate_expected_context(expected: ExpectedContext) -> None:
    service_kind = _service_kind(expected.service_kind, "expected_context_invalid")
    _text(expected.environment, ENVIRONMENT_PATTERN, "expected_context_invalid")
    _text(expected.repository, REPOSITORY_PATTERN, "expected_context_invalid")
    _text(expected.git_sha, GIT_SHA_PATTERN, "expected_context_invalid")
    _positive_integer(expected.workflow_run_id, "expected_context_invalid")
    _positive_integer(expected.workflow_run_attempt, "expected_context_invalid")
    _hash(expected.endpoint_identity_sha256, "expected_context_invalid")
    _hash(expected.tls_spki_sha256, "expected_context_invalid")
    _text(expected.nonce, NONCE_PATTERN, "expected_context_invalid")
    if service_kind == "llm":
        _hash(expected.provider_identity_sha256, "expected_context_invalid")
        _hash(expected.model_identity_sha256, "expected_context_invalid")
        if expected.dataset_identity_sha256 is not None:
            _raise("expected_context_invalid")
    else:
        _hash(expected.dataset_identity_sha256, "expected_context_invalid")
        if (
            expected.provider_identity_sha256 is not None
            or expected.model_identity_sha256 is not None
        ):
            _raise("expected_context_invalid")


def _verify_context(payload: Mapping[str, object], expected: ExpectedContext) -> None:
    common = {
        "service_kind": expected.service_kind,
        "environment": expected.environment,
        "repository": expected.repository,
        "git_sha": expected.git_sha,
        "workflow_run_id": expected.workflow_run_id,
        "workflow_run_attempt": expected.workflow_run_attempt,
        "endpoint_identity_sha256": expected.endpoint_identity_sha256,
        "tls_spki_sha256": expected.tls_spki_sha256,
        "nonce": expected.nonce,
    }
    if any(payload.get(key) != value for key, value in common.items()):
        _raise("context_mismatch")
    if expected.service_kind == "llm":
        if (
            payload.get("provider_identity_sha256") != expected.provider_identity_sha256
            or payload.get("model_identity_sha256") != expected.model_identity_sha256
        ):
            _raise("context_mismatch")
    elif payload.get("dataset_identity_sha256") != expected.dataset_identity_sha256:
        _raise("context_mismatch")


def _verify_time_window(
    payload: Mapping[str, object], policy: _TrustPolicy, key: _PolicyKey, *, now: datetime
) -> None:
    issued_at = _timestamp(payload.get("issued_at"), "time_invalid")
    not_before = _timestamp(payload.get("not_before"), "time_invalid")
    expires_at = _timestamp(payload.get("expires_at"), "time_invalid")
    if (
        not_before > issued_at
        or issued_at >= expires_at
        or issued_at > now
        or not_before > now
        or now >= expires_at
        or expires_at - not_before > policy.max_lifetime
        or now - issued_at > policy.max_age
        or key.not_before > not_before
        or expires_at > key.expires_at
        or now < key.not_before
        or now >= key.expires_at
    ):
        _raise("time_invalid")


def verify_attestation(
    attestation: Mapping[str, object],
    policy_document: Mapping[str, object],
    *,
    expected: ExpectedContext,
    now: datetime | None = None,
) -> None:
    """Verify signature, trust policy, context, service constraints and freshness."""

    _validate_expected_context(expected)
    document = _mapping(attestation, "attestation_schema_invalid")
    _exact_keys(document, ATTESTATION_FIELDS, "attestation_schema_invalid")
    signed_document = {key: document[key] for key in SIGNED_FIELDS}
    signed, service_kind = _validate_signed_document(signed_document)
    payload = _mapping(signed.get("payload"), "attestation_schema_invalid")
    policy = _parse_policy(policy_document)

    key_id = _text(signed.get("key_id"), KEY_ID_PATTERN, "key_id_invalid")
    key = policy.keys.get(key_id)
    if key is None:
        _raise("untrusted_key")
    operations = _operations(payload.get("allowed_operations"), service_kind, "operations_invalid")
    if (
        key.service_kind != service_kind
        or key.environment != payload.get("environment")
        or key.repository != payload.get("repository")
        or key.allowed_operations != operations
    ):
        _raise("policy_binding_invalid")

    signature = _decode_base64url(
        document.get("signature"), expected_size=64, code="signature_invalid"
    )
    try:
        Ed25519PublicKey.from_public_bytes(key.public_key_bytes).verify(
            signature, canonical_signed_bytes(signed)
        )
    except (InvalidSignature, ValueError):
        _raise("signature_invalid")

    _verify_context(payload, expected)
    instant = datetime.now(UTC) if now is None else now
    if instant.tzinfo is None:
        _raise("expected_context_invalid")
    _verify_time_window(payload, policy, key, now=instant.astimezone(UTC))


def _reject_json_constant(_: str) -> NoReturn:
    raise ValueError("non-finite JSON number")


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(key)
        result[key] = value
    return result


def _read_stable_regular_file(path: Path, *, max_bytes: int) -> bytes:
    descriptor = -1
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            _raise("input_invalid")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            or opened.st_size > max_bytes
        ):
            _raise("input_invalid")
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = -1
            payload = stream.read(max_bytes + 1)
            after = os.fstat(stream.fileno())
        current = path.lstat()
    except (OSError, ValueError):
        _raise("input_invalid")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    opened_identity = (
        opened.st_dev,
        opened.st_ino,
        opened.st_size,
        opened.st_mtime_ns,
    )
    if (
        not payload
        or len(payload) > max_bytes
        or len(payload) != opened.st_size
        or opened_identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or opened_identity != (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)
        or not stat.S_ISREG(current.st_mode)
    ):
        _raise("input_invalid")
    return payload


def _load_json(path: Path, *, max_bytes: int) -> Mapping[str, object]:
    raw = _read_stable_regular_file(path, max_bytes=max_bytes)
    if b"\x00" in raw:
        _raise("input_invalid")
    try:
        text = raw.decode("utf-8")
        parsed: object = json.loads(
            text,
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
        )
    except (
        UnicodeError,
        json.JSONDecodeError,
        ValueError,
        RecursionError,
        _DuplicateJsonKey,
    ):
        _raise("input_invalid")
    return _mapping(parsed, "input_invalid")


def verify_files(*, attestation_path: Path, policy_path: Path, expected: ExpectedContext) -> None:
    attestation = _load_json(attestation_path, max_bytes=MAX_ATTESTATION_BYTES)
    policy = _load_json(policy_path, max_bytes=MAX_POLICY_BYTES)
    verify_attestation(attestation, policy, expected=expected)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attestation", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--service-kind", choices=("llm", "ragflow"), required=True)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--git-sha", required=True)
    parser.add_argument("--workflow-run-id", type=int, required=True)
    parser.add_argument("--workflow-run-attempt", type=int, required=True)
    parser.add_argument("--endpoint-identity-sha256", required=True)
    parser.add_argument("--tls-spki-sha256", required=True)
    parser.add_argument("--nonce", required=True)
    parser.add_argument("--provider-identity-sha256")
    parser.add_argument("--model-identity-sha256")
    parser.add_argument("--dataset-identity-sha256")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    expected = ExpectedContext(
        service_kind=args.service_kind,
        environment=args.environment,
        repository=args.repository,
        git_sha=args.git_sha,
        workflow_run_id=args.workflow_run_id,
        workflow_run_attempt=args.workflow_run_attempt,
        endpoint_identity_sha256=args.endpoint_identity_sha256,
        tls_spki_sha256=args.tls_spki_sha256,
        nonce=args.nonce,
        provider_identity_sha256=args.provider_identity_sha256,
        model_identity_sha256=args.model_identity_sha256,
        dataset_identity_sha256=args.dataset_identity_sha256,
    )
    try:
        verify_files(
            attestation_path=args.attestation,
            policy_path=args.policy,
            expected=expected,
        )
    except AttestationVerificationError as error:
        print(
            f"endpoint owner attestation verification failed: {error.code}",
            file=sys.stderr,
        )
        return 1
    print("endpoint owner attestation verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
