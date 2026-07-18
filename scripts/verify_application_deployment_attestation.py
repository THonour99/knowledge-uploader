"""Verify a deployment owner's short-lived Ed25519 application attestation.

This contract is intentionally distinct from an external endpoint-owner
attestation. It verifies an independently signed environment-owner confirmation
that binds one exact main-CI bundle to one hashed protected application endpoint.
The verifier does not itself observe deployment-platform state. URLs, credentials,
application responses and signing keys are not accepted fields.
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

ATTESTATION_SCHEMA: Final = "knowledge-uploader.application-deployment-owner-attestation.v1"
POLICY_SCHEMA: Final = "knowledge-uploader.application-deployment-owner-trust-policy.v1"
CONTRACT_VERSION: Final = 1
ALGORITHM: Final = "Ed25519"
OWNER_ROLE: Final = "application_deployment_owner"
DEPLOYMENT_PERMISSION: Final = "confirm.application-deployment"

MAX_ATTESTATION_BYTES: Final = 64 * 1024
MAX_POLICY_BYTES: Final = 128 * 1024
MAX_POLICY_KEYS: Final = 32
MAX_HARD_LIFETIME_SECONDS: Final = 3600

HASH_PATTERN: Final = re.compile(r"[0-9a-f]{64}")
DIGEST_PATTERN: Final = re.compile(r"sha256:[0-9a-f]{64}")
GIT_SHA_PATTERN: Final = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
REPOSITORY_PATTERN: Final = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
ENVIRONMENT_PATTERN: Final = re.compile(r"[a-z][a-z0-9-]{1,31}")
KEY_ID_PATTERN: Final = re.compile(r"[a-z0-9][a-z0-9._-]{2,63}")
POLICY_ID_PATTERN: Final = re.compile(r"[a-z0-9][a-z0-9._-]{2,127}")
NONCE_PATTERN: Final = re.compile(r"[A-Za-z0-9_-]{32,128}")
TIMESTAMP_PATTERN: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
BASE64URL_PATTERN: Final = re.compile(r"[A-Za-z0-9_-]+")

SIGNED_FIELDS: Final = frozenset({"schema", "version", "algorithm", "key_id", "payload"})
ATTESTATION_FIELDS: Final = SIGNED_FIELDS | {"signature"}
PAYLOAD_FIELDS: Final = frozenset(
    {
        "owner_role",
        "permission",
        "environment",
        "repository",
        "git_sha",
        "workflow_run_id",
        "workflow_run_attempt",
        "nonce",
        "app_endpoint_identity_sha256",
        "app_tls_spki_sha256",
        "main_ci_run_id",
        "main_ci_run_attempt",
        "main_bundle_artifact_id",
        "main_bundle_artifact_digest",
        "deployment_identity_sha256",
        "artifact_deployed",
        "issued_at",
        "not_before",
        "expires_at",
    }
)
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
        "owner_role",
        "environment",
        "repository",
        "permissions",
        "not_before",
        "expires_at",
    }
)


class DeploymentAttestationVerificationError(RuntimeError):
    """A fail-closed verification error containing only a safe error code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _DuplicateJsonKey(ValueError):
    pass


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> NoReturn:
        raise DeploymentAttestationVerificationError("arguments_invalid")


@dataclass(frozen=True)
class ExpectedDeploymentContext:
    """Values independently supplied by workflow trust and the live app probe."""

    environment: str
    repository: str
    git_sha: str
    workflow_run_id: int
    workflow_run_attempt: int
    nonce: str
    app_endpoint_identity_sha256: str
    app_tls_spki_sha256: str
    main_ci_run_id: int
    main_ci_run_attempt: int
    main_bundle_artifact_id: int
    main_bundle_artifact_digest: str
    deployment_identity_sha256: str


@dataclass(frozen=True)
class _PolicyKey:
    key_id: str
    public_key_bytes: bytes
    environment: str
    repository: str
    not_before: datetime
    expires_at: datetime


@dataclass(frozen=True)
class _TrustPolicy:
    max_lifetime: timedelta
    max_age: timedelta
    keys: Mapping[str, _PolicyKey]


def _raise(code: str) -> NoReturn:
    raise DeploymentAttestationVerificationError(code)


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


def _permissions(value: object, code: str) -> tuple[str, ...]:
    raw = _sequence(value, code)
    permissions = tuple(item for item in raw if isinstance(item, str))
    if len(permissions) != len(raw) or permissions != (DEPLOYMENT_PERMISSION,):
        _raise(code)
    return permissions


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


