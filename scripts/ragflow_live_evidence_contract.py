"""Strict, privacy-preserving contracts for protected RAGFlow live evidence."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Final, NoReturn

PROBE_SCHEMA: Final = "knowledge-uploader.ragflow-live-probe.v1"
JANITOR_SCHEMA: Final = "knowledge-uploader.ragflow-live-janitor.v1"
EVIDENCE_SCHEMA: Final = "knowledge-uploader.ragflow-live-evidence.v1"
CONTRACT_VERSION: Final = 1
REQUIREMENT_ID: Final = "EXT-RAGFLOW-001"
WORKFLOW_PATH: Final = ".github/workflows/protected-ragflow-evidence.yml"

MAX_JSON_BYTES: Final = 256 * 1024
HASH_PATTERN: Final = re.compile(r"[0-9a-f]{64}")
GIT_SHA_PATTERN: Final = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
REPOSITORY_PATTERN: Final = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
ENVIRONMENT_PATTERN: Final = re.compile(r"[a-z][a-z0-9-]{1,31}")
TIMESTAMP_PATTERN: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")

PROBE_FIELDS: Final = frozenset(
    {
        "schema",
        "version",
        "requirement_id",
        "verdict",
        "evidence_kind",
        "probe_mode",
        "network_timeout_simulation",
        "fault_injection",
        "environment",
        "repository",
        "git_sha",
        "workflow",
        "main_ci",
        "trust",
        "owner_attestation",
        "deployment_attestation",
        "identities",
        "stages",
        "cleanup",
        "started_at",
        "finished_at",
    }
)
WORKFLOW_FIELDS: Final = frozenset({"path", "run_id", "run_attempt"})
MAIN_CI_FIELDS: Final = frozenset(
    {
        "run_id",
        "run_attempt",
        "bundle_artifact_id",
        "bundle_artifact_digest",
    }
)
TRUST_FIELDS: Final = frozenset({"workflow_trust_sha256"})
ATTESTATION_FIELDS: Final = frozenset({"attestation_sha256", "policy_sha256", "nonce_sha256"})
DEPLOYMENT_ATTESTATION_FIELDS: Final = frozenset(
    {"attestation_sha256", "policy_sha256", "deployment_identity_sha256"}
)
IDENTITY_FIELDS: Final = frozenset(
    {
        "endpoint_identity_sha256",
        "tls_spki_sha256",
        "dataset_identity_sha256",
        "dataset_mapping_id_sha256",
        "category_id_sha256",
        "app_endpoint_identity_sha256",
        "app_tls_spki_sha256",
        "app_file_id_sha256",
        "remote_name_sha256",
        "remote_document_id_sha256",
        "first_task_id_sha256",
        "repeat_task_id_sha256",
        "delete_task_id_sha256",
    }
)
STAGE_FIELDS: Final = frozenset(
    {"initial_dataset", "preseed", "first_sync", "repeat_sync", "parse", "application_delete"}
)
INITIAL_FIELDS: Final = frozenset({"dataset_total", "exact_name_count"})
PRESEED_FIELDS: Final = frozenset(
    {"dataset_total", "exact_name_count", "remote_id_match", "commit_observed"}
)
SYNC_FIELDS: Final = frozenset(
    {
        "task_type",
        "task_status",
        "app_file_status",
        "app_parse_status",
        "dataset_total",
        "exact_name_count",
        "remote_id_match",
        "reconciliation_log_observed",
        "remote_upload_log_observed",
        "parse_start_log_observed",
    }
)
REPEAT_SYNC_FIELDS: Final = SYNC_FIELDS | {"request_mode"}
PARSE_FIELDS: Final = frozenset({"app_terminal", "remote_terminal", "remote_run", "task_terminal"})
DELETE_FIELDS: Final = frozenset(
    {
        "requested",
        "delete_task_status",
        "dataset_total",
        "exact_name_count",
        "confirmed",
    }
)
CLEANUP_FIELDS: Final = frozenset(
    {
        "application_cleanup_confirmed",
        "emergency_direct_cleanup_used",
        "dataset_total",
        "exact_name_count",
        "confirmed",
    }
)

JANITOR_FIELDS: Final = frozenset(
    {
        "schema",
        "version",
        "environment",
        "repository",
        "git_sha",
        "workflow",
        "main_ci",
        "trust",
        "owner_attestation",
        "deployment_attestation",
        "identities",
        "cleanup",
        "started_at",
        "finished_at",
    }
)
JANITOR_IDENTITY_FIELDS: Final = frozenset(
    {
        "endpoint_identity_sha256",
        "tls_spki_sha256",
        "dataset_identity_sha256",
        "app_endpoint_identity_sha256",
        "app_tls_spki_sha256",
        "canary_filename_sha256",
    }
)
JANITOR_CLEANUP_FIELDS: Final = frozenset(
    {
        "app_candidates_seen",
        "app_delete_requests",
        "remote_candidates_seen",
        "remote_delete_requests",
        "dataset_total",
        "canary_remote_count",
        "confirmed",
    }
)


class EvidenceContractError(RuntimeError):
    """Fail-closed contract error carrying only a non-sensitive code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _DuplicateJsonKey(ValueError):
    pass


