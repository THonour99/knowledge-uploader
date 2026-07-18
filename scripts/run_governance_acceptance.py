#!/usr/bin/env python3
"""Run protected local evidence for AI-001, VER-001 and EXP-001.

The runner intentionally uses an isolated Docker Compose project and an exact
``*_test`` PostgreSQL database.  LLM and RAGFlow coverage is protocol-substitute
only: this command never verifies a real provider and never evaluates COST-002.
Raw process output is not archived because failures can contain document or
prompt material; only SHA-256 digests and an allowlisted pytest summary are
sealed into the external evidence directory.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ElementTree
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

EXPECTED_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
PYTEST_SUMMARY_PATTERN = re.compile(
    r"^(?:\d+ (?:passed|failed|skipped|error|errors|xfailed|xpassed))"
    r"(?:, \d+ (?:passed|failed|skipped|error|errors|xfailed|xpassed))* in [0-9.]+s$"
)
TEST_DATABASE_NAME = "knowledge_uploader_governance_acceptance_test"
TEST_REDIS_DB = "15"
EVIDENCE_SCHEMA_VERSION = "governance-acceptance.v1"
COMPOSE_SOURCE_ENVIRONMENT_KEYS = frozenset(
    {
        "COMPOSE_DISABLE_ENV_FILE",
        "COMPOSE_ENV_FILES",
        "COMPOSE_FILE",
        "COMPOSE_PATH_SEPARATOR",
        "COMPOSE_PROFILES",
        "COMPOSE_PROJECT_DIRECTORY",
        "COMPOSE_PROJECT_NAME",
    }
)

Executor = Literal["backend_pytest", "frontend_vitest"]


class GovernanceAcceptanceError(RuntimeError):
    """The protected acceptance precondition or execution contract failed."""


@dataclass(frozen=True)
class TestTarget:
    executor: Executor
    node: str
    assertion: str


@dataclass(frozen=True)
class AcceptancePlan:
    acceptance_id: str
    scope: str
    targets: tuple[TestTarget, ...]


@dataclass(frozen=True)
class CandidateIdentity:
    git_sha: str
    porcelain_v1_all: str

    @property
    def clean(self) -> bool:
        return not self.porcelain_v1_all


@dataclass(frozen=True)
class ProcessResult:
    name: str
    command: tuple[str, ...]
    returncode: int
    stdout: bytes
    stderr: bytes
    duration_ms: int


@dataclass(frozen=True)
class ReportClosure:
    passed: bool
    expected_targets: int
    executed_targets: int
    total_cases: int
    passed_cases: int
    nonpassed_cases: int
    report_sha256: str | None
    reason: str


ACCEPTANCE_PLAN: tuple[AcceptancePlan, ...] = (
    AcceptancePlan(
        acceptance_id="AI-001",
        scope="OpenAI-compatible protocol substitute and governed analysis persistence",
        targets=(
            TestTarget(
                "backend_pytest",
                "backend/app/tests/unit/test_openai_compatible_llm.py::"
                "test_complete_returns_actual_model_usage_and_json_request",
                "request uses the OpenAI-compatible JSON contract and persists actual model/usage",
            ),
            TestTarget(
                "backend_pytest",
                "backend/app/tests/unit/test_openai_compatible_llm.py::"
                "test_http_statuses_map_to_sanitized_retry_policy",
                "429/5xx and permanent HTTP classes map to bounded sanitized retry policy",
            ),
            TestTarget(
                "backend_pytest",
                "backend/app/tests/unit/test_openai_compatible_llm.py::"
                "test_timeout_is_retryable_without_transport_message_leak",
                "timeout is retryable without leaking transport details",
            ),
            TestTarget(
                "backend_pytest",
                "backend/app/tests/unit/test_openai_compatible_llm.py::"
                "test_malformed_or_unsafe_response_is_permanent",
                "malformed protocol responses fail permanently and safely",
            ),
            TestTarget(
                "backend_pytest",
                "backend/app/tests/unit/test_ai_task_retry.py::"
                "test_transient_error_retries_with_exponential_backoff",
                "transient provider failures consume a bounded retry budget with backoff",
            ),
            TestTarget(
                "backend_pytest",
                "backend/app/tests/unit/test_ai_task_retry.py::"
                "test_retry_exhausted_marks_analysis_failed_with_retry_count",
                "retry exhaustion records a stable failure category and retry count",
            ),
            TestTarget(
                "backend_pytest",
                "backend/app/tests/unit/test_ai_tasks.py::"
                "test_formal_llm_repair_records_each_call_and_cost",
                "strict JSON repair records structured output, model, token usage and known cost",
            ),
            TestTarget(
                "backend_pytest",
                "backend/app/tests/unit/test_ai_tasks.py::"
                "test_malformed_llm_output_is_repaired_once_without_raw_persistence",
                "invalid JSON is repaired once and raw provider content is not persisted",
            ),
            TestTarget(
                "backend_pytest",
                "backend/app/tests/unit/test_ai_tasks.py::"
                "test_external_provider_blocked_by_policy_fails_closed",
                "external provider calls stay blocked and this evidence cannot claim EXT-LLM",
            ),
            TestTarget(
                "backend_pytest",
                "backend/app/tests/unit/test_capacity_cost_governance.py::"
                "test_usage_worker_fails_closed_when_legacy_writer_drifts_a_confirmed_price",
                "unknown pricing cannot be represented as known zero cost",
            ),
            TestTarget(
                "backend_pytest",
                "backend/app/tests/e2e/test_governance_acceptance.py::"
                "test_ai_001_persists_prompt_version_and_unknown_pricing_safely",
                "PostgreSQL persistence includes prompt version/provenance and excludes "
                "raw evidence",
            ),
        ),
    ),
    AcceptancePlan(
        acceptance_id="VER-001",
        scope="Recoverable v2 replacement of v1 with one current version",
        targets=(
            TestTarget(
                "backend_pytest",
                "backend/app/tests/unit/test_document_api.py::"
                "test_concurrent_replacement_upload_creates_one_contiguous_version_chain",
                "concurrent replacement allows one v2, preserves a contiguous acyclic chain",
            ),
            TestTarget(
                "backend_pytest",
                "backend/app/tests/unit/test_document_lifecycle.py::"
                "test_replacement_snapshots_remote_action_and_inherits_governance",
                "replacement upload preserves history and records governance/audit snapshot",
            ),
            TestTarget(
                "backend_pytest",
                "backend/app/tests/unit/test_document_version_ragflow.py::"
                "test_version_switch_recovers_old_remote_failure_and_records_candidate_timeline",
                "old remote cleanup failure is recoverable and leaves one current version/timeline",
            ),
            TestTarget(
                "backend_pytest",
                "backend/app/tests/unit/test_document_version_ragflow.py::"
                "test_unknown_predecessor_delete_outcome_is_persisted_for_reconciliation",
                "unknown remote delete outcome fails closed with durable reconciliation evidence",
            ),
            TestTarget(
                "backend_pytest",
                "backend/app/tests/unit/test_document_version_ragflow.py::"
                "test_candidate_remote_activation_is_idempotent_after_database_failure",
                "candidate activation resumes idempotently after a local commit failure",
            ),
            TestTarget(
                "backend_pytest",
                "backend/app/tests/unit/test_document_version_ragflow.py::"
                "test_archive_snapshot_preserves_predecessor_and_marks_it_non_current",
                "archive policy retains predecessor history while switching current identity",
            ),
            TestTarget(
                "backend_pytest",
                "backend/app/tests/unit/test_document_version_migration.py::"
                "test_v001_version_governance_upgrade_constraints_and_round_trip",
                "PostgreSQL constraints preserve one current row and reject malformed chains",
            ),
            TestTarget(
                "backend_pytest",
                "backend/app/tests/unit/test_document_api.py::"
                "test_upload_can_submit_after_upload_when_ai_is_skipped",
                "document writes retain transactional audit and outbox evidence",
            ),
        ),
    ),
    AcceptancePlan(
        acceptance_id="EXP-001",
        scope="Expiry responsibility routing without silent ownership mutation or deletion",
        targets=(
            TestTarget(
                "backend_pytest",
                "backend/app/tests/unit/test_document_api.py::"
                "test_owner_options_and_expiry_owner_draft_contract",
                "owner/expires changes validate scope, reset markers and remain "
                "optimistic-lock safe",
            ),
            TestTarget(
                "backend_pytest",
                "backend/app/tests/unit/test_document_api.py::"
                "test_delegated_owner_access_is_read_only_department_scoped_and_audited",
                "responsible workbench visibility follows current version and permission scope",
            ),
            TestTarget(
                "backend_pytest",
                "backend/app/tests/unit/test_document_expiry_repository.py::"
                "test_refresh_statuses_and_mark_notification_sent_are_idempotent",
                "expiring/expired are auxiliary states and delivery markers are idempotent",
            ),
            TestTarget(
                "backend_pytest",
                "backend/app/tests/unit/test_document_expiry_tasks.py::"
                "test_expiry_scan_persists_id_only_outbox_in_same_transaction",
                "expiry scan and minimal outbox event commit atomically without document deletion",
            ),
            TestTarget(
                "backend_pytest",
                "backend/app/tests/unit/test_notification_service.py::"
                "test_expiry_prefers_active_owner_falls_back_and_deduplicates_admin",
                "disabled owner receives nothing; active uploader fallback and department "
                "admin are alerted",
            ),
            TestTarget(
                "backend_pytest",
                "backend/app/tests/unit/test_notification_service.py::"
                "test_expiry_event_snapshot_skips_delayed_patch_archive_and_historical_rows",
                "stale/historical expiry events are ignored and replay remains idempotent",
            ),
            TestTarget(
                "backend_pytest",
                "backend/app/tests/unit/test_notification_service.py::"
                "test_in_app_notification_listing_and_reads_are_channel_and_user_scoped",
                "notification list/read operations cannot cross user scope",
            ),
            TestTarget(
                "frontend_vitest",
                "frontend/src/layouts/TopHeader.test.tsx::"
                "builds links only from the structured resource contract",
                "notification deep links are built only from allowlisted structured resources",
            ),
            TestTarget(
                "frontend_vitest",
                "frontend/src/layouts/TopHeader.test.tsx::"
                "rejects malformed resource IDs and unknown resource types",
                "malformed or unauthorized resource identities never become executable links",
            ),
            TestTarget(
                "frontend_vitest",
                "frontend/src/layouts/TopHeader.test.tsx::"
                "falls back to an allowlisted file for users without task-log access",
                "role changes constrain the deep-link target instead of disclosing task scope",
            ),
        ),
    ),
)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sanitized_environment(source: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    environment = dict(source)
    removed = sorted(
        {key.upper() for key in environment if key.upper() in COMPOSE_SOURCE_ENVIRONMENT_KEYS}
    )
    for key in tuple(environment):
        if key.upper() in COMPOSE_SOURCE_ENVIRONMENT_KEYS:
            environment.pop(key)
    return environment, removed


def _compose_prefix(
    docker_executable: str,
    project_name: str,
    candidate_root: Path,
) -> tuple[str, ...]:
    return (
        docker_executable,
        "compose",
        "--project-name",
        project_name,
        "--project-directory",
        str(candidate_root),
        "--file",
        str(candidate_root / "docker-compose.yml"),
    )


def _file_sha256(path: Path) -> str | None:
    try:
        return _sha256(path.read_bytes())
    except OSError:
        return None


def _config_digest(result: ProcessResult) -> str | None:
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return _sha256(result.stdout)


def _compose_binding_passed(
    *,
    expected_source_sha256: str,
    source_sha256_before: str | None,
    source_sha256_after: str | None,
    config_before: ProcessResult,
    config_after: ProcessResult,
) -> bool:
    config_sha256_before = _config_digest(config_before)
    config_sha256_after = _config_digest(config_after)
    return (
        source_sha256_before == expected_source_sha256
        and source_sha256_after == expected_source_sha256
        and config_sha256_before is not None
        and config_sha256_after == config_sha256_before
    )


def _validate_expected_sha(value: str) -> str:
    if not EXPECTED_SHA_PATTERN.fullmatch(value):
        raise GovernanceAcceptanceError("expected Git SHA must be exactly 40 lowercase hex chars")
    return value


def _validate_test_database_name(value: str) -> str:
    if value != TEST_DATABASE_NAME or not value.endswith("_test"):
        raise GovernanceAcceptanceError(
            f"governance acceptance database must be {TEST_DATABASE_NAME}"
        )
    return value


def _git_bytes(repo_root: Path, *arguments: str) -> bytes:
    completed = subprocess.run(
        ("git", *arguments),
        cwd=repo_root,
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise GovernanceAcceptanceError(f"git {' '.join(arguments)} failed")
    return bytes(completed.stdout)


def candidate_identity(repo_root: Path) -> CandidateIdentity:
    sha = _git_bytes(repo_root, "rev-parse", "HEAD").decode("ascii").strip().lower()
    status = (
        _git_bytes(
            repo_root,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        )
        .decode("utf-8", errors="strict")
        .strip()
    )
    return CandidateIdentity(git_sha=sha, porcelain_v1_all=status)


def _assert_candidate(identity: CandidateIdentity, expected_sha: str) -> None:
    if identity.git_sha != expected_sha:
        raise GovernanceAcceptanceError("expected Git SHA does not match HEAD")
    if not identity.clean:
        raise GovernanceAcceptanceError(
            "protected governance acceptance requires a fully clean worktree including "
            "all untracked files"
        )


def _validate_output_dir(repo_root: Path, output_dir: Path) -> Path:
    if not output_dir.is_absolute():
        raise GovernanceAcceptanceError("evidence output directory must be absolute")
    resolved_root = repo_root.resolve()
    resolved_output = output_dir.resolve()
    if resolved_output == resolved_root or resolved_output.is_relative_to(resolved_root):
        raise GovernanceAcceptanceError("evidence output directory must be outside the repository")
    if resolved_output.exists():
        raise GovernanceAcceptanceError("evidence output directory must not already exist")
    return resolved_output


def _split_test_node(node: str) -> tuple[Path, str]:
    path_text, separator, test_name = node.partition("::")
    if separator != "::" or not test_name or "::" in test_name:
        raise GovernanceAcceptanceError(f"invalid exact test node: {node}")
    return Path(path_text), test_name


def _validate_vitest_target(source: str, test_name: str, node: str) -> None:
    suite_modifier = re.compile(r"\b(?:describe|suite)\.(?:skip|todo|only)\s*\(")
    if suite_modifier.search(source):
        raise GovernanceAcceptanceError(f"vitest suite is disabled or exclusive for target: {node}")

    declaration = re.compile(
        r"\b(?:it|test)(?:\.(skip|todo|only))?\s*\(\s*"
        + r"([\"'])"
        + re.escape(test_name)
        + r"\2\s*,"
    )
    matches = declaration.findall(source)
    if len(matches) != 1:
        raise GovernanceAcceptanceError(f"vitest assertion is missing or ambiguous: {node}")
    if matches[0][0]:
        raise GovernanceAcceptanceError(f"vitest assertion is disabled or exclusive: {node}")


def _validate_test_targets(
    repo_root: Path,
    plans: Sequence[AcceptancePlan] = ACCEPTANCE_PLAN,
) -> None:
    seen: set[str] = set()
    for plan in plans:
        if plan.acceptance_id not in {"AI-001", "VER-001", "EXP-001"}:
            raise GovernanceAcceptanceError(f"unexpected acceptance id: {plan.acceptance_id}")
        for target in plan.targets:
            identity = f"{target.executor}:{target.node}"
            if identity in seen:
                raise GovernanceAcceptanceError(f"duplicate test target: {target.node}")
            seen.add(identity)
            relative_path, test_name = _split_test_node(target.node)
            absolute_path = (repo_root / relative_path).resolve()
            if not absolute_path.is_relative_to(repo_root.resolve()) or not absolute_path.is_file():
                raise GovernanceAcceptanceError(f"test target file is missing: {relative_path}")
            source = absolute_path.read_text(encoding="utf-8")
            if target.executor == "backend_pytest":
                module = ast.parse(source, filename=str(relative_path))
                declared = {
                    node.name
                    for node in module.body
                    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                }
                if test_name not in declared:
                    raise GovernanceAcceptanceError(f"pytest node is missing: {target.node}")
            elif target.executor == "frontend_vitest":
                _validate_vitest_target(source, test_name, target.node)
            else:
                raise GovernanceAcceptanceError(f"unsupported executor: {target.executor}")


def _run_process(
    name: str,
    command: Sequence[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout_seconds: int = 900,
) -> ProcessResult:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            tuple(command),
            cwd=cwd,
            env=env,
            check=False,
            capture_output=True,
            timeout=timeout_seconds,
        )
        return ProcessResult(
            name=name,
            command=tuple(command),
            returncode=completed.returncode,
            stdout=bytes(completed.stdout),
            stderr=bytes(completed.stderr),
            duration_ms=round((time.monotonic() - started) * 1000),
        )
    except subprocess.TimeoutExpired:
        return ProcessResult(
            name=name,
            command=tuple(command),
            returncode=124,
            stdout=b"",
            stderr=b"timeout",
            duration_ms=round((time.monotonic() - started) * 1000),
        )
    except OSError as exc:
        return ProcessResult(
            name=name,
            command=tuple(command),
            returncode=127,
            stdout=b"",
            stderr=type(exc).__name__.encode("ascii"),
            duration_ms=round((time.monotonic() - started) * 1000),
        )


def _safe_pytest_summary(result: ProcessResult) -> str | None:
    for line in reversed(result.stdout.decode("utf-8", errors="replace").splitlines()):
        candidate = line.strip()
        if PYTEST_SUMMARY_PATTERN.fullmatch(candidate):
            return candidate
    return None


def _phase_evidence(result: ProcessResult) -> dict[str, object]:
    return {
        "name": result.name,
        "command": list(result.command),
        "returncode": result.returncode,
        "duration_ms": result.duration_ms,
        "stdout_sha256": _sha256(result.stdout),
        "stderr_sha256": _sha256(result.stderr),
        "pytest_summary": _safe_pytest_summary(result),
        "raw_logs_archived": False,
    }


def _container_backend_node(node: str) -> str:
    relative_path, test_name = _split_test_node(node)
    try:
        container_path = relative_path.relative_to("backend")
    except ValueError as exc:
        raise GovernanceAcceptanceError(
            f"backend pytest node is outside the backend build context: {node}"
        ) from exc
    if not container_path.is_relative_to(Path("app") / "tests"):
        raise GovernanceAcceptanceError(f"backend pytest node is outside app/tests: {node}")
    return f"{container_path.as_posix()}::{test_name}"


def _backend_nodes() -> tuple[str, ...]:
    return tuple(
        _container_backend_node(target.node)
        for plan in ACCEPTANCE_PLAN
        for target in plan.targets
        if target.executor == "backend_pytest"
    )


def _frontend_test_file(node: str) -> str:
    relative_path, _test_name = _split_test_node(node)
    try:
        frontend_path = relative_path.relative_to("frontend")
    except ValueError as exc:
        raise GovernanceAcceptanceError(
            f"vitest node is outside the frontend package: {node}"
        ) from exc
    if not frontend_path.is_relative_to("src"):
        raise GovernanceAcceptanceError(f"vitest node is outside frontend/src: {node}")
    return frontend_path.as_posix()


def _frontend_files() -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                _frontend_test_file(target.node)
                for plan in ACCEPTANCE_PLAN
                for target in plan.targets
                if target.executor == "frontend_vitest"
            }
        )
    )


def _frontend_report_nodes() -> tuple[str, ...]:
    return tuple(
        f"{_frontend_test_file(target.node)}::{_split_test_node(target.node)[1]}"
        for plan in ACCEPTANCE_PLAN
        for target in plan.targets
        if target.executor == "frontend_vitest"
    )


def _frontend_name_pattern() -> str:
    names = (_split_test_node(node)[1] for node in _frontend_report_nodes())
    return "(?:" + "|".join(re.escape(name) for name in names) + ")$"


def _resolve_executable(name: str) -> str:
    resolved = shutil.which(name)
    if resolved is None:
        raise GovernanceAcceptanceError(f"required executable is unavailable: {name}")
    return resolved


def _not_run_result(name: str, reason: str) -> ProcessResult:
    return ProcessResult(
        name=name,
        command=(),
        returncode=125,
        stdout=b"",
        stderr=reason.encode("ascii"),
        duration_ms=0,
    )


def _report_closure(
    *,
    report_path: Path,
    expected_nodes: Sequence[str],
    cases: Sequence[tuple[str, bool]],
    structurally_valid: bool,
) -> ReportClosure:
    expected = set(expected_nodes)
    actual = {identity for identity, _passed in cases}
    passed_cases = sum(1 for _identity, passed in cases if passed)
    nonpassed_cases = len(cases) - passed_cases
    if not structurally_valid:
        reason = "invalid_report"
    elif actual != expected:
        reason = "target_identity_mismatch"
    elif nonpassed_cases:
        reason = "nonpassed_case"
    elif not cases:
        reason = "empty_report"
    else:
        reason = "passed"
    return ReportClosure(
        passed=reason == "passed",
        expected_targets=len(expected),
        executed_targets=len(actual & expected),
        total_cases=len(cases),
        passed_cases=passed_cases,
        nonpassed_cases=nonpassed_cases,
        report_sha256=_sha256(report_path.read_bytes()) if report_path.is_file() else None,
        reason=reason,
    )


def _pytest_report_closure(report_path: Path, expected_nodes: Sequence[str]) -> ReportClosure:
    try:
        root = ElementTree.fromstring(report_path.read_bytes())
    except (OSError, ElementTree.ParseError):
        return _report_closure(
            report_path=report_path,
            expected_nodes=expected_nodes,
            cases=(),
            structurally_valid=False,
        )

    cases: list[tuple[str, bool]] = []
    structurally_valid = True
    for testcase in root.findall(".//testcase"):
        classname = testcase.get("classname")
        name = testcase.get("name")
        if not classname or not name:
            structurally_valid = False
            continue
        function_name = name.partition("[")[0]
        identity = f"{classname.replace('.', '/')}.py::{function_name}"
        nonpassed = any(
            testcase.find(result_tag) is not None for result_tag in ("failure", "error", "skipped")
        )
        cases.append((identity, not nonpassed))
    return _report_closure(
        report_path=report_path,
        expected_nodes=expected_nodes,
        cases=cases,
        structurally_valid=structurally_valid,
    )


def _vitest_report_closure(
    report_path: Path,
    expected_nodes: Sequence[str],
    frontend_root: Path,
) -> ReportClosure:
    try:
        decoded: Any = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return _report_closure(
            report_path=report_path,
            expected_nodes=expected_nodes,
            cases=(),
            structurally_valid=False,
        )
    if not isinstance(decoded, dict):
        return _report_closure(
            report_path=report_path,
            expected_nodes=expected_nodes,
            cases=(),
            structurally_valid=False,
        )

    payload = cast(dict[str, Any], decoded)
    raw_results = payload.get("testResults")
    if not isinstance(raw_results, list):
        return _report_closure(
            report_path=report_path,
            expected_nodes=expected_nodes,
            cases=(),
            structurally_valid=False,
        )

    cases: list[tuple[str, bool]] = []
    structurally_valid = True
    resolved_frontend = frontend_root.resolve()
    for raw_result in raw_results:
        if not isinstance(raw_result, dict):
            structurally_valid = False
            continue
        result = cast(dict[str, Any], raw_result)
        raw_name = result.get("name")
        assertions = result.get("assertionResults")
        if not isinstance(raw_name, str) or not isinstance(assertions, list):
            structurally_valid = False
            continue
        result_path = Path(raw_name)
        if not result_path.is_absolute():
            result_path = frontend_root / result_path
        try:
            relative_file = result_path.resolve().relative_to(resolved_frontend).as_posix()
        except ValueError:
            structurally_valid = False
            continue
        for raw_assertion in assertions:
            if not isinstance(raw_assertion, dict):
                structurally_valid = False
                continue
            assertion = cast(dict[str, Any], raw_assertion)
            title = assertion.get("title")
            status = assertion.get("status")
            if not isinstance(title, str) or not isinstance(status, str):
                structurally_valid = False
                continue
            cases.append((f"{relative_file}::{title}", status == "passed"))

    expected = set(expected_nodes)
    target_cases = [(identity, passed) for identity, passed in cases if identity in expected]
    unexpected_passed = any(identity not in expected and passed for identity, passed in cases)
    passed_cases = sum(1 for _identity, passed in cases if passed)
    total_count = payload.get("numTotalTests")
    passed_count = payload.get("numPassedTests")
    failed_count = payload.get("numFailedTests")
    pending_count = payload.get("numPendingTests")
    todo_count = payload.get("numTodoTests")
    counts = (passed_count, failed_count, pending_count, todo_count)
    top_level_valid = (
        payload.get("success") is True
        and type(total_count) is int
        and all(type(value) is int for value in counts)
        and total_count == sum(cast(int, value) for value in counts)
        and total_count == len(cases)
        and passed_count == passed_cases
        and failed_count == 0
        and not unexpected_passed
    )
    return _report_closure(
        report_path=report_path,
        expected_nodes=expected_nodes,
        cases=target_cases,
        structurally_valid=structurally_valid and top_level_valid,
    )


def _report_evidence(closure: ReportClosure) -> dict[str, object]:
    return {
        "passed": closure.passed,
        "expected_targets": closure.expected_targets,
        "executed_targets": closure.executed_targets,
        "total_cases": closure.total_cases,
        "passed_cases": closure.passed_cases,
        "nonpassed_cases": closure.nonpassed_cases,
        "report_sha256": closure.report_sha256,
        "reason": closure.reason,
        "raw_report_archived": False,
    }


def _plan_evidence(
    plan: AcceptancePlan,
    *,
    backend_passed: bool,
    frontend_passed: bool,
    shared_gates_passed: bool,
) -> dict[str, object]:
    executors = {target.executor for target in plan.targets}
    executor_passed = ("backend_pytest" not in executors or backend_passed) and (
        "frontend_vitest" not in executors or frontend_passed
    )
    return {
        "acceptance_id": plan.acceptance_id,
        "scope": plan.scope,
        "status": "passed" if executor_passed and shared_gates_passed else "failed",
        "targets": [
            {
                "executor": target.executor,
                "node": target.node,
                "assertion": target.assertion,
            }
            for target in plan.targets
        ],
    }


def _resource_check_passed(result: ProcessResult) -> bool:
    return result.returncode == 0 and not result.stdout.strip()


def _final_status(
    *,
    compose_bound: bool,
    build_passed: bool,
    image_bound: bool,
    backend_passed: bool,
    frontend_passed: bool,
    cleanup_passed: bool,
    candidate_unchanged: bool,
) -> str:
    if all(
        (
            compose_bound,
            build_passed,
            image_bound,
            backend_passed,
            frontend_passed,
            cleanup_passed,
            candidate_unchanged,
        )
    ):
        return "candidate_passed"
    return "failed"


def _seal_evidence(output_dir: Path, evidence: dict[str, object]) -> None:
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        payload = (json.dumps(evidence, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        evidence_path = temporary / "evidence.json"
        evidence_path.write_bytes(payload)
        (temporary / "manifest.sha256").write_text(
            f"{_sha256(payload)}  evidence.json\n",
            encoding="utf-8",
            newline="\n",
        )
        temporary.replace(output_dir)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _runtime_names(expected_sha: str) -> tuple[str, str]:
    run_token = secrets.token_hex(4)
    project_name = f"ku-gov-{expected_sha[:12]}-{run_token}"
    image_tag = f"knowledge-uploader-governance:{expected_sha[:12]}-{run_token}"
    return project_name, image_tag


def _safe_candidate_identity(repo_root: Path) -> CandidateIdentity | None:
    try:
        return candidate_identity(repo_root)
    except (GovernanceAcceptanceError, UnicodeError, OSError):
        return None


def _result_ascii(result: ProcessResult) -> str | None:
    if result.returncode != 0:
        return None
    try:
        return result.stdout.decode("ascii").strip()
    except UnicodeError:
        return None


def _worktree_absent(result: ProcessResult, candidate_root: Path) -> bool:
    if result.returncode != 0:
        return False
    expected = candidate_root.resolve()
    for line in result.stdout.decode("utf-8", errors="replace").splitlines():
        if not line.startswith("worktree "):
            continue
        listed = Path(line.removeprefix("worktree ")).resolve()
        if listed == expected:
            return False
    return True


def _remove_tree_result(name: str, target: Path) -> ProcessResult:
    started = time.monotonic()
    try:
        if target.exists():
            shutil.rmtree(target)
        passed = not target.exists()
        return ProcessResult(
            name=name,
            command=(),
            returncode=0 if passed else 1,
            stdout=b"",
            stderr=b"" if passed else b"tree_still_exists",
            duration_ms=round((time.monotonic() - started) * 1000),
        )
    except OSError as exc:
        return ProcessResult(
            name=name,
            command=(),
            returncode=1,
            stdout=b"",
            stderr=type(exc).__name__.encode("ascii"),
            duration_ms=round((time.monotonic() - started) * 1000),
        )


def _execute(*, repo_root: Path, expected_sha: str, output_dir: Path) -> int:
    expected_sha = _validate_expected_sha(expected_sha)
    _validate_test_database_name(TEST_DATABASE_NAME)
    output_dir = _validate_output_dir(repo_root, output_dir)
    _validate_test_targets(repo_root)
    npm_executable = _resolve_executable("npm")
    docker_executable = _resolve_executable("docker")
    git_executable = _resolve_executable("git")
    before = candidate_identity(repo_root)
    _assert_candidate(before, expected_sha)
    expected_tree = (
        _git_bytes(repo_root, "rev-parse", f"{expected_sha}^{{tree}}").decode("ascii").strip()
    )
    if not EXPECTED_SHA_PATTERN.fullmatch(expected_tree):
        raise GovernanceAcceptanceError("candidate Git tree identity is invalid")
    expected_compose_source_sha256 = _sha256(
        _git_bytes(repo_root, "show", f"{expected_sha}:docker-compose.yml")
    )

    project_name, image_tag = _runtime_names(expected_sha)
    runtime_root = Path(tempfile.mkdtemp(prefix=f"{project_name}-"))
    candidate_root = runtime_root / "candidate"
    reports_root = runtime_root / "reports"
    reports_root.mkdir()
    backend_report_path = reports_root / "backend.junit.xml"
    frontend_report_path = reports_root / "frontend.vitest.json"
    environment, removed_compose_environment = _sanitized_environment(dict(os.environ))
    environment.update(
        {
            "APP_ENV": "test",
            "BACKEND_BUILD_TARGET": "development",
            "BACKEND_IMAGE": image_tag,
            "VCS_REF": expected_sha,
        }
    )
    compose = _compose_prefix(docker_executable, project_name, candidate_root)
    phases: list[ProcessResult] = []
    backend_expected = _backend_nodes()
    frontend_expected = _frontend_report_nodes()
    backend_report = _report_closure(
        report_path=backend_report_path,
        expected_nodes=backend_expected,
        cases=(),
        structurally_valid=False,
    )
    frontend_report = _report_closure(
        report_path=frontend_report_path,
        expected_nodes=frontend_expected,
        cases=(),
        structurally_valid=False,
    )
    worktree_add = _not_run_result("candidate_worktree_add", "not_run")
    tree_inspect = _not_run_result("candidate_tree_identity", "not_run")
    compose_config_before = _not_run_result("compose_config_before", "not_run")
    compose_config_after = _not_run_result("compose_config_after", "not_run")
    build = _not_run_result("backend_image_build", "not_run")
    image_inspect = _not_run_result("backend_image_revision", "not_run")
    backend = _not_run_result("backend_governance_pytest", "not_run")
    frontend_install = _not_run_result("frontend_npm_ci", "not_run")
    frontend = _not_run_result("frontend_notification_vitest", "not_run")
    candidate_before: CandidateIdentity | None = None
    candidate_after: CandidateIdentity | None = None
    candidate_tree_hash: str | None = None
    candidate_source_sha256_before: str | None = None
    candidate_source_sha256_after: str | None = None
    candidate_bound = False
    compose_bound = False
    candidate_targets_valid = False
    image_bound = False
    image_revision = ""
    internal_error = False

    try:
        worktree_add = _run_process(
            "candidate_worktree_add",
            (
                git_executable,
                "worktree",
                "add",
                "--detach",
                str(candidate_root),
                expected_sha,
            ),
            cwd=repo_root,
            env=environment,
            timeout_seconds=180,
        )
        phases.append(worktree_add)
        if worktree_add.returncode == 0:
            candidate_before = _safe_candidate_identity(candidate_root)
            try:
                _validate_test_targets(candidate_root)
                candidate_targets_valid = True
            except GovernanceAcceptanceError:
                candidate_targets_valid = False
            tree_inspect = _run_process(
                "candidate_tree_identity",
                (git_executable, "-C", str(candidate_root), "rev-parse", "HEAD^{tree}"),
                cwd=repo_root,
                env=environment,
                timeout_seconds=30,
            )
            candidate_tree_hash = _result_ascii(tree_inspect)
            candidate_source_sha256_before = _file_sha256(candidate_root / "docker-compose.yml")
        phases.append(tree_inspect)
        candidate_bound = (
            candidate_targets_valid
            and candidate_before is not None
            and candidate_before.git_sha == expected_sha
            and candidate_before.clean
            and candidate_tree_hash == expected_tree
            and candidate_source_sha256_before == expected_compose_source_sha256
        )

        if candidate_bound:
            compose_config_before = _run_process(
                "compose_config_before",
                (*compose, "config"),
                cwd=candidate_root,
                env=environment,
                timeout_seconds=120,
            )
        else:
            compose_config_before = _not_run_result("compose_config_before", "candidate_not_bound")
        phases.append(compose_config_before)
        compose_ready = candidate_bound and _config_digest(compose_config_before) is not None

        if compose_ready:
            build = _run_process(
                "backend_image_build",
                (*compose, "build", "--pull=false", "backend-api"),
                cwd=candidate_root,
                env=environment,
                timeout_seconds=900,
            )
        else:
            build = _not_run_result("backend_image_build", "candidate_not_bound")
        phases.append(build)

        if build.returncode == 0:
            image_inspect = _run_process(
                "backend_image_revision",
                (
                    docker_executable,
                    "image",
                    "inspect",
                    image_tag,
                    '--format={{ index .Config.Labels "org.opencontainers.image.revision" }}',
                ),
                cwd=candidate_root,
                env=environment,
                timeout_seconds=60,
            )
        else:
            image_inspect = _not_run_result("backend_image_revision", "build_failed")
        phases.append(image_inspect)
        image_revision = _result_ascii(image_inspect) or ""
        image_bound = image_revision == expected_sha

        if build.returncode == 0 and image_bound:
            report_mount = f"{reports_root.resolve().as_posix()}:/acceptance-reports"
            backend = _run_process(
                "backend_governance_pytest",
                (
                    *compose,
                    "run",
                    "--rm",
                    "--volume",
                    report_mount,
                    "-e",
                    f"TEST_DATABASE_NAME={TEST_DATABASE_NAME}",
                    "-e",
                    "TEST_CACHE_REDIS_URL=redis://redis:6379/15",
                    "-e",
                    "APP_ENV=test",
                    "backend-api",
                    "pytest",
                    "-q",
                    "--disable-warnings",
                    "--junitxml=/acceptance-reports/backend.junit.xml",
                    "-o",
                    "junit_family=xunit2",
                    "-o",
                    "xfail_strict=true",
                    *backend_expected,
                ),
                cwd=candidate_root,
                env=environment,
                timeout_seconds=900,
            )
            backend_report = _pytest_report_closure(backend_report_path, backend_expected)
        else:
            backend = _not_run_result("backend_governance_pytest", "image_not_candidate_bound")
        phases.append(backend)

        if compose_ready:
            frontend_install = _run_process(
                "frontend_npm_ci",
                (
                    npm_executable,
                    "--prefix",
                    str(candidate_root / "frontend"),
                    "ci",
                    "--prefer-offline",
                    "--no-audit",
                    "--no-fund",
                ),
                cwd=candidate_root,
                env=environment,
                timeout_seconds=900,
            )
        else:
            frontend_install = _not_run_result("frontend_npm_ci", "candidate_not_bound")
        phases.append(frontend_install)

        if frontend_install.returncode == 0:
            frontend = _run_process(
                "frontend_notification_vitest",
                (
                    npm_executable,
                    "--prefix",
                    str(candidate_root / "frontend"),
                    "run",
                    "test:run",
                    "--",
                    *_frontend_files(),
                    "--reporter=json",
                    f"--outputFile={frontend_report_path}",
                    "--testNamePattern",
                    _frontend_name_pattern(),
                ),
                cwd=candidate_root,
                env=environment,
                timeout_seconds=600,
            )
            frontend_report = _vitest_report_closure(
                frontend_report_path,
                frontend_expected,
                candidate_root / "frontend",
            )
        else:
            frontend = _not_run_result("frontend_notification_vitest", "npm_ci_failed")
        phases.append(frontend)
        candidate_after = _safe_candidate_identity(candidate_root)
        candidate_source_sha256_after = _file_sha256(candidate_root / "docker-compose.yml")
        if candidate_bound:
            compose_config_after = _run_process(
                "compose_config_after",
                (*compose, "config"),
                cwd=candidate_root,
                env=environment,
                timeout_seconds=120,
            )
        else:
            compose_config_after = _not_run_result("compose_config_after", "candidate_not_bound")
        phases.append(compose_config_after)
    except Exception as exc:
        internal_error = True
        phases.append(
            ProcessResult(
                name="runner_internal_error",
                command=(),
                returncode=1,
                stdout=b"",
                stderr=type(exc).__name__.encode("ascii", errors="replace"),
                duration_ms=0,
            )
        )
    finally:
        if candidate_after is None and candidate_root.is_dir():
            candidate_after = _safe_candidate_identity(candidate_root)
        cleanup_cwd = (
            candidate_root if (candidate_root / "docker-compose.yml").is_file() else repo_root
        )
        down = _run_process(
            "compose_down",
            (*compose, "down", "--volumes", "--remove-orphans", "--timeout", "30"),
            cwd=cleanup_cwd,
            env=environment,
            timeout_seconds=180,
        )
        phases.append(down)
        container_check = _run_process(
            "compose_container_cleanup_check",
            (
                docker_executable,
                "ps",
                "-aq",
                "--filter",
                f"label=com.docker.compose.project={project_name}",
            ),
            cwd=repo_root,
            env=environment,
            timeout_seconds=60,
        )
        phases.append(container_check)
        volume_check = _run_process(
            "compose_volume_cleanup_check",
            (
                docker_executable,
                "volume",
                "ls",
                "-q",
                "--filter",
                f"label=com.docker.compose.project={project_name}",
            ),
            cwd=repo_root,
            env=environment,
            timeout_seconds=60,
        )
        phases.append(volume_check)
        network_check = _run_process(
            "compose_network_cleanup_check",
            (
                docker_executable,
                "network",
                "ls",
                "-q",
                "--filter",
                f"label=com.docker.compose.project={project_name}",
            ),
            cwd=repo_root,
            env=environment,
            timeout_seconds=60,
        )
        phases.append(network_check)
        image_remove = _run_process(
            "candidate_image_remove",
            (docker_executable, "image", "rm", image_tag),
            cwd=repo_root,
            env=environment,
            timeout_seconds=180,
        )
        phases.append(image_remove)
        image_absent = _run_process(
            "candidate_image_cleanup_check",
            (docker_executable, "image", "ls", "-q", image_tag),
            cwd=repo_root,
            env=environment,
            timeout_seconds=60,
        )
        phases.append(image_absent)
        worktree_remove = _run_process(
            "candidate_worktree_remove",
            (git_executable, "worktree", "remove", "--force", str(candidate_root)),
            cwd=repo_root,
            env=environment,
            timeout_seconds=300,
        )
        phases.append(worktree_remove)
        candidate_fallback_remove = _remove_tree_result(
            "candidate_tree_fallback_remove",
            candidate_root,
        )
        phases.append(candidate_fallback_remove)
        worktree_list = _run_process(
            "candidate_worktree_cleanup_check",
            (git_executable, "worktree", "list", "--porcelain"),
            cwd=repo_root,
            env=environment,
            timeout_seconds=60,
        )
        phases.append(worktree_list)
        runtime_remove = _remove_tree_result("runtime_report_tree_remove", runtime_root)
        phases.append(runtime_remove)

    candidate_tree_unchanged = (
        candidate_after is not None
        and candidate_after.git_sha == expected_sha
        and candidate_after.clean
    )
    compose_bound = _compose_binding_passed(
        expected_source_sha256=expected_compose_source_sha256,
        source_sha256_before=candidate_source_sha256_before,
        source_sha256_after=candidate_source_sha256_after,
        config_before=compose_config_before,
        config_after=compose_config_after,
    )
    image_removed = _resource_check_passed(image_absent)
    worktree_removed = candidate_fallback_remove.returncode == 0 and _worktree_absent(
        worktree_list, candidate_root
    )
    reports_removed = runtime_remove.returncode == 0 and not runtime_root.exists()
    cleanup_passed = (
        down.returncode == 0
        and _resource_check_passed(container_check)
        and _resource_check_passed(volume_check)
        and _resource_check_passed(network_check)
        and image_removed
        and worktree_removed
        and reports_removed
    )
    after = _safe_candidate_identity(repo_root)
    candidate_unchanged = (
        after == before
        and candidate_tree_unchanged
        and candidate_bound
        and compose_bound
        and not internal_error
    )
    build_passed = build.returncode == 0
    backend_passed = backend.returncode == 0 and backend_report.passed
    frontend_passed = (
        frontend_install.returncode == 0 and frontend.returncode == 0 and frontend_report.passed
    )
    status = _final_status(
        compose_bound=compose_bound,
        build_passed=build_passed,
        image_bound=image_bound,
        backend_passed=backend_passed,
        frontend_passed=frontend_passed,
        cleanup_passed=cleanup_passed,
        candidate_unchanged=candidate_unchanged,
    )
    shared_gates_passed = (
        candidate_bound
        and compose_bound
        and build_passed
        and image_bound
        and cleanup_passed
        and candidate_unchanged
    )
    generated_at = datetime.now(UTC).isoformat()
    evidence: dict[str, object] = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "status": status,
        "generated_at": generated_at,
        "candidate": {
            "expected_git_sha": expected_sha,
            "git_sha_before": before.git_sha,
            "git_sha_after": after.git_sha if after is not None else None,
            "worktree_clean_before": before.clean,
            "worktree_clean_after": after.clean if after is not None else False,
            "status_command": "git status --porcelain=v1 --untracked-files=all",
            "candidate_unchanged": candidate_unchanged,
        },
        "candidate_source": {
            "expected_tree_sha": expected_tree,
            "executed_tree_sha": candidate_tree_hash,
            "detached_worktree_bound": candidate_bound,
            "exact_targets_validated_in_detached_tree": candidate_targets_valid,
            "detached_worktree_clean_after": candidate_tree_unchanged,
            "detached_worktree_removed": worktree_removed,
            "expected_compose_source_sha256": expected_compose_source_sha256,
            "compose_source_sha256_before": candidate_source_sha256_before,
            "compose_source_sha256_after": candidate_source_sha256_after,
            "compose_config_sha256_before": _config_digest(compose_config_before),
            "compose_config_sha256_after": _config_digest(compose_config_after),
            "compose_binding_passed": compose_bound,
            "compose_project_directory_bound": True,
            "compose_files": ["docker-compose.yml"],
            "compose_environment_keys_removed": removed_compose_environment,
        },
        "runtime_isolation": {
            "compose_project": project_name,
            "postgres_database": TEST_DATABASE_NAME,
            "postgres_database_suffix_guard": "_test",
            "redis_database": TEST_REDIS_DB,
            "isolated_compose_project": True,
            "containers_removed": _resource_check_passed(container_check),
            "volumes_removed": _resource_check_passed(volume_check),
            "networks_removed": _resource_check_passed(network_check),
            "reports_removed": reports_removed,
            "cleanup_passed": cleanup_passed,
        },
        "candidate_image": {
            "tag": image_tag,
            "oci_revision": image_revision if image_bound else None,
            "revision_bound": image_bound,
            "removed_after_run": image_removed,
        },
        "machine_reports": {
            "backend_junit": _report_evidence(backend_report),
            "frontend_vitest_json": _report_evidence(frontend_report),
        },
        "external_llm_verified": False,
        "protocol_substitute_only": True,
        "cost002_not_evaluated": True,
        "raw_logs": {
            "archived": False,
            "reason": "privacy boundary: prompt/original/key material is never sealed",
            "hash_algorithm": "sha256",
        },
        "acceptance": [
            _plan_evidence(
                plan,
                backend_passed=backend_passed,
                frontend_passed=frontend_passed,
                shared_gates_passed=shared_gates_passed,
            )
            for plan in ACCEPTANCE_PLAN
        ],
        "phases": [_phase_evidence(phase) for phase in phases],
        "atomic_seal": {
            "output_directory_preexisted": False,
            "directory_replace": True,
            "manifest_algorithm": "sha256",
        },
    }
    _seal_evidence(output_dir, evidence)
    return 0 if status == "candidate_passed" else 1


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-git-sha", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    arguments = _parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    try:
        return _execute(
            repo_root=repo_root,
            expected_sha=str(arguments.expected_git_sha),
            output_dir=arguments.output_dir,
        )
    except GovernanceAcceptanceError as exc:
        sys.stderr.write(f"governance acceptance refused: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