def _validate_payload(value: object) -> Mapping[str, object]:
    payload = _mapping(value, "attestation_schema_invalid")
    _exact_keys(payload, PAYLOAD_FIELDS, "attestation_schema_invalid")
    if (
        payload.get("owner_role") != OWNER_ROLE
        or payload.get("permission") != DEPLOYMENT_PERMISSION
    ):
        _raise("deployment_authority_invalid")
    _text(payload.get("environment"), ENVIRONMENT_PATTERN, "payload_invalid")
    _text(payload.get("repository"), REPOSITORY_PATTERN, "payload_invalid")
    _text(payload.get("git_sha"), GIT_SHA_PATTERN, "payload_invalid")
    _positive_integer(payload.get("workflow_run_id"), "workflow_binding_invalid")
    _positive_integer(payload.get("workflow_run_attempt"), "workflow_binding_invalid")
    _text(payload.get("nonce"), NONCE_PATTERN, "payload_invalid")
    _hash(payload.get("app_endpoint_identity_sha256"), "payload_invalid")
    _hash(payload.get("app_tls_spki_sha256"), "payload_invalid")
    _positive_integer(payload.get("main_ci_run_id"), "artifact_binding_invalid")
    _positive_integer(payload.get("main_ci_run_attempt"), "artifact_binding_invalid")
    _positive_integer(payload.get("main_bundle_artifact_id"), "artifact_binding_invalid")
    _text(
        payload.get("main_bundle_artifact_digest"),
        DIGEST_PATTERN,
        "artifact_binding_invalid",
    )
    _hash(payload.get("deployment_identity_sha256"), "deployment_binding_invalid")
    if payload.get("artifact_deployed") is not True:
        _raise("deployment_binding_invalid")
    _timestamp(payload.get("issued_at"), "time_invalid")
    _timestamp(payload.get("not_before"), "time_invalid")
    _timestamp(payload.get("expires_at"), "time_invalid")
    return payload


def _validate_signed_document(value: object) -> Mapping[str, object]:
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
    _validate_payload(signed.get("payload"))
    return signed


def canonical_signed_bytes(signed_document: Mapping[str, object]) -> bytes:
    """Return the sole canonical representation accepted for signatures."""

    signed = _validate_signed_document(signed_document)
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
        if item.get("algorithm") != ALGORITHM or item.get("owner_role") != OWNER_ROLE:
            _raise("policy_invalid")
        public_key = _decode_base64url(
            item.get("public_key_base64url"), expected_size=32, code="policy_invalid"
        )
        try:
            Ed25519PublicKey.from_public_bytes(public_key)
        except ValueError:
            _raise("policy_invalid")
        environment = _text(item.get("environment"), ENVIRONMENT_PATTERN, "policy_invalid")
        repository = _text(item.get("repository"), REPOSITORY_PATTERN, "policy_invalid")
        _permissions(item.get("permissions"), "policy_invalid")
        not_before = _timestamp(item.get("not_before"), "policy_invalid")
        expires_at = _timestamp(item.get("expires_at"), "policy_invalid")
        if not_before >= expires_at or key_id in keys or public_key in public_keys:
            _raise("policy_invalid")
        keys[key_id] = _PolicyKey(
            key_id=key_id,
            public_key_bytes=public_key,
            environment=environment,
            repository=repository,
            not_before=not_before,
            expires_at=expires_at,
        )
        public_keys.add(public_key)
    return _TrustPolicy(
        max_lifetime=timedelta(seconds=max_lifetime),
        max_age=timedelta(seconds=max_age),
        keys=keys,
    )


def _validate_expected_context(expected: ExpectedDeploymentContext) -> None:
    _text(expected.environment, ENVIRONMENT_PATTERN, "expected_context_invalid")
    _text(expected.repository, REPOSITORY_PATTERN, "expected_context_invalid")
    _text(expected.git_sha, GIT_SHA_PATTERN, "expected_context_invalid")
    _positive_integer(expected.workflow_run_id, "expected_context_invalid")
    _positive_integer(expected.workflow_run_attempt, "expected_context_invalid")
    _text(expected.nonce, NONCE_PATTERN, "expected_context_invalid")
    _hash(expected.app_endpoint_identity_sha256, "expected_context_invalid")
    _hash(expected.app_tls_spki_sha256, "expected_context_invalid")
    _positive_integer(expected.main_ci_run_id, "expected_context_invalid")
    _positive_integer(expected.main_ci_run_attempt, "expected_context_invalid")
    _positive_integer(expected.main_bundle_artifact_id, "expected_context_invalid")
    _text(
        expected.main_bundle_artifact_digest,
        DIGEST_PATTERN,
        "expected_context_invalid",
    )
    _hash(expected.deployment_identity_sha256, "expected_context_invalid")


