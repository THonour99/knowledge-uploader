"""Fail closed unless protected-release infrastructure evidence is complete."""

from __future__ import annotations

import argparse
import ast
import base64
import binascii
import hashlib
import importlib
import json
import math
import os
import re
import shlex
import stat
import subprocess
import sys
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

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
DR_RELEASE_POLICY_CONTRACT_PATH = "ops/policies/dr-release-policy.json"
DR_RELEASE_POLICY_EVIDENCE = "dr-release-policy.json"
DR_RELEASE_POLICY_SCHEMA = "knowledge-uploader.dr-release-policy.v1"
RELEASE_GATE_EVIDENCE_FILENAMES = (
    "alertmanager-notification.json",
    "dr-release.json",
    "rabbitmq-dlq-replay.json",
    "email-delivery.json",
    "promtool.json",
    "infrastructure-e2e.json",
    "dgx-spark-evidence.json",
)
REQUIRED_RELEASE_GATE_EVIDENCE = frozenset(
    {
        DR_RELEASE_POLICY_EVIDENCE,
        "alertmanager.yml",
        "release-workflow-trust.json",
        *RELEASE_GATE_EVIDENCE_FILENAMES,
    }
)
DR_RELEASE_POLICY_KEYS = frozenset(
    {"schema", "max_rpo_seconds", "max_rto_seconds", "measurement", "owner"}
)
EVIDENCE_MAX_AGE = timedelta(hours=2)
DELIVERY_REQUIRED_FIELDS: dict[str, frozenset[str]] = {
    "webhook_configs": frozenset({"url", "url_file"}),
    "email_configs": frozenset({"to"}),
    "slack_configs": frozenset({"api_url", "api_url_file"}),
    "msteams_configs": frozenset({"webhook_url"}),
    "pagerduty_configs": frozenset({"routing_key", "routing_key_file", "service_key"}),
    "wechat_configs": frozenset({"corp_id", "api_secret", "api_secret_file"}),
}
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
SAFE_REPLAY_TASK_QUEUES = {
    "ragflow.create_upload_task": "ragflow_queue",
    "ragflow.create_delete_task": "ragflow_queue",
}
SAFE_REPLAY_TASKS = frozenset(SAFE_REPLAY_TASK_QUEUES)
RELEASE_GIT_SHA_PATTERN = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
PROMETHEUS_VALIDATOR_IMAGE = (
    "prom/prometheus:v3.12.0"
    "@sha256:69f5241418838263316593f7274a304b095c40bcf22e57272865da91bd60a8ac"
)
MINIO_MC_IMAGE = (
    "minio/mc:RELEASE.2024-04-18T16-45-29Z"
    "@sha256:5a84109d6b29bab96c3122e4a7ba888fbf48d4cdc83bc8bf88e3a7ac67b970b8"
)
MINIO_SERVER_IMAGE = (
    "minio/minio:RELEASE.2024-04-18T19-09-19Z"
    "@sha256:036a068d7d6b69400da6bc07a480bee1e241ef3c341c41d988ed11f520f85124"
)
ALERTMANAGER_VALIDATOR_IMAGE = (
    "prom/alertmanager:v0.28.1"
    "@sha256:27c475db5fb156cab31d5c18a4251ac7ed567746a2483ff264516437a39b15ba"
)
SUPPORTED_VALIDATOR_ARCHITECTURES = frozenset({"amd64", "arm64"})
MINIO_METRICS_TOKEN_DIR = "/run/secrets/minio-metrics"
MINIO_METRICS_TOKEN_FILE = f"{MINIO_METRICS_TOKEN_DIR}/token"
MINIO_METRICS_SENTINEL = "metrics-bearer-only-no-data-plane"
MINIO_CA_CONTAINER_FILE = "/run/secrets/minio-ca/ca.crt"
PROTECTED_MINIO_CLIENT_SERVICES = frozenset(
    {
        "rabbitmq-topology",
        "backend-api",
        "outbox-dispatcher",
        "operational-metrics",
        "worker-document",
        "worker-ai",
        "worker-ragflow",
        "worker-notification",
        "scheduler",
    }
)
JWT_CANDIDATE_PATTERN = re.compile(
    rb"(?<![A-Za-z0-9_-])([A-Za-z0-9_-]{2,}\.[A-Za-z0-9_-]{2,}\.[A-Za-z0-9_-]{2,})(?![A-Za-z0-9_-])"
)
JWT_STRING_IDENTITY_CLAIMS = frozenset({"iss", "sub", "jti", "accessKey"})
COMPOSE_PROJECT_PATTERN = re.compile(r"[a-z0-9][a-z0-9_.-]{0,62}")
REQUIRED_INFRASTRUCTURE_RESULTS = frozenset(
    {
        "compose_up",
        "alembic_head",
        "ready",
        "gateway",
        "email_verification_floor",
        "gateway_tls",
        "workers",
        "smtp_starttls",
        "rabbitmq_topology",
        "minio_tls",
        "minio_metrics_auth",
        "upload_review_ragflow",
        "ragflow_tls",
        "dlq_protocol",
        "dependency_fault_recovery",
        "prometheus_minio_tls",
        "cleanup",
    }
)
INFRASTRUCTURE_EVIDENCE_KEYS = frozenset(
    {
        "evidence_contract_version",
        "status",
        "generated_at",
        "git_sha",
        "environment",
        "run_id",
        "compose_project",
        "source_worktree_clean",
        "architecture",
        "docker_architecture",
        "full_compose_e2e",
        "resolved_compose_sha256",
        "backend_image",
        "backend_image_id",
        "backend_image_revision",
        "frontend_image",
        "frontend_image_id",
        "frontend_image_revision",
        "service_container_ids",
        "service_image_ids",
        "worker_queue_consumers",
        "business_probe",
        "fault_recovery",
        "prometheus_minio_tls",
        "minio_metrics_auth",
        "rabbitmq_probe_run_id",
        "rabbitmq_evidence_sha256",
        "tls",
        "tls_certificate_sha256",
        "cleanup_status",
        "results",
    }
)
REQUIRED_SERVICE_CONTAINERS = frozenset(
    {
        "nginx",
        "frontend",
        "backend-api",
        "outbox-dispatcher",
        "operational-metrics",
        "worker-document",
        "worker-ai",
        "worker-ragflow",
        "worker-notification",
        "scheduler",
        "mock-ragflow",
        "mock-smtp",
        "postgres",
        "rabbitmq",
        "redis",
        "minio",
        "prometheus",
    }
)
REQUIRED_WORKER_QUEUES = frozenset(
    {
        "document_queue",
        "ai_queue",
        "ragflow_queue",
        "notification_queue",
    }
)