def _raise(code: str) -> NoReturn:
    raise EvidenceContractError(code)


def _mapping(value: object, code: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        _raise(code)
    return value


def _exact_keys(value: Mapping[str, object], expected: frozenset[str], code: str) -> None:
    if set(value) != expected:
        _raise(code)


def _text(value: object, pattern: re.Pattern[str], code: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        _raise(code)
    return value


def _hash(value: object, code: str) -> str:
    return _text(value, HASH_PATTERN, code)


def _positive_integer(value: object, code: str) -> int:
    if type(value) is not int or value < 1:
        _raise(code)
    return value


def _non_negative_integer(value: object, code: str) -> int:
    if type(value) is not int or value < 0:
        _raise(code)
    return value


def _timestamp(value: object, code: str) -> str:
    return _text(value, TIMESTAMP_PATTERN, code)


def _validate_common(
    value: Mapping[str, object],
    *,
    expected_repository: str | None,
    expected_git_sha: str | None,
    expected_environment: str | None,
    expected_run_id: int | None,
    expected_run_attempt: int | None,
    expected_main_run_id: int | None,
    expected_main_run_attempt: int | None,
) -> None:
    environment = _text(value.get("environment"), ENVIRONMENT_PATTERN, "context_invalid")
    repository = _text(value.get("repository"), REPOSITORY_PATTERN, "context_invalid")
    git_sha = _text(value.get("git_sha"), GIT_SHA_PATTERN, "context_invalid")
    if expected_environment is not None and environment != expected_environment:
        _raise("context_mismatch")
    if expected_repository is not None and repository != expected_repository:
        _raise("context_mismatch")
    if expected_git_sha is not None and git_sha != expected_git_sha:
        _raise("context_mismatch")

    workflow = _mapping(value.get("workflow"), "workflow_invalid")
    _exact_keys(workflow, WORKFLOW_FIELDS, "workflow_invalid")
    if workflow.get("path") != WORKFLOW_PATH:
        _raise("workflow_invalid")
    run_id = _positive_integer(workflow.get("run_id"), "workflow_invalid")
    run_attempt = _positive_integer(workflow.get("run_attempt"), "workflow_invalid")
    if expected_run_id is not None and run_id != expected_run_id:
        _raise("context_mismatch")
    if expected_run_attempt is not None and run_attempt != expected_run_attempt:
        _raise("context_mismatch")

    main_ci = _mapping(value.get("main_ci"), "main_ci_invalid")
    _exact_keys(main_ci, MAIN_CI_FIELDS, "main_ci_invalid")
    main_run_id = _positive_integer(main_ci.get("run_id"), "main_ci_invalid")
    main_run_attempt = _positive_integer(main_ci.get("run_attempt"), "main_ci_invalid")
    _positive_integer(main_ci.get("bundle_artifact_id"), "main_ci_invalid")
    digest = main_ci.get("bundle_artifact_digest")
    if not isinstance(digest, str) or not digest.startswith("sha256:"):
        _raise("main_ci_invalid")
    _hash(digest.removeprefix("sha256:"), "main_ci_invalid")
    if expected_main_run_id is not None and main_run_id != expected_main_run_id:
        _raise("context_mismatch")
    if expected_main_run_attempt is not None and main_run_attempt != expected_main_run_attempt:
        _raise("context_mismatch")

    trust = _mapping(value.get("trust"), "trust_invalid")
    _exact_keys(trust, TRUST_FIELDS, "trust_invalid")
    _hash(trust.get("workflow_trust_sha256"), "trust_invalid")

    attestation = _mapping(value.get("owner_attestation"), "attestation_binding_invalid")
    _exact_keys(attestation, ATTESTATION_FIELDS, "attestation_binding_invalid")
    for field in ATTESTATION_FIELDS:
        _hash(attestation.get(field), "attestation_binding_invalid")

    deployment = _mapping(
        value.get("deployment_attestation"), "deployment_attestation_binding_invalid"
    )
    _exact_keys(
        deployment,
        DEPLOYMENT_ATTESTATION_FIELDS,
        "deployment_attestation_binding_invalid",
    )
    for field in DEPLOYMENT_ATTESTATION_FIELDS:
        _hash(deployment.get(field), "deployment_attestation_binding_invalid")

    started_at = _timestamp(value.get("started_at"), "time_invalid")
    finished_at = _timestamp(value.get("finished_at"), "time_invalid")
    if finished_at < started_at:
        _raise("time_invalid")


def _validate_sync_stage(value: object, *, repeat: bool) -> Mapping[str, object]:
    stage = _mapping(value, "sync_stage_invalid")
    _exact_keys(stage, REPEAT_SYNC_FIELDS if repeat else SYNC_FIELDS, "sync_stage_invalid")
    expected_task_type = "ragflow_status_check" if repeat else "ragflow_upload"
    if (
        stage.get("task_type") != expected_task_type
        or stage.get("task_status") != "succeeded"
        or stage.get("app_file_status") != "parsed"
    ):
        _raise("sync_not_terminal")
    if stage.get("app_parse_status") not in {"3", "DONE"}:
        _raise("parse_not_terminal")
    if stage.get("dataset_total") != 1 or stage.get("exact_name_count") != 1:
        _raise("duplicate_remote_document")
    if stage.get("remote_id_match") is not True:
        _raise("remote_identity_mismatch")
    if type(stage.get("reconciliation_log_observed")) is not bool:
        _raise("sync_stage_invalid")
    if type(stage.get("remote_upload_log_observed")) is not bool:
        _raise("sync_stage_invalid")
    if repeat:
        if stage.get("request_mode") != "new_task":
            _raise("repeat_sync_invalid")
        if (
            stage.get("remote_upload_log_observed") is not False
            or stage.get("parse_start_log_observed") is not False
        ):
            _raise("duplicate_remote_upload")
    return stage


def validate_probe(
    value: object,
    *,
    expected_repository: str | None = None,
    expected_git_sha: str | None = None,
    expected_environment: str | None = None,
    expected_run_id: int | None = None,
    expected_run_attempt: int | None = None,
    expected_main_run_id: int | None = None,
    expected_main_run_attempt: int | None = None,
) -> Mapping[str, object]:
    """Validate a successful real-service probe without trusting producer assertions."""

    probe = _mapping(value, "probe_invalid")
    _exact_keys(probe, PROBE_FIELDS, "probe_invalid")
    if (
        probe.get("schema") != PROBE_SCHEMA
        or type(probe.get("version")) is not int
        or probe.get("version") != CONTRACT_VERSION
        or probe.get("requirement_id") != REQUIREMENT_ID
        or probe.get("verdict") != "ready"
        or probe.get("evidence_kind") != "real_external_service"
        or probe.get("probe_mode") != "preseeded_remote_reconciliation"
        or probe.get("network_timeout_simulation") is not False
        or probe.get("fault_injection") is not False
    ):
        _raise("probe_invalid")
    _validate_common(
        probe,
        expected_repository=expected_repository,
        expected_git_sha=expected_git_sha,
        expected_environment=expected_environment,
        expected_run_id=expected_run_id,
        expected_run_attempt=expected_run_attempt,
        expected_main_run_id=expected_main_run_id,
        expected_main_run_attempt=expected_main_run_attempt,
    )

    identities = _mapping(probe.get("identities"), "identities_invalid")
    _exact_keys(identities, IDENTITY_FIELDS, "identities_invalid")
    for field in IDENTITY_FIELDS:
        _hash(identities.get(field), "identities_invalid")
    if (
        len(
            {
                identities.get("first_task_id_sha256"),
                identities.get("repeat_task_id_sha256"),
                identities.get("delete_task_id_sha256"),
            }
        )
        != 3
    ):
        _raise("identities_invalid")

    stages = _mapping(probe.get("stages"), "stages_invalid")
    _exact_keys(stages, STAGE_FIELDS, "stages_invalid")
    initial = _mapping(stages.get("initial_dataset"), "initial_dataset_invalid")
    _exact_keys(initial, INITIAL_FIELDS, "initial_dataset_invalid")
    if initial.get("dataset_total") != 0 or initial.get("exact_name_count") != 0:
        _raise("dataset_not_initially_empty")

    preseed = _mapping(stages.get("preseed"), "preseed_invalid")
    _exact_keys(preseed, PRESEED_FIELDS, "preseed_invalid")
    if (
        preseed.get("dataset_total") != 1
        or preseed.get("exact_name_count") != 1
        or preseed.get("remote_id_match") is not True
        or preseed.get("commit_observed") is not True
    ):
        _raise("preseed_invalid")

    first_sync = _validate_sync_stage(stages.get("first_sync"), repeat=False)
    _validate_sync_stage(stages.get("repeat_sync"), repeat=True)
    if first_sync.get("reconciliation_log_observed") is not True:
        _raise("reconciliation_not_observed")
    if (
        first_sync.get("remote_upload_log_observed") is not False
        or first_sync.get("parse_start_log_observed") is not True
    ):
        _raise("duplicate_remote_upload")

    parse = _mapping(stages.get("parse"), "parse_invalid")
    _exact_keys(parse, PARSE_FIELDS, "parse_invalid")
    if (
        parse.get("app_terminal") is not True
        or parse.get("remote_terminal") is not True
        or parse.get("remote_run") not in {"3", "DONE"}
        or parse.get("task_terminal") is not True
    ):
        _raise("parse_not_terminal")

    deletion = _mapping(stages.get("application_delete"), "delete_invalid")
    _exact_keys(deletion, DELETE_FIELDS, "delete_invalid")
    if (
        deletion.get("requested") is not True
        or deletion.get("delete_task_status") != "succeeded"
        or deletion.get("dataset_total") != 0
        or deletion.get("exact_name_count") != 0
        or deletion.get("confirmed") is not True
    ):
        _raise("application_cleanup_unconfirmed")

    cleanup = _mapping(probe.get("cleanup"), "cleanup_invalid")
    _exact_keys(cleanup, CLEANUP_FIELDS, "cleanup_invalid")
    if (
        cleanup.get("application_cleanup_confirmed") is not True
        or cleanup.get("emergency_direct_cleanup_used") is not False
        or cleanup.get("dataset_total") != 0
        or cleanup.get("exact_name_count") != 0
        or cleanup.get("confirmed") is not True
    ):
        _raise("cleanup_unconfirmed")
    return probe


def validate_janitor(
    value: object,
    *,
    expected_repository: str | None = None,
    expected_git_sha: str | None = None,
    expected_environment: str | None = None,
    expected_run_id: int | None = None,
    expected_run_attempt: int | None = None,
    expected_main_run_id: int | None = None,
    expected_main_run_attempt: int | None = None,
) -> Mapping[str, object]:
    """Validate the independent post-probe cleanup confirmation."""

    janitor = _mapping(value, "janitor_invalid")
    _exact_keys(janitor, JANITOR_FIELDS, "janitor_invalid")
    if (
        janitor.get("schema") != JANITOR_SCHEMA
        or type(janitor.get("version")) is not int
        or janitor.get("version") != CONTRACT_VERSION
    ):
        _raise("janitor_invalid")
    _validate_common(
        janitor,
        expected_repository=expected_repository,
        expected_git_sha=expected_git_sha,
        expected_environment=expected_environment,
        expected_run_id=expected_run_id,
        expected_run_attempt=expected_run_attempt,
        expected_main_run_id=expected_main_run_id,
        expected_main_run_attempt=expected_main_run_attempt,
    )
    identities = _mapping(janitor.get("identities"), "janitor_identities_invalid")
    _exact_keys(identities, JANITOR_IDENTITY_FIELDS, "janitor_identities_invalid")
    for field in JANITOR_IDENTITY_FIELDS:
        _hash(identities.get(field), "janitor_identities_invalid")
    cleanup = _mapping(janitor.get("cleanup"), "janitor_cleanup_invalid")
    _exact_keys(cleanup, JANITOR_CLEANUP_FIELDS, "janitor_cleanup_invalid")
    for field in {
        "app_candidates_seen",
        "app_delete_requests",
        "remote_candidates_seen",
        "remote_delete_requests",
        "dataset_total",
        "canary_remote_count",
    }:
        _non_negative_integer(cleanup.get(field), "janitor_cleanup_invalid")
    if (
        cleanup.get("dataset_total") != 0
        or cleanup.get("canary_remote_count") != 0
        or cleanup.get("confirmed") is not True
    ):
        _raise("janitor_cleanup_unconfirmed")
    return janitor


def _same_binding(probe: Mapping[str, object], janitor: Mapping[str, object]) -> None:
    for field in {
        "environment",
        "repository",
        "git_sha",
        "workflow",
        "main_ci",
        "trust",
        "deployment_attestation",
    }:
        if probe.get(field) != janitor.get(field):
            _raise("collector_binding_mismatch")
    if probe.get("owner_attestation") != janitor.get("owner_attestation"):
        _raise("collector_binding_mismatch")
    probe_identities = _mapping(probe.get("identities"), "collector_binding_mismatch")
    janitor_identities = _mapping(janitor.get("identities"), "collector_binding_mismatch")
    for field in {
        "endpoint_identity_sha256",
        "tls_spki_sha256",
        "dataset_identity_sha256",
        "app_endpoint_identity_sha256",
        "app_tls_spki_sha256",
    }:
        if probe_identities.get(field) != janitor_identities.get(field):
            _raise("collector_binding_mismatch")
    if str(janitor.get("started_at")) < str(probe.get("finished_at")):
        _raise("collector_time_order_invalid")


def collect_evidence(
    probe_value: object,
    janitor_value: object,
    *,
    probe_sha256: str,
    janitor_sha256: str,
    expected_repository: str,
    expected_git_sha: str,
    expected_environment: str,
    expected_run_id: int,
    expected_run_attempt: int,
    expected_main_run_id: int,
    expected_main_run_attempt: int,
) -> Mapping[str, object]:
    """Return the final evidence only after producer and janitor independently pass."""

    _hash(probe_sha256, "collector_digest_invalid")
    _hash(janitor_sha256, "collector_digest_invalid")
    probe = validate_probe(
        probe_value,
        expected_repository=expected_repository,
        expected_git_sha=expected_git_sha,
        expected_environment=expected_environment,
        expected_run_id=expected_run_id,
        expected_run_attempt=expected_run_attempt,
        expected_main_run_id=expected_main_run_id,
        expected_main_run_attempt=expected_main_run_attempt,
    )
    janitor = validate_janitor(
        janitor_value,
        expected_repository=expected_repository,
        expected_git_sha=expected_git_sha,
        expected_environment=expected_environment,
        expected_run_id=expected_run_id,
        expected_run_attempt=expected_run_attempt,
        expected_main_run_id=expected_main_run_id,
        expected_main_run_attempt=expected_main_run_attempt,
    )
    _same_binding(probe, janitor)
    return {
        "schema": EVIDENCE_SCHEMA,
        "version": CONTRACT_VERSION,
        "requirement_id": REQUIREMENT_ID,
        "verdict": "ready",
        "probe_sha256": probe_sha256,
        "janitor_sha256": janitor_sha256,
        "proof": probe,
        "independent_cleanup": janitor,
    }


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(key)
        result[key] = value
    return result


def _reject_json_constant(_: str) -> NoReturn:
    raise ValueError("non-finite JSON number")


def read_stable_bytes(path: Path, *, max_bytes: int = MAX_JSON_BYTES) -> tuple[bytes, str]:
    """Read one stable regular file and return bytes plus its SHA-256 binding."""

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
            or opened.st_size < 1
            or opened.st_size > max_bytes
        ):
            _raise("input_invalid")
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = -1
            raw = stream.read(max_bytes + 1)
            after = os.fstat(stream.fileno())
        current = path.lstat()
    except OSError:
        _raise("input_invalid")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    identity = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
    if (
        len(raw) != opened.st_size
        or len(raw) > max_bytes
        or identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or identity != (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)
        or not stat.S_ISREG(current.st_mode)
        or b"\x00" in raw
    ):
        _raise("input_invalid")
    return raw, hashlib.sha256(raw).hexdigest()


def read_json_document(
    path: Path, *, max_bytes: int = MAX_JSON_BYTES
) -> tuple[Mapping[str, object], str, bytes]:
    """Read one stable regular JSON document, its digest and its exact bytes."""

    raw, digest = read_stable_bytes(path, max_bytes=max_bytes)
    try:
        value: object = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, ValueError, json.JSONDecodeError, RecursionError, _DuplicateJsonKey):
        _raise("input_invalid")
    return _mapping(value, "input_invalid"), digest, raw


