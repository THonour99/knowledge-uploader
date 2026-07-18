"""Candidate-bound local OBS-001 Prometheus acceptance implementation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
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
from typing import cast

import yaml  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = ROOT / "scripts" / "run_observability_acceptance.py"
COMPOSE_PATH = ROOT / "ops" / "observability" / "acceptance.compose.yml"
ALERTS_PATH = ROOT / "ops" / "observability" / "alerts.yml"
ALERT_TEST_PATH = ROOT / "ops" / "observability" / "alerts.test.yml"
PROMETHEUS_PATH = ROOT / "ops" / "observability" / "prometheus.yml"
RUNBOOK_PATH = ROOT / "ops" / "runbooks" / "observability.md"
PROMETHEUS_IMAGE = "prom/prometheus:v3.12.0"
NODE_EXPORTER_IMAGE = "quay.io/prometheus/node-exporter:v1.11.1"
EVIDENCE_SCHEMA = "knowledge-uploader.observability-local-evidence.v1"
EVIDENCE_TTL_SECONDS = 24 * 60 * 60
EXPECTED_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
EVALUATION_INTERVAL_SECONDS = 5
PROMETHEUS_JOB = "observability-acceptance"


class ObservabilityAcceptanceError(RuntimeError):
    """A safe, named OBS-001 acceptance failure."""

    def __init__(self, step: str) -> None:
        super().__init__(step)
        self.step = step


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
        "prometheus_image_identity",
        "node_exporter_image_identity",
        "compose_down",
        "container_cleanup",
        "volume_cleanup",
        "network_cleanup",
    }
)
SOURCE_PATHS = (
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


def _git_bytes(*arguments: str) -> bytes:
    completed = subprocess.run(  # - fixed executable and arguments
        ("git", *arguments),
        cwd=ROOT,
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise ObservabilityAcceptanceError("git_identity")
    return bytes(completed.stdout)


def candidate_identity() -> CandidateIdentity:
    sha = _git_bytes("rev-parse", "HEAD").decode("ascii").strip().lower()
    status = (
        _git_bytes("status", "--porcelain=v1", "--untracked-files=all")
        .decode("utf-8", errors="strict")
        .strip()
    )
    return CandidateIdentity(git_sha=sha, porcelain_v1_all=status)


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


def _image_identity(
    name: str,
    image: str,
    *,
    environment: dict[str, str],
) -> tuple[ProcessResult, dict[str, object]]:
    result = _run_process(
        name,
        (
            "docker",
            "image",
            "inspect",
            image,
            "--format",
            "{{.Id}}|{{.Os}}|{{.Architecture}}",
        ),
        environment=environment,
        timeout_seconds=30,
    )
    _require(result)
    fields = result.stdout.decode("utf-8", errors="replace").strip().split("|")
    if len(fields) != 3 or not fields[0].startswith("sha256:"):
        raise ObservabilityAcceptanceError(name)
    return result, {
        "reference": image,
        "image_id": fields[0],
        "os": fields[1],
        "architecture": fields[2],
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
    before = candidate_identity()
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

        prom_result, prom_identity = _image_identity(
            "prometheus_image_identity",
            PROMETHEUS_IMAGE,
            environment=environment,
        )
        node_result, node_identity = _image_identity(
            "node_exporter_image_identity",
            NODE_EXPORTER_IMAGE,
            environment=environment,
        )
        phases.extend((prom_result, node_result))
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
    after = candidate_identity()
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
            "status_command": "git status --porcelain=v1 --untracked-files=all",
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
    expected_sha = _validate_expected_sha(expected_sha)
    _assert_candidate(candidate_identity(), expected_sha)
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