MAX_EVIDENCE_BYTES = 4 * 1024 * 1024
CONTRACT_INPUT_PATHS = frozenset(
    {
        "docker-compose.yml",
        "docker-compose.observability.yml",
        "docker-compose.observability.protected.yml",
        "ops/observability/prometheus.yml",
        "ops/observability/prometheus.protected.yml",
        "ops/observability/alerts.yml",
        DR_RELEASE_POLICY_CONTRACT_PATH,
        "backend/app/workers/rabbitmq_topology.py",
        "backend/Dockerfile",
        "ops/Dockerfile",
        ".github/workflows/knowledge-uploader.yml",
        "backend/app/core/jwt_validation.py",
        "backend/app/core/strict_json.py",
        "backend/app/core/minio_endpoint.py",
        "backend/scripts/minio_bootstrap.py",
        "backend/scripts/minio_metrics_token_init.py",
    }
)
WORKFLOW_TRUST_SCHEMA = "knowledge-uploader.release-workflow-trust.v1"
EXTERNAL_WORKFLOW = ".github/workflows/protected-external-evidence.yml"
OUTPUT_COMMON_KEYS = frozenset(
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
SOURCE_METADATA_KEYS = frozenset(
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
SOURCE_SCHEMAS = {
    "alertmanager-notification.json": "knowledge-uploader.alertmanager-webhook-source.v1",
    "dr-release.json": "knowledge-uploader.dr-release-source.v1",
    "email-delivery.json": "knowledge-uploader.smtp-delivery-source.v1",
    "promtool.json": "knowledge-uploader.observability-validator-source.v1",
}
OUTPUT_SCHEMAS = {
    "alertmanager-notification.json": "knowledge-uploader.alertmanager-webhook-evidence.v1",
    "dr-release.json": "knowledge-uploader.dr-release-evidence.v1",
    "email-delivery.json": "knowledge-uploader.smtp-delivery-evidence.v1",
    "promtool.json": "knowledge-uploader.observability-validator-evidence.v1",
}
SOURCE_TOOLS = {
    "alertmanager-notification.json": "alertmanager-webhook-receiver",
    "dr-release.json": "backup-restore-drill",
    "email-delivery.json": "smtp-delivery-probe",
    "promtool.json": "observability-validator",
}
ALERT_RECEIPT_KEYS = frozenset(
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
DR_RECEIPT_KEYS = frozenset(
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
EMAIL_RECEIPT_KEYS = frozenset(
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
VALIDATOR_RECEIPT_KEYS = frozenset(
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
RECEIPT_KEYS = {
    "alertmanager-notification.json": ALERT_RECEIPT_KEYS,
    "dr-release.json": DR_RECEIPT_KEYS,
    "email-delivery.json": EMAIL_RECEIPT_KEYS,
    "promtool.json": VALIDATOR_RECEIPT_KEYS,
}
FINGERPRINT_PATTERN = re.compile(r"[0-9a-f]{16,64}")
SAFE_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
EMAIL_VALUE_PATTERN = re.compile(
    r"(?i)(?:^|[^A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
    r"(?:$|[^A-Za-z0-9.-])"
)
SECRET_VALUE_PATTERN = re.compile(
    r"(?i)(?:^|[^A-Za-z0-9])(?:sk-[A-Za-z0-9_-]{8,}|bearer\s+[A-Za-z0-9._~-]{8,})"
)


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be a JSON object")
    return value


def _safe_evidence_file(root: Path, filename: str) -> Path:
    candidate = root / filename
    if candidate.is_symlink() or not candidate.is_file():
        raise RuntimeError(f"evidence file is missing or unsafe: {filename}")
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise RuntimeError(f"evidence path escapes root: {filename}") from error
    return resolved


def _read_stable_regular_file(path: Path, *, label: str) -> bytes:
    descriptor = -1
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise RuntimeError(f"{label} is not a regular file")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            or opened.st_size > MAX_EVIDENCE_BYTES
        ):
            raise RuntimeError(f"{label} changed before it could be read")
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = -1
            payload = stream.read(MAX_EVIDENCE_BYTES + 1)
            after = os.fstat(stream.fileno())
        current = path.lstat()
    except OSError as error:
        raise RuntimeError(f"cannot read {label}") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if (
        len(payload) > MAX_EVIDENCE_BYTES
        or len(payload) != opened.st_size
        or (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
        or not stat.S_ISREG(current.st_mode)
    ):
        raise RuntimeError(f"{label} changed while it was read")
    return payload


def _snapshot_exact_evidence_directory(
    evidence_root: Path,
    required_names: frozenset[str],
) -> dict[str, bytes]:
    if evidence_root.is_symlink() or not evidence_root.is_dir():
        raise RuntimeError("release evidence directory is missing or unsafe")
    root = evidence_root.resolve()
    try:
        before_entries = {entry.name: entry.lstat() for entry in root.iterdir()}
    except OSError as error:
        raise RuntimeError("cannot enumerate release evidence directory") from error
    missing = sorted(required_names - set(before_entries))
    extra = sorted(set(before_entries) - required_names)
    unsafe = sorted(
        name for name, metadata in before_entries.items() if not stat.S_ISREG(metadata.st_mode)
    )
    if missing or extra or unsafe:
        raise RuntimeError(
            "release evidence directory inventory mismatch: "
            f"missing={missing}, extra={extra}, unsafe={unsafe}"
        )
    payloads = {
        filename: _read_stable_regular_file(root / filename, label=filename)
        for filename in sorted(required_names)
    }
    try:
        after_entries = {entry.name: entry.lstat() for entry in root.iterdir()}
    except OSError as error:
        raise RuntimeError("cannot re-enumerate release evidence directory") from error
    before_identity = {
        name: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
        for name, value in before_entries.items()
    }
    after_identity = {
        name: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
        for name, value in after_entries.items()
    }
    if before_identity != after_identity:
        raise RuntimeError("release evidence directory changed while it was read")
    return payloads


def snapshot_contract_payloads() -> dict[str, bytes]:
    """Capture one caller-owned generation of every protected contract input."""

    return {
        relative: _read_stable_regular_file(
            ROOT / relative,
            label=f"release contract input {relative}",
        )
        for relative in sorted(CONTRACT_INPUT_PATHS)
    }


def _contract_payload_mapping(
    payloads: Mapping[str, bytes],
) -> Mapping[str, bytes]:
    missing = sorted(CONTRACT_INPUT_PATHS - set(payloads))
    extra = sorted(set(payloads) - CONTRACT_INPUT_PATHS)
    if missing or extra:
        raise RuntimeError(
            f"release contract payload inventory mismatch: missing={missing}, extra={extra}"
        )
    return payloads


def _reject_json_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _decode_base64url_bytes(segment: bytes) -> bytes:
    padding = b"=" * (-len(segment) % 4)
    return base64.b64decode(segment + padding, altchars=b"-_", validate=True)


def _nonempty_jwt_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _has_nonempty_jwt_identity(claims: dict[str, object]) -> bool:
    if any(_nonempty_jwt_string(claims.get(name)) for name in JWT_STRING_IDENTITY_CLAIMS):
        return True
    audience = claims.get("aud")
    if _nonempty_jwt_string(audience):
        return True
    return (
        isinstance(audience, list)
        and bool(audience)
        and all(_nonempty_jwt_string(item) for item in audience)
    )


def _contains_semantic_jwt(payload: bytes) -> bool:
    for match in JWT_CANDIDATE_PATTERN.finditer(payload):
        segments = match.group(1).split(b".")
        try:
            header = json.loads(
                _decode_base64url_bytes(segments[0]).decode("utf-8"),
                parse_constant=_reject_json_constant,
                object_pairs_hook=_reject_duplicate_pairs,
            )
            claims = json.loads(
                _decode_base64url_bytes(segments[1]).decode("utf-8"),
                parse_constant=_reject_json_constant,
                object_pairs_hook=_reject_duplicate_pairs,
            )
            signature = _decode_base64url_bytes(segments[2])
        except (
            UnicodeDecodeError,
            binascii.Error,
            json.JSONDecodeError,
            ValueError,
        ):
            continue
        algorithm = header.get("alg") if isinstance(header, dict) else None
        if (
            isinstance(algorithm, str)
            and bool(algorithm.strip())
            and algorithm.strip().lower() != "none"
            and isinstance(claims, dict)
            and _has_nonempty_jwt_identity(claims)
            and bool(signature)
        ):
            return True
    return False


def reject_semantic_jwts(payloads: Mapping[str, bytes]) -> None:
    """Reject semantic bearer JWTs in every caller-owned raw payload."""

    for filename, payload in sorted(payloads.items()):
        if _contains_semantic_jwt(payload):
            raise RuntimeError(f"{filename} contains bearer credential material")


def _reject_semantic_jwts(payloads: Mapping[str, bytes]) -> None:
    reject_semantic_jwts(payloads)


def _load_evidence(root: Path, filename: str) -> dict[str, Any]:
    path = _safe_evidence_file(root, filename)
    payload = _read_stable_regular_file(path, label=filename)
    return _parse_evidence_payload(payload, filename)


def _parse_evidence_payload(payload: bytes, filename: str) -> dict[str, Any]:
    try:
        loaded = json.loads(
            payload.decode("utf-8"),
            parse_constant=_reject_json_constant,
            object_pairs_hook=_reject_duplicate_pairs,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise RuntimeError(f"{filename} is not strict JSON") from error
    return _mapping(loaded, filename)


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


def _timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise RuntimeError(f"{label} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise RuntimeError(f"{label} must be an ISO timestamp") from error
    if parsed.tzinfo is None:
        raise RuntimeError(f"{label} must include a timezone")
    return parsed.astimezone(UTC)


def _fresh(timestamp: datetime, *, now: datetime) -> bool:
    return now - EVIDENCE_MAX_AGE <= timestamp <= now + timedelta(minutes=5)


def _positive_integer(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise RuntimeError(f"{label} must be a positive integer")
    return value


def _load_dr_release_policy(payload: bytes) -> dict[str, Any]:
    policy = _parse_evidence_payload(payload, "DR release policy")
    _exact_keys(policy, DR_RELEASE_POLICY_KEYS, "DR release policy")
    if policy.get("schema") != DR_RELEASE_POLICY_SCHEMA:
        raise RuntimeError("DR release policy schema is unsupported")
    _positive_integer(policy.get("max_rpo_seconds"), "DR policy max_rpo_seconds")
    _positive_integer(policy.get("max_rto_seconds"), "DR policy max_rto_seconds")
    for field in ("measurement", "owner"):
        value = policy.get(field)
        if not isinstance(value, str) or not value.strip() or len(value) > 256:
            raise RuntimeError(f"DR policy {field} is invalid")
    return policy


def _exact_keys(value: dict[str, Any], expected: frozenset[str], label: str) -> None:
    if set(value) != expected:
        raise RuntimeError(f"{label} schema mismatch")


def _external_collector_identity(
    trust: dict[str, Any],
    *,
    git_sha: str,
    now: datetime,
) -> tuple[int, int]:
    if trust.get("schema") != WORKFLOW_TRUST_SCHEMA:
        raise RuntimeError("workflow trust schema is unsupported")
    if not _fresh(_timestamp(trust.get("generated_at"), "workflow trust generated_at"), now=now):
        raise RuntimeError("workflow trust summary is stale or from the future")
    evidence_runs = trust.get("evidence_runs")
    if not isinstance(evidence_runs, list):
        raise RuntimeError("workflow trust evidence_runs must be a list")
    records = [
        _mapping(record, "workflow trust evidence run")
        for record in evidence_runs
        if isinstance(record, dict)
    ]
    if {record.get("role") for record in records} != {"dgx", "external"}:
        raise RuntimeError("workflow trust evidence role inventory is incomplete")
    external_matches = [record for record in records if record.get("role") == "external"]
    if len(external_matches) != 1:
        raise RuntimeError("workflow trust external run identity is ambiguous")
    external = external_matches[0]
    _exact_keys(
        external,
        frozenset(
            {
                "role",
                "run_id",
                "run_attempt",
                "workflow_path",
                "event",
                "head_sha",
                "head_branch",
                "status",
                "conclusion",
                "created_at",
                "updated_at",
                "artifact",
            }
        ),
        "workflow trust external run",
    )
    if (
        external.get("workflow_path") != EXTERNAL_WORKFLOW
        or external.get("event") != "workflow_dispatch"
        or external.get("head_sha") != git_sha
        or external.get("status") != "completed"
        or external.get("conclusion") != "success"
    ):
        raise RuntimeError("workflow trust external run identity is invalid")
    created_at = _timestamp(external.get("created_at"), "workflow trust external created_at")
    updated_at = _timestamp(external.get("updated_at"), "workflow trust external updated_at")
    if updated_at < created_at or not _fresh(updated_at, now=now):
        raise RuntimeError("workflow trust external run timestamps are invalid")
    return (
        _positive_integer(external.get("run_id"), "workflow trust external run_id"),
        _positive_integer(external.get("run_attempt"), "workflow trust external run_attempt"),
    )


def _validate_external_projection(
    evidence: dict[str, Any],
    *,
    filename: str,
    git_sha: str,
    environment: str,
    collector_run_id: int,
    collector_run_attempt: int,
    now: datetime,
) -> tuple[str, int, dict[str, Any]]:
    _exact_keys(evidence, OUTPUT_COMMON_KEYS, filename)
    if (
        evidence.get("schema") != OUTPUT_SCHEMAS[filename]
        or evidence.get("status") != "passed"
        or evidence.get("git_sha") != git_sha
        or evidence.get("environment") != environment
        or evidence.get("collector_run_id") != collector_run_id
        or evidence.get("collector_run_attempt") != collector_run_attempt
    ):
        raise RuntimeError(f"{filename} collector identity mismatch")
    collected_at = _timestamp(evidence.get("generated_at"), f"{filename} generated_at")
    if not _fresh(collected_at, now=now):
        raise RuntimeError(f"{filename} is stale or from the future")

    source = _mapping(evidence.get("source"), f"{filename} source")
    _exact_keys(source, SOURCE_METADATA_KEYS, f"{filename} source")
    if (
        source.get("schema") != SOURCE_SCHEMAS[filename]
        or source.get("tool") != SOURCE_TOOLS[filename]
    ):
        raise RuntimeError(f"{filename} source identity mismatch")
    run_id = source.get("run_id")
    if not _is_uuid(run_id):
        raise RuntimeError(f"{filename} source run_id is invalid")
    run_attempt = _positive_integer(source.get("run_attempt"), f"{filename} source run_attempt")
    source_generated_at = _timestamp(
        source.get("generated_at"),
        f"{filename} source generated_at",
    )
    if not _fresh(source_generated_at, now=now) or source_generated_at > collected_at + timedelta(
        minutes=5
    ):
        raise RuntimeError(f"{filename} source time is stale or inconsistent")
    for field in ("file_sha256", "canonical_sha256"):
        if (
            not isinstance(source.get(field), str)
            or SHA256_PATTERN.fullmatch(str(source[field])) is None
        ):
            raise RuntimeError(f"{filename} source {field} is invalid")
    receipt = _mapping(evidence.get("receipt"), f"{filename} receipt")
    _exact_keys(receipt, RECEIPT_KEYS[filename], f"{filename} receipt")
    reconstructed = {
        "schema": source["schema"],
        "generated_at": source["generated_at"],
        "git_sha": evidence["git_sha"],
        "environment": evidence["environment"],
        "source_run_id": run_id,
        "source_run_attempt": run_attempt,
        "source_tool": source["tool"],
        "status": evidence["status"],
        "receipt": receipt,
    }
    if source.get("canonical_sha256") != _canonical_sha256(reconstructed):
        raise RuntimeError(f"{filename} source canonical checksum mismatch")
    return str(run_id), run_attempt, receipt


def _is_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def _is_release_git_sha(value: object) -> bool:
    return isinstance(value, str) and RELEASE_GIT_SHA_PATTERN.fullmatch(value) is not None


def _is_sha256_image_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and value.startswith("sha256:")
        and SHA256_PATTERN.fullmatch(value.removeprefix("sha256:")) is not None
    )


def _infrastructure_e2e_errors(
    evidence: dict[str, Any],
    *,
    git_sha: str,
) -> list[str]:
    errors: list[str] = []
    _require(
        set(evidence) == INFRASTRUCTURE_EVIDENCE_KEYS,
        "infrastructure E2E top-level schema mismatch",
        errors,
    )
    _require(
        evidence.get("status") == "development_passed",
        "infrastructure E2E raw execution status is invalid",
        errors,
    )
    _require(
        evidence.get("full_compose_e2e") == "development_passed",
        "infrastructure full Compose E2E is missing",
        errors,
    )
    _require(
        evidence.get("source_worktree_clean") is True,
        "infrastructure E2E source worktree was not clean",
        errors,
    )
    _require(
        evidence.get("cleanup_status") == "passed",
        "infrastructure E2E cleanup did not pass",
        errors,
    )
    _require(
        str(evidence.get("architecture", "")).strip().lower() in {"arm64", "aarch64"},
        "infrastructure E2E did not run on ARM64",
        errors,
    )
    _require(_is_uuid(evidence.get("run_id")), "infrastructure run_id is invalid", errors)
    compose_project = evidence.get("compose_project")
    _require(
        isinstance(compose_project, str)
        and COMPOSE_PROJECT_PATTERN.fullmatch(compose_project) is not None,
        "infrastructure Compose project identity is invalid",
        errors,
    )
    _require(
        isinstance(evidence.get("resolved_compose_sha256"), str)
        and SHA256_PATTERN.fullmatch(str(evidence["resolved_compose_sha256"])) is not None,
        "resolved Compose digest is invalid",
        errors,
    )
    _require(
        isinstance(evidence.get("tls_certificate_sha256"), str)
        and SHA256_PATTERN.fullmatch(str(evidence["tls_certificate_sha256"])) is not None,
        "infrastructure TLS certificate digest is invalid",
        errors,
    )
    _require(
        evidence.get("rabbitmq_probe_run_id") == evidence.get("run_id"),
        "infrastructure RabbitMQ run identity is invalid",
        errors,
    )
    for image_name in ("backend", "frontend"):
        _require(
            evidence.get(f"{image_name}_image_revision") == git_sha,
            f"infrastructure {image_name} image revision mismatch",
            errors,
        )
        _require(
            _is_sha256_image_id(evidence.get(f"{image_name}_image_id")),
            f"infrastructure {image_name} image content id is invalid",
            errors,
        )
    results = evidence.get("results")
    _require(
        isinstance(results, dict)
        and set(results) == REQUIRED_INFRASTRUCTURE_RESULTS
        and all(results.get(name) == "passed" for name in REQUIRED_INFRASTRUCTURE_RESULTS),
        "infrastructure E2E is missing detailed passed results",
        errors,
    )
    service_container_ids = evidence.get("service_container_ids")
    _require(
        isinstance(service_container_ids, dict)
        and set(service_container_ids) == REQUIRED_SERVICE_CONTAINERS
        and all(
            isinstance(service_container_ids.get(name), str)
            and SHA256_PATTERN.fullmatch(str(service_container_ids[name])) is not None
            for name in REQUIRED_SERVICE_CONTAINERS
        ),
        "infrastructure service container identities are incomplete",
        errors,
    )
    worker_queue_consumers = evidence.get("worker_queue_consumers")
    _require(
        isinstance(worker_queue_consumers, dict)
        and set(worker_queue_consumers) == REQUIRED_WORKER_QUEUES
        and all(
            isinstance(worker_queue_consumers.get(queue), int)
            and not isinstance(worker_queue_consumers.get(queue), bool)
            and int(worker_queue_consumers[queue]) >= 1
            for queue in REQUIRED_WORKER_QUEUES
        ),
        "infrastructure worker queue consumers are incomplete",
        errors,
    )
    business_probe = evidence.get("business_probe")
    _require(
        isinstance(business_probe, dict)
        and business_probe.get("status") == "passed"
        and business_probe.get("email_verification_floor") == "passed"
        and business_probe.get("mock_smtp_delivery") == "passed",
        "infrastructure email verification behavior is missing",
        errors,
    )
    errors.extend(_infrastructure_resilience_errors(evidence))
    return errors


def _infrastructure_resilience_errors(evidence: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    _require(
        evidence.get("evidence_contract_version") == 5,
        "infrastructure evidence contract version is unsupported",
        errors,
    )
    raw_tls = evidence.get("tls")
    tls = raw_tls if isinstance(raw_tls, dict) else {}
    _require(
        set(tls)
        == {
            "status",
            "ca_sha256",
            "certificate_bundle_sha256",
            "certificates",
            "verified_channels",
        },
        "infrastructure TLS schema mismatch",
        errors,
    )
    _require(tls.get("status") == "passed", "infrastructure TLS did not pass", errors)
    for field in ("ca_sha256", "certificate_bundle_sha256"):
        value = tls.get(field)
        _require(
            isinstance(value, str) and SHA256_PATTERN.fullmatch(value) is not None,
            f"infrastructure TLS {field} is invalid",
            errors,
        )
    raw_certificates = tls.get("certificates")
    certificates = raw_certificates if isinstance(raw_certificates, dict) else {}
    _require(
        set(certificates) == {"minio", "ragflow", "smtp", "gateway"}
        and all(
            isinstance(value, str) and SHA256_PATTERN.fullmatch(value) is not None
            for value in certificates.values()
        ),
        "infrastructure TLS certificate identities are incomplete",
        errors,
    )
    channels = tls.get("verified_channels")
    _require(
        isinstance(channels, list)
        and len(channels) == 4
        and set(channels) == {"gateway_https", "minio_https", "ragflow_https", "smtp_starttls"},
        "infrastructure TLS channel proof is incomplete",
        errors,
    )
    _require(
        evidence.get("tls_certificate_sha256") == tls.get("certificate_bundle_sha256"),
        "legacy TLS bundle digest does not match the structured proof",
        errors,
    )
    raw_prometheus_minio = evidence.get("prometheus_minio_tls")
    prometheus_minio = raw_prometheus_minio if isinstance(raw_prometheus_minio, dict) else {}
    _require(
        set(prometheus_minio)
        == {
            "status",
            "job",
            "health",
            "scrape_url",
            "config_sha256",
            "ca_file",
            "server_name",
            "certificate_verification",
        },
        "infrastructure Prometheus MinIO TLS schema mismatch",
        errors,
    )
    _require(
        prometheus_minio.get("status") == "passed"
        and prometheus_minio.get("job") == "minio"
        and prometheus_minio.get("health") == "up"
        and prometheus_minio.get("scrape_url") == "https://minio:9000/minio/v2/metrics/cluster"
        and prometheus_minio.get("ca_file") == "/etc/prometheus/tls/ca.crt"
        and prometheus_minio.get("server_name") == "minio"
        and prometheus_minio.get("certificate_verification") == "required",
        "infrastructure Prometheus did not prove a verified HTTPS MinIO scrape",
        errors,
    )
    _require(
        isinstance(prometheus_minio.get("config_sha256"), str)
        and SHA256_PATTERN.fullmatch(str(prometheus_minio["config_sha256"])) is not None,
        "infrastructure protected Prometheus config digest is invalid",
        errors,
    )

    raw_minio_auth = evidence.get("minio_metrics_auth")
    minio_auth = raw_minio_auth if isinstance(raw_minio_auth, dict) else {}
    _require(
        set(minio_auth)
        == {
            "status",
            "auth_mode",
            "initializer",
            "anonymous_access",
            "atomic_publish",
            "refresh",
            "emergency_revocation",
            "identity_reconciliation",
            "collector",
        }
        and minio_auth.get("status") == "passed"
        and minio_auth.get("auth_mode") == "jwt_bearer_file",
        "infrastructure MinIO metrics auth schema mismatch",
        errors,
    )
    raw_initializer = minio_auth.get("initializer")
    initializer = raw_initializer if isinstance(raw_initializer, dict) else {}
    _require(
        set(initializer)
        == {
            "status",
            "container_exit",
            "logs",
            "token_file",
            "mode",
            "uid",
            "gid",
        }
        and initializer.get("status") == "passed"
        and initializer.get("container_exit") == "exited_0"
        and initializer.get("logs") == "empty"
        and initializer.get("token_file") == "strict_semantic_jwt_single_lf"
        and initializer.get("mode") == "0440"
        and initializer.get("uid") == 65534
        and initializer.get("gid") == 65534,
        "infrastructure MinIO metrics initializer proof is incomplete",
        errors,
    )
    raw_anonymous = minio_auth.get("anonymous_access")
    anonymous = raw_anonymous if isinstance(raw_anonymous, dict) else {}
    _require(
        set(anonymous) == {"status", "http_status"}
        and anonymous.get("status") == "denied"
        and anonymous.get("http_status") in {401, 403},
        "infrastructure anonymous MinIO metrics access was not denied",
        errors,
    )
    raw_atomic = minio_auth.get("atomic_publish")
    atomic = raw_atomic if isinstance(raw_atomic, dict) else {}
    _require(
        set(atomic)
        == {
            "status",
            "concurrent_runs",
            "concurrent_successes",
            "term_exit_code",
            "term_cleanup",
            "sigkill_exit_code",
            "sigkill_orphan_observed",
            "post_sigkill_recovery",
            "cleanup_after_no_initializer",
            "final_temporary_file_count",
        }
        and atomic.get("status") == "passed"
        and atomic.get("concurrent_runs") == 2
        and atomic.get("concurrent_successes") == 2
        and atomic.get("term_exit_code") in {1, 143}
        and atomic.get("term_cleanup") == "passed"
        and atomic.get("sigkill_exit_code") == 137
        and atomic.get("sigkill_orphan_observed") is True
        and atomic.get("post_sigkill_recovery") == "passed"
        and atomic.get("cleanup_after_no_initializer") is True
        and atomic.get("final_temporary_file_count") == 0,
        "infrastructure MinIO atomic token publication proof is incomplete",
        errors,
    )
    raw_refresh = minio_auth.get("refresh")
    refresh = raw_refresh if isinstance(raw_refresh, dict) else {}
    _require(
        set(refresh)
        == {
            "status",
            "semantics",
            "credential_changed",
            "mtime_advanced",
            "previous_jwt_http_status",
            "refreshed_jwt_http_status",
            "consumer_processes_unchanged",
            "prometheus_health_before",
            "prometheus_health_after",
        }
        and refresh.get("status") == "passed"
        and refresh.get("semantics") == "consumer_refresh_not_revocation"
        and refresh.get("credential_changed") is True
        and refresh.get("mtime_advanced") is True
        and refresh.get("previous_jwt_http_status") == 200
        and refresh.get("refreshed_jwt_http_status") == 200
        and refresh.get("consumer_processes_unchanged") is True
        and refresh.get("prometheus_health_before") == "up"
        and refresh.get("prometheus_health_after") == "up",
        "infrastructure MinIO routine refresh semantics are incomplete",
        errors,
    )
    raw_emergency = minio_auth.get("emergency_revocation")
    emergency = raw_emergency if isinstance(raw_emergency, dict) else {}
    _require(
        set(emergency)
        == {
            "status",
            "method",
            "previous_jwt_http_status_after_restart",
            "refreshed_jwt_http_status_after_restart",
            "replacement_jwt_http_status",
            "minio_recreated",
            "bootstrap_reconciled",
            "expected_minio_interruption",
            "consumer_processes_unchanged",
            "automatic_consumer_recovery",
            "prometheus_health_after_recovery",
        }
        and emergency.get("status") == "passed"
        and emergency.get("method") == "root_credential_rotation_and_minio_restart"
        and emergency.get("previous_jwt_http_status_after_restart") == 403
        and emergency.get("refreshed_jwt_http_status_after_restart") == 403
        and emergency.get("replacement_jwt_http_status") == 200
        and emergency.get("minio_recreated") is True
        and emergency.get("bootstrap_reconciled") is True
        and emergency.get("expected_minio_interruption") is True
        and emergency.get("consumer_processes_unchanged") is True
        and emergency.get("automatic_consumer_recovery") is True
        and emergency.get("prometheus_health_after_recovery") == "up",
        "infrastructure MinIO emergency revocation proof is incomplete",
        errors,
    )
    raw_identity = minio_auth.get("identity_reconciliation")
    identity = raw_identity if isinstance(raw_identity, dict) else {}
    _require(
        set(identity)
        == {
            "status",
            "stale_direct_policy_removed",
            "stale_group_membership_removed",
            "intended_policy_attached",
            "intended_bucket_operations",
            "secondary_bucket_operations_denied",
            "admin_operations_denied",
        }
        and identity.get("status") == "passed"
        and identity.get("stale_direct_policy_removed") is True
        and identity.get("stale_group_membership_removed") is True
        and identity.get("intended_policy_attached") is True
        and identity.get("intended_bucket_operations") == ["get", "put", "delete"]
        and identity.get("secondary_bucket_operations_denied") == ["list", "get", "put"]
        and identity.get("admin_operations_denied") == ["info", "user_list", "policy_list"],
        "infrastructure MinIO data-plane identity reconciliation is incomplete",
        errors,
    )
    raw_collector = minio_auth.get("collector")
    collector = raw_collector if isinstance(raw_collector, dict) else {}
    _require(
        set(collector) == {"status", "component", "last_success_advanced"}
        and collector.get("status") == "passed"
        and collector.get("component") == "minio_capacity"
        and collector.get("last_success_advanced") is True,
        "infrastructure MinIO capacity collector did not recover in place",
        errors,
    )

    service_image_ids = evidence.get("service_image_ids")
    _require(
        isinstance(service_image_ids, dict)
        and set(service_image_ids) == REQUIRED_SERVICE_CONTAINERS
        and all(_is_sha256_image_id(value) for value in service_image_ids.values()),
        "infrastructure service image identities are incomplete",
        errors,
    )
    raw_fault_recovery = evidence.get("fault_recovery")
    fault_recovery = raw_fault_recovery if isinstance(raw_fault_recovery, dict) else {}
    _require(
        set(fault_recovery) == {"rabbitmq", "redis", "minio", "ragflow"},
        "fault recovery dependency inventory is incomplete",
        errors,
    )
    expected = {
        "rabbitmq": (
            "rabbitmq",
            "ready_503",
            "persistent_message_held_while_broker_unavailable",
            "rabbitmq_durable_queue",
        ),
        "redis": (
            "redis",
            "ready_503",
            "celery_retry_requeued_while_cache_unavailable",
            "celery_retry_message",
        ),
        "minio": (
            "minio",
            "ready_503",
            "postgres_failed_sync_task_before_remote_upload",
            "postgres_sync_task",
        ),
        "ragflow": (
            "mock-ragflow",
            "tls_endpoint_unreachable",
            "postgres_failed_sync_task_before_remote_upload",
            "postgres_sync_task",
        ),
    }
    common_entry_keys = {
        "status",
        "run_id",
        "service",
        "target_file_id",
        "outage_observed",
        "failure_observation",
        "durability_anchor",
        "queue_messages_before",
        "queue_messages_after_restore",
        "remote_upload_delta",
        "remote_document_count",
        "terminal_state",
        "event_loss_detected",
        "duplicate_remote_document",
    }
    extra_entry_keys = {
        "rabbitmq": {"broker_message_persisted"},
        "redis": {
            "retry_task_id",
            "retry_task_name",
            "retry_queue",
            "retry_count_observed",
            "retry_status_before_restore",
        },
        "minio": {"failed_task_id", "retry_status_before", "retry_status_after"},
        "ragflow": {"failed_task_id", "retry_status_before", "retry_status_after"},
    }

    target_file_ids: set[str] = set()
    outer_run_id = evidence.get("run_id")
    for dependency, (
        service,
        outage,
        failure_observation,
        durability_anchor,
    ) in expected.items():
        raw_entry = fault_recovery.get(dependency)
        entry = raw_entry if isinstance(raw_entry, dict) else {}
        _require(
            set(entry) == common_entry_keys | extra_entry_keys[dependency],
            f"fault recovery {dependency} schema mismatch",
            errors,
        )
        target_file_id = entry.get("target_file_id")
        _require(
            _is_uuid(target_file_id),
            f"fault recovery {dependency} target file identity is invalid",
            errors,
        )
        if isinstance(target_file_id, str):
            target_file_ids.add(target_file_id)
        required_values = {
            "status": "passed",
            "run_id": outer_run_id,
            "service": service,
            "outage_observed": outage,
            "failure_observation": failure_observation,
            "durability_anchor": durability_anchor,
            "queue_messages_before": 1,
            "queue_messages_after_restore": 1,
            "remote_upload_delta": 1,
            "remote_document_count": 1,
            "terminal_state": "parsed",
            "event_loss_detected": False,
            "duplicate_remote_document": False,
        }
        _require(
            all(entry.get(field) == value for field, value in required_values.items()),
            f"fault recovery {dependency} proof is incomplete",
            errors,
        )
        if dependency == "rabbitmq":
            _require(
                entry.get("broker_message_persisted") is True,
                "fault recovery rabbitmq persistence receipt is invalid",
                errors,
            )
        elif dependency == "redis":
            _require(
                _is_uuid(entry.get("retry_task_id")),
                "fault recovery redis retry task identity is invalid",
                errors,
            )
            _require(
                entry.get("retry_task_name") == "ragflow.create_upload_task"
                and entry.get("retry_queue") == "ragflow_queue"
                and entry.get("retry_count_observed") == 1
                and not isinstance(entry.get("retry_count_observed"), bool)
                and entry.get("retry_status_before_restore") == "requeued",
                "fault recovery redis retry receipt is incomplete",
                errors,
            )
        else:
            _require(
                _is_uuid(entry.get("failed_task_id")),
                f"fault recovery {dependency} failed task identity is invalid",
                errors,
            )
            _require(
                entry.get("retry_status_before") == "failed"
                and entry.get("retry_status_after") == "queued",
                f"fault recovery {dependency} retry receipt is incomplete",
                errors,
            )
        for field in (
            "queue_messages_before",
            "queue_messages_after_restore",
            "remote_upload_delta",
            "remote_document_count",
        ):
            _require(
                not isinstance(entry.get(field), bool),
                f"fault recovery {dependency} {field} has an invalid type",
                errors,
            )
    _require(
        len(target_file_ids) == len(expected),
        "fault recovery target files are not unique",
        errors,
    )
    return errors


def _rabbitmq_replay_binding_errors(
    *,
    exhausted: dict[str, Any],
    replayed: dict[str, Any],
    resolved: dict[str, Any],
) -> list[str]:
    """Bind replay evidence to the exact safe queue and deterministic task id."""
    errors: list[str] = []
    task_name = exhausted.get("task_name")
    exhausted_queue = exhausted.get("queue_name")
    expected_queue = SAFE_REPLAY_TASK_QUEUES.get(task_name) if isinstance(task_name, str) else None
    _require(
        expected_queue is not None and exhausted_queue == expected_queue,
        "RabbitMQ exhausted task is not bound to its allowlisted queue",
        errors,
    )
    _require(
        replayed.get("queue_name") == exhausted_queue,
        "RabbitMQ replay queue does not match the exhausted queue",
        errors,
    )
    _require(
        resolved.get("queue_name") == exhausted_queue,
        "RabbitMQ resolved queue does not match the exhausted queue",
        errors,
    )

    original_task_id = exhausted.get("task_id")
    replay_task_id = replayed.get("replay_task_id")
    if (
        isinstance(exhausted_queue, str)
        and isinstance(original_task_id, str)
        and _is_uuid(original_task_id)
    ):
        expected_replay_task_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"rabbitmq-replay:{exhausted_queue}:{uuid.UUID(original_task_id)}",
            )
        )
        _require(
            replay_task_id == expected_replay_task_id,
            "RabbitMQ replay task id is not the deterministic queue/original binding",
            errors,
        )
    else:
        errors.append("RabbitMQ replay identity inputs are invalid")
    return errors


def _rabbitmq_exhaustion_errors(exhausted: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    _require(exhausted.get("result") == "dead_lettered", "exhausted task missed DLQ", errors)
    _require(
        isinstance(exhausted.get("attempts"), int)
        and not isinstance(exhausted.get("attempts"), bool)
        and exhausted.get("attempts") == 4
        and exhausted.get("retry_count") == 3
        and exhausted.get("dead_letter_reason") == "rejected"
        and exhausted.get("delivery_path") == "celery_worker_retry_exhaustion",
        "RabbitMQ exhaustion did not prove the worker's final rejected attempt",
        errors,
    )
    _require(exhausted.get("dlq_count_after") == 1, "exhausted DLQ count is invalid", errors)
    _require(
        exhausted.get("task_name") in SAFE_REPLAY_TASKS,
        "exhausted RabbitMQ task is not safely reconstructable",
        errors,
    )
    return errors


def _sha256_path(path: Path) -> str:
    return _sha256_bytes(
        _read_stable_regular_file(
            path,
            label=f"repository release input {path.name}",
        )
    )


def _validator_image_evidence_errors(
    evidence: dict[str, Any],
    *,
    name: str,
    expected_reference: str,
) -> list[str]:
    errors: list[str] = []
    expected_digest = expected_reference.rsplit("@", maxsplit=1)[1]
    prefix = f"{name}_"
    _require(
        evidence.get(f"{prefix}image") == expected_reference,
        f"{name} validator image reference is not the approved manifest-list digest",
        errors,
    )
    _require(
        evidence.get(f"{prefix}manifest_list_digest") == expected_digest,
        f"{name} validator manifest-list digest does not match the approved digest",
        errors,
    )
    _require(
        _is_sha256_image_id(evidence.get(f"{prefix}image_id")),
        f"{name} validator image id is invalid",
        errors,
    )
    _require(
        evidence.get(f"{prefix}image_os") == "linux",
        f"{name} validator image is not Linux",
        errors,
    )
    image_architecture = evidence.get(f"{prefix}image_architecture")
    docker_architecture = evidence.get(f"{prefix}docker_architecture")
    _require(
        image_architecture in SUPPORTED_VALIDATOR_ARCHITECTURES,
        f"{name} validator image architecture is unsupported",
        errors,
    )
    _require(
        docker_architecture == image_architecture,
        f"{name} validator image architecture does not match the Docker daemon",
        errors,
    )
    return errors


def _alertmanager_config_payload(config: Path | bytes) -> bytes:
    if isinstance(config, bytes):
        return config
    if config.is_symlink() or not config.is_file():
        raise RuntimeError("Alertmanager evidence config is missing or unsafe")
    return _read_stable_regular_file(config.resolve(), label="alertmanager.yml")


def _promtool_evidence_errors(
    evidence: dict[str, Any],
    *,
    alertmanager_config: Path | bytes,
    contract_payloads: Mapping[str, bytes] | None = None,
) -> list[str]:
    errors: list[str] = []
    _require(
        set(evidence) == VALIDATOR_RECEIPT_KEYS,
        "validator receipt schema mismatch",
        errors,
    )
    contracts = _contract_payload_mapping(
        snapshot_contract_payloads() if contract_payloads is None else contract_payloads
    )
    expected_hashes = {
        "prometheus_config_sha256": _sha256_bytes(
            contracts["ops/observability/prometheus.protected.yml"]
        ),
        "prometheus_rules_sha256": _sha256_bytes(contracts["ops/observability/alerts.yml"]),
        "alertmanager_config_sha256": _sha256_bytes(
            _alertmanager_config_payload(alertmanager_config)
        ),
    }
    _require(evidence.get("prometheus_config") == "passed", "promtool config missing", errors)
    _require(evidence.get("prometheus_rules") == "passed", "promtool rules missing", errors)
    _require(evidence.get("alertmanager_config") == "passed", "amtool config missing", errors)
    for field, expected in expected_hashes.items():
        _require(
            evidence.get(field) == expected,
            f"promtool evidence {field} does not match the validated file",
            errors,
        )
    errors.extend(
        _validator_image_evidence_errors(
            evidence,
            name="prometheus",
            expected_reference=PROMETHEUS_VALIDATOR_IMAGE,
        )
    )
    errors.extend(
        _validator_image_evidence_errors(
            evidence,
            name="alertmanager",
            expected_reference=ALERTMANAGER_VALIDATOR_IMAGE,
        )
    )
    _require(
        evidence.get("prometheus_docker_architecture")
        == evidence.get("alertmanager_docker_architecture"),
        "validator images were not executed on one Docker architecture",
        errors,
    )
    return errors


def _safe_offsite_uri(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    return (
        parsed.scheme in {"s3", "https"}
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
    )


def _resolved_compose_services() -> dict[str, Any]:
    try:
        command = ["docker", "compose"]
        for filename in (
            "docker-compose.yml",
            "docker-compose.observability.yml",
            "docker-compose.observability.protected.yml",
        ):
            command.extend(["-f", str(ROOT / filename)])
        command.extend(["config", "--format", "json"])
        completed = subprocess.run(
            command,
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        config = _mapping(json.loads(completed.stdout), "resolved Compose config")
        return _mapping(config.get("services"), "resolved Compose services")
    except (OSError, subprocess.SubprocessError, ValueError, RuntimeError) as error:
        raise RuntimeError("could not resolve protected Docker Compose services") from error


def _resolved_backend_api_service() -> dict[str, Any]:
    services = _resolved_compose_services()
    return _mapping(services.get("backend-api"), "resolved backend-api service")


def _backend_api_hosts(backend: dict[str, Any]) -> set[str]:
    ports = backend.get("ports")
    if not isinstance(ports, list):
        return set()
    return {
        str(port.get("host_ip", ""))
        for port in ports
        if isinstance(port, dict) and port.get("target") in {8000, "8000"}
    }


def _resolved_backend_api_hosts() -> set[str]:
    return _backend_api_hosts(_resolved_backend_api_service())


def _backend_api_environment(backend: dict[str, Any]) -> dict[str, str]:
    raw_environment = backend.get("environment")
    if not isinstance(raw_environment, dict):
        raise RuntimeError("resolved backend-api environment is invalid")
    return {str(key): str(value) for key, value in raw_environment.items() if value is not None}


def _resolved_service_environment(service: object, label: str) -> dict[str, str]:
    current = _mapping(service, label)
    raw_environment = _mapping(current.get("environment"), f"{label} environment")
    return {str(key): str(value) for key, value in raw_environment.items() if value is not None}


def _resolved_minio_root_errors(services: Mapping[str, object]) -> list[str]:
    errors: list[str] = []
    minio_service = services.get("minio")
    minio_image = minio_service.get("image") if isinstance(minio_service, Mapping) else None
    _require(
        minio_image == MINIO_SERVER_IMAGE,
        "resolved MinIO server image is not the approved immutable digest",
        errors,
    )
    backend_service = services.get("backend-api")
    backend_build = backend_service.get("build") if isinstance(backend_service, Mapping) else None
    backend_build_args = backend_build.get("args") if isinstance(backend_build, Mapping) else None
    _require(
        isinstance(backend_build_args, Mapping)
        and backend_build_args.get("MINIO_MC_IMAGE") == MINIO_MC_IMAGE,
        "resolved backend mc image is not the approved immutable digest",
        errors,
    )
    root_services = ("minio", "minio-bootstrap", "minio-metrics-token-init")
    environments = {
        name: _resolved_service_environment(
            services.get(name),
            f"resolved {name} service",
        )
        for name in root_services
    }
    services_with_root = {
        str(name)
        for name, service in services.items()
        if isinstance(service, Mapping)
        and (
            "MINIO_ROOT_USER" in service.get("environment", {})
            or "MINIO_ROOT_PASSWORD" in service.get("environment", {})
        )
    }
    _require(
        services_with_root == set(root_services),
        "resolved MinIO root credential escaped the three authorized services",
        errors,
    )
    pairs = {
        (
            environment.get("MINIO_ROOT_USER", "").strip(),
            environment.get("MINIO_ROOT_PASSWORD", "").strip(),
        )
        for environment in environments.values()
    }
    _require(
        len(pairs) == 1,
        "resolved MinIO root credentials disagree across server and init services",
        errors,
    )
    root_user, root_password = next(iter(pairs))
    _require(
        bool(root_user)
        and bool(root_password)
        and root_user not in {"knowledge-root", "minioadmin"}
        and root_password not in {"knowledge_root_password", "minioadmin"},
        "resolved protected MinIO root credentials are empty or use a known default",
        errors,
    )
    bootstrap = environments["minio-bootstrap"]
    initializer = environments["minio-metrics-token-init"]
    _require(
        bootstrap.get("MINIO_ENDPOINT", "").strip() == "minio:9000"
        and initializer.get("MINIO_ENDPOINT", "").strip() == "minio:9000",
        "resolved privileged MinIO endpoint is not the internal minio:9000 authority",
        errors,
    )
    data_access = bootstrap.get("MINIO_ACCESS_KEY", "").strip()
    data_secret = bootstrap.get("MINIO_SECRET_KEY", "").strip()
    _require(
        root_user != data_access and root_password != data_secret,
        "resolved MinIO root and data-plane credentials are not isolated",
        errors,
    )
    return errors


def _email_delivery_evidence_errors(
    evidence: dict[str, Any],
    *,
    now: datetime | None = None,
) -> list[str]:
    errors: list[str] = []
    timestamp = (now or datetime.now(UTC)).astimezone(UTC)
    _require(
        set(evidence) == EMAIL_RECEIPT_KEYS,
        "email delivery receipt schema mismatch",
        errors,
    )
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
    for field, expected in required_values.items():
        _require(
            evidence.get(field) == expected,
            f"email delivery receipt {field} is invalid",
            errors,
        )
    _require(
        evidence.get("publish_failure_public_statuses")
        == {
            "register": 201,
            "resend_verification": 200,
            "forgot_password": 200,
        },
        "email publisher outage public statuses do not match the generic accepted contract",
        errors,
    )
    digest_fields = (
        "registration_message_id_sha256",
        "password_reset_message_id_sha256",
        "registration_smtp_receipt_sha256",
        "password_reset_smtp_receipt_sha256",
    )
    digests = [evidence.get(field) for field in digest_fields]
    _require(
        all(
            isinstance(value, str) and SHA256_PATTERN.fullmatch(value) is not None
            for value in digests
        ),
        "email message/SMTP receipt digest is invalid",
        errors,
    )
    _require(
        len(set(str(value) for value in digests)) == len(digests),
        "email evidence reused a Message-ID or SMTP receipt digest",
        errors,
    )
    for field in ("registration_delivered_at", "password_reset_delivered_at"):
        try:
            delivered_at = _timestamp(evidence.get(field), f"email {field}")
        except RuntimeError:
            errors.append(f"email {field} is invalid")
        else:
            _require(
                _fresh(delivered_at, now=timestamp),
                f"email {field} is stale or from the future",
                errors,
            )
    return errors


def _alert_delivery_receipt_errors(
    receipt: dict[str, Any],
    *,
    now: datetime,
) -> list[str]:
    errors: list[str] = []
    _require(set(receipt) == ALERT_RECEIPT_KEYS, "Alertmanager receipt schema mismatch", errors)
    _require(
        receipt.get("alert_name") == "KnowledgeUploaderProtectedReleaseProbe",
        "Alertmanager receipt uses an unexpected alert",
        errors,
    )
    _require(
        receipt.get("receiver_type") == "webhook", "alert receipt is not webhook-bound", errors
    )
    receiver = receipt.get("receiver_name")
    _require(
        isinstance(receiver, str) and SAFE_ID_PATTERN.fullmatch(receiver) is not None,
        "Alertmanager receiver identity is invalid",
        errors,
    )
    fingerprint = receipt.get("alert_fingerprint")
    _require(
        isinstance(fingerprint, str) and FINGERPRINT_PATTERN.fullmatch(fingerprint) is not None,
        "Alertmanager fingerprint is invalid",
        errors,
    )
    for field in ("webhook_delivery_id_sha256", "webhook_receipt_sha256"):
        value = receipt.get(field)
        _require(
            isinstance(value, str) and SHA256_PATTERN.fullmatch(value) is not None,
            f"Alertmanager {field} is invalid",
            errors,
        )
    status_code = receipt.get("webhook_status_code")
    _require(
        isinstance(status_code, int)
        and not isinstance(status_code, bool)
        and 200 <= status_code < 300,
        "Alertmanager webhook did not return a successful delivery status",
        errors,
    )
    times: list[datetime] = []
    for field in ("firing_at", "delivered_at", "resolved_at"):
        try:
            parsed = _timestamp(receipt.get(field), f"alert {field}")
        except RuntimeError:
            errors.append(f"Alertmanager {field} is invalid")
        else:
            times.append(parsed)
            _require(_fresh(parsed, now=now), f"Alertmanager {field} is stale", errors)
    if len(times) == 3:
        _require(
            times[0] <= times[1] <= times[2], "Alertmanager delivery times are invalid", errors
        )
    return errors


def _non_negative_number(value: object) -> float | None:
    if not isinstance(value, int | float) or isinstance(value, bool):
        return None
    normalized = float(value)
    return normalized if math.isfinite(normalized) and normalized >= 0 else None


def _dr_release_evidence_errors(
    receipt: dict[str, Any],
    *,
    now: datetime,
    policy: Mapping[str, Any],
    policy_sha256: str,
) -> list[str]:
    errors: list[str] = []
    _require(set(receipt) == DR_RECEIPT_KEYS, "DR receipt schema mismatch", errors)
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
        value = receipt.get(field)
        _require(
            isinstance(value, str) and SHA256_PATTERN.fullmatch(value) is not None,
            f"DR {field} is invalid",
            errors,
        )
    _require(
        receipt.get("policy_sha256") == policy_sha256,
        "DR receipt policy digest does not match the repository policy",
        errors,
    )
    for field in (
        "backup_id",
        "alembic_revision",
        "recovery_pair_id",
    ):
        value = receipt.get(field)
        _require(
            isinstance(value, str) and SAFE_ID_PATTERN.fullmatch(value) is not None,
            f"DR {field} is invalid",
            errors,
        )
    parsed_times: dict[str, datetime] = {}
    for field in (
        "restore_started_at",
        "restore_completed_at",
        "last_archived_at",
        "retention_until",
    ):
        try:
            parsed_times[field] = _timestamp(receipt.get(field), f"DR {field}")
        except RuntimeError:
            errors.append(f"DR {field} is invalid")
    if {"restore_started_at", "restore_completed_at"} <= set(parsed_times):
        _require(
            parsed_times["restore_started_at"] <= parsed_times["restore_completed_at"],
            "DR restore completion precedes its start",
            errors,
        )
        _require(
            _fresh(parsed_times["restore_started_at"], now=now)
            and _fresh(parsed_times["restore_completed_at"], now=now),
            "DR restore drill is stale or from the future",
            errors,
        )
    if "last_archived_at" in parsed_times:
        _require(
            now - timedelta(hours=1)
            <= parsed_times["last_archived_at"]
            <= now + timedelta(minutes=5),
            "last archived WAL is outside the one-hour/future tolerance",
            errors,
        )
    if "retention_until" in parsed_times:
        _require(
            parsed_times["retention_until"] >= now + timedelta(days=30),
            "backup retention is shorter than 30 days",
            errors,
        )
    rpo = _non_negative_number(receipt.get("rpo_seconds"))
    rpo_target = _non_negative_number(receipt.get("rpo_target_seconds"))
    rto = _non_negative_number(receipt.get("rto_seconds"))
    rto_target = _non_negative_number(receipt.get("rto_target_seconds"))
    max_rpo = _positive_integer(policy.get("max_rpo_seconds"), "DR policy max_rpo_seconds")
    max_rto = _positive_integer(policy.get("max_rto_seconds"), "DR policy max_rto_seconds")
    _require(
        rpo is not None and rpo_target is not None and rpo_target > 0 and rpo <= rpo_target,
        "restore RPO target missed",
        errors,
    )
    _require(
        rpo is not None and rpo_target is not None and rpo_target <= max_rpo and rpo <= max_rpo,
        "restore RPO exceeds the repository policy",
        errors,
    )
    _require(
        rto is not None and rto_target is not None and rto_target > 0 and rto <= rto_target,
        "restore RTO target missed",
        errors,
    )
    _require(
        rto is not None and rto_target is not None and rto_target <= max_rto and rto <= max_rto,
        "restore RTO exceeds the repository policy",
        errors,
    )
    for field in ("minio_missing_objects", "minio_orphan_objects", "minio_mismatched_objects"):
        _require(
            receipt.get(field) == 0 and not isinstance(receipt.get(field), bool),
            f"restore has non-zero {field}",
            errors,
        )
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
    for field, expected in required_values.items():
        _require(receipt.get(field) == expected, f"DR {field} is invalid", errors)
    _require(
        receipt.get("minio_replication_enabled") is True
        or receipt.get("coordinated_snapshot") is True,
        "MinIO has neither replication nor a coordinated snapshot",
        errors,
    )
    return errors


def _require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def _alertmanager_receiver_errors(config_path: Path | bytes) -> list[str]:
    errors: list[str] = []
    loaded = yaml.safe_load(_alertmanager_config_payload(config_path).decode("utf-8"))
    config = _mapping(loaded, "Alertmanager config")
    route = _mapping(config.get("route"), "Alertmanager route")
    receivers = config.get("receivers")
    if not isinstance(receivers, list):
        return [*errors, "Alertmanager receivers must be a list"]
    receivers_by_name: dict[str, dict[str, Any]] = {}
    for raw_receiver in receivers:
        receiver = _mapping(raw_receiver, "Alertmanager receiver")
        name = receiver.get("name")
        if isinstance(name, str) and name.strip():
            receivers_by_name[name] = receiver

    route_receivers = _collect_route_receivers(route, inherited_receiver=None, errors=errors)
    for route_receiver in route_receivers:
        selected = receivers_by_name.get(route_receiver)
        if selected is None:
            errors.append(f"Alertmanager route receiver does not exist: {route_receiver}")
            continue
        normalized_name = route_receiver.strip().lower()
        _require(
            all(marker not in normalized_name for marker in ("blackhole", "null", "discard")),
            f"Alertmanager route points to a blackhole receiver: {route_receiver}",
            errors,
        )
        _require(
            _receiver_has_delivery(selected),
            f"Alertmanager route receiver has no valid delivery config: {route_receiver}",
            errors,
        )
    return errors


def _alertmanager_inline_secret_errors(config_path: Path | bytes) -> list[str]:
    loaded = yaml.safe_load(_alertmanager_config_payload(config_path).decode("utf-8"))
    errors = [
        "Alertmanager evidence config contains inline HTTP header secret material at: " + path
        for path in sensitive_http_header_paths(loaded)
    ]

    def walk(value: object, path: tuple[str, ...]) -> None:
        if isinstance(value, dict):
            for raw_key, child in value.items():
                key = str(raw_key).strip().lower()
                child_path = (*path, key)
                if (
                    key in INLINE_ALERTMANAGER_SECRET_FIELDS
                    and not key.endswith("_file")
                    and child not in (None, "")
                ):
                    errors.append(
                        "Alertmanager evidence config contains inline secret field: "
                        + ".".join(child_path)
                    )
                walk(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, (*path, str(index)))
        elif isinstance(value, str) and (
            EMAIL_VALUE_PATTERN.search(f" {value} ") is not None
            or SECRET_VALUE_PATTERN.search(f" {value}") is not None
        ):
            errors.append(
                "Alertmanager evidence config contains a sensitive value at: " + ".".join(path)
            )

    walk(loaded, ())
    return errors


def _receiver_has_delivery(receiver: dict[str, Any]) -> bool:
    for config_name, required_fields in DELIVERY_REQUIRED_FIELDS.items():
        deliveries = receiver.get(config_name)
        if not isinstance(deliveries, list):
            continue
        for delivery in deliveries:
            if not isinstance(delivery, dict):
                continue
            if any(bool(delivery.get(field)) for field in required_fields):
                return True
    return False


def _collect_route_receivers(
    route: dict[str, Any],
    *,
    inherited_receiver: str | None,
    errors: list[str],
) -> set[str]:
    raw_receiver = route.get("receiver", inherited_receiver)
    if not isinstance(raw_receiver, str) or not raw_receiver.strip():
        errors.append("Alertmanager route has no receiver and cannot inherit one")
        receiver = inherited_receiver
    else:
        receiver = raw_receiver.strip()
    selected = {receiver} if receiver is not None else set()
    child_routes = route.get("routes", [])
    if child_routes is None:
        child_routes = []
    if not isinstance(child_routes, list):
        errors.append("Alertmanager child routes must be a list")
        return selected
    for child in child_routes:
        if not isinstance(child, dict):
            errors.append("Alertmanager child route must be an object")
            continue
        selected.update(
            _collect_route_receivers(
                child,
                inherited_receiver=receiver,
                errors=errors,
            )
        )
    return selected


def _alertmanager_receipt_binding_errors(
    config_path: Path | bytes,
    receipt: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    loaded = yaml.safe_load(_alertmanager_config_payload(config_path).decode("utf-8"))
    config = _mapping(loaded, "Alertmanager config")
    route = _mapping(config.get("route"), "Alertmanager route")
    route_errors: list[str] = []
    selected = _collect_route_receivers(route, inherited_receiver=None, errors=route_errors)
    errors.extend(route_errors)
    receiver_name = receipt.get("receiver_name")
    _require(
        isinstance(receiver_name, str) and receiver_name in selected,
        "Alertmanager receipt receiver is not selected by the evidence config",
        errors,
    )
    raw_receivers = config.get("receivers")
    receivers = raw_receivers if isinstance(raw_receivers, list) else []
    matches = [
        receiver
        for receiver in receivers
        if isinstance(receiver, dict) and receiver.get("name") == receiver_name
    ]
    _require(
        len(matches) == 1,
        "Alertmanager receipt receiver config is missing or duplicated",
        errors,
    )
    if len(matches) == 1:
        webhooks = matches[0].get("webhook_configs")
        _require(
            isinstance(webhooks, list)
            and bool(webhooks)
            and all(
                isinstance(webhook, dict)
                and isinstance(webhook.get("url_file"), str)
                and bool(str(webhook["url_file"]).strip())
                and not webhook.get("url")
                for webhook in webhooks
            ),
            "Alertmanager receipt receiver is not bound to secret-file webhook delivery",
            errors,
        )
    return errors


def _validate_evidence_identity(
    evidence: dict[str, Any],
    *,
    filename: str,
    git_sha: str,
    environment: str,
    now: datetime,
    errors: list[str],
) -> None:
    _require(evidence.get("git_sha") == git_sha, f"{filename} git_sha mismatch", errors)
    _require(
        evidence.get("environment") == environment,
        f"{filename} environment mismatch",
        errors,
    )
    generated_at = _timestamp(evidence.get("generated_at"), f"{filename} generated_at")
    _require(
        _fresh(generated_at, now=now),
        f"{filename} evidence is stale or from the future",
        errors,
    )


def _compose_service(services: dict[str, Any], name: str) -> dict[str, Any]:
    value = services.get(name)
    return value if isinstance(value, dict) else {}


def _service_environment(service: dict[str, Any]) -> dict[str, Any]:
    value = service.get("environment")
    return value if isinstance(value, dict) else {}


def _service_volumes(service: dict[str, Any]) -> list[object]:
    value = service.get("volumes")
    return value if isinstance(value, list) else []


def _dependency_condition(service: dict[str, Any], dependency: str) -> object:
    dependencies = service.get("depends_on")
    if not isinstance(dependencies, dict):
        return None
    value = dependencies.get(dependency)
    return value.get("condition") if isinstance(value, dict) else None


def _active_dockerfile_instructions(source: str) -> list[str]:
    return [
        instruction
        for raw_line in source.splitlines()
        if (instruction := raw_line.strip()) and not instruction.startswith("#")
    ]


def _has_single_pinned_mc_stage(
    source: str,
    *,
    argument: str,
    alias: str,
) -> bool:
    instructions = _active_dockerfile_instructions(source)
    expected_argument = f"ARG {argument}={MINIO_MC_IMAGE}"
    expected_from = f"FROM --platform=$TARGETPLATFORM ${{{argument}}} AS {alias}"
    expected_copy = f"COPY --from={alias} /usr/bin/mc /usr/local/bin/mc"
    argument_pattern = re.compile(rf"^ARG\s+{re.escape(argument)}(?:=|\s|$)", re.IGNORECASE)
    argument_instructions = [
        instruction
        for instruction in instructions
        if argument_pattern.match(instruction) is not None
    ]
    from_instructions = [
        instruction
        for instruction in instructions
        if instruction.upper().startswith("FROM ") and f"${{{argument}}}" in instruction
    ]
    copy_instructions = [
        instruction
        for instruction in instructions
        if instruction.upper().startswith("COPY ") and f"--from={alias}" in instruction
    ]
    return (
        argument_instructions == [expected_argument]
        and from_instructions == [expected_from]
        and copy_instructions == [expected_copy]
    )


def _shell_tokens(source: str) -> list[str] | None:
    try:
        return shlex.split(source, comments=True, posix=True)
    except ValueError:
        return None


def _is_backend_build_run(source: str) -> bool:
    tokens = _shell_tokens(source)
    if tokens is None or "backend/Dockerfile" not in tokens:
        return False
    return any(
        tokens[index : index + 3] == ["docker", "buildx", "build"]
        for index in range(len(tokens) - 2)
    )


def _backend_build_uses_pinned_mc(source: str) -> bool:
    tokens = _shell_tokens(source)
    if tokens is None:
        return False
    build_starts = [
        index
        for index in range(len(tokens) - 2)
        if tokens[index : index + 3] == ["docker", "buildx", "build"]
    ]
    if len(build_starts) != 1:
        return False
    command = tokens[build_starts[0] + 3 :]
    for index, token in enumerate(command):
        if token in {";", "&&", "||", "else", "fi"}:
            command = command[:index]
            break

    dockerfiles: list[str] = []
    values: list[str] = []
    for index, token in enumerate(command):
        if token in {"-f", "--file"}:
            if index + 1 >= len(command):
                return False
            dockerfiles.append(command[index + 1])
        elif token.startswith("--file="):
            dockerfiles.append(token.removeprefix("--file="))
        elif token == "--build-arg":
            if index + 1 >= len(command):
                return False
            value = command[index + 1]
            if value.startswith("MINIO_MC_IMAGE="):
                values.append(value)
        elif token.startswith("--build-arg=MINIO_MC_IMAGE="):
            values.append(token.removeprefix("--build-arg="))

    shadowed = any(
        token.startswith("MINIO_MC_IMAGE=") and (not index or tokens[index - 1] != "--build-arg")
        for index, token in enumerate(tokens)
    )
    return (
        not shadowed
        and dockerfiles == ["backend/Dockerfile"]
        and values == [f"MINIO_MC_IMAGE={MINIO_MC_IMAGE}"]
    )


def _minio_token_validator_contract_errors(
    *,
    dockerfile_payload: bytes,
    ops_dockerfile_payload: bytes,
    workflow_payload: bytes,
    endpoint_validator_payload: bytes,
    strict_json_payload: bytes,
    shared_validator_payload: bytes,
    bootstrap_payload: bytes,
    validator_payload: bytes,
) -> list[str]:
    errors: list[str] = []
    try:
        dockerfile = dockerfile_payload.decode("utf-8")
        ops_dockerfile = ops_dockerfile_payload.decode("utf-8")
        workflow_source = workflow_payload.decode("utf-8")
        workflow = yaml.safe_load(workflow_source)
        endpoint_validator = endpoint_validator_payload.decode("utf-8")
        strict_json = strict_json_payload.decode("utf-8")
        shared_validator = shared_validator_payload.decode("utf-8")
        bootstrap = bootstrap_payload.decode("utf-8")
        validator = validator_payload.decode("utf-8")
        ast.parse(endpoint_validator, filename="backend/app/core/minio_endpoint.py")
        ast.parse(strict_json, filename="backend/app/core/strict_json.py")
        ast.parse(shared_validator, filename="backend/app/core/jwt_validation.py")
        ast.parse(bootstrap, filename="backend/scripts/minio_bootstrap.py")
        ast.parse(validator, filename="backend/scripts/minio_metrics_token_init.py")
    except (UnicodeError, SyntaxError, yaml.YAMLError):
        return ["MinIO bootstrap, image, workflow, or token validator source is invalid"]
    if not isinstance(workflow, dict):
        return ["knowledge uploader workflow is not a YAML object"]
    _require(
        _has_single_pinned_mc_stage(
            dockerfile,
            argument="MINIO_MC_IMAGE",
            alias="minio-client",
        ),
        "backend mc stage is not pinned to the approved target-platform digest",
        errors,
    )
    _require(
        _has_single_pinned_mc_stage(
            ops_dockerfile,
            argument="MC_IMAGE",
            alias="target-mc",
        ),
        "ops mc stage is not pinned to the approved target-platform digest",
        errors,
    )
    jobs = workflow.get("jobs")
    backend_build_entries: list[tuple[dict[str, object], dict[str, object], str]] = []
    if isinstance(jobs, dict):
        for job in jobs.values():
            if not isinstance(job, dict):
                continue
            steps = job.get("steps")
            if not isinstance(steps, list):
                continue
            for step in steps:
                if not isinstance(step, dict):
                    continue
                run = step.get("run")
                if isinstance(run, str) and _is_backend_build_run(run):
                    backend_build_entries.append((job, step, run))
    _require(
        bool(backend_build_entries)
        and all(
            isinstance(job.get("env"), dict)
            and job["env"].get("MINIO_MC_IMAGE") == MINIO_MC_IMAGE
            and (not isinstance(step.get("env"), dict) or "MINIO_MC_IMAGE" not in step["env"])
            and _backend_build_uses_pinned_mc(run)
            for job, step, run in backend_build_entries
        ),
        "CI backend build does not forward one unshadowed approved immutable mc digest",
        errors,
    )
    strict_json_requirements = (
        "json.loads(",
        "parse_constant=_reject_constant",
        "object_pairs_hook=_reject_duplicate_pairs",
        "math.isfinite(value)",
        "if not isinstance(value, dict)",
    )
    shared_requirements = (
        "strict_json_object(_decode_base64url(segment))",
        "_has_nonempty_identity_claim(claims)",
        "_STRING_IDENTITY_CLAIMS",
        'claims.get("exp")',
        'for claim in ("nbf", "iat")',
        "_TOKEN_PATTERN.fullmatch(token)",
        "math.isfinite(numeric)",
        "signature = _decode_base64url(signature_segment)",
    )
    endpoint_requirements = (
        "urlsplit(",
        "parsed.username is not None",
        "parsed.password is not None",
        "parsed.path",
        "parsed.query",
        "parsed.fragment",
        "allowed_hosts",
        "allowed_ports",
    )
    bootstrap_requirements = (
        "strict_json_object(line)",
        "strict_minio_base_url(",
        'allowed_hosts={"minio"}',
        "allowed_ports={9000}",
        "ACCESS_KEY_PATTERN.fullmatch(root_user)",
        "ACCESS_KEY_PATTERN.fullmatch(access_key)",
        "SECRET_KEY_PATTERN.fullmatch(root_password)",
        "SECRET_KEY_PATTERN.fullmatch(secret_key)",
        "root_user in DEFAULT_ROOT_USERS",
        "root_password in DEFAULT_ROOT_PASSWORDS",
        "access_key in DEFAULT_DATA_USERS",
        "secret_key in DEFAULT_DATA_SECRETS",
        "_verify_exact_bucket_policy(",
        'set(policy) != {"Statement", "Version"}',
        'set(statement) != {"Action", "Effect", "Resource"}',
        'statement.get("Effect") != "Allow"',
        "len(statements) != 2",
        "len(value) != len(set(value))",
        "actual != expected",
        "allow_empty: bool = False",
        "if allow_empty:",
        "allow_empty=True",
        '["admin", "group", "list", "bootstrap"]',
        '["admin", "group", "remove", "bootstrap", group, access_key]',
        '["admin", "user", "remove", "bootstrap", access_key]',
        '"entities", "bootstrap", "--policy", policy',
        "if groups or users - {access_key}:",
        '["admin", "policy", "remove", "bootstrap", POLICY_NAME]',
        '"create", "bootstrap", POLICY_NAME, str(policy_path)',
        '"attach", "bootstrap", POLICY_NAME, "--user", access_key',
        "policies != {POLICY_NAME}",
        "users != {access_key} or groups",
        "tempfile.mkdtemp(",
        "tempfile.mkstemp(",
        "os.fchmod(descriptor, 0o600)",
        "os.fsync(stream.fileno())",
        "policy_path.unlink(missing_ok=True)",
        "verified_path.unlink(missing_ok=True)",
        "shutil.rmtree(working_directory)",
        "signal.SIGTERM",
        "subprocess.Popen(",
        "stdin=subprocess.DEVNULL",
        "tempfile.TemporaryFile()",
        "stdout=stdout_stream",
        "stderr=stderr_stream",
        "_bounded_output_sizes(",
        'start_new_session=os.name == "posix"',
        "_cleanup_communicate(",
        "except BootstrapInterrupted:",
        "raise BootstrapInterrupted from cleanup_error",
        "except BaseException as error:",
    )
    validator_requirements = (
        "is_semantic_time_bound_jwt(token)",
        "strict_minio_base_url(",
        'allowed_hosts={"minio"}',
        "allowed_ports={9000}",
        "class _NoRedirect(HTTPRedirectHandler)",
        "ProxyHandler({})",
        "response.geturl() != metrics_url",
        "MINIO_SECURE",
        "MINIO_CA_CERT_FILE",
        "ssl.create_default_context(cafile=str(_validated_ca_file()))",
        'headers={"Authorization": f"Bearer {token}"}',
        "if response.status != 200",
        "tempfile.mkstemp(",
        'prefix=".token.tmp."',
        "temporary_path.replace(TOKEN_PATH)",
        "os.fchown(stream.fileno(), 65534, 65534)",
        "os.fchmod(stream.fileno(), 0o440)",
        "os.fsync(stream.fileno())",
        "os.fsync(directory_descriptor)",
        "signal.SIGTERM",
        'for name in ("SIGHUP", "SIGINT", "SIGTERM")',
        "MC_CONFIG_DIR",
        "COMMAND_TIMEOUT_SECONDS = 30.0",
        "subprocess.Popen(",
        "stdin=subprocess.DEVNULL",
        "tempfile.TemporaryFile()",
        "stdout=stdout_stream",
        "stderr=stderr_stream",
        "_bounded_output_sizes(",
        'start_new_session=os.name == "posix"',
        "_cleanup_communicate(",
        "except TokenInitializationInterrupted:",
        "raise TokenInitializationInterrupted from cleanup_error",
        "except BaseException as error:",
        "tempfile.mkdtemp(",
        "shutil.rmtree(working_directory)",
        "_open_token_directory()",
        "os.fchmod(descriptor, 0o755)",
    )
    sources = (endpoint_validator, strict_json, shared_validator, bootstrap, validator)
    _require(
        all(fragment in endpoint_validator for fragment in endpoint_requirements)
        and all(fragment in strict_json for fragment in strict_json_requirements)
        and all(fragment in shared_validator for fragment in shared_requirements)
        and all(fragment in bootstrap for fragment in bootstrap_requirements)
        and all(fragment in validator for fragment in validator_requirements)
        and all("print(" not in source for source in sources)
        and all("import logging" not in source for source in sources)
        and "sys.stderr" not in bootstrap
        and "sys.stderr" not in validator
        and "--insecure" not in bootstrap,
        "MinIO bootstrap or token validator lacks strict identity, JSON, CA, or atomic checks",
        errors,
    )
    return errors


def _minio_compose_contract_errors(
    compose: dict[str, Any],
    observability: dict[str, Any],
    protected: dict[str, Any],
    *,
    compose_payload: bytes,
    protected_payload: bytes,
) -> list[str]:
    errors: list[str] = []
    services_value = compose.get("services")
    services = services_value if isinstance(services_value, dict) else {}
    app_environment_value = compose.get("x-app-environment")
    app_environment = app_environment_value if isinstance(app_environment_value, dict) else {}
    minio = _compose_service(services, "minio")
    minio_environment = _service_environment(minio)
    bootstrap = _compose_service(services, "minio-bootstrap")
    bootstrap_environment = _service_environment(bootstrap)
    initializer = _compose_service(services, "minio-metrics-token-init")
    initializer_environment = _service_environment(initializer)
    operational = _compose_service(services, "operational-metrics")
    operational_environment = _service_environment(operational)
    backend_build_value = compose.get("x-backend-build")
    backend_build = backend_build_value if isinstance(backend_build_value, dict) else {}
    backend_build_args_value = backend_build.get("args")
    backend_build_args = (
        backend_build_args_value if isinstance(backend_build_args_value, dict) else {}
    )

    _require(
        minio.get("image") == f"${{MINIO_SERVER_IMAGE:-{MINIO_SERVER_IMAGE}}}",
        "MinIO server image default is not the approved immutable digest",
        errors,
    )
    _require(
        backend_build_args.get("MINIO_MC_IMAGE") == f"${{MINIO_MC_IMAGE:-{MINIO_MC_IMAGE}}}",
        "backend mc image default is not the approved immutable digest",
        errors,
    )

    _require(
        minio_environment
        == {
            "MINIO_ROOT_USER": "${MINIO_ROOT_USER:-knowledge-root}",
            "MINIO_ROOT_PASSWORD": "${MINIO_ROOT_PASSWORD:-knowledge_root_password}",
            "MINIO_PROMETHEUS_AUTH_TYPE": "jwt",
        },
        "MinIO server does not use an isolated root credential and JWT metrics",
        errors,
    )
    _require(
        app_environment.get("MINIO_ACCESS_KEY") == "${MINIO_ACCESS_KEY:-knowledge}"
        and app_environment.get("MINIO_SECRET_KEY") == "${MINIO_SECRET_KEY:-knowledge_password}"
        and "MINIO_ROOT_USER" not in app_environment
        and "MINIO_ROOT_PASSWORD" not in app_environment
        and "MINIO_METRICS_BEARER_TOKEN_FILE" not in app_environment,
        "application environment does not isolate MinIO data-plane credentials",
        errors,
    )

    _require(
        bootstrap.get("image") == "${BACKEND_IMAGE:-knowledge-uploader-backend:dev}"
        and bootstrap.get("entrypoint") == ["python", "-m", "scripts.minio_bootstrap"]
        and bootstrap.get("restart") == "no"
        and bootstrap.get("command") is None,
        "MinIO data-plane bootstrap image or lifecycle is unsafe",
        errors,
    )
    _require(
        bootstrap_environment
        == {
            "MINIO_ENDPOINT": "${MINIO_ENDPOINT:-minio:9000}",
            "MINIO_ROOT_USER": "${MINIO_ROOT_USER:-knowledge-root}",
            "MINIO_ROOT_PASSWORD": "${MINIO_ROOT_PASSWORD:-knowledge_root_password}",
            "MINIO_ACCESS_KEY": "${MINIO_ACCESS_KEY:-knowledge}",
            "MINIO_SECRET_KEY": "${MINIO_SECRET_KEY:-knowledge_password}",
            "MINIO_BUCKET": "${MINIO_BUCKET:-knowledge-files}",
            "MINIO_SECURE": "${MINIO_SECURE:-false}",
            "MINIO_CA_CERT_FILE": "${MINIO_CA_CERT_FILE:-}",
        },
        "MinIO data-plane bootstrap credential and CA inputs are incomplete",
        errors,
    )
    _require(
        _dependency_condition(bootstrap, "minio") == "service_healthy",
        "MinIO data-plane bootstrap does not wait for healthy MinIO",
        errors,
    )

    _require(
        initializer.get("image") == "${BACKEND_IMAGE:-knowledge-uploader-backend:dev}"
        and initializer.get("entrypoint") == ["python", "-m", "scripts.minio_metrics_token_init"]
        and initializer.get("restart") == "no"
        and initializer.get("command") is None,
        "MinIO metrics initializer image or lifecycle is unsafe",
        errors,
    )
    _require(
        initializer_environment
        == {
            "MINIO_ENDPOINT": "${MINIO_ENDPOINT:-minio:9000}",
            "MINIO_ROOT_USER": "${MINIO_ROOT_USER:-knowledge-root}",
            "MINIO_ROOT_PASSWORD": "${MINIO_ROOT_PASSWORD:-knowledge_root_password}",
            "MINIO_SECURE": "${MINIO_SECURE:-false}",
            "MINIO_CA_CERT_FILE": "${MINIO_CA_CERT_FILE:-}",
        },
        "MinIO metrics initializer is not confined to short-lived root credentials",
        errors,
    )
    _require(
        "MINIO_METRICS_TOKEN_ROTATE" not in initializer_environment
        and "MINIO_ACCESS_KEY" not in initializer_environment
        and "MINIO_SECRET_KEY" not in initializer_environment,
        "MinIO metrics initializer contains a dead rotation flag or data-plane credential",
        errors,
    )
    _require(
        _service_volumes(initializer) == [f"minio-metrics-auth:{MINIO_METRICS_TOKEN_DIR}"],
        "MinIO metrics initializer token volume is missing or read-only",
        errors,
    )
    _require(
        _dependency_condition(initializer, "minio") == "service_healthy"
        and _dependency_condition(initializer, "minio-bootstrap")
        == "service_completed_successfully",
        "MinIO metrics initializer dependency chain is incomplete",
        errors,
    )

    _require(
        operational_environment.get("MINIO_ACCESS_KEY") == MINIO_METRICS_SENTINEL
        and operational_environment.get("MINIO_SECRET_KEY") == MINIO_METRICS_SENTINEL
        and operational_environment.get("MINIO_METRICS_BEARER_TOKEN_FILE")
        == MINIO_METRICS_TOKEN_FILE
        and "MINIO_ROOT_USER" not in operational_environment
        and "MINIO_ROOT_PASSWORD" not in operational_environment,
        "operational metrics service retains a MinIO root or data-plane credential",
        errors,
    )
    _require(
        _service_volumes(operational) == [f"minio-metrics-auth:{MINIO_METRICS_TOKEN_DIR}:ro"]
        and _dependency_condition(operational, "minio-metrics-token-init")
        == "service_completed_successfully",
        "operational metrics bearer volume or initializer dependency is unsafe",
        errors,
    )

    observability_services_value = observability.get("services")
    observability_services = (
        observability_services_value if isinstance(observability_services_value, dict) else {}
    )
    prometheus = _compose_service(observability_services, "prometheus")
    _require(
        f"minio-metrics-auth:{MINIO_METRICS_TOKEN_DIR}:ro" in _service_volumes(prometheus)
        and _dependency_condition(prometheus, "minio-metrics-token-init")
        == "service_completed_successfully",
        "Prometheus bearer volume or initializer dependency is unsafe",
        errors,
    )

    allowed_root_services = {"minio", "minio-bootstrap", "minio-metrics-token-init"}
    services_with_root = {
        name
        for name, service in services.items()
        if isinstance(service, dict)
        and (
            "MINIO_ROOT_USER" in _service_environment(service)
            or "MINIO_ROOT_PASSWORD" in _service_environment(service)
        )
    }
    _require(
        services_with_root == allowed_root_services,
        "MinIO root credential escaped the server and short-lived init services",
        errors,
    )
    services_with_bearer_file = {
        name
        for name, service in services.items()
        if isinstance(service, dict)
        and "MINIO_METRICS_BEARER_TOKEN_FILE" in _service_environment(service)
    }
    _require(
        services_with_bearer_file == {"operational-metrics"},
        "MinIO metrics bearer path escaped the collector service",
        errors,
    )

    protected_services_value = protected.get("services")
    protected_services = (
        protected_services_value if isinstance(protected_services_value, dict) else {}
    )
    protected_minio = _compose_service(protected_services, "minio")
    protected_root_environment = {
        "MINIO_ROOT_USER": "${MINIO_ROOT_USER:?MINIO_ROOT_USER is required}",
        "MINIO_ROOT_PASSWORD": ("${MINIO_ROOT_PASSWORD:?MINIO_ROOT_PASSWORD is required}"),
    }
    _require(
        _service_environment(protected_minio) == protected_root_environment,
        "protected MinIO root credentials are not required explicitly",
        errors,
    )
    protected_minio_volumes = _service_volumes(protected_minio)
    expected_tls_mounts = {
        "${MINIO_TLS_DIR:?MINIO_TLS_DIR is required}/public.crt:"
        "/root/.minio/certs/public.crt:ro",
        "${MINIO_TLS_DIR:?MINIO_TLS_DIR is required}/private.key:"
        "/root/.minio/certs/private.key:ro",
        "${MINIO_TLS_DIR:?MINIO_TLS_DIR is required}/ca.crt:"
        "/root/.minio/certs/CAs/protected-ca.crt:ro",
    }
    _require(
        set(protected_minio_volumes) == expected_tls_mounts,
        "protected MinIO server certificate, key, and CA mounts are incomplete",
        errors,
    )
    healthcheck = protected_minio.get("healthcheck")
    health_test = healthcheck.get("test") if isinstance(healthcheck, dict) else None
    serialized_health = json.dumps(health_test, sort_keys=True)
    _require(
        "https://minio:9000/minio/health/cluster" in serialized_health
        and "--cacert /root/.minio/certs/CAs/protected-ca.crt" in serialized_health
        and "127.0.0.1:9000" not in serialized_health
        and " -k" not in serialized_health
        and "--insecure" not in serialized_health,
        "protected MinIO healthcheck does not verify the DNS:minio SAN with the public CA",
        errors,
    )

    for init_name in ("minio-bootstrap", "minio-metrics-token-init"):
        protected_init = _compose_service(protected_services, init_name)
        _require(
            _service_environment(protected_init)
            == {
                **protected_root_environment,
                "MINIO_ENDPOINT": "minio:9000",
                "MINIO_SECURE": "true",
                "MINIO_CA_CERT_FILE": "/run/secrets/minio-ca/ca.crt",
                "SSL_CERT_FILE": "/run/secrets/minio-ca/ca.crt",
            }
            and _service_volumes(protected_init)
            == [
                "${MINIO_TLS_DIR:?MINIO_TLS_DIR is required}/ca.crt:"
                "/run/secrets/minio-ca/ca.crt:ro"
            ],
            f"protected {init_name} does not verify the MinIO CA",
            errors,
        )

    expected_client_volume = (
        "${MINIO_TLS_DIR:?MINIO_TLS_DIR is required}/ca.crt:" f"{MINIO_CA_CONTAINER_FILE}:ro"
    )
    for service_name in PROTECTED_MINIO_CLIENT_SERVICES:
        service = _compose_service(protected_services, service_name)
        _require(
            _service_environment(service)
            == {
                "MINIO_SECURE": "true",
                "MINIO_CA_CERT_FILE": MINIO_CA_CONTAINER_FILE,
            }
            and _service_volumes(service) == [expected_client_volume],
            f"protected {service_name} MinIO CA contract is incomplete",
            errors,
        )

    protected_prometheus = _compose_service(protected_services, "prometheus")
    _require(
        set(_service_volumes(protected_prometheus))
        == {
            "${PROMETHEUS_CONFIG_FILE:?PROMETHEUS_CONFIG_FILE is required}:"
            "/etc/prometheus/prometheus.yml:ro",
            "${MINIO_TLS_DIR:?MINIO_TLS_DIR is required}/ca.crt:" "/etc/prometheus/tls/ca.crt:ro",
        },
        "protected Prometheus config and MinIO CA mounts are incomplete",
        errors,
    )

    serialized_contract = b"\n".join((compose_payload, protected_payload)).lower()
    _require(
        b"minio_prometheus_auth_type: public" not in serialized_contract
        and b"minio_metrics_bearer_token:" not in serialized_contract
        and b"minio_metrics_token_rotate" not in serialized_contract
        and b"prometheus_tls_dir" not in serialized_contract
        and b".rotation.lock" not in serialized_contract
        and b"--insecure" not in protected_payload.lower(),
        "MinIO metrics auth or TLS contract contains a downgrade",
        errors,
    )
    return errors


def check_contract(
    contract_payloads: Mapping[str, bytes] | None = None,
) -> list[str]:
    contracts = _contract_payload_mapping(
        snapshot_contract_payloads() if contract_payloads is None else contract_payloads
    )
    _load_dr_release_policy(contracts[DR_RELEASE_POLICY_CONTRACT_PATH])
    errors: list[str] = []
    compose = _mapping(
        yaml.safe_load(contracts["docker-compose.yml"].decode("utf-8")),
        "Compose config",
    )
    observability = _mapping(
        yaml.safe_load(contracts["docker-compose.observability.yml"].decode("utf-8")),
        "observability Compose config",
    )
    protected_observability = _mapping(
        yaml.safe_load(contracts["docker-compose.observability.protected.yml"].decode("utf-8")),
        "protected observability Compose config",
    )
    prometheus = _mapping(
        yaml.safe_load(contracts["ops/observability/prometheus.yml"].decode("utf-8")),
        "Prometheus config",
    )
    protected_prometheus = _mapping(
        yaml.safe_load(contracts["ops/observability/prometheus.protected.yml"].decode("utf-8")),
        "protected Prometheus config",
    )
    compose_services = _mapping(compose.get("services"), "Compose services")
    backend_api = _mapping(compose_services.get("backend-api"), "backend-api service")
    backend_build = _mapping(backend_api.get("build"), "backend-api build")
    backend_ports = backend_api.get("ports")
    observability_services = _mapping(
        observability.get("services"),
        "observability services",
    )
    prometheus_service = _mapping(
        observability_services.get("prometheus"),
        "Prometheus service",
    )
    protected_prometheus_service = _mapping(
        _mapping(
            protected_observability.get("services"),
            "protected observability services",
        ).get("prometheus"),
        "protected Prometheus service",
    )
    prometheus_ports = prometheus_service.get("ports")
    _require(
        isinstance(backend_ports, list)
        and "${BACKEND_API_HOST:-127.0.0.1}:${BACKEND_API_PORT:-18000}:8000" in backend_ports,
        "backend API default host binding is not loopback",
        errors,
    )
    _require(
        backend_build.get("target") == "${BACKEND_BUILD_TARGET:-runtime}",
        "backend image does not default to the runtime target",
        errors,
    )
    _require(
        isinstance(prometheus_ports, list)
        and "127.0.0.1:${PROMETHEUS_HOST_PORT:-19090}:9090" in prometheus_ports,
        "Prometheus host binding is not loopback",
        errors,
    )
    _require(
        "alertmanager:9093" in _prometheus_alertmanager_targets(prometheus),
        "Prometheus has no Alertmanager target",
        errors,
    )
    protected_volumes = protected_prometheus_service.get("volumes")
    _require(
        isinstance(protected_volumes, list)
        and (
            "${PROMETHEUS_CONFIG_FILE:?PROMETHEUS_CONFIG_FILE is required}"
            ":/etc/prometheus/prometheus.yml:ro"
        )
        in protected_volumes
        and ("${MINIO_TLS_DIR:?MINIO_TLS_DIR is required}/ca.crt" ":/etc/prometheus/tls/ca.crt:ro")
        in protected_volumes,
        "protected Prometheus config and MinIO CA mounts are not required read-only",
        errors,
    )
    errors.extend(_protected_minio_scrape_errors(protected_prometheus))
    errors.extend(
        _minio_token_validator_contract_errors(
            dockerfile_payload=contracts["backend/Dockerfile"],
            ops_dockerfile_payload=contracts["ops/Dockerfile"],
            workflow_payload=contracts[".github/workflows/knowledge-uploader.yml"],
            endpoint_validator_payload=contracts["backend/app/core/minio_endpoint.py"],
            strict_json_payload=contracts["backend/app/core/strict_json.py"],
            shared_validator_payload=contracts["backend/app/core/jwt_validation.py"],
            bootstrap_payload=contracts["backend/scripts/minio_bootstrap.py"],
            validator_payload=contracts["backend/scripts/minio_metrics_token_init.py"],
        )
    )
    errors.extend(
        _minio_compose_contract_errors(
            compose,
            observability,
            protected_observability,
            compose_payload=contracts["docker-compose.yml"],
            protected_payload=contracts["docker-compose.observability.protected.yml"],
        )
    )
    errors.extend(
        _topology_contract_errors(
            contracts["backend/app/workers/rabbitmq_topology.py"],
            filename="backend/app/workers/rabbitmq_topology.py",
        )
    )
    return errors


def _prometheus_alertmanager_targets(config: dict[str, Any]) -> set[str]:
    alerting = config.get("alerting")
    if not isinstance(alerting, dict):
        return set()
    managers = alerting.get("alertmanagers")
    if not isinstance(managers, list):
        return set()
    targets: set[str] = set()
    for manager in managers:
        if not isinstance(manager, dict):
            continue
        static_configs = manager.get("static_configs")
        if not isinstance(static_configs, list):
            continue
        for static_config in static_configs:
            if not isinstance(static_config, dict):
                continue
            raw_targets = static_config.get("targets")
            if isinstance(raw_targets, list):
                targets.update(target for target in raw_targets if isinstance(target, str))
    return targets


def _protected_minio_scrape_errors(config: dict[str, Any]) -> list[str]:
    scrape_configs = config.get("scrape_configs")
    jobs = (
        {str(item.get("job_name")): item for item in scrape_configs if isinstance(item, dict)}
        if isinstance(scrape_configs, list)
        else {}
    )
    minio = jobs.get("minio")
    errors: list[str] = []
    _require(isinstance(minio, dict), "protected Prometheus MinIO job is missing", errors)
    if not isinstance(minio, dict):
        return errors
    tls = minio.get("tls_config")
    authorization = minio.get("authorization")
    _require(minio.get("scheme") == "https", "protected MinIO scrape is not HTTPS", errors)
    _require(
        authorization
        == {
            "type": "Bearer",
            "credentials_file": "/run/secrets/minio-metrics/token",
        },
        "protected MinIO scrape does not use the mounted bearer credential file",
        errors,
    )
    _require(
        isinstance(tls, dict)
        and tls.get("ca_file") == "/etc/prometheus/tls/ca.crt"
        and tls.get("server_name") == "minio"
        and tls.get("insecure_skip_verify") is False,
        "protected MinIO scrape does not require the mounted CA and hostname verification",
        errors,
    )
    static_configs = minio.get("static_configs")
    targets = (
        {
            target
            for item in static_configs
            if isinstance(item, dict) and isinstance(item.get("targets"), list)
            for target in item["targets"]
            if isinstance(target, str)
        }
        if isinstance(static_configs, list)
        else set()
    )
    _require(targets == {"minio:9000"}, "protected MinIO scrape target is invalid", errors)
    return errors


def _topology_contract_errors(payload: bytes, *, filename: str) -> list[str]:
    tree = ast.parse(payload.decode("utf-8"), filename=filename)
    expected_queues = {
        "document_queue",
        "ai_queue",
        "ragflow_queue",
        "notification_queue",
    }
    declared_queues: set[str] = set()
    has_dead_letter_exchange = False
    has_dead_letter_routing = False
    has_dlq_suffix = False
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "TASK_QUEUE_NAMES"
            and isinstance(node.value, ast.Tuple)
        ):
            declared_queues = {
                element.value
                for element in node.value.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            }
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "TASK_QUEUE_NAMES":
                    if isinstance(node.value, ast.Tuple):
                        declared_queues = {
                            element.value
                            for element in node.value.elts
                            if isinstance(element, ast.Constant) and isinstance(element.value, str)
                        }
        if isinstance(node, ast.Dict):
            keys = {
                key.value
                for key in node.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
            has_dead_letter_exchange |= "x-dead-letter-exchange" in keys
            has_dead_letter_routing |= "x-dead-letter-routing-key" in keys
        if isinstance(node, ast.JoinedStr):
            has_dlq_suffix |= any(
                isinstance(value, ast.Constant)
                and isinstance(value.value, str)
                and ".dlq" in value.value
                for value in node.values
            )
    errors: list[str] = []
    _require(declared_queues == expected_queues, "RabbitMQ task queue set is incomplete", errors)
    _require(
        has_dead_letter_exchange and has_dead_letter_routing,
        "RabbitMQ task queues have no dead-letter exchange/routing contract",
        errors,
    )
    _require(has_dlq_suffix, "RabbitMQ dead-letter queues are not declared", errors)
    return errors


def check_evidence(
    *,
    evidence_root: Path,
    alertmanager_config: Path,
    backend_api_host: str,
    git_sha: str,
    environment: str,
    evidence_payloads: dict[str, bytes] | None = None,
    contract_payloads: Mapping[str, bytes] | None = None,
    now: datetime | None = None,
) -> list[str]:
    evidence_filenames = RELEASE_GATE_EVIDENCE_FILENAMES
    required_payloads = REQUIRED_RELEASE_GATE_EVIDENCE
    timestamp = (now or datetime.now(UTC)).astimezone(UTC)
    contracts = _contract_payload_mapping(
        snapshot_contract_payloads() if contract_payloads is None else contract_payloads
    )
    errors = check_contract(contracts)
    _require(
        _is_release_git_sha(git_sha),
        "protected release requires a full 40- or 64-character git SHA",
        errors,
    )
    if evidence_payloads is None:
        _require(
            backend_api_host in {"127.0.0.1", "::1", "localhost"},
            "BACKEND_API_HOST must be loopback in a protected environment",
            errors,
        )
        resolved_services = _resolved_compose_services()
        resolved_backend = _mapping(
            resolved_services.get("backend-api"),
            "resolved backend-api service",
        )
        errors.extend(_resolved_minio_root_errors(resolved_services))
        resolved_backend_hosts = _backend_api_hosts(resolved_backend)
        _require(
            bool(resolved_backend_hosts)
            and resolved_backend_hosts <= {"127.0.0.1", "::1", "localhost"},
            "resolved backend API port is not bound exclusively to loopback",
            errors,
        )
        _require(
            backend_api_host in resolved_backend_hosts,
            "BACKEND_API_HOST does not match the resolved Compose binding",
            errors,
        )
        backend_environment = _backend_api_environment(resolved_backend)
        _require(
            bool(backend_environment.get("SMTP_HOST", "").strip()),
            "SMTP_HOST is empty in the resolved protected Compose service",
            errors,
        )
        _require(
            bool(
                backend_environment.get("SMTP_FROM", "").strip()
                or backend_environment.get("SMTP_USER", "").strip()
            ),
            "SMTP_FROM and SMTP_USER are both empty in the resolved protected Compose service",
            errors,
        )
        _require(
            backend_environment.get("REQUIRE_EMAIL_VERIFICATION", "").strip().lower()
            in {"1", "true", "yes", "on"},
            "REQUIRE_EMAIL_VERIFICATION is not enabled in protected Compose",
            errors,
        )
        payloads = _snapshot_exact_evidence_directory(
            evidence_root,
            required_payloads,
        )
        evidence_root = evidence_root.resolve()
        expected_config_path = evidence_root / "alertmanager.yml"
        config_path = alertmanager_config.resolve()
        _require(
            not alertmanager_config.is_symlink() and config_path == expected_config_path,
            "Alertmanager config must be the safe copy inside the evidence bundle",
            errors,
        )
        _require(
            config_path.name != "alertmanager.example.yml",
            "example Alertmanager blackhole config is forbidden",
            errors,
        )
    else:
        payloads = dict(evidence_payloads)
    missing_payloads = sorted(required_payloads - set(payloads))
    extra_payloads = sorted(set(payloads) - required_payloads)
    if missing_payloads or extra_payloads:
        raise RuntimeError(
            "release evidence payload inventory mismatch: "
            f"missing={missing_payloads}, extra={extra_payloads}"
        )
    reject_semantic_jwts(payloads)
    config_payload = payloads["alertmanager.yml"]
    dr_policy_payload = contracts[DR_RELEASE_POLICY_CONTRACT_PATH]
    dr_policy = _load_dr_release_policy(dr_policy_payload)
    _require(
        payloads[DR_RELEASE_POLICY_EVIDENCE] == dr_policy_payload,
        "DR policy evidence does not match the repository policy bytes",
        errors,
    )
    errors.extend(_alertmanager_receiver_errors(config_payload))
    errors.extend(_alertmanager_inline_secret_errors(config_payload))

    trust = _parse_evidence_payload(
        payloads["release-workflow-trust.json"],
        "release-workflow-trust.json",
    )
    collector_run_id, collector_run_attempt = _external_collector_identity(
        trust,
        git_sha=git_sha,
        now=timestamp,
    )
    evidence_by_name = {
        filename: _parse_evidence_payload(payloads[filename], filename)
        for filename in evidence_filenames
    }
    for filename, evidence in evidence_by_name.items():
        _validate_evidence_identity(
            evidence,
            filename=filename,
            git_sha=git_sha,
            environment=environment,
            now=timestamp,
            errors=errors,
        )

    external_names = (
        "alertmanager-notification.json",
        "dr-release.json",
        "email-delivery.json",
        "promtool.json",
    )
    external_receipts: dict[str, dict[str, Any]] = {}
    source_runs: set[tuple[str, int]] = set()
    for filename in external_names:
        run_id, run_attempt, receipt = _validate_external_projection(
            evidence_by_name[filename],
            filename=filename,
            git_sha=git_sha,
            environment=environment,
            collector_run_id=collector_run_id,
            collector_run_attempt=collector_run_attempt,
            now=timestamp,
        )
        source_runs.add((run_id, run_attempt))
        external_receipts[filename] = receipt
    _require(
        len(source_runs) == len(external_names),
        "external evidence source runs overlap",
        errors,
    )

    alert_receipt = external_receipts["alertmanager-notification.json"]
    errors.extend(_alert_delivery_receipt_errors(alert_receipt, now=timestamp))
    errors.extend(_alertmanager_receipt_binding_errors(config_payload, alert_receipt))
    errors.extend(
        _dr_release_evidence_errors(
            external_receipts["dr-release.json"],
            now=timestamp,
            policy=dr_policy,
            policy_sha256=_sha256_bytes(dr_policy_payload),
        )
    )

    replay = evidence_by_name["rabbitmq-dlq-replay.json"]
    _require(replay.get("status") == "passed", "RabbitMQ safe replay test did not pass", errors)
    success = _mapping(replay.get("success"), "RabbitMQ success probe")
    intermediate = _mapping(
        replay.get("intermediate_retry"),
        "RabbitMQ intermediate retry probe",
    )
    exhausted = _mapping(replay.get("exhausted"), "RabbitMQ exhausted probe")
    replayed = _mapping(replay.get("replay"), "RabbitMQ replay stage")
    resolved = _mapping(replay.get("resolved"), "RabbitMQ resolved stage")
    probe_run_id = replay.get("probe_run_id")
    _require(_is_uuid(probe_run_id), "RabbitMQ probe run id is invalid", errors)
    for stage_name, stage in (
        ("success", success),
        ("intermediate_retry", intermediate),
        ("exhausted", exhausted),
    ):
        _require(_is_uuid(stage.get("task_id")), f"{stage_name} task id is invalid", errors)
        _require(
            stage.get("correlation_id") == stage.get("task_id"),
            f"{stage_name} correlation id does not match task id",
            errors,
        )
        _require(
            stage.get("probe_run_id") == probe_run_id,
            f"{stage_name} does not belong to the same RabbitMQ probe run",
            errors,
        )
        _require(
            isinstance(stage.get("task_name"), str) and bool(str(stage["task_name"]).strip()),
            f"{stage_name} task name is missing",
            errors,
        )
    task_ids = {
        success.get("task_id"),
        intermediate.get("task_id"),
        exhausted.get("task_id"),
    }
    _require(len(task_ids) == 3, "RabbitMQ probe stage task IDs are not unique", errors)
    _require(success.get("result") == "passed", "RabbitMQ success probe failed", errors)
    _require(
        success.get("dlq_count_after") == 0,
        "successful RabbitMQ task entered a DLQ",
        errors,
    )
    _require(
        intermediate.get("result") == "passed"
        and isinstance(intermediate.get("retries_observed"), int)
        and not isinstance(intermediate.get("retries_observed"), bool)
        and int(intermediate["retries_observed"]) >= 1,
        "RabbitMQ intermediate retry was not observed",
        errors,
    )
    _require(
        intermediate.get("dlq_count_during_retry") == 0,
        "RabbitMQ intermediate retry entered a DLQ",
        errors,
    )
    errors.extend(_rabbitmq_exhaustion_errors(exhausted))
    _require(
        replayed.get("task_name") in SAFE_REPLAY_TASKS,
        "RabbitMQ replay task is not whitelisted",
        errors,
    )
    _require(
        replayed.get("probe_run_id") == probe_run_id
        and replayed.get("task_name") == exhausted.get("task_name"),
        "RabbitMQ replay task/run identity does not match exhaustion",
        errors,
    )
    _require(
        replayed.get("original_task_id") == exhausted.get("task_id")
        and replayed.get("original_correlation_id") == exhausted.get("correlation_id"),
        "RabbitMQ replay is not bound to the exhausted task",
        errors,
    )
    _require(_is_uuid(replayed.get("replay_task_id")), "replay task id is invalid", errors)
    _require(
        replayed.get("replay_correlation_id") == replayed.get("replay_task_id"),
        "replay correlation id does not match replay task id",
        errors,
    )
    _require(
        replayed.get("raw_payload_copied") is False,
        "RabbitMQ replay copied raw payload",
        errors,
    )
    _require(
        replayed.get("persistent_message") is True,
        "RabbitMQ clean-room replay was not published persistently",
        errors,
    )
    _require(
        replayed.get("replay_policy") == "clean_room_allowlist_only",
        "RabbitMQ replay policy is not the reviewed clean-room allowlist",
        errors,
    )
    _require(_is_uuid(replayed.get("audit_log_id")), "RabbitMQ replay audit id missing", errors)
    _require(replayed.get("result") == "queued", "RabbitMQ replay was not queued", errors)
    _require(
        replayed.get("replay_task_id") not in task_ids,
        "RabbitMQ replay task id reused a probe stage id",
        errors,
    )
    errors.extend(
        _rabbitmq_replay_binding_errors(
            exhausted=exhausted,
            replayed=replayed,
            resolved=resolved,
        )
    )
    _require(
        resolved.get("original_task_id") == exhausted.get("task_id")
        and resolved.get("replay_task_id") == replayed.get("replay_task_id")
        and resolved.get("replay_correlation_id") == replayed.get("replay_correlation_id")
        and resolved.get("audit_log_id") == replayed.get("audit_log_id"),
        "RabbitMQ resolved stage identity does not match replay",
        errors,
    )
    _require(
        resolved.get("probe_run_id") == probe_run_id,
        "RabbitMQ resolved stage does not belong to the probe run",
        errors,
    )
    _require(resolved.get("result") == "passed", "RabbitMQ replay did not resolve", errors)
    _require(resolved.get("dlq_count_after") == 0, "RabbitMQ DLQ was not drained", errors)
    _require(
        resolved.get("domain_state") == "passed",
        "RabbitMQ replay did not restore domain state",
        errors,
    )

    errors.extend(
        _email_delivery_evidence_errors(
            external_receipts["email-delivery.json"],
            now=timestamp,
        )
    )

    _require(
        evidence_by_name["infrastructure-e2e.json"].get("status") == "development_passed",
        "infrastructure-e2e.json raw status is invalid",
        errors,
    )
    _require(
        evidence_by_name["dgx-spark-evidence.json"].get("status") == "passed",
        "dgx-spark-evidence.json did not pass",
        errors,
    )
    errors.extend(
        _promtool_evidence_errors(
            external_receipts["promtool.json"],
            alertmanager_config=config_payload,
            contract_payloads=contracts,
        )
    )
    infrastructure = evidence_by_name["infrastructure-e2e.json"]
    errors.extend(_infrastructure_e2e_errors(infrastructure, git_sha=git_sha))
    raw_prometheus_minio = infrastructure.get("prometheus_minio_tls")
    prometheus_minio = raw_prometheus_minio if isinstance(raw_prometheus_minio, dict) else {}
    _require(
        prometheus_minio.get("config_sha256")
        == _sha256_bytes(contracts["ops/observability/prometheus.protected.yml"]),
        "infrastructure Prometheus config digest does not match the protected contract",
        errors,
    )
    _require(
        infrastructure.get("rabbitmq_probe_run_id") == replay.get("probe_run_id"),
        "infrastructure and RabbitMQ evidence do not share one run identity",
        errors,
    )
    _require(
        infrastructure.get("rabbitmq_evidence_sha256")
        == _sha256_bytes(payloads["rabbitmq-dlq-replay.json"]),
        "infrastructure evidence is not bound to rabbitmq-dlq-replay.json",
        errors,
    )
    dgx = evidence_by_name["dgx-spark-evidence.json"]
    _require(dgx.get("architecture") in {"arm64", "aarch64"}, "DGX evidence is not ARM64", errors)
    _require(
        dgx.get("docker_architecture") in {"arm64", "aarch64"},
        "DGX Docker daemon evidence is not ARM64",
        errors,
    )
    _require(
        dgx.get("backend_image_revision") == git_sha
        and dgx.get("frontend_image_revision") == git_sha,
        "DGX image revision labels do not match the release SHA",
        errors,
    )
    _require(
        isinstance(dgx.get("backend_image_id"), str)
        and str(dgx["backend_image_id"]).startswith("sha256:")
        and isinstance(dgx.get("frontend_image_id"), str)
        and str(dgx["frontend_image_id"]).startswith("sha256:"),
        "DGX image content IDs are missing",
        errors,
    )
    _require(dgx.get("full_compose_e2e") == "passed", "DGX full Compose E2E missing", errors)
    _require(
        dgx.get("run_id") == infrastructure.get("run_id")
        and dgx.get("compose_project") == infrastructure.get("compose_project")
        and dgx.get("resolved_compose_sha256") == infrastructure.get("resolved_compose_sha256"),
        "DGX proof does not share the infrastructure run/Compose identity",
        errors,
    )
    _require(
        dgx.get("backend_image_id") == infrastructure.get("backend_image_id")
        and dgx.get("frontend_image_id") == infrastructure.get("frontend_image_id"),
        "DGX proof image content IDs do not match infrastructure E2E",
        errors,
    )
    _require(
        dgx.get("compose_e2e_evidence_sha256")
        == _sha256_bytes(payloads["infrastructure-e2e.json"]),
        "DGX proof is not bound to infrastructure-e2e.json",
        errors,
    )
    return errors


def validate_evidence_payloads(
    payloads: dict[str, bytes],
    *,
    git_sha: str,
    environment: str,
    contract_payloads: Mapping[str, bytes] | None = None,
    now: datetime | None = None,
) -> list[str]:
    """Validate release evidence semantics using only caller-owned snapshots."""

    return check_evidence(
        evidence_root=Path(),
        alertmanager_config=Path("alertmanager.yml"),
        backend_api_host="127.0.0.1",
        git_sha=git_sha,
        environment=environment,
        evidence_payloads=payloads,
        contract_payloads=contract_payloads,
        now=now,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract-only", action="store_true")
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument("--alertmanager-config", type=Path)
    parser.add_argument("--backend-api-host", default="127.0.0.1")
    parser.add_argument("--git-sha")
    parser.add_argument("--environment", choices=("staging", "production"))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.contract_only:
        errors = check_contract()
    else:
        if (
            args.evidence_dir is None
            or args.alertmanager_config is None
            or args.git_sha is None
            or args.environment is None
        ):
            sys.stderr.write(
                "ERROR: protected gate requires evidence dir, Alertmanager config, "
                "git SHA, and environment\n"
            )
            return 2
        try:
            errors = check_evidence(
                evidence_root=args.evidence_dir.resolve(),
                alertmanager_config=args.alertmanager_config,
                backend_api_host=args.backend_api_host,
                git_sha=args.git_sha,
                environment=args.environment,
            )
        except RuntimeError as error:
            sys.stderr.write(f"ERROR: {error}\n")
            return 1
    if errors:
        sys.stderr.write("\n".join(f"ERROR: {error}" for error in errors) + "\n")
        return 1
    sys.stdout.write("protected release gate passed\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