def read_json(path: Path, *, max_bytes: int = MAX_JSON_BYTES) -> tuple[Mapping[str, object], str]:
    """Read one stable regular JSON file and return its SHA-256 binding."""

    value, digest, _raw = read_json_document(path, max_bytes=max_bytes)
    return value, digest


def write_json(path: Path, value: Mapping[str, object]) -> str:
    """Create canonical LF JSON without replacing an existing evidence file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return hashlib.sha256(content).hexdigest()


def write_bytes(path: Path, content: bytes, *, max_bytes: int = MAX_JSON_BYTES) -> str:
    """Create an exact regular-file copy without replacing an existing artifact."""

    if max_bytes < 1 or not content or len(content) > max_bytes or b"\x00" in content:
        _raise("output_invalid")
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return hashlib.sha256(content).hexdigest()


def write_checksum(path: Path, *, digest: str, target_name: str) -> None:
    """Write a conventional checksum sidecar after validating all fields."""

    _hash(digest, "checksum_invalid")
    if not target_name or Path(target_name).name != target_name:
        _raise("checksum_invalid")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n", closefd=True) as stream:
            descriptor = -1
            stream.write(f"{digest}  {target_name}\n")
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def sha256_text(value: str) -> str:
    """Return a lowercase SHA-256 digest without retaining the source value."""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()
