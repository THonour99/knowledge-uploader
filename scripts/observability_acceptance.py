# ruff: noqa: E402, PTH118, PTH120 -- isolation precedes imports.

"""Candidate-bound local OBS-001 Prometheus acceptance implementation."""

from __future__ import annotations

import importlib.util
import os
import sys
from typing import Protocol, cast


class _AcceptanceEntry(Protocol):
    def consume_launcher_claim(self, repo_root: str) -> None: ...

    def runtime_isolation_error(self, repo_root: str) -> str | None: ...


def _load_acceptance_entry() -> _AcceptanceEntry:
    module_path = os.path.join(os.path.dirname(__file__), "acceptance_entry.py")
    spec = importlib.util.spec_from_file_location(
        "knowledge_uploader_observability_acceptance_entry",
        module_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("acceptance entry helper loader unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return cast(_AcceptanceEntry, module)


_acceptance_entry = _load_acceptance_entry()
_LAUNCHER_CLAIM_CONSUMED = False

if __name__ == "__main__":
    _repository = os.path.realpath(os.path.join(os.path.dirname(__file__), os.pardir))
    try:
        _acceptance_entry.consume_launcher_claim(_repository)
        _LAUNCHER_CLAIM_CONSUMED = True
    except RuntimeError as _claim_error:
        raise SystemExit(f"observability acceptance refused: {_claim_error}") from _claim_error
    import site

    site.main()
    if sys.argv[1:] == ["--isolation-probe"]:
        sys.stdout.write("observability Python isolation verified\n")
        raise SystemExit(0)


import argparse
import hashlib
import json
import re
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import yaml  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[1]


class _GitSnapshot(Protocol):
    head: str
    status: str


class _VerifyGitSnapshot(Protocol):
    def __call__(
        self,
        repo_root: Path,
        *,
        expected_sha: str,
        relative_paths: tuple[Path, ...],
        source_sha256: dict[str, str],
    ) -> _GitSnapshot: ...


def _load_acceptance_git() -> ModuleType:
    module_path = ROOT / "scripts" / "acceptance_git.py"
    spec = importlib.util.spec_from_file_location(
        "knowledge_uploader_observability_acceptance_git",
        module_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("acceptance Git helper loader unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_acceptance_git = _load_acceptance_git()
AcceptanceGitError = cast(type[RuntimeError], _acceptance_git.AcceptanceGitError)
verify_git_snapshot = cast(_VerifyGitSnapshot, _acceptance_git.verify_git_snapshot)

RUNNER_PATH = ROOT / "scripts" / "run_observability_acceptance.py"
ACCEPTANCE_GIT_PATH = ROOT / "scripts" / "acceptance_git.py"
ACCEPTANCE_ENTRY_PATH = ROOT / "scripts" / "acceptance_entry.py"
ACCEPTANCE_LAUNCHER_PATH = ROOT / "scripts" / "acceptance_launcher.py"
COMPOSE_PATH = ROOT / "ops" / "observability" / "acceptance.compose.yml"
ALERTS_PATH = ROOT / "ops" / "observability" / "alerts.yml"
ALERT_TEST_PATH = ROOT / "ops" / "observability" / "alerts.test.yml"
PROMETHEUS_PATH = ROOT / "ops" / "observability" / "prometheus.yml"
RUNBOOK_PATH = ROOT / "ops" / "runbooks" / "observability.md"
PROMETHEUS_MANIFEST_DIGEST = (
    "sha256:69f5241418838263316593f7274a304b095c40bcf22e57272865da91bd60a8ac"
)
PROMETHEUS_IMAGE = f"prom/prometheus:v3.12.0@{PROMETHEUS_MANIFEST_DIGEST}"
PROMETHEUS_REPOSITORY_DIGEST = f"prom/prometheus@{PROMETHEUS_MANIFEST_DIGEST}"
NODE_EXPORTER_MANIFEST_DIGEST = (
    "sha256:0f422f62c15f154af8d8572b23d623aebfb10cec73a5c654d18f911f3f9df241"
)
NODE_EXPORTER_IMAGE = f"quay.io/prometheus/node-exporter:v1.11.1@{NODE_EXPORTER_MANIFEST_DIGEST}"
NODE_EXPORTER_REPOSITORY_DIGEST = (
    f"quay.io/prometheus/node-exporter@{NODE_EXPORTER_MANIFEST_DIGEST}"
)
APPROVED_IMAGE_CONTRACTS = (
    (
        "prometheus",
        "prometheus",
        PROMETHEUS_IMAGE,
        PROMETHEUS_MANIFEST_DIGEST,
        PROMETHEUS_REPOSITORY_DIGEST,
    ),
    (
        "metrics-fixture",
        "node_exporter",
        NODE_EXPORTER_IMAGE,
        NODE_EXPORTER_MANIFEST_DIGEST,
        NODE_EXPORTER_REPOSITORY_DIGEST,
    ),
)
EVIDENCE_SCHEMA = "knowledge-uploader.observability-local-evidence.v1"
EVIDENCE_TTL_SECONDS = 24 * 60 * 60
EXPECTED_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
EVALUATION_INTERVAL_SECONDS = 5
CONTAINER_ID_PATTERN = re.compile(r"^[0-9a-f]{64}$")
IMAGE_ID_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
PROMETHEUS_JOB = "observability-acceptance"


class ObservabilityAcceptanceError(RuntimeError):
    """A safe, named OBS-001 acceptance failure."""

    def __init__(self, step: str) -> None:
        super().__init__(step)
        self.step = step


def _require_isolated_runtime() -> None:
    if not _LAUNCHER_CLAIM_CONSUMED:
        raise ObservabilityAcceptanceError("launcher_claim")
    if _acceptance_entry.runtime_isolation_error(str(ROOT)) is not None:
        raise ObservabilityAcceptanceError("python_isolation")


@dataclass(frozen=True)
class AlertContract:
    name: str
    configured_for: str
    configured_for_seconds: int
    runbook: str


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
class RuleSnapshot:
    name: str
    state: str
    health: str
    duration_seconds: int
    active_at: str | None
    runbook: str | None


ALERT_CONTRACTS = (
    AlertContract(
        name="KnowledgeUploaderOutboxBacklog",
        configured_for="10m",
        configured_for_seconds=600,
        runbook="ops/runbooks/observability.md#knowledgeuploaderoutboxbacklog",
    ),
    AlertContract(
        name="KnowledgeUploaderDocumentWorkerOffline",
        configured_for="2m",
        configured_for_seconds=120,
        runbook="ops/runbooks/observability.md#knowledgeuploaderworkeroffline",
    ),
    AlertContract(
        name="KnowledgeUploaderReviewSlaOverdue",
        configured_for="5m",
        configured_for_seconds=300,
        runbook="ops/runbooks/observability.md#knowledgeuploaderreviewslaoverdue",
    ),
    AlertContract(
        name="KnowledgeUploaderRagflowSyncFailureRateHigh",
        configured_for="5m",
        configured_for_seconds=300,
        runbook="ops/runbooks/observability.md#knowledgeuploaderragflowsyncfailureratehigh",
    ),
)
ALERT_BY_NAME = {contract.name: contract for contract in ALERT_CONTRACTS}
REQUIRED_PHASES = frozenset(
    {
        "compose_contract",
        "promtool_production_config",
        "promtool_production_rules",
        "promtool_rule_transitions",
        "compose_up",
        "prometheus_container_lookup",
        "prometheus_container_identity",
        "prometheus_image_identity",
        "node_exporter_container_lookup",
        "node_exporter_container_identity",
        "node_exporter_image_identity",
        "compose_down",
        "container_cleanup",
        "volume_cleanup",
        "network_cleanup",
    }
)
SOURCE_PATHS = (
    ACCEPTANCE_GIT_PATH,
    ACCEPTANCE_ENTRY_PATH,
    ACCEPTANCE_LAUNCHER_PATH,
    COMPOSE_PATH,
    ALERTS_PATH,
    ALERT_TEST_PATH,
    PROMETHEUS_PATH,
    RUNBOOK_PATH,
    RUNNER_PATH,
    Path(__file__).resolve(),
)
FIRING_METRICS = """# OBS-001 aggregate-only fixture
knowledge_uploader_outbox_pending 101
rabbitmq_detailed_queue_consumers{queue="document_queue"} 0
knowledge_uploader_review_overdue 1
knowledge_uploader_ragflow_sync_outcomes_window{result="success"} 4
knowledge_uploader_ragflow_sync_outcomes_window{result="failure"} 1
"""
RESOLVED_METRICS = """# OBS-001 aggregate-only fixture
knowledge_uploader_outbox_pending 0
rabbitmq_detailed_queue_consumers{queue="document_queue"} 1
knowledge_uploader_review_overdue 0
knowledge_uploader_ragflow_sync_outcomes_window{result="success"} 5
knowledge_uploader_ragflow_sync_outcomes_window{result="failure"} 0
"""
FIRING_QUERIES = (
    ("knowledge_uploader_outbox_pending", 101.0),
    ('rabbitmq_detailed_queue_consumers{queue="document_queue"}', 0.0),
    ("knowledge_uploader_review_overdue", 1.0),
    ('knowledge_uploader_ragflow_sync_outcomes_window{result="success"}', 4.0),
    ('knowledge_uploader_ragflow_sync_outcomes_window{result="failure"}', 1.0),
)
RESOLVED_QUERIES = (
    ("knowledge_uploader_outbox_pending", 0.0),
    ('rabbitmq_detailed_queue_consumers{queue="document_queue"}', 1.0),
    ("knowledge_uploader_review_overdue", 0.0),
    ('knowledge_uploader_ragflow_sync_outcomes_window{result="success"}', 5.0),
    ('knowledge_uploader_ragflow_sync_outcomes_window{result="failure"}', 0.0),
)


def _mapping(value: object, step: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ObservabilityAcceptanceError(step)
    return cast(dict[str, object], value)


def _sequence(value: object, step: str) -> list[object]:
    if not isinstance(value, list):
        raise ObservabilityAcceptanceError(step)
    return cast(list[object], value)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _source_hashes() -> dict[str, str]:
    return {
        path.relative_to(ROOT).as_posix(): _sha256_bytes(path.read_bytes()) for path in SOURCE_PATHS
    }


def _validate_expected_sha(value: str) -> str:
    normalized = value.strip().lower()
    if EXPECTED_SHA_PATTERN.fullmatch(normalized) is None:
        raise ObservabilityAcceptanceError("expected_git_sha")
    return normalized


def candidate_identity(expected_sha: str) -> CandidateIdentity:
    try:
        snapshot = verify_git_snapshot(
            ROOT,
            expected_sha=expected_sha,
            relative_paths=tuple(path.relative_to(ROOT) for path in SOURCE_PATHS),
            source_sha256=_source_hashes(),
        )
    except AcceptanceGitError as error:
        raise ObservabilityAcceptanceError("git_identity") from error
    return CandidateIdentity(git_sha=snapshot.head, porcelain_v1_all=snapshot.status)


def _assert_candidate(identity: CandidateIdentity, expected_sha: str) -> None:
    if identity.git_sha != expected_sha:
        raise ObservabilityAcceptanceError("candidate_sha")
    if not identity.clean:
        raise ObservabilityAcceptanceError("candidate_worktree")


def _validate_output_dir(output_dir: Path) -> Path:
    if not output_dir.is_absolute():
        raise ObservabilityAcceptanceError("evidence_output_absolute")
    resolved = output_dir.resolve()
    root = ROOT.resolve()
    if resolved == root or resolved.is_relative_to(root):
        raise ObservabilityAcceptanceError("evidence_output_external")
    if resolved.exists():
        raise ObservabilityAcceptanceError("evidence_output_new")
    return resolved


def _heading_anchor(heading: str) -> str:
    return re.sub(r"[^a-z0-9-]", "", heading.strip().lower().replace(" ", "-"))


def _static_contract() -> list[dict[str, object]]:
    payload = _mapping(yaml.safe_load(ALERTS_PATH.read_text(encoding="utf-8")), "alerts_yaml")
    groups = _sequence(payload.get("groups"), "alerts_groups")
    found: dict[str, dict[str, object]] = {}
    for raw_group in groups:
        group = _mapping(raw_group, "alerts_group")
        for raw_rule in _sequence(group.get("rules"), "alerts_rules"):
            rule = _mapping(raw_rule, "alert_rule")
            name = rule.get("alert")
            if isinstance(name, str) and name in ALERT_BY_NAME:
                if name in found:
                    raise ObservabilityAcceptanceError("target_rule_duplicate")
                found[name] = rule

    headings = {
        _heading_anchor(line[3:])
        for line in RUNBOOK_PATH.read_text(encoding="utf-8").splitlines()
        if line.startswith("## ")
    }
    evidence: list[dict[str, object]] = []
    for contract in ALERT_CONTRACTS:
        target_rule = found.get(contract.name)
        if target_rule is None:
            raise ObservabilityAcceptanceError("target_rule_missing")
        annotations = _mapping(target_rule.get("annotations"), "target_rule_annotations")
        expression = target_rule.get("expr")
        runbook = annotations.get("runbook")
        if (
            target_rule.get("for") != contract.configured_for
            or not isinstance(expression, str)
            or not expression.strip()
            or runbook != contract.runbook
        ):
            raise ObservabilityAcceptanceError("target_rule_contract")
        anchor = contract.runbook.partition("#")[2]
        if not anchor or anchor not in headings:
            raise ObservabilityAcceptanceError("runbook_anchor")

        evidence.append(
            {
                "name": contract.name,
                "configured_for": contract.configured_for,
                "configured_for_seconds": contract.configured_for_seconds,
                "expression_sha256": _sha256_bytes(expression.strip().encode("utf-8")),
                "runbook": contract.runbook,
                "runbook_anchor_present": True,
            }
        )

    test_payload = _mapping(
        yaml.safe_load(ALERT_TEST_PATH.read_text(encoding="utf-8")),
        "alert_test_yaml",
    )
    observations: dict[str, list[tuple[int, bool]]] = {
        contract.name: [] for contract in ALERT_CONTRACTS
    }
    for raw_test in _sequence(test_payload.get("tests"), "alert_tests"):
        test = _mapping(raw_test, "alert_test")
        for raw_evaluation in _sequence(test.get("alert_rule_test"), "alert_rule_test"):
            evaluation = _mapping(raw_evaluation, "alert_evaluation")
            name = evaluation.get("alertname")
            if not isinstance(name, str) or name not in observations:
                continue
            raw_time = evaluation.get("eval_time")
            if not isinstance(raw_time, str) or not raw_time.endswith("m"):
                raise ObservabilityAcceptanceError("alert_test_time")
            seconds = int(raw_time[:-1]) * 60
            firing = bool(_sequence(evaluation.get("exp_alerts"), "expected_alerts"))
            observations[name].append((seconds, firing))

    for contract in ALERT_CONTRACTS:
        timeline = sorted(observations[contract.name])
        firing_times = [seconds for seconds, firing in timeline if firing]
        if not firing_times:
            raise ObservabilityAcceptanceError("alert_test_firing")
        first_firing = min(firing_times)
        has_pre_window = any(
            not firing and seconds < contract.configured_for_seconds for seconds, firing in timeline
        )
        has_resolution = any(not firing and seconds > first_firing for seconds, firing in timeline)
        if not has_pre_window or not has_resolution:
            raise ObservabilityAcceptanceError("alert_test_resolution")
    return evidence


def _run_process(
    name: str,
    command: Sequence[str],
    *,
    environment: dict[str, str],
    timeout_seconds: int,
) -> ProcessResult:
    started = time.monotonic()
    try:
        completed = subprocess.run(  # - argument vector, never a shell
            tuple(command),
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=True,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return ProcessResult(
            name=name,
            command=tuple(command),
            returncode=127,
            stdout=b"",
            stderr=type(exc).__name__.encode("ascii"),
            duration_ms=round((time.monotonic() - started) * 1000),
        )
    return ProcessResult(
        name=name,
        command=tuple(command),
        returncode=completed.returncode,
        stdout=bytes(completed.stdout),
        stderr=bytes(completed.stderr),
        duration_ms=round((time.monotonic() - started) * 1000),
    )


def _phase_evidence(result: ProcessResult) -> dict[str, object]:
    return {
        "name": result.name,
        "returncode": result.returncode,
        "duration_ms": result.duration_ms,
        "stdout_sha256": _sha256_bytes(result.stdout),
        "stderr_sha256": _sha256_bytes(result.stderr),
        "raw_output_archived": False,
    }


def _require(result: ProcessResult) -> None:
    if result.returncode != 0:
        raise ObservabilityAcceptanceError(result.name)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    temporary.replace(path)


def _prometheus_config() -> str:
    return """global:
  scrape_interval: 5s
  evaluation_interval: 5s

rule_files:
  - /etc/prometheus/alerts.yml

scrape_configs:
  - job_name: observability-acceptance
    metrics_path: /metrics
    static_configs:
      - targets:
          - metrics-fixture:9100
"""


def _api_json(base_url: str, path: str, *, query: str | None = None) -> dict[str, object]:
    if not base_url.startswith("http://127.0.0.1:"):
        raise ObservabilityAcceptanceError("prometheus_api_origin")
    url = f"{base_url}{path}"
    if query is not None:
        url = f"{url}?{urllib.parse.urlencode({'query': query})}"
    try:
        with urllib.request.urlopen(url, timeout=5) as response:  # - loopback only
            payload: object = json.loads(response.read())
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise ObservabilityAcceptanceError("prometheus_api") from exc
    result = _mapping(payload, "prometheus_api_payload")
    if result.get("status") != "success":
        raise ObservabilityAcceptanceError("prometheus_api_status")
    return result


def _target_snapshot(base_url: str) -> dict[str, object]:
    payload = _api_json(base_url, "/api/v1/targets")
    data = _mapping(payload.get("data"), "prometheus_targets_data")
    for raw_target in _sequence(data.get("activeTargets"), "prometheus_active_targets"):
        target = _mapping(raw_target, "prometheus_target")
        labels = _mapping(target.get("labels"), "prometheus_target_labels")
        if labels.get("job") != PROMETHEUS_JOB:
            continue
        scrape_url = target.get("scrapeUrl")
        return {
            "job": PROMETHEUS_JOB,
            "health": target.get("health"),
            "scrape_url_matches_fixture": (
                isinstance(scrape_url, str) and scrape_url == "http://metrics-fixture:9100/metrics"
            ),
            "last_error_empty": target.get("lastError") == "",
        }
    raise ObservabilityAcceptanceError("prometheus_target_missing")


def _query_value(base_url: str, expression: str) -> float:
    payload = _api_json(base_url, "/api/v1/query", query=expression)
    data = _mapping(payload.get("data"), "prometheus_query_data")
    result = _sequence(data.get("result"), "prometheus_query_result")
    if len(result) != 1:
        raise ObservabilityAcceptanceError("prometheus_query_cardinality")
    sample = _mapping(result[0], "prometheus_query_sample")
    value = _sequence(sample.get("value"), "prometheus_query_value")
    if len(value) != 2 or not isinstance(value[1], str):
        raise ObservabilityAcceptanceError("prometheus_query_value")
    try:
        return float(value[1])
    except ValueError as exc:
        raise ObservabilityAcceptanceError("prometheus_query_value") from exc


def _assert_metric_values(
    base_url: str,
    queries: Sequence[tuple[str, float]],
) -> dict[str, float]:
    snapshot: dict[str, float] = {}
    for expression, expected in queries:
        value = _query_value(base_url, expression)
        if value != expected:
            raise ObservabilityAcceptanceError("prometheus_metric_value")
        snapshot[expression] = value
    return snapshot


def _rule_snapshots(base_url: str) -> dict[str, RuleSnapshot]:
    payload = _api_json(base_url, "/api/v1/rules")
    data = _mapping(payload.get("data"), "prometheus_rules_data")
    snapshots: dict[str, RuleSnapshot] = {}
    for raw_group in _sequence(data.get("groups"), "prometheus_rule_groups"):
        group = _mapping(raw_group, "prometheus_rule_group")
        for raw_rule in _sequence(group.get("rules"), "prometheus_runtime_rules"):
            rule = _mapping(raw_rule, "prometheus_runtime_rule")
            name = rule.get("name")
            if not isinstance(name, str) or name not in ALERT_BY_NAME:
                continue
            raw_duration = rule.get("duration")
            if not isinstance(raw_duration, int | float):
                raise ObservabilityAcceptanceError("prometheus_rule_duration")
            active_at: str | None = None
            raw_alerts = rule.get("alerts")
            if isinstance(raw_alerts, list) and raw_alerts:
                first_alert = _mapping(raw_alerts[0], "prometheus_runtime_alert")
                raw_active_at = first_alert.get("activeAt")
                if isinstance(raw_active_at, str):
                    active_at = raw_active_at
            annotations = _mapping(rule.get("annotations"), "prometheus_runtime_annotations")
            runbook = annotations.get("runbook")
            if name in snapshots:
                raise ObservabilityAcceptanceError("prometheus_runtime_rule_duplicate")
            snapshots[name] = RuleSnapshot(
                name=name,
                state=str(rule.get("state")),
                health=str(rule.get("health")),
                duration_seconds=round(float(raw_duration)),
                active_at=active_at,
                runbook=runbook if isinstance(runbook, str) else None,
            )
    if set(snapshots) != set(ALERT_BY_NAME):
        raise ObservabilityAcceptanceError("prometheus_runtime_rules_missing")
    for name, snapshot in snapshots.items():
        contract = ALERT_BY_NAME[name]
        if (
            snapshot.health != "ok"
            or snapshot.duration_seconds != contract.configured_for_seconds
            or snapshot.runbook != contract.runbook
        ):
            raise ObservabilityAcceptanceError("prometheus_runtime_rule_contract")
    return snapshots


def _wait_for_pending(
    base_url: str,
    *,
    timeout_seconds: int,
    poll_seconds: float,
) -> tuple[dict[str, RuleSnapshot], dict[str, float], dict[str, object]]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            target = _target_snapshot(base_url)
            metrics = _assert_metric_values(base_url, FIRING_QUERIES)
            rules = _rule_snapshots(base_url)
        except ObservabilityAcceptanceError:
            time.sleep(poll_seconds)
            continue
        if (
            target["health"] == "up"
            and target["scrape_url_matches_fixture"] is True
            and target["last_error_empty"] is True
            and all(snapshot.state == "pending" for snapshot in rules.values())
        ):
            return rules, metrics, target
        time.sleep(poll_seconds)
    raise ObservabilityAcceptanceError("pending_timeout")


def _wait_for_firing(
    base_url: str,
    pending: dict[str, RuleSnapshot],
    *,
    timeout_seconds: int,
    poll_seconds: float,
) -> dict[str, dict[str, object]]:
    deadline = time.monotonic() + timeout_seconds
    observed_at = datetime.now(UTC).isoformat()
    evidence: dict[str, dict[str, object]] = {
        name: {
            "pending_observed_at": observed_at,
            "prometheus_active_at": snapshot.active_at,
        }
        for name, snapshot in pending.items()
    }
    next_progress = time.monotonic()
    while time.monotonic() < deadline:
        target = _target_snapshot(base_url)
        if target.get("health") != "up":
            raise ObservabilityAcceptanceError("target_lost")
        snapshots = _rule_snapshots(base_url)
        observed_at = datetime.now(UTC).isoformat()
        for name, snapshot in snapshots.items():
            if snapshot.state == "firing" and "firing_observed_at" not in evidence[name]:
                evidence[name]["firing_observed_at"] = observed_at
                evidence[name]["firing_state"] = "firing"
                evidence[name]["prometheus_active_at"] = (
                    snapshot.active_at or evidence[name]["prometheus_active_at"]
                )
        if all("firing_observed_at" in value for value in evidence.values()):
            return evidence
        if time.monotonic() >= next_progress:
            remaining = sorted(
                name for name, value in evidence.items() if "firing_observed_at" not in value
            )
            print(f"[observability-acceptance] waiting_for_firing={','.join(remaining)}")
            next_progress = time.monotonic() + 30
        time.sleep(poll_seconds)
    raise ObservabilityAcceptanceError("firing_timeout")


def _wait_for_resolved(
    base_url: str,
    transitions: dict[str, dict[str, object]],
    *,
    timeout_seconds: int,
    poll_seconds: float,
) -> tuple[dict[str, float], dict[str, object]]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        target = _target_snapshot(base_url)
        if target.get("health") != "up":
            raise ObservabilityAcceptanceError("target_lost")
        try:
            metrics = _assert_metric_values(base_url, RESOLVED_QUERIES)
        except ObservabilityAcceptanceError:
            time.sleep(poll_seconds)
            continue
        snapshots = _rule_snapshots(base_url)
        if all(snapshot.state == "inactive" for snapshot in snapshots.values()):
            observed_at = datetime.now(UTC).isoformat()
            for transition in transitions.values():
                transition["resolved_observed_at"] = observed_at
                transition["resolved_state"] = "inactive"
            return metrics, target
        time.sleep(poll_seconds)
    raise ObservabilityAcceptanceError("resolved_timeout")


def _compose_command(project: str, *arguments: str) -> tuple[str, ...]:
    return (
        "docker",
        "compose",
        "--project-name",
        project,
        "--file",
        str(COMPOSE_PATH),
        *arguments,
    )


def _resolved_image_contract(
    services: dict[str, object],
) -> dict[str, dict[str, object]]:
    evidence: dict[str, dict[str, object]] = {}
    for (
        service_name,
        evidence_name,
        approved_reference,
        approved_manifest_digest,
        approved_repository_digest,
    ) in APPROVED_IMAGE_CONTRACTS:
        service = _mapping(
            services.get(service_name),
            f"{evidence_name}_compose_service",
        )
        resolved_reference = service.get("image")
        if resolved_reference != approved_reference:
            raise ObservabilityAcceptanceError(f"{evidence_name}_compose_image_identity")
        evidence[evidence_name] = {
            "approved_reference": approved_reference,
            "approved_manifest_digest": approved_manifest_digest,
            "approved_repository_digest": approved_repository_digest,
            "resolved_compose_reference": resolved_reference,
            "verified_before_start": True,
        }
    return evidence


def _image_identity(
    evidence_name: str,
    service_name: str,
    approved_reference: str,
    approved_manifest_digest: str,
    approved_repository_digest: str,
    resolved_reference: str,
    *,
    project: str,
    environment: dict[str, str],
) -> tuple[list[ProcessResult], dict[str, object]]:
    lookup = _run_process(
        f"{evidence_name}_container_lookup",
        _compose_command(project, "ps", "--quiet", service_name),
        environment=environment,
        timeout_seconds=30,
    )
    _require(lookup)
    container_ids = [
        line.strip().lower()
        for line in lookup.stdout.decode("ascii", errors="strict").splitlines()
        if line.strip()
    ]
    if len(container_ids) != 1 or CONTAINER_ID_PATTERN.fullmatch(container_ids[0]) is None:
        raise ObservabilityAcceptanceError(f"{evidence_name}_container_lookup")
    container_id = container_ids[0]

    container = _run_process(
        f"{evidence_name}_container_identity",
        (
            "docker",
            "container",
            "inspect",
            container_id,
            "--format",
            "{{.Id}}|{{.Image}}|{{.Config.Image}}",
        ),
        environment=environment,
        timeout_seconds=30,
    )
    _require(container)
    container_fields = container.stdout.decode("utf-8", errors="strict").strip().split("|")
    if (
        len(container_fields) != 3
        or container_fields[0].lower() != container_id
        or IMAGE_ID_PATTERN.fullmatch(container_fields[1].lower()) is None
        or container_fields[2] != resolved_reference
        or container_fields[2] != approved_reference
    ):
        raise ObservabilityAcceptanceError(f"{evidence_name}_container_identity")
    container_image_id = container_fields[1].lower()

    image = _run_process(
        f"{evidence_name}_image_identity",
        (
            "docker",
            "image",
            "inspect",
            approved_reference,
            "--format",
            "{{json .RepoDigests}}|{{.Id}}|{{.Os}}|{{.Architecture}}",
        ),
        environment=environment,
        timeout_seconds=30,
    )
    _require(image)
    image_fields = image.stdout.decode("utf-8", errors="strict").strip().split("|")
    try:
        raw_repository_digests: object = json.loads(image_fields[0])
    except (IndexError, json.JSONDecodeError) as exc:
        raise ObservabilityAcceptanceError(f"{evidence_name}_image_identity") from exc
    repository_digests = (
        sorted(raw_repository_digests)
        if isinstance(raw_repository_digests, list)
        and all(isinstance(item, str) for item in raw_repository_digests)
        else []
    )
    if (
        len(image_fields) != 4
        or IMAGE_ID_PATTERN.fullmatch(image_fields[1].lower()) is None
        or image_fields[1].lower() != container_image_id
        or image_fields[2] != "linux"
        or not image_fields[3]
        or approved_repository_digest not in repository_digests
        or not approved_reference.endswith(f"@{approved_manifest_digest}")
        or not approved_repository_digest.endswith(f"@{approved_manifest_digest}")
    ):
        raise ObservabilityAcceptanceError(f"{evidence_name}_image_identity")
    return [lookup, container, image], {
        "reference": approved_reference,
        "approved_reference": approved_reference,
        "approved_manifest_digest": approved_manifest_digest,
        "approved_repository_digest": approved_repository_digest,
        "resolved_compose_reference": resolved_reference,
        "container_config_reference": container_fields[2],
        "container_id": container_id,
        "container_image_id": container_image_id,
        "local_image_id": image_fields[1].lower(),
        "repository_digests": repository_digests,
        "repository_digest_match": True,
        "os": image_fields[2],
        "architecture": image_fields[3],
    }


def _resource_absent(result: ProcessResult) -> bool:
    return result.returncode == 0 and not result.stdout.strip()


def _cleanup_runtime(
    *,
    project: str,
    runtime_dir: Path,
    environment: dict[str, str],
    phases: list[ProcessResult],
) -> tuple[bool, bool]:
    down = _run_process(
        "compose_down",
        _compose_command(
            project,
            "down",
            "--volumes",
            "--remove-orphans",
            "--timeout",
            "30",
        ),
        environment=environment,
        timeout_seconds=120,
    )
    phases.append(down)
    cleanup_results = []
    for name, command in (
        (
            "container_cleanup",
            (
                "docker",
                "ps",
                "-aq",
                "--filter",
                f"label=com.docker.compose.project={project}",
            ),
        ),
        (
            "volume_cleanup",
            (
                "docker",
                "volume",
                "ls",
                "-q",
                "--filter",
                f"label=com.docker.compose.project={project}",
            ),
        ),
        (
            "network_cleanup",
            (
                "docker",
                "network",
                "ls",
                "-q",
                "--filter",
                f"label=com.docker.compose.project={project}",
            ),
        ),
    ):
        result = _run_process(
            name,
            command,
            environment=environment,
            timeout_seconds=30,
        )
        phases.append(result)
        cleanup_results.append(result)
    docker_cleanup_passed = down.returncode == 0 and all(
        _resource_absent(result) for result in cleanup_results
    )

    host_runtime_dir_removed = False
    for _ in range(3):
        shutil.rmtree(runtime_dir, ignore_errors=True)
        if not runtime_dir.exists():
            host_runtime_dir_removed = True
            break
        time.sleep(1)
    return docker_cleanup_passed, host_runtime_dir_removed


def _seal_evidence(output_dir: Path, evidence: dict[str, object]) -> None:
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        payload = (json.dumps(evidence, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        (temporary / "evidence.json").write_bytes(payload)
        (temporary / "manifest.sha256").write_text(
            f"{_sha256_bytes(payload)}  evidence.json\n",
            encoding="utf-8",
            newline="\n",
        )
        temporary.replace(output_dir)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _mapping_or_empty(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return cast(dict[str, object], value)


def _image_evidence_errors(runtime: dict[str, object]) -> list[str]:
    errors: list[str] = []
    resolved_images = _mapping_or_empty(runtime.get("resolved_compose_images"))
    actual_images = _mapping_or_empty(runtime.get("images"))
    for (
        _service_name,
        evidence_name,
        approved_reference,
        approved_manifest_digest,
        approved_repository_digest,
    ) in APPROVED_IMAGE_CONTRACTS:
        resolved = _mapping_or_empty(resolved_images.get(evidence_name))
        actual = _mapping_or_empty(actual_images.get(evidence_name))
        raw_repository_digests = actual.get("repository_digests")
        repository_digests = (
            raw_repository_digests
            if isinstance(raw_repository_digests, list)
            and all(isinstance(item, str) for item in raw_repository_digests)
            else []
        )
        container_id = actual.get("container_id")
        container_image_id = actual.get("container_image_id")
        local_image_id = actual.get("local_image_id")
        architecture = actual.get("architecture")
        if (
            resolved.get("approved_reference") != approved_reference
            or resolved.get("approved_manifest_digest") != approved_manifest_digest
            or resolved.get("approved_repository_digest") != approved_repository_digest
            or resolved.get("resolved_compose_reference") != approved_reference
            or resolved.get("verified_before_start") is not True
            or actual.get("reference") != approved_reference
            or actual.get("approved_reference") != approved_reference
            or actual.get("approved_manifest_digest") != approved_manifest_digest
            or actual.get("approved_repository_digest") != approved_repository_digest
            or actual.get("resolved_compose_reference") != approved_reference
            or actual.get("container_config_reference") != approved_reference
            or not isinstance(container_id, str)
            or CONTAINER_ID_PATTERN.fullmatch(container_id) is None
            or not isinstance(container_image_id, str)
            or IMAGE_ID_PATTERN.fullmatch(container_image_id) is None
            or local_image_id != container_image_id
            or approved_repository_digest not in repository_digests
            or actual.get("repository_digest_match") is not True
            or actual.get("os") != "linux"
            or not isinstance(architecture, str)
            or not architecture
        ):
            errors.append(f"image:{evidence_name}")
    return errors


def evidence_errors(
    evidence: dict[str, object],
    *,
    expected_sha: str,
    now: datetime,
) -> list[str]:
    errors: list[str] = []
    if evidence.get("schema") != EVIDENCE_SCHEMA:
        errors.append("schema")
    if evidence.get("status") != "candidate_passed":
        errors.append("status")

    generated_at = _parse_timestamp(evidence.get("generated_at"))
    expires_at = _parse_timestamp(evidence.get("expires_at"))
    if generated_at is None or expires_at is None:
        errors.append("timestamps")
    else:
        if expires_at - generated_at != timedelta(seconds=EVIDENCE_TTL_SECONDS):
            errors.append("ttl")
        if generated_at > now + timedelta(minutes=5):
            errors.append("future")
        if now >= expires_at:
            errors.append("expired")

    candidate = _mapping_or_empty(evidence.get("candidate"))
    if (
        candidate.get("expected_git_sha") != expected_sha
        or candidate.get("git_sha_before") != expected_sha
        or candidate.get("git_sha_after") != expected_sha
        or candidate.get("worktree_clean_before") is not True
        or candidate.get("worktree_clean_after") is not True
        or candidate.get("candidate_unchanged") is not True
        or candidate.get("git_replace_refs_absent") is not True
        or candidate.get("git_grafts_absent") is not True
        or candidate.get("git_hidden_index_flags_absent") is not True
        or candidate.get("git_info_exclude_inactive") is not True
        or candidate.get("git_global_excludes_disabled") is not True
        or candidate.get("untracked_execution_inputs_absent") is not True
        or candidate.get("minimum_git_version") != "2.36.0"
        or candidate.get("sources_match_commit_blobs") is not True
    ):
        errors.append("candidate")

    if evidence.get("source_sha256") != _source_hashes():
        errors.append("source_sha256")

    boundary = _mapping_or_empty(evidence.get("external_boundary"))
    if (
        boundary.get("external_webhook_verified") is not False
        or boundary.get("alertmanager_started") is not False
        or boundary.get("ext_webhook_001_status") != "pending_external_gate"
        or boundary.get("promtool_is_webhook_evidence") is not False
        or boundary.get("protected_minio_auth_verified") is not False
        or boundary.get("synthetic_auth_placeholder") is not True
    ):
        errors.append("external_boundary")

    runtime = _mapping_or_empty(evidence.get("runtime"))
    target = _mapping_or_empty(runtime.get("prometheus_target"))
    if (
        target.get("health") != "up"
        or target.get("scrape_url_matches_fixture") is not True
        or target.get("last_error_empty") is not True
    ):
        errors.append("prometheus_target")
    if runtime.get("production_for_windows_used") is not True:
        errors.append("production_for_windows")
    if (
        runtime.get("cleanup_passed") is not True
        or runtime.get("docker_cleanup_passed") is not True
        or runtime.get("host_runtime_dir_removed") is not True
    ):
        errors.append("cleanup")
    errors.extend(_image_evidence_errors(runtime))

    raw_alerts = runtime.get("alerts")
    alerts = (
        {item.get("name"): item for item in raw_alerts if isinstance(item, dict)}
        if isinstance(raw_alerts, list)
        else {}
    )
    if (
        not isinstance(raw_alerts, list)
        or len(raw_alerts) != len(ALERT_BY_NAME)
        or set(alerts) != set(ALERT_BY_NAME)
    ):
        errors.append("alerts")
    else:
        for contract in ALERT_CONTRACTS:
            alert = cast(dict[str, object], alerts[contract.name])
            active_at = _parse_timestamp(alert.get("prometheus_active_at"))
            pending_at = _parse_timestamp(alert.get("pending_observed_at"))
            firing_at = _parse_timestamp(alert.get("firing_observed_at"))
            resolved_at = _parse_timestamp(alert.get("resolved_observed_at"))
            ordered_window = (
                active_at is not None
                and pending_at is not None
                and firing_at is not None
                and resolved_at is not None
                and active_at <= pending_at <= firing_at < resolved_at
                and firing_at - active_at
                >= timedelta(seconds=contract.configured_for_seconds - EVALUATION_INTERVAL_SECONDS)
                and (generated_at is None or resolved_at <= generated_at)
            )
            if (
                alert.get("configured_for_seconds") != contract.configured_for_seconds
                or alert.get("runbook") != contract.runbook
                or alert.get("firing_state") != "firing"
                or alert.get("resolved_state") != "inactive"
                or not ordered_window
            ):
                errors.append(f"alert:{contract.name}")

    phases = evidence.get("phases")
    phase_map = (
        {item.get("name"): item.get("returncode") for item in phases if isinstance(item, dict)}
        if isinstance(phases, list)
        else {}
    )
    if (
        not isinstance(phases, list)
        or len(phases) != len(REQUIRED_PHASES)
        or set(phase_map) != REQUIRED_PHASES
        or any(phase_map.get(name) != 0 for name in REQUIRED_PHASES)
    ):
        errors.append("phases")
    return errors


def _load_sealed_evidence(evidence_dir: Path) -> dict[str, object]:
    payload_path = evidence_dir / "evidence.json"
    manifest_path = evidence_dir / "manifest.sha256"
    try:
        payload = payload_path.read_bytes()
        manifest = manifest_path.read_text(encoding="utf-8")
        decoded: object = json.loads(payload)
    except (OSError, json.JSONDecodeError) as exc:
        raise ObservabilityAcceptanceError("evidence_read") from exc
    if manifest != f"{_sha256_bytes(payload)}  evidence.json\n":
        raise ObservabilityAcceptanceError("evidence_manifest")
    return _mapping(decoded, "evidence_payload")


def _execute(
    *,
    expected_sha: str,
    output_dir: Path,
    startup_timeout_seconds: int,
    firing_timeout_seconds: int,
    resolution_timeout_seconds: int,
    poll_seconds: float,
) -> int:
    _require_isolated_runtime()
    expected_sha = _validate_expected_sha(expected_sha)
    output_dir = _validate_output_dir(output_dir)
    minimum_firing_timeout = max(item.configured_for_seconds for item in ALERT_CONTRACTS) + 30
    if (
        startup_timeout_seconds < 30
        or firing_timeout_seconds < minimum_firing_timeout
        or resolution_timeout_seconds < 15
        or poll_seconds < 1
    ):
        raise ObservabilityAcceptanceError("timeout_contract")
    static_contract = _static_contract()
    before = candidate_identity(expected_sha)
    _assert_candidate(before, expected_sha)

    project = f"ku-obs-{expected_sha[:12]}-{secrets.token_hex(4)}"
    environment = os.environ.copy()
    phases: list[ProcessResult] = []
    failure_step: str | None = None
    target: dict[str, object] = {}
    firing_metrics: dict[str, float] = {}
    resolved_metrics: dict[str, float] = {}
    transitions: dict[str, dict[str, object]] = {}
    images: dict[str, object] = {}
    resolved_images: dict[str, dict[str, object]] = {}
    compose_sha256: str | None = None
    docker_cleanup_passed = False
    host_runtime_dir_removed = False

    runtime_dir = Path(tempfile.mkdtemp(prefix=f"{project}-"))
    try:
        fixture_dir = runtime_dir / "fixture"
        fixture_dir.mkdir()
        fixture_path = fixture_dir / "observability.prom"
        config_path = runtime_dir / "prometheus.yml"
        _atomic_write(fixture_path, FIRING_METRICS)
        _atomic_write(config_path, _prometheus_config())
        minio_token_path = runtime_dir / "minio-metrics-token"
        _atomic_write(minio_token_path, "obs-acceptance-config-check-only\n")
        port = _free_port()
        base_url = f"http://127.0.0.1:{port}"
        environment.update(
            {
                "OBSERVABILITY_FIXTURE_DIR": str(fixture_dir.resolve()),
                "OBSERVABILITY_PROMETHEUS_CONFIG": str(config_path.resolve()),
                "OBSERVABILITY_PRODUCTION_PROMETHEUS_CONFIG": str(PROMETHEUS_PATH.resolve()),
                "OBSERVABILITY_ALERTS_FILE": str(ALERTS_PATH.resolve()),
                "OBSERVABILITY_ALERT_TEST_FILE": str(ALERT_TEST_PATH.resolve()),
                "OBSERVABILITY_MINIO_TOKEN_FILE": str(minio_token_path.resolve()),
                "OBSERVABILITY_PROMETHEUS_PORT": str(port),
            }
        )
        compose_contract = _run_process(
            "compose_contract",
            _compose_command(project, "config", "--format", "json"),
            environment=environment,
            timeout_seconds=60,
        )
        phases.append(compose_contract)
        _require(compose_contract)
        resolved_compose = _mapping(json.loads(compose_contract.stdout), "resolved_compose")
        services = _mapping(resolved_compose.get("services"), "resolved_services")
        if set(services) != {"metrics-fixture", "prometheus"} or "alertmanager" in services:
            raise ObservabilityAcceptanceError("compose_contract")
        compose_sha256 = _sha256_bytes(compose_contract.stdout)
        resolved_images = _resolved_image_contract(services)

        commands = (
            (
                "promtool_production_config",
                _compose_command(
                    project,
                    "run",
                    "--rm",
                    "--no-deps",
                    "--entrypoint",
                    "promtool",
                    "prometheus",
                    "check",
                    "config",
                    "/etc/prometheus/prometheus.production.yml",
                ),
            ),
            (
                "promtool_production_rules",
                _compose_command(
                    project,
                    "run",
                    "--rm",
                    "--no-deps",
                    "--entrypoint",
                    "promtool",
                    "prometheus",
                    "check",
                    "rules",
                    "/etc/prometheus/alerts.yml",
                ),
            ),
            (
                "promtool_rule_transitions",
                _compose_command(
                    project,
                    "run",
                    "--rm",
                    "--no-deps",
                    "--workdir",
                    "/etc/prometheus",
                    "--entrypoint",
                    "promtool",
                    "prometheus",
                    "test",
                    "rules",
                    "alerts.test.yml",
                ),
            ),
        )
        for name, command in commands:
            phase = _run_process(
                name,
                command,
                environment=environment,
                timeout_seconds=180,
            )
            phases.append(phase)
            _require(phase)

        up = _run_process(
            "compose_up",
            _compose_command(
                project,
                "up",
                "--detach",
                "--no-build",
                "metrics-fixture",
                "prometheus",
            ),
            environment=environment,
            timeout_seconds=120,
        )
        phases.append(up)
        _require(up)

        prom_results, prom_identity = _image_identity(
            "prometheus",
            "prometheus",
            PROMETHEUS_IMAGE,
            PROMETHEUS_MANIFEST_DIGEST,
            PROMETHEUS_REPOSITORY_DIGEST,
            str(resolved_images["prometheus"]["resolved_compose_reference"]),
            project=project,
            environment=environment,
        )
        node_results, node_identity = _image_identity(
            "node_exporter",
            "metrics-fixture",
            NODE_EXPORTER_IMAGE,
            NODE_EXPORTER_MANIFEST_DIGEST,
            NODE_EXPORTER_REPOSITORY_DIGEST,
            str(resolved_images["node_exporter"]["resolved_compose_reference"]),
            project=project,
            environment=environment,
        )
        phases.extend((*prom_results, *node_results))
        images = {
            "prometheus": prom_identity,
            "node_exporter": node_identity,
        }

        pending, firing_metrics, target = _wait_for_pending(
            base_url,
            timeout_seconds=startup_timeout_seconds,
            poll_seconds=poll_seconds,
        )
        transitions = _wait_for_firing(
            base_url,
            pending,
            timeout_seconds=firing_timeout_seconds,
            poll_seconds=poll_seconds,
        )
        _atomic_write(fixture_path, RESOLVED_METRICS)
        resolved_metrics, target = _wait_for_resolved(
            base_url,
            transitions,
            timeout_seconds=resolution_timeout_seconds,
            poll_seconds=poll_seconds,
        )
    except (
        ObservabilityAcceptanceError,
        OSError,
        ValueError,
        json.JSONDecodeError,
        yaml.YAMLError,
    ) as exc:
        failure_step = (
            exc.step if isinstance(exc, ObservabilityAcceptanceError) else type(exc).__name__
        )
    finally:
        docker_cleanup_passed, host_runtime_dir_removed = _cleanup_runtime(
            project=project,
            runtime_dir=runtime_dir,
            environment=environment,
            phases=phases,
        )

    cleanup_passed = docker_cleanup_passed and host_runtime_dir_removed
    if not host_runtime_dir_removed and failure_step is None:
        failure_step = "host_runtime_cleanup"
    after = candidate_identity(expected_sha)
    candidate_unchanged = after == before and after.clean and after.git_sha == expected_sha
    status = (
        "candidate_passed"
        if failure_step is None and cleanup_passed and candidate_unchanged
        else "failed"
    )
    now = datetime.now(UTC)
    static_by_name = {str(item["name"]): item for item in static_contract}
    alert_evidence = []
    for contract in ALERT_CONTRACTS:
        transition = transitions.get(contract.name, {})
        alert_evidence.append(
            {
                **static_by_name[contract.name],
                **transition,
                "name": contract.name,
                "configured_for_seconds": contract.configured_for_seconds,
                "runbook": contract.runbook,
            }
        )

    evidence: dict[str, object] = {
        "schema": EVIDENCE_SCHEMA,
        "status": status,
        "generated_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=EVIDENCE_TTL_SECONDS)).isoformat(),
        "failure_step": failure_step,
        "candidate": {
            "expected_git_sha": expected_sha,
            "git_sha_before": before.git_sha,
            "git_sha_after": after.git_sha,
            "worktree_clean_before": before.clean,
            "worktree_clean_after": after.clean,
            "candidate_unchanged": candidate_unchanged,
            "git_replace_refs_absent": True,
            "git_grafts_absent": True,
            "git_hidden_index_flags_absent": True,
            "git_info_exclude_inactive": True,
            "git_global_excludes_disabled": True,
            "untracked_execution_inputs_absent": True,
            "minimum_git_version": "2.36.0",
            "sources_match_commit_blobs": True,
            "status_command": (
                "git --no-replace-objects status --porcelain=v1 --untracked-files=all"
            ),
        },
        "source_sha256": _source_hashes(),
        "external_boundary": {
            "external_webhook_verified": False,
            "alertmanager_started": False,
            "ext_webhook_001_status": "pending_external_gate",
            "promtool_is_webhook_evidence": False,
            "protected_minio_auth_verified": False,
            "synthetic_auth_placeholder": True,
        },
        "runtime": {
            "compose_project": project,
            "unique_project": True,
            "resolved_compose_sha256": compose_sha256,
            "prometheus_target": target,
            "firing_metric_values": firing_metrics,
            "resolved_metric_values": resolved_metrics,
            "alerts": alert_evidence,
            "images": images,
            "resolved_compose_images": resolved_images,
            "production_for_windows_used": True,
            "cleanup_passed": cleanup_passed,
            "docker_cleanup_passed": docker_cleanup_passed,
            "host_runtime_dir_removed": host_runtime_dir_removed,
        },
        "phases": [_phase_evidence(phase) for phase in phases],
        "raw_logs": {
            "archived": False,
            "hash_algorithm": "sha256",
        },
        "atomic_seal": {
            "output_directory_preexisted": False,
            "directory_replace": True,
            "manifest_algorithm": "sha256",
        },
    }
    if status == "candidate_passed":
        errors = evidence_errors(evidence, expected_sha=expected_sha, now=now)
        if errors:
            evidence["status"] = "failed"
            evidence["failure_step"] = "evidence_self_validation"
            status = "failed"
    _seal_evidence(output_dir, evidence)
    return 0 if status == "candidate_passed" else 1


def _verify(*, expected_sha: str, evidence_dir: Path) -> int:
    _require_isolated_runtime()
    expected_sha = _validate_expected_sha(expected_sha)
    _assert_candidate(candidate_identity(expected_sha), expected_sha)
    resolved = evidence_dir.resolve()
    if resolved == ROOT.resolve() or resolved.is_relative_to(ROOT.resolve()):
        raise ObservabilityAcceptanceError("evidence_output_external")
    evidence = _load_sealed_evidence(resolved)
    errors = evidence_errors(evidence, expected_sha=expected_sha, now=datetime.now(UTC))
    if errors:
        print(f"observability evidence rejected: {','.join(sorted(set(errors)))}")
        return 1
    print(f"observability evidence valid for {expected_sha}")
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-git-sha", required=True)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--output-dir", type=Path)
    target.add_argument("--verify-evidence-dir", type=Path)
    parser.add_argument("--startup-timeout-seconds", type=int, default=90)
    parser.add_argument("--firing-timeout-seconds", type=int, default=720)
    parser.add_argument("--resolution-timeout-seconds", type=int, default=90)
    parser.add_argument("--poll-seconds", type=float, default=5)
    return parser.parse_args()


def main() -> int:
    arguments = _parse_args()
    try:
        if arguments.verify_evidence_dir is not None:
            return _verify(
                expected_sha=str(arguments.expected_git_sha),
                evidence_dir=cast(Path, arguments.verify_evidence_dir),
            )
        return _execute(
            expected_sha=str(arguments.expected_git_sha),
            output_dir=cast(Path, arguments.output_dir),
            startup_timeout_seconds=int(arguments.startup_timeout_seconds),
            firing_timeout_seconds=int(arguments.firing_timeout_seconds),
            resolution_timeout_seconds=int(arguments.resolution_timeout_seconds),
            poll_seconds=float(arguments.poll_seconds),
        )
    except ObservabilityAcceptanceError as exc:
        print(f"observability acceptance refused: {exc.step}")
        return 2