def _verify_context(payload: Mapping[str, object], expected: ExpectedDeploymentContext) -> None:
    bound_values: Mapping[str, object] = {
        "environment": expected.environment,
        "repository": expected.repository,
        "git_sha": expected.git_sha,
        "workflow_run_id": expected.workflow_run_id,
        "workflow_run_attempt": expected.workflow_run_attempt,
        "nonce": expected.nonce,
        "app_endpoint_identity_sha256": expected.app_endpoint_identity_sha256,
        "app_tls_spki_sha256": expected.app_tls_spki_sha256,
        "main_ci_run_id": expected.main_ci_run_id,
        "main_ci_run_attempt": expected.main_ci_run_attempt,
        "main_bundle_artifact_id": expected.main_bundle_artifact_id,
        "main_bundle_artifact_digest": expected.main_bundle_artifact_digest,
        "deployment_identity_sha256": expected.deployment_identity_sha256,
    }
    if any(payload.get(key) != value for key, value in bound_values.items()):
        _raise("context_mismatch")


def _verify_time_window(
    payload: Mapping[str, object],
    policy: _TrustPolicy,
    key: _PolicyKey,
    *,
    now: datetime,
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


def verify_application_deployment_attestation(
    attestation: Mapping[str, object],
    policy_document: Mapping[str, object],
    *,
    expected: ExpectedDeploymentContext,
    now: datetime | None = None,
) -> None:
    """Verify authority, signature, exact deployment binding and freshness."""

    _validate_expected_context(expected)
    document = _mapping(attestation, "attestation_schema_invalid")
    _exact_keys(document, ATTESTATION_FIELDS, "attestation_schema_invalid")
    signed_document = {key: document[key] for key in SIGNED_FIELDS}
    signed = _validate_signed_document(signed_document)
    payload = _validate_payload(signed.get("payload"))
    policy = _parse_policy(policy_document)

    key_id = _text(signed.get("key_id"), KEY_ID_PATTERN, "key_id_invalid")
    key = policy.keys.get(key_id)
    if key is None:
        _raise("untrusted_key")
    if key.environment != payload.get("environment") or key.repository != payload.get("repository"):
        _raise("policy_binding_invalid")

    signature = _decode_base64url(
        document.get("signature"), expected_size=64, code="signature_invalid"
    )
    try:
        Ed25519PublicKey.from_public_bytes(key.public_key_bytes).verify(
            signature,
            canonical_signed_bytes(signed),
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
        parsed: object = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError):
        _raise("input_invalid")
    return _mapping(parsed, "input_invalid")


def verify_files(
    *,
    attestation_path: Path,
    policy_path: Path,
    expected: ExpectedDeploymentContext,
) -> None:
    attestation = _load_json(attestation_path, max_bytes=MAX_ATTESTATION_BYTES)
    policy = _load_json(policy_path, max_bytes=MAX_POLICY_BYTES)
    verify_application_deployment_attestation(
        attestation,
        policy,
        expected=expected,
    )


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(description=__doc__)
    parser.add_argument("--attestation", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--git-sha", required=True)
    parser.add_argument("--workflow-run-id", type=int, required=True)
    parser.add_argument("--workflow-run-attempt", type=int, required=True)
    parser.add_argument("--nonce", required=True)
    parser.add_argument("--app-endpoint-identity-sha256", required=True)
    parser.add_argument("--app-tls-spki-sha256", required=True)
    parser.add_argument("--main-ci-run-id", type=int, required=True)
    parser.add_argument("--main-ci-run-attempt", type=int, required=True)
    parser.add_argument("--main-bundle-artifact-id", type=int, required=True)
    parser.add_argument("--main-bundle-artifact-digest", required=True)
    parser.add_argument("--deployment-identity-sha256", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        expected = ExpectedDeploymentContext(
            environment=args.environment,
            repository=args.repository,
            git_sha=args.git_sha,
            workflow_run_id=args.workflow_run_id,
            workflow_run_attempt=args.workflow_run_attempt,
            nonce=args.nonce,
            app_endpoint_identity_sha256=args.app_endpoint_identity_sha256,
            app_tls_spki_sha256=args.app_tls_spki_sha256,
            main_ci_run_id=args.main_ci_run_id,
            main_ci_run_attempt=args.main_ci_run_attempt,
            main_bundle_artifact_id=args.main_bundle_artifact_id,
            main_bundle_artifact_digest=args.main_bundle_artifact_digest,
            deployment_identity_sha256=args.deployment_identity_sha256,
        )
        verify_files(
            attestation_path=args.attestation,
            policy_path=args.policy,
            expected=expected,
        )
    except DeploymentAttestationVerificationError as error:
        print(
            f"application deployment attestation verification failed: {error.code}",
            file=sys.stderr,
        )
        return 1
    print("application deployment attestation verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
