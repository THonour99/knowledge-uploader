"""Collect protected-environment evidence without manufacturing operational results."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import os
import re
import stat
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, TypedDict

import yaml  # type: ignore[import-untyped]

if TYPE_CHECKING:
    from scripts.alertmanager_secret_scan import sensitive_http_header_paths
else:
    try:
        from scripts.alertmanager_secret_scan import sensitive_http_header_paths
    except ModuleNotFoundError:  # pragma: no cover - direct script execution
        sensitive_http_header_paths = importlib.import_module(
            "alertmanager_secret_scan"
        ).sensitive_http_header_paths

ROOT = Path(__file__).resolve().parents[1]
OBSERVABILITY_DIR = ROOT / "ops" / "observability"
PROTECTED_PROMETHEUS_CONFIG = OBSERVABILITY_DIR / "prometheus.protected.yml"
DR_RELEASE_POLICY_PATH = ROOT / "ops" / "policies" / "dr-release-policy.json"
DR_RELEASE_POLICY_OUTPUT = "dr-release-policy.json"
SOURCE_EVIDENCE_FILES = (
    "alertmanager-notification.json",
    "dr-release.json",
    "email-delivery.json",
    "validator-receipt.json",
)
OUTPUT_FILES = (
    "alertmanager-notification.json",
    "dr-release.json",
    DR_RELEASE_POLICY_OUTPUT,
    "email-delivery.json",
    "alertmanager.yml",
    "promtool.json",
)
GIT_SHA_PATTERN = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})")
IMAGE_ID_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
MANIFEST_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
EVIDENCE_MAX_AGE = timedelta(hours=2)
PROMETHEUS_IMAGE = (
    "prom/prometheus:v3.12.0"
    "@sha256:69f5241418838263316593f7274a304b095c40bcf22e57272865da91bd60a8ac"
)
ALERTMANAGER_IMAGE = (
    "prom/alertmanager:v0.28.1"
    "@sha256:27c475db5fb156cab31d5c18a4251ac7ed567746a2483ff264516437a39b15ba"
)
SUPPORTED_VALIDATOR_ARCHITECTURES = frozenset({"amd64", "arm64"})
MAX_SOURCE_EVIDENCE_BYTES = 4 * 1024 * 1024
DR_RELEASE_POLICY_SCHEMA: Final = "knowledge-uploader.dr-release-policy.v1"
DR_RELEASE_POLICY_KEYS: Final = frozenset(
    {"schema", "max_rpo_seconds", "max_rto_seconds", "measurement", "owner"}
)
SOURCE_COMMON_KEYS: Final = frozenset(
    {
        "schema",
        "generated_at",
        "git_sha",
        "environment",
        "source_run_id",
        "source_run_attempt",
        "source_tool",
        "status",
        "receipt",
    }
)
SOURCE_METADATA_KEYS: Final = frozenset(
    {
        "schema",
        "generated_at",
        "run_id",
        "run_attempt",
        "tool",
        "file_sha256",
        "canonical_sha256",
    }
)
OUTPUT_COMMON_KEYS: Final = frozenset(
    {
        "schema",
        "generated_at",
        "git_sha",
        "environment",
        "collector_run_id",
        "collector_run_attempt",
        "status",
        "source",
        "receipt",
    }
)
SOURCE_SCHEMAS: Final = {
    "alertmanager-notification.json": "knowledge-uploader.alertmanager-webhook-source.v1",
    "dr-release.json": "knowledge-uploader.dr-release-source.v1",
    "email-delivery.json": "knowledge-uploader.smtp-delivery-source.v1",
    "validator-receipt.json": "knowledge-uploader.observability-validator-source.v1",
}
OUTPUT_SCHEMAS: Final = {
    "alertmanager-notification.json": "knowledge-uploader.alertmanager-webhook-evidence.v1",
    "dr-release.json": "knowledge-uploader.dr-release-evidence.v1",
    "email-delivery.json": "knowledge-uploader.smtp-delivery-evidence.v1",
    "validator-receipt.json": "knowledge-uploader.observability-validator-evidence.v1",
}
SOURCE_TO_OUTPUT: Final = {
    "alertmanager-notification.json": "alertmanager-notification.json",
    "dr-release.json": "dr-release.json",
    "email-delivery.json": "email-delivery.json",
    "validator-receipt.json": "promtool.json",
}
SOURCE_TOOLS: Final = {
    "alertmanager-notification.json": "alertmanager-webhook-receiver",
    "dr-release.json": "backup-restore-drill",
    "email-delivery.json": "smtp-delivery-probe",
    "validator-receipt.json": "observability-validator",
}
ALERT_RECEIPT_KEYS: Final = frozenset(
    {
        "alert_name",
        "alert_fingerprint",
        "receiver_name",
        "receiver_type",
        "webhook_delivery_id_sha256",
        "webhook_receipt_sha256",
        "webhook_status_code",
        "firing_at",
        "delivered_at",
        "resolved_at",
    }
)
DR_RECEIPT_KEYS: Final = frozenset(
    {
        "backup_id",
        "backup_manifest_sha256",
        "restore_evidence_sha256",
        "restore_started_at",
        "restore_completed_at",
        "rpo_seconds",
        "rpo_target_seconds",
        "rto_seconds",
        "rto_target_seconds",
        "policy_sha256",
        "alembic_revision",
        "database_tables_sha256",
        "minio_missing_objects",
        "minio_orphan_objects",
        "minio_mismatched_objects",
        "recovery_pair_id",
        "postgres_restore_point_sha256",
        "minio_restore_point_sha256",
        "postgres_pitr_enabled",
        "last_archived_at",
        "full_backup_encrypted",
        "full_backup_immutable",
        "offsite_location_sha256",
        "retention_until",
        "minio_versioning_enabled",
        "minio_replication_enabled",
        "coordinated_snapshot",
        "key_version_sha256",
        "decrypt_validation",
        "plaintext_emitted",
        "main_chain_smoke",
        "cleanup_validation",
    }
)
EMAIL_RECEIPT_KEYS: Final = frozenset(
    {
        "registration_delivery",
        "password_reset_delivery",
        "registration_message_id_sha256",
        "password_reset_message_id_sha256",
        "registration_smtp_receipt_sha256",
        "password_reset_smtp_receipt_sha256",
        "registration_smtp_result",
        "password_reset_smtp_result",
        "registration_delivered_at",
        "password_reset_delivered_at",
        "persistent_message",
        "broker_expiry_at_or_before_token_expiry",
        "publisher_confirm",
        "encrypted_envelope_observed",
        "plaintext_token_observed",
        "dlq_plaintext_token_observed",
        "publish_failure_public_response_indistinguishable",
        "publish_failure_public_statuses",
        "publish_failure_metric_recorded",
        "retry_issued_fresh_token",
        "smtp_delivery_semantics",
    }
)
VALIDATOR_RECEIPT_KEYS: Final = frozenset(
    {
        "prometheus_config",
        "prometheus_rules",
        "alertmanager_config",
        "prometheus_config_sha256",
        "prometheus_rules_sha256",
        "alertmanager_config_sha256",
        "prometheus_image",
        "prometheus_manifest_list_digest",
        "prometheus_image_id",
        "prometheus_image_os",
        "prometheus_image_architecture",
        "prometheus_docker_architecture",
        "alertmanager_image",
        "alertmanager_manifest_list_digest",
        "alertmanager_image_id",
        "alertmanager_image_os",
        "alertmanager_image_architecture",
        "alertmanager_docker_architecture",
    }
)
RECEIPT_KEYS: Final = {
    "alertmanager-notification.json": ALERT_RECEIPT_KEYS,
    "dr-release.json": DR_RECEIPT_KEYS,
    "email-delivery.json": EMAIL_RECEIPT_KEYS,
    "validator-receipt.json": VALIDATOR_RECEIPT_KEYS,
}
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
UUID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)
SAFE_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
EMAIL_VALUE_PATTERN = re.compile(
    r"(?i)(?:^|[^A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
    r"(?:$|[^A-Za-z0-9.-])"
)
URL_USERINFO_PATTERN = re.compile(r"(?i)https?://[^/\s]+:[^@\s]+@")
SECRET_VALUE_PATTERN = re.compile(
    r"(?i)(?:^|[^A-Za-z0-9])(?:sk-[A-Za-z0-9_-]{8,}|bearer\s+[A-Za-z0-9._~-]{8,})"
)
FINGERPRINT_PATTERN = re.compile(r"[0-9a-f]{16,64}")
INLINE_ALERTMANAGER_SECRET_FIELDS = frozenset(
    {
        "api_key",
        "api_secret",
        "api_url",
        "auth_password",
        "auth_secret",
        "bearer_token",
        "bot_token",
        "client_secret",
        "credentials",
        "password",
        "routing_key",
        "secret_key",
        "service_key",
        "slack_api_url",
        "token",
        "token_id",
        "token_secret",
        "url",
        "webhook_url",
    }
)
FORBIDDEN_EVIDENCE_FIELDS = frozenset(
    {
        "api_key",
        "authorization",
        "broker_payload",
        "broker_url",
        "ciphertext",
        "connection_string",
        "cookie",
        "database_url",
        "email_body",
        "password",
        "raw_token",
        "secret",
        "smtp_password",
        "token",
        "webhook_url",
    }
)


class EvidencePreparationError(RuntimeError):
    """A bounded preparation failure that does not expose command output or evidence data."""

    def __init__(self, step: str) -> None:
        super().__init__(f"external evidence preparation failed at step: {step}")
        self.step = step


class ValidatorImageEvidence(TypedDict):
    """Content-addressed validator identity captured on the protected runner."""

    reference: str
    manifest_list_digest: str
    image_id: str
    operating_system: str
    architecture: str
    docker_architecture: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(command: list[str], *, step: str, timeout_seconds: float = 180.0) -> str:
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise EvidencePreparationError(step) from error
    if completed.returncode != 0:
        raise EvidencePreparationError(step)
    return completed.stdout.strip()


def _normalize_architecture(value: object, *, step: str) -> str:
    if not isinstance(value, str):
        raise EvidencePreparationError(step)
    normalized = {
        "aarch64": "arm64",
        "arm64": "arm64",
        "amd64": "amd64",
        "x86_64": "amd64",
    }.get(value.strip().lower())
    if normalized not in SUPPORTED_VALIDATOR_ARCHITECTURES:
        raise EvidencePreparationError(step)
    return normalized


def _validator_manifest_digest(reference: str, *, expected: str, step: str) -> str:
    if reference != expected or "@" not in reference:
        raise EvidencePreparationError(step)
    digest = reference.rsplit("@", maxsplit=1)[1]
    if MANIFEST_DIGEST_PATTERN.fullmatch(digest) is None:
        raise EvidencePreparationError(step)
    return digest


def _docker_architecture() -> str:
    return _normalize_architecture(
        _run(
            ["docker", "info", "--format", "{{.Architecture}}"],
            step="docker_architecture",
        ),
        step="docker_architecture",
    )


def _image_metadata(
    reference: str,
    *,
    manifest_list_digest: str,
    docker_architecture: str,
    step: str,
) -> ValidatorImageEvidence:
    raw = _run(
        ["docker", "image", "inspect", "--format", "{{json .}}", reference],
        step=step,
    )
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError as error:
        raise EvidencePreparationError(step) from error
    if not isinstance(loaded, dict):
        raise EvidencePreparationError(step)
    image_id = loaded.get("Id")
    operating_system = loaded.get("Os")
    architecture = _normalize_architecture(loaded.get("Architecture"), step=step)
    if (
        not isinstance(image_id, str)
        or IMAGE_ID_PATTERN.fullmatch(image_id) is None
        or operating_system != "linux"
        or architecture != docker_architecture
    ):
        raise EvidencePreparationError(step)
    return {
        "reference": reference,
        "manifest_list_digest": manifest_list_digest,
        "image_id": image_id,
        "operating_system": operating_system,
        "architecture": architecture,
        "docker_architecture": docker_architecture,
    }


def _pull_validator_image(
    reference: str,
    *,
    expected: str,
    docker_architecture: str,
    step: str,
) -> ValidatorImageEvidence:
    manifest_list_digest = _validator_manifest_digest(
        reference,
        expected=expected,
        step=f"{step}_reference",
    )
    _run(
        [
            "docker",
            "pull",
            "--platform",
            f"linux/{docker_architecture}",
            reference,
        ],
        step=f"{step}_pull",
    )
    return _image_metadata(
        reference,
        manifest_list_digest=manifest_list_digest,
        docker_architecture=docker_architecture,
        step=f"{step}_inspect",
    )


def _source_file(source_dir: Path, filename: str) -> Path:
    candidate = source_dir / filename
    if candidate.is_symlink() or not candidate.is_file():
        raise EvidencePreparationError(f"source_{filename}")
    resolved = candidate.resolve()
    try:
        resolved.relative_to(source_dir)
    except ValueError as error:
        raise EvidencePreparationError(f"source_{filename}") from error
    return resolved


def _read_stable_regular_file(path: Path, *, step: str) -> bytes:
    descriptor = -1
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise EvidencePreparationError(step)
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            or opened.st_size > MAX_SOURCE_EVIDENCE_BYTES
        ):
            raise EvidencePreparationError(step)
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = -1
            payload = stream.read(MAX_SOURCE_EVIDENCE_BYTES + 1)
            after = os.fstat(stream.fileno())
        current = path.lstat()
    except (OSError, ValueError) as error:
        raise EvidencePreparationError(step) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if (
        len(payload) > MAX_SOURCE_EVIDENCE_BYTES
        or len(payload) != opened.st_size
        or (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
        or not stat.S_ISREG(current.st_mode)
    ):
        raise EvidencePreparationError(step)
    return payload


def _reject_json_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _load_json_object(payload: bytes, *, step: str) -> dict[str, Any]:
    try:
        loaded = json.loads(
            payload.decode("utf-8"),
            parse_constant=_reject_json_constant,
            object_pairs_hook=_reject_duplicate_pairs,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise EvidencePreparationError(step) from error
    if not isinstance(loaded, dict) or not all(isinstance(key, str) for key in loaded):
        raise EvidencePreparationError(step)
    return loaded


def _exact_keys(value: Mapping[str, object], expected: frozenset[str], *, step: str) -> None:
    if set(value) != expected:
        raise EvidencePreparationError(step)


def _mapping(value: object, *, step: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise EvidencePreparationError(step)
    return value


def _timestamp(value: object, *, step: str) -> datetime:
    if not isinstance(value, str):
        raise EvidencePreparationError(step)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise EvidencePreparationError(step) from error
    if parsed.tzinfo is None:
        raise EvidencePreparationError(step)
    return parsed.astimezone(UTC)


def _fresh(value: object, *, now: datetime, step: str) -> datetime:
    parsed = _timestamp(value, step=step)
    if not now - EVIDENCE_MAX_AGE <= parsed <= now + timedelta(minutes=5):
        raise EvidencePreparationError(step)
    return parsed


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return _sha256_bytes(payload)


def _digest(value: object, *, step: str) -> str:
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        raise EvidencePreparationError(step)
    return value


def _safe_id(value: object, *, step: str) -> str:
    if not isinstance(value, str) or SAFE_ID_PATTERN.fullmatch(value) is None:
        raise EvidencePreparationError(step)
    return value


def _positive_integer(value: object, *, step: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise EvidencePreparationError(step)
    return value


def _load_dr_release_policy(payload: bytes) -> dict[str, Any]:
    step = "dr_release_policy"
    policy = _load_json_object(payload, step=step)
    _exact_keys(policy, DR_RELEASE_POLICY_KEYS, step=step)
    if policy.get("schema") != DR_RELEASE_POLICY_SCHEMA:
        raise EvidencePreparationError(step)
    _positive_integer(policy.get("max_rpo_seconds"), step=step)
    _positive_integer(policy.get("max_rto_seconds"), step=step)
    for field in ("measurement", "owner"):
        value = policy.get(field)
        if not isinstance(value, str) or not value.strip() or len(value) > 256:
            raise EvidencePreparationError(step)
    return policy


def _non_negative_number(value: object, *, step: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise EvidencePreparationError(step)
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0:
        raise EvidencePreparationError(step)
    return normalized


def _reject_sensitive_evidence_fields(evidence: Mapping[str, object], *, filename: str) -> None:
    def walk(value: object) -> None:
        if isinstance(value, dict):
            for raw_key, child in value.items():
                if str(raw_key).strip().lower() in FORBIDDEN_EVIDENCE_FIELDS:
                    raise EvidencePreparationError(f"sensitive_field_{filename}")
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
        elif isinstance(value, str) and (
            EMAIL_VALUE_PATTERN.search(f" {value} ") is not None
            or URL_USERINFO_PATTERN.search(value) is not None
            or SECRET_VALUE_PATTERN.search(f" {value}") is not None
        ):
            raise EvidencePreparationError(f"sensitive_value_{filename}")

    walk(evidence)


def _validate_alert_receipt(receipt: Mapping[str, object], *, now: datetime) -> None:
    step = "receipt_alertmanager-notification.json"
    if (
        receipt.get("alert_name") != "KnowledgeUploaderProtectedReleaseProbe"
        or receipt.get("receiver_type") != "webhook"
        or not isinstance(receipt.get("alert_fingerprint"), str)
        or FINGERPRINT_PATTERN.fullmatch(str(receipt["alert_fingerprint"])) is None
    ):
        raise EvidencePreparationError(step)
    _safe_id(receipt.get("receiver_name"), step=step)
    _digest(receipt.get("webhook_delivery_id_sha256"), step=step)
    _digest(receipt.get("webhook_receipt_sha256"), step=step)
    status_code = receipt.get("webhook_status_code")
    if (
        not isinstance(status_code, int)
        or isinstance(status_code, bool)
        or not 200 <= status_code < 300
    ):
        raise EvidencePreparationError(step)
    firing = _fresh(receipt.get("firing_at"), now=now, step=step)
    delivered = _fresh(receipt.get("delivered_at"), now=now, step=step)
    resolved = _fresh(receipt.get("resolved_at"), now=now, step=step)
    if not firing <= delivered <= resolved:
        raise EvidencePreparationError(step)


def _validate_dr_receipt(
    receipt: Mapping[str, object],
    *,
    now: datetime,
    policy: Mapping[str, object],
    policy_sha256: str,
) -> None:
    step = "receipt_dr-release.json"
    for field in (
        "backup_manifest_sha256",
        "restore_evidence_sha256",
        "database_tables_sha256",
        "postgres_restore_point_sha256",
        "minio_restore_point_sha256",
        "offsite_location_sha256",
        "key_version_sha256",
        "policy_sha256",
    ):
        _digest(receipt.get(field), step=step)
    if receipt.get("policy_sha256") != policy_sha256:
        raise EvidencePreparationError(step)
    _safe_id(receipt.get("backup_id"), step=step)
    _safe_id(receipt.get("alembic_revision"), step=step)
    _safe_id(receipt.get("recovery_pair_id"), step=step)
    started = _fresh(receipt.get("restore_started_at"), now=now, step=step)
    completed = _fresh(receipt.get("restore_completed_at"), now=now, step=step)
    if completed < started:
        raise EvidencePreparationError(step)
    rpo = _non_negative_number(receipt.get("rpo_seconds"), step=step)
    rpo_target = _non_negative_number(receipt.get("rpo_target_seconds"), step=step)
    rto = _non_negative_number(receipt.get("rto_seconds"), step=step)
    rto_target = _non_negative_number(receipt.get("rto_target_seconds"), step=step)
    max_rpo = _positive_integer(policy.get("max_rpo_seconds"), step=step)
    max_rto = _positive_integer(policy.get("max_rto_seconds"), step=step)
    if (
        rpo_target <= 0
        or rto_target <= 0
        or rpo_target > max_rpo
        or rto_target > max_rto
        or rpo > rpo_target
        or rto > rto_target
        or rpo > max_rpo
        or rto > max_rto
    ):
        raise EvidencePreparationError(step)
    for field in ("minio_missing_objects", "minio_orphan_objects", "minio_mismatched_objects"):
        if receipt.get(field) != 0 or isinstance(receipt.get(field), bool):
            raise EvidencePreparationError(step)
    last_archived = _timestamp(receipt.get("last_archived_at"), step=step)
    retention = _timestamp(receipt.get("retention_until"), step=step)
    if not now - timedelta(hours=1) <= last_archived <= now + timedelta(minutes=5):
        raise EvidencePreparationError(step)
    if retention < now + timedelta(days=30):
        raise EvidencePreparationError(step)
    required_values = {
        "postgres_pitr_enabled": True,
        "full_backup_encrypted": True,
        "full_backup_immutable": True,
        "minio_versioning_enabled": True,
        "decrypt_validation": "passed",
        "plaintext_emitted": False,
        "main_chain_smoke": "passed",
        "cleanup_validation": "passed",
    }
    if any(receipt.get(field) != value for field, value in required_values.items()):
        raise EvidencePreparationError(step)
    if not (
        receipt.get("minio_replication_enabled") is True
        or receipt.get("coordinated_snapshot") is True
    ):
        raise EvidencePreparationError(step)


def _validate_email_receipt(receipt: Mapping[str, object], *, now: datetime) -> None:
    step = "receipt_email-delivery.json"
    digest_fields = (
        "registration_message_id_sha256",
        "password_reset_message_id_sha256",
        "registration_smtp_receipt_sha256",
        "password_reset_smtp_receipt_sha256",
    )
    digests = [_digest(receipt.get(field), step=step) for field in digest_fields]
    if len(set(digests)) != len(digests):
        raise EvidencePreparationError(step)
    required_values = {
        "registration_delivery": "passed",
        "password_reset_delivery": "passed",
        "registration_smtp_result": "accepted",
        "password_reset_smtp_result": "accepted",
        "persistent_message": True,
        "broker_expiry_at_or_before_token_expiry": True,
        "publisher_confirm": "passed",
        "encrypted_envelope_observed": True,
        "plaintext_token_observed": False,
        "dlq_plaintext_token_observed": False,
        "publish_failure_public_response_indistinguishable": True,
        "publish_failure_metric_recorded": True,
        "retry_issued_fresh_token": True,
        "smtp_delivery_semantics": "at_most_once_attempt",
    }
    if any(receipt.get(field) != value for field, value in required_values.items()):
        raise EvidencePreparationError(step)
    statuses = _mapping(receipt.get("publish_failure_public_statuses"), step=step)
    _exact_keys(
        statuses, frozenset({"register", "resend_verification", "forgot_password"}), step=step
    )
    if statuses != {"register": 201, "resend_verification": 200, "forgot_password": 200}:
        raise EvidencePreparationError(step)
    _fresh(receipt.get("registration_delivered_at"), now=now, step=step)
    _fresh(receipt.get("password_reset_delivered_at"), now=now, step=step)


def _validate_validator_receipt(receipt: Mapping[str, object]) -> None:
    step = "receipt_validator-receipt.json"
    if any(
        receipt.get(field) != "passed"
        for field in ("prometheus_config", "prometheus_rules", "alertmanager_config")
    ):
        raise EvidencePreparationError(step)
    for field in (
        "prometheus_config_sha256",
        "prometheus_rules_sha256",
        "alertmanager_config_sha256",
    ):
        _digest(receipt.get(field), step=step)
    expected_images = {
        "prometheus": PROMETHEUS_IMAGE,
        "alertmanager": ALERTMANAGER_IMAGE,
    }
    daemon_architectures: set[str] = set()
    for name, reference in expected_images.items():
        if receipt.get(f"{name}_image") != reference:
            raise EvidencePreparationError(step)
        expected_digest = reference.rsplit("@", maxsplit=1)[1]
        if receipt.get(f"{name}_manifest_list_digest") != expected_digest:
            raise EvidencePreparationError(step)
        image_id = receipt.get(f"{name}_image_id")
        if not isinstance(image_id, str) or IMAGE_ID_PATTERN.fullmatch(image_id) is None:
            raise EvidencePreparationError(step)
        architecture = receipt.get(f"{name}_image_architecture")
        daemon = receipt.get(f"{name}_docker_architecture")
        if (
            receipt.get(f"{name}_image_os") != "linux"
            or architecture not in SUPPORTED_VALIDATOR_ARCHITECTURES
            or daemon != architecture
        ):
            raise EvidencePreparationError(step)
        daemon_architectures.add(str(daemon))
    if len(daemon_architectures) != 1:
        raise EvidencePreparationError(step)


def _validate_source_evidence(
    evidence: dict[str, Any],
    *,
    filename: str,
    git_sha: str,
    environment: str,
    now: datetime,
    dr_policy: Mapping[str, object],
    dr_policy_sha256: str,
) -> dict[str, Any]:
    step = f"schema_{filename}"
    _reject_sensitive_evidence_fields(evidence, filename=filename)
    _exact_keys(evidence, SOURCE_COMMON_KEYS, step=step)
    if (
        evidence.get("schema") != SOURCE_SCHEMAS[filename]
        or evidence.get("status") != "passed"
        or evidence.get("git_sha") != git_sha
        or evidence.get("environment") != environment
        or evidence.get("source_tool") != SOURCE_TOOLS[filename]
    ):
        raise EvidencePreparationError(f"identity_{filename}")
    run_id = evidence.get("source_run_id")
    if not isinstance(run_id, str) or UUID_PATTERN.fullmatch(run_id) is None:
        raise EvidencePreparationError(f"identity_{filename}")
    _positive_integer(evidence.get("source_run_attempt"), step=f"identity_{filename}")
    _fresh(evidence.get("generated_at"), now=now, step=f"identity_{filename}")
    receipt = _mapping(evidence.get("receipt"), step=step)
    _exact_keys(receipt, RECEIPT_KEYS[filename], step=step)
    if filename == "alertmanager-notification.json":
        _validate_alert_receipt(receipt, now=now)
    elif filename == "dr-release.json":
        _validate_dr_receipt(
            receipt,
            now=now,
            policy=dr_policy,
            policy_sha256=dr_policy_sha256,
        )
    elif filename == "email-delivery.json":
        _validate_email_receipt(receipt, now=now)
    elif filename == "validator-receipt.json":
        _validate_validator_receipt(receipt)
    else:
        raise EvidencePreparationError(step)
    return {key: receipt[key] for key in sorted(receipt)}


def _project_source_evidence(
    evidence: Mapping[str, object],
    *,
    filename: str,
    source_payload: bytes,
    receipt: Mapping[str, object],
    collected_at: datetime,
    collector_run_id: int,
    collector_run_attempt: int,
) -> dict[str, object]:
    return {
        "schema": OUTPUT_SCHEMAS[filename],
        "generated_at": collected_at.isoformat(),
        "git_sha": evidence["git_sha"],
        "environment": evidence["environment"],
        "collector_run_id": collector_run_id,
        "collector_run_attempt": collector_run_attempt,
        "status": evidence["status"],
        "source": {
            "schema": evidence["schema"],
            "generated_at": evidence["generated_at"],
            "run_id": evidence["source_run_id"],
            "run_attempt": evidence["source_run_attempt"],
            "tool": evidence["source_tool"],
            "file_sha256": _sha256_bytes(source_payload),
            "canonical_sha256": _canonical_sha256(evidence),
        },
        "receipt": dict(receipt),
    }


def _reject_inline_alertmanager_secrets(config_payload: bytes) -> None:
    try:
        loaded = yaml.safe_load(config_payload.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as error:
        raise EvidencePreparationError("alertmanager_secret_scan") from error

    if sensitive_http_header_paths(loaded):
        raise EvidencePreparationError("alertmanager_sensitive_http_header")

    def walk(value: object) -> None:
        if isinstance(value, dict):
            for raw_key, child in value.items():
                key = str(raw_key).strip().lower()
                if (
                    key in INLINE_ALERTMANAGER_SECRET_FIELDS
                    and not key.endswith("_file")
                    and child not in (None, "")
                ):
                    raise EvidencePreparationError("alertmanager_inline_secret")
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
        elif isinstance(value, str) and (
            EMAIL_VALUE_PATTERN.search(f" {value} ") is not None
            or URL_USERINFO_PATTERN.search(value) is not None
            or SECRET_VALUE_PATTERN.search(f" {value}") is not None
        ):
            raise EvidencePreparationError("alertmanager_sensitive_value")

    walk(loaded)


def _validate_alertmanager_receipt_binding(
    config_payload: bytes,
    *,
    receiver_name: str,
) -> None:
    try:
        loaded = yaml.safe_load(config_payload.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as error:
        raise EvidencePreparationError("alertmanager_receipt_binding") from error
    config = _mapping(loaded, step="alertmanager_receipt_binding")
    route = _mapping(config.get("route"), step="alertmanager_receipt_binding")
    selected_receivers: set[str] = set()

    def collect_routes(value: Mapping[str, object], inherited: str | None) -> None:
        raw_receiver = value.get("receiver", inherited)
        if not isinstance(raw_receiver, str) or not raw_receiver:
            raise EvidencePreparationError("alertmanager_receipt_binding")
        selected_receivers.add(raw_receiver)
        children = value.get("routes", [])
        if children is None:
            children = []
        if not isinstance(children, list):
            raise EvidencePreparationError("alertmanager_receipt_binding")
        for child in children:
            collect_routes(_mapping(child, step="alertmanager_receipt_binding"), raw_receiver)

    collect_routes(route, None)
    if receiver_name not in selected_receivers:
        raise EvidencePreparationError("alertmanager_receipt_binding")
    receivers = config.get("receivers")
    if not isinstance(receivers, list):
        raise EvidencePreparationError("alertmanager_receipt_binding")
    matches = [
        _mapping(receiver, step="alertmanager_receipt_binding")
        for receiver in receivers
        if isinstance(receiver, dict) and receiver.get("name") == receiver_name
    ]
    if len(matches) != 1:
        raise EvidencePreparationError("alertmanager_receipt_binding")
    webhooks = matches[0].get("webhook_configs")
    if (
        not isinstance(webhooks, list)
        or not webhooks
        or not all(
            isinstance(webhook, dict)
            and isinstance(webhook.get("url_file"), str)
            and bool(str(webhook["url_file"]).strip())
            and not webhook.get("url")
            for webhook in webhooks
        )
    ):
        raise EvidencePreparationError("alertmanager_receipt_binding")


def _run_observability_checks(
    *,
    alertmanager_config: Path,
    prometheus_image: str,
    alertmanager_image: str,
) -> tuple[ValidatorImageEvidence, ValidatorImageEvidence]:
    docker_architecture = _docker_architecture()
    prometheus_before = _pull_validator_image(
        prometheus_image,
        expected=PROMETHEUS_IMAGE,
        docker_architecture=docker_architecture,
        step="prometheus_image",
    )
    alertmanager_before = _pull_validator_image(
        alertmanager_image,
        expected=ALERTMANAGER_IMAGE,
        docker_architecture=docker_architecture,
        step="alertmanager_image",
    )
    observability_mount = f"{OBSERVABILITY_DIR.resolve()}:/work:ro"
    alertmanager_mount = f"{alertmanager_config.parent}:/alertmanager:ro"
    _run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "/bin/promtool",
            "--volume",
            observability_mount,
            prometheus_image,
            "check",
            "config",
            "/work/prometheus.protected.yml",
        ],
        step="prometheus_config",
    )
    _run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "/bin/promtool",
            "--volume",
            observability_mount,
            prometheus_image,
            "test",
            "rules",
            "/work/alerts.test.yml",
        ],
        step="prometheus_rules",
    )
    _run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "/bin/amtool",
            "--volume",
            alertmanager_mount,
            alertmanager_image,
            "check-config",
            f"/alertmanager/{alertmanager_config.name}",
        ],
        step="alertmanager_config",
    )
    prometheus_after = _image_metadata(
        prometheus_image,
        manifest_list_digest=prometheus_before["manifest_list_digest"],
        docker_architecture=docker_architecture,
        step="prometheus_image_postcheck",
    )
    alertmanager_after = _image_metadata(
        alertmanager_image,
        manifest_list_digest=alertmanager_before["manifest_list_digest"],
        docker_architecture=docker_architecture,
        step="alertmanager_image_postcheck",
    )
    if prometheus_after != prometheus_before:
        raise EvidencePreparationError("prometheus_image_identity_changed")
    if alertmanager_after != alertmanager_before:
        raise EvidencePreparationError("alertmanager_image_identity_changed")
    return prometheus_after, alertmanager_after


def _atomic_write(path: Path, payload: bytes) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def prepare(
    *,
    source_dir: Path,
    output_dir: Path,
    git_sha: str,
    environment: str,
    collector_run_id: int,
    collector_run_attempt: int,
    prometheus_image: str,
    alertmanager_image: str,
) -> tuple[Path, ...]:
    if GIT_SHA_PATTERN.fullmatch(git_sha) is None:
        raise EvidencePreparationError("git_identity")
    if environment not in {"staging", "production"}:
        raise EvidencePreparationError("environment")
    _positive_integer(collector_run_id, step="collector_run_identity")
    _positive_integer(collector_run_attempt, step="collector_run_identity")
    _validator_manifest_digest(
        prometheus_image,
        expected=PROMETHEUS_IMAGE,
        step="prometheus_image_reference",
    )
    _validator_manifest_digest(
        alertmanager_image,
        expected=ALERTMANAGER_IMAGE,
        step="alertmanager_image_reference",
    )
    source = source_dir.resolve()
    if not source.is_dir():
        raise EvidencePreparationError("source_directory")
    if output_dir.exists() and output_dir.is_symlink():
        raise EvidencePreparationError("output_directory")
    output = output_dir.resolve()
    if output == source or source in output.parents:
        raise EvidencePreparationError("output_directory")
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise EvidencePreparationError("output_directory")
    output.mkdir(parents=True, exist_ok=True)

    now = datetime.now(UTC)
    dr_policy_payload = _read_stable_regular_file(
        DR_RELEASE_POLICY_PATH,
        step="snapshot_dr_release_policy",
    )
    dr_policy = _load_dr_release_policy(dr_policy_payload)
    dr_policy_sha256 = _sha256_bytes(dr_policy_payload)
    source_paths = {filename: _source_file(source, filename) for filename in SOURCE_EVIDENCE_FILES}
    alertmanager_config = _source_file(source, "alertmanager.yml")
    source_payloads = {
        filename: _read_stable_regular_file(path, step=f"snapshot_{filename}")
        for filename, path in source_paths.items()
    }
    alertmanager_payload = _read_stable_regular_file(
        alertmanager_config,
        step="snapshot_alertmanager.yml",
    )
    _reject_inline_alertmanager_secrets(alertmanager_payload)

    source_evidence: dict[str, dict[str, Any]] = {}
    receipts: dict[str, dict[str, Any]] = {}
    for filename, payload in source_payloads.items():
        evidence = _load_json_object(payload, step=f"parse_{filename}")
        receipt = _validate_source_evidence(
            evidence,
            filename=filename,
            git_sha=git_sha,
            environment=environment,
            now=now,
            dr_policy=dr_policy,
            dr_policy_sha256=dr_policy_sha256,
        )
        source_evidence[filename] = evidence
        receipts[filename] = receipt
    source_runs = {
        (str(evidence["source_run_id"]), int(evidence["source_run_attempt"]))
        for evidence in source_evidence.values()
    }
    if len(source_runs) != len(SOURCE_EVIDENCE_FILES):
        raise EvidencePreparationError("source_run_overlap")

    alert_receipt = receipts["alertmanager-notification.json"]
    _validate_alertmanager_receipt_binding(
        alertmanager_payload,
        receiver_name=str(alert_receipt["receiver_name"]),
    )

    repository_hashes_before = {
        "prometheus_config_sha256": _sha256(PROTECTED_PROMETHEUS_CONFIG),
        "prometheus_rules_sha256": _sha256(OBSERVABILITY_DIR / "alerts.yml"),
    }
    with tempfile.TemporaryDirectory(
        prefix="external-evidence-validator-", dir=output.parent
    ) as raw:
        temporary_config = Path(raw) / "alertmanager.yml"
        temporary_config.write_bytes(alertmanager_payload)
        prometheus_validator, alertmanager_validator = _run_observability_checks(
            alertmanager_config=temporary_config,
            prometheus_image=prometheus_image,
            alertmanager_image=alertmanager_image,
        )
        alertmanager_config_sha256 = _sha256(temporary_config)
    repository_hashes_after = {
        "prometheus_config_sha256": _sha256(PROTECTED_PROMETHEUS_CONFIG),
        "prometheus_rules_sha256": _sha256(OBSERVABILITY_DIR / "alerts.yml"),
    }
    if repository_hashes_after != repository_hashes_before:
        raise EvidencePreparationError("validator_inputs_changed")
    if (
        _read_stable_regular_file(
            DR_RELEASE_POLICY_PATH,
            step="snapshot_dr_release_policy_postcheck",
        )
        != dr_policy_payload
    ):
        raise EvidencePreparationError("dr_release_policy_changed")

    observed_validator_receipt: dict[str, object] = {
        "prometheus_config": "passed",
        "prometheus_rules": "passed",
        "alertmanager_config": "passed",
        **repository_hashes_after,
        "alertmanager_config_sha256": alertmanager_config_sha256,
        "prometheus_image": prometheus_validator["reference"],
        "prometheus_manifest_list_digest": prometheus_validator["manifest_list_digest"],
        "prometheus_image_id": prometheus_validator["image_id"],
        "prometheus_image_os": prometheus_validator["operating_system"],
        "prometheus_image_architecture": prometheus_validator["architecture"],
        "prometheus_docker_architecture": prometheus_validator["docker_architecture"],
        "alertmanager_image": alertmanager_validator["reference"],
        "alertmanager_manifest_list_digest": alertmanager_validator["manifest_list_digest"],
        "alertmanager_image_id": alertmanager_validator["image_id"],
        "alertmanager_image_os": alertmanager_validator["operating_system"],
        "alertmanager_image_architecture": alertmanager_validator["architecture"],
        "alertmanager_docker_architecture": alertmanager_validator["docker_architecture"],
    }
    if receipts["validator-receipt.json"] != observed_validator_receipt:
        raise EvidencePreparationError("validator_receipt_mismatch")

    collected_at = datetime.now(UTC)
    projections = {
        filename: _project_source_evidence(
            source_evidence[filename],
            filename=filename,
            source_payload=source_payloads[filename],
            receipt=receipts[filename],
            collected_at=collected_at,
            collector_run_id=collector_run_id,
            collector_run_attempt=collector_run_attempt,
        )
        for filename in SOURCE_EVIDENCE_FILES
    }
    for filename, projection in projections.items():
        _reject_sensitive_evidence_fields(projection, filename=SOURCE_TO_OUTPUT[filename])

    try:
        _atomic_write(output / DR_RELEASE_POLICY_OUTPUT, dr_policy_payload)
        _atomic_write(output / "alertmanager.yml", alertmanager_payload)
        for source_name, projection in projections.items():
            output_name = SOURCE_TO_OUTPUT[source_name]
            payload = (
                json.dumps(
                    projection, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False
                )
                + "\n"
            ).encode("utf-8")
            _atomic_write(output / output_name, payload)
    except OSError as error:
        for filename in OUTPUT_FILES:
            candidate = output / filename
            if candidate.is_file() and not candidate.is_symlink():
                candidate.unlink()
        raise EvidencePreparationError("output_write") from error
    return tuple(output / filename for filename in OUTPUT_FILES)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--git-sha", required=True)
    parser.add_argument("--collector-run-id", required=True, type=int)
    parser.add_argument("--collector-run-attempt", required=True, type=int)
    parser.add_argument(
        "--environment",
        choices=("staging", "production"),
        required=True,
    )
    parser.add_argument("--prometheus-image", default=PROMETHEUS_IMAGE)
    parser.add_argument("--alertmanager-image", default=ALERTMANAGER_IMAGE)
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    try:
        files = prepare(
            source_dir=arguments.source_dir,
            output_dir=arguments.output_dir,
            git_sha=arguments.git_sha,
            environment=arguments.environment,
            collector_run_id=arguments.collector_run_id,
            collector_run_attempt=arguments.collector_run_attempt,
            prometheus_image=arguments.prometheus_image,
            alertmanager_image=arguments.alertmanager_image,
        )
    except EvidencePreparationError as error:
        sys.stderr.write(f"{error}\n")
        return 1
    sys.stdout.write(
        json.dumps(
            {
                "status": "prepared",
                "files": [path.name for path in files],
            },
            sort_keys=True,
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
