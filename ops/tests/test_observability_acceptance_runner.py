from __future__ import annotations

import copy
import hashlib
import importlib.util
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import pytest
import yaml


def _load_acceptance() -> ModuleType:
    path = Path(__file__).parents[2] / "scripts" / "observability_acceptance.py"
    spec = importlib.util.spec_from_file_location("observability_acceptance_contract", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load observability acceptance module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


acceptance = _load_acceptance()


@pytest.fixture(autouse=True)
def _allow_unit_test_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(acceptance, "_require_isolated_runtime", lambda: None)


SHA = "a" * 40


def _valid_evidence(now: datetime) -> dict[str, object]:
    alerts = []
    for contract in acceptance.ALERT_CONTRACTS:
        firing_at = now - timedelta(seconds=10)
        active_at = firing_at - timedelta(seconds=contract.configured_for_seconds)
        pending_at = active_at + timedelta(seconds=5)
        resolved_at = now - timedelta(seconds=5)
        alerts.append(
            {
                "name": contract.name,
                "configured_for_seconds": contract.configured_for_seconds,
                "runbook": contract.runbook,
                "prometheus_active_at": active_at.isoformat(),
                "pending_observed_at": pending_at.isoformat(),
                "firing_observed_at": firing_at.isoformat(),
                "firing_state": "firing",
                "resolved_observed_at": resolved_at.isoformat(),
                "resolved_state": "inactive",
            }
        )
    resolved_images: dict[str, dict[str, object]] = {}
    images: dict[str, dict[str, object]] = {}
    for index, (
        _service_name,
        evidence_name,
        approved_reference,
        approved_manifest_digest,
        approved_repository_digest,
    ) in enumerate(acceptance.APPROVED_IMAGE_CONTRACTS):
        container_id = str(index + 1) * 64
        image_id = f"sha256:{chr(ord('b') + index) * 64}"
        resolved_images[evidence_name] = {
            "approved_reference": approved_reference,
            "approved_manifest_digest": approved_manifest_digest,
            "approved_repository_digest": approved_repository_digest,
            "resolved_compose_reference": approved_reference,
            "verified_before_start": True,
        }
        images[evidence_name] = {
            "reference": approved_reference,
            "approved_reference": approved_reference,
            "approved_manifest_digest": approved_manifest_digest,
            "approved_repository_digest": approved_repository_digest,
            "resolved_compose_reference": approved_reference,
            "container_config_reference": approved_reference,
            "container_id": container_id,
            "container_image_id": image_id,
            "local_image_id": image_id,
            "repository_digests": [approved_repository_digest],
            "repository_digest_match": True,
            "os": "linux",
            "architecture": "amd64",
        }
    return {
        "schema": acceptance.EVIDENCE_SCHEMA,
        "status": "candidate_passed",
        "generated_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=acceptance.EVIDENCE_TTL_SECONDS)).isoformat(),
        "candidate": {
            "expected_git_sha": SHA,
            "git_sha_before": SHA,
            "git_sha_after": SHA,
            "worktree_clean_before": True,
            "worktree_clean_after": True,
            "candidate_unchanged": True,
            "git_replace_refs_absent": True,
            "git_grafts_absent": True,
            "git_hidden_index_flags_absent": True,
            "git_info_exclude_inactive": True,
            "git_global_excludes_disabled": True,
            "untracked_execution_inputs_absent": True,
            "minimum_git_version": "2.36.0",
            "sources_match_commit_blobs": True,
        },
        "source_sha256": acceptance._source_hashes(),
        "external_boundary": {
            "external_webhook_verified": False,
            "alertmanager_started": False,
            "ext_webhook_001_status": "pending_external_gate",
            "promtool_is_webhook_evidence": False,
            "protected_minio_auth_verified": False,
            "synthetic_auth_placeholder": True,
        },
        "runtime": {
            "prometheus_target": {
                "health": "up",
                "scrape_url_matches_fixture": True,
                "last_error_empty": True,
            },
            "alerts": alerts,
            "resolved_compose_images": resolved_images,
            "images": images,
            "cleanup_passed": True,
            "production_for_windows_used": True,
            "docker_cleanup_passed": True,
            "host_runtime_dir_removed": True,
        },
        "phases": [{"name": name, "returncode": 0} for name in sorted(acceptance.REQUIRED_PHASES)],
    }


def _first_alert(evidence: dict[str, object]) -> dict[str, object]:
    runtime = evidence["runtime"]
    assert isinstance(runtime, dict)
    alerts = runtime["alerts"]
    assert isinstance(alerts, list)
    alert = alerts[0]
    assert isinstance(alert, dict)
    return alert


def test_static_contract_covers_real_windows_resolutions_and_runbooks() -> None:
    contract = acceptance._static_contract()

    assert {item["name"] for item in contract} == set(acceptance.ALERT_BY_NAME)
    assert {item["configured_for_seconds"] for item in contract} == {120, 300, 600}
    assert all(item["runbook_anchor_present"] is True for item in contract)
    assert acceptance.ACCEPTANCE_ENTRY_PATH in acceptance.SOURCE_PATHS
    assert acceptance.ACCEPTANCE_LAUNCHER_PATH in acceptance.SOURCE_PATHS
    runbook = acceptance.RUNBOOK_PATH.read_text(encoding="utf-8")
    assert "python -I -S -X utf8 scripts/acceptance_launcher.py observability" in runbook


def test_static_contract_rejects_duplicate_target_alert(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = yaml.safe_load(acceptance.ALERTS_PATH.read_text(encoding="utf-8"))
    target_group = None
    duplicate = None
    for group in payload["groups"]:
        for rule in group["rules"]:
            if rule.get("alert") == acceptance.ALERT_CONTRACTS[0].name:
                target_group = group
                duplicate = copy.deepcopy(rule)
                break
    assert target_group is not None
    assert duplicate is not None
    target_group["rules"].append(duplicate)
    alerts_path = tmp_path / "alerts.yml"
    alerts_path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(acceptance, "ALERTS_PATH", alerts_path)

    with pytest.raises(acceptance.ObservabilityAcceptanceError) as caught:
        acceptance._static_contract()

    assert caught.value.step == "target_rule_duplicate"


def test_runtime_rules_reject_duplicate_target_alert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rules = [
        {
            "name": contract.name,
            "state": "pending",
            "health": "ok",
            "duration": contract.configured_for_seconds,
            "annotations": {"runbook": contract.runbook},
            "alerts": [],
        }
        for contract in acceptance.ALERT_CONTRACTS
    ]
    rules.append(copy.deepcopy(rules[0]))
    payload = {"status": "success", "data": {"groups": [{"rules": rules}]}}
    monkeypatch.setattr(acceptance, "_api_json", lambda *_args, **_kwargs: payload)

    with pytest.raises(acceptance.ObservabilityAcceptanceError) as caught:
        acceptance._rule_snapshots("http://127.0.0.1:19090")

    assert caught.value.step == "prometheus_runtime_rule_duplicate"


def test_compose_is_isolated_and_never_starts_alertmanager() -> None:
    payload = yaml.safe_load(acceptance.COMPOSE_PATH.read_text(encoding="utf-8"))
    services = payload["services"]

    assert set(services) == {"metrics-fixture", "prometheus"}
    assert "alertmanager" not in services
    assert services["metrics-fixture"]["read_only"] is True
    assert services["prometheus"]["read_only"] is True
    assert "127.0.0.1:" in services["prometheus"]["ports"][0]
    assert services["metrics-fixture"]["image"] == acceptance.NODE_EXPORTER_IMAGE
    assert services["prometheus"]["image"] == acceptance.PROMETHEUS_IMAGE
    assert "@sha256:" in services["metrics-fixture"]["image"]
    assert "@sha256:" in services["prometheus"]["image"]


def test_resolved_compose_images_must_match_approved_digests() -> None:
    payload = yaml.safe_load(acceptance.COMPOSE_PATH.read_text(encoding="utf-8"))
    services = payload["services"]

    evidence = acceptance._resolved_image_contract(services)

    assert evidence["prometheus"]["verified_before_start"] is True
    assert (
        evidence["node_exporter"]["approved_repository_digest"]
        == acceptance.NODE_EXPORTER_REPOSITORY_DIGEST
    )

    mutated = copy.deepcopy(services)
    mutated["metrics-fixture"]["image"] = "quay.io/prometheus/node-exporter:v1.11.1"
    with pytest.raises(acceptance.ObservabilityAcceptanceError) as caught:
        acceptance._resolved_image_contract(mutated)
    assert caught.value.step == "node_exporter_compose_image_identity"


def test_running_image_identity_binds_container_config_id_and_repo_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container_id = "1" * 64
    image_id = "sha256:" + "b" * 64
    results = iter(
        (
            acceptance.ProcessResult(
                name="prometheus_container_lookup",
                command=(),
                returncode=0,
                stdout=f"{container_id}\n".encode(),
                stderr=b"",
                duration_ms=1,
            ),
            acceptance.ProcessResult(
                name="prometheus_container_identity",
                command=(),
                returncode=0,
                stdout=(f"{container_id}|{image_id}|{acceptance.PROMETHEUS_IMAGE}\n".encode()),
                stderr=b"",
                duration_ms=1,
            ),
            acceptance.ProcessResult(
                name="prometheus_image_identity",
                command=(),
                returncode=0,
                stdout=(
                    f'["{acceptance.PROMETHEUS_REPOSITORY_DIGEST}"]|{image_id}|linux|amd64\n'
                ).encode(),
                stderr=b"",
                duration_ms=1,
            ),
        )
    )

    def fake_run_process(
        name: str,
        _command: object,
        *,
        environment: dict[str, str],
        timeout_seconds: int,
    ) -> object:
        assert environment == {}
        assert timeout_seconds == 30
        result = next(results)
        assert result.name == name
        return result

    monkeypatch.setattr(acceptance, "_run_process", fake_run_process)

    phases, identity = acceptance._image_identity(
        "prometheus",
        "prometheus",
        acceptance.PROMETHEUS_IMAGE,
        acceptance.PROMETHEUS_MANIFEST_DIGEST,
        acceptance.PROMETHEUS_REPOSITORY_DIGEST,
        acceptance.PROMETHEUS_IMAGE,
        project="ku-obs-test",
        environment={},
    )

    assert [phase.name for phase in phases] == [
        "prometheus_container_lookup",
        "prometheus_container_identity",
        "prometheus_image_identity",
    ]
    assert identity["container_image_id"] == image_id
    assert identity["local_image_id"] == image_id
    assert identity["repository_digest_match"] is True


def test_fixture_metrics_use_only_aggregate_fixed_labels() -> None:
    fixture = acceptance.FIRING_METRICS + acceptance.RESOLVED_METRICS

    assert 'queue="document_queue"' in fixture
    for forbidden in (
        "user_id",
        "file_id",
        "department_id",
        "email",
        "token",
        "prompt",
        "object_key",
        "api_key",
    ):
        assert forbidden not in fixture.lower()


def test_candidate_guard_rejects_dirty_or_mismatched_sha() -> None:
    assert acceptance.ACCEPTANCE_GIT_PATH in acceptance.SOURCE_PATHS
    with pytest.raises(acceptance.ObservabilityAcceptanceError):
        acceptance._assert_candidate(
            acceptance.CandidateIdentity(git_sha=SHA, porcelain_v1_all="?? unrelated"),
            SHA,
        )
    with pytest.raises(acceptance.ObservabilityAcceptanceError):
        acceptance._assert_candidate(
            acceptance.CandidateIdentity(git_sha="b" * 40, porcelain_v1_all=""),
            SHA,
        )


def test_execute_always_invokes_cleanup_when_interrupted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_dir = tmp_path / "runtime"
    cleanup_calls: list[tuple[str, Path]] = []

    def fake_mkdtemp(*, prefix: str) -> str:
        assert prefix.startswith("ku-obs-")
        runtime_dir.mkdir()
        return str(runtime_dir)

    def interrupt_process(*_args: object, **_kwargs: object) -> None:
        raise KeyboardInterrupt

    def fake_cleanup_runtime(
        *,
        project: str,
        runtime_dir: Path,
        environment: dict[str, str],
        phases: list[object],
    ) -> tuple[bool, bool]:
        assert environment["OBSERVABILITY_PROMETHEUS_PORT"] == "19090"
        assert phases == []
        cleanup_calls.append((project, runtime_dir))
        return True, True

    monkeypatch.setattr(acceptance, "_validate_output_dir", lambda path: path)
    monkeypatch.setattr(acceptance, "_static_contract", lambda: [])
    monkeypatch.setattr(
        acceptance,
        "candidate_identity",
        lambda _expected_sha: acceptance.CandidateIdentity(git_sha=SHA, porcelain_v1_all=""),
    )
    monkeypatch.setattr(acceptance.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(acceptance, "_free_port", lambda: 19090)
    monkeypatch.setattr(acceptance, "_run_process", interrupt_process)
    monkeypatch.setattr(acceptance, "_cleanup_runtime", fake_cleanup_runtime)

    with pytest.raises(KeyboardInterrupt):
        acceptance._execute(
            expected_sha=SHA,
            output_dir=tmp_path / "evidence",
            startup_timeout_seconds=30,
            firing_timeout_seconds=630,
            resolution_timeout_seconds=15,
            poll_seconds=1,
        )

    assert len(cleanup_calls) == 1
    project, cleaned_runtime_dir = cleanup_calls[0]
    assert project.startswith(f"ku-obs-{SHA[:12]}-")
    assert cleaned_runtime_dir == runtime_dir


def test_execute_cleans_runtime_when_setup_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_dir = tmp_path / "runtime"
    cleanup_calls: list[tuple[str, Path]] = []
    sealed: list[dict[str, object]] = []

    def fake_mkdtemp(*, prefix: str) -> str:
        assert prefix.startswith("ku-obs-")
        runtime_dir.mkdir()
        return str(runtime_dir)

    def fail_write(*_args: object, **_kwargs: object) -> None:
        raise OSError("synthetic setup failure")

    def fake_cleanup_runtime(
        *,
        project: str,
        runtime_dir: Path,
        environment: dict[str, str],
        phases: list[object],
    ) -> tuple[bool, bool]:
        assert "OBSERVABILITY_PROMETHEUS_PORT" not in environment
        assert phases == []
        cleanup_calls.append((project, runtime_dir))
        (runtime_dir / "fixture").rmdir()
        runtime_dir.rmdir()
        return True, True

    monkeypatch.setattr(acceptance, "_validate_output_dir", lambda path: path)
    monkeypatch.setattr(
        acceptance,
        "_static_contract",
        lambda: [{"name": contract.name} for contract in acceptance.ALERT_CONTRACTS],
    )
    monkeypatch.setattr(
        acceptance,
        "candidate_identity",
        lambda _expected_sha: acceptance.CandidateIdentity(git_sha=SHA, porcelain_v1_all=""),
    )
    monkeypatch.setattr(acceptance.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(acceptance, "_atomic_write", fail_write)
    monkeypatch.setattr(acceptance, "_cleanup_runtime", fake_cleanup_runtime)
    monkeypatch.setattr(
        acceptance,
        "_seal_evidence",
        lambda _output, evidence: sealed.append(evidence),
    )

    result = acceptance._execute(
        expected_sha=SHA,
        output_dir=tmp_path / "evidence",
        startup_timeout_seconds=30,
        firing_timeout_seconds=630,
        resolution_timeout_seconds=15,
        poll_seconds=1,
    )

    assert result == 1
    assert len(cleanup_calls) == 1
    assert not runtime_dir.exists()
    assert len(sealed) == 1
    assert sealed[0]["failure_step"] == "OSError"


def test_evidence_accepts_current_candidate_bound_local_runtime() -> None:
    now = datetime.now(UTC)

    assert (
        acceptance.evidence_errors(
            _valid_evidence(now),
            expected_sha=SHA,
            now=now,
        )
        == []
    )


@pytest.mark.parametrize(
    "mutation",
    (
        "failed_status",
        "stale_sha",
        "expired",
        "external_webhook_claim",
        "alertmanager_started",
        "promtool_webhook_claim",
        "minio_auth_claim",
        "non_synthetic_auth",
        "short_window",
        "time_order",
        "production_window_false",
        "host_cleanup_failed",
        "missing_transition",
        "cleanup_failed",
        "phase_failed",
        "source_drift",
        "resolved_compose_image",
        "container_config_image",
        "repository_digest_missing",
        "container_image_id_mismatch",
    ),
)
def test_evidence_rejects_failed_stale_or_overclaimed_receipts(mutation: str) -> None:
    now = datetime.now(UTC)
    evidence = _valid_evidence(now)
    if mutation == "failed_status":
        evidence["status"] = "failed"
    elif mutation == "stale_sha":
        candidate = evidence["candidate"]
        assert isinstance(candidate, dict)
        candidate["git_sha_after"] = "b" * 40
    elif mutation == "expired":
        generated = now - timedelta(hours=25)
        evidence["generated_at"] = generated.isoformat()
        evidence["expires_at"] = (generated + timedelta(hours=24)).isoformat()
    elif mutation == "external_webhook_claim":
        boundary = evidence["external_boundary"]
        assert isinstance(boundary, dict)
        boundary["external_webhook_verified"] = True
    elif mutation == "alertmanager_started":
        boundary = evidence["external_boundary"]
        assert isinstance(boundary, dict)
        boundary["alertmanager_started"] = True
    elif mutation == "promtool_webhook_claim":
        boundary = evidence["external_boundary"]
        assert isinstance(boundary, dict)
        boundary["promtool_is_webhook_evidence"] = True
    elif mutation == "minio_auth_claim":
        boundary = evidence["external_boundary"]
        assert isinstance(boundary, dict)
        boundary["protected_minio_auth_verified"] = True
    elif mutation == "non_synthetic_auth":
        boundary = evidence["external_boundary"]
        assert isinstance(boundary, dict)
        boundary["synthetic_auth_placeholder"] = False
    elif mutation == "short_window":
        alert = _first_alert(evidence)
        firing_at = datetime.fromisoformat(str(alert["firing_observed_at"]))
        configured = alert["configured_for_seconds"]
        assert isinstance(configured, int)
        alert["prometheus_active_at"] = (firing_at - timedelta(seconds=configured - 10)).isoformat()
    elif mutation == "time_order":
        alert = _first_alert(evidence)
        firing_at = datetime.fromisoformat(str(alert["firing_observed_at"]))
        alert["resolved_observed_at"] = (firing_at - timedelta(seconds=1)).isoformat()
    elif mutation == "production_window_false":
        runtime = evidence["runtime"]
        assert isinstance(runtime, dict)
        runtime["production_for_windows_used"] = False
    elif mutation == "host_cleanup_failed":
        runtime = evidence["runtime"]
        assert isinstance(runtime, dict)
        runtime["host_runtime_dir_removed"] = False
    elif mutation == "missing_transition":
        runtime = evidence["runtime"]
        assert isinstance(runtime, dict)
        alerts = runtime["alerts"]
        assert isinstance(alerts, list)
        alert = alerts[0]
        assert isinstance(alert, dict)
        alert.pop("resolved_observed_at")
    elif mutation == "cleanup_failed":
        runtime = evidence["runtime"]
        assert isinstance(runtime, dict)
        runtime["cleanup_passed"] = False
    elif mutation == "phase_failed":
        phases = evidence["phases"]
        assert isinstance(phases, list)
        phase = phases[0]
        assert isinstance(phase, dict)
        phase["returncode"] = 1
    elif mutation == "source_drift":
        evidence["source_sha256"] = {"alerts.yml": "0" * 64}
    elif mutation == "resolved_compose_image":
        runtime = evidence["runtime"]
        assert isinstance(runtime, dict)
        resolved_images = runtime["resolved_compose_images"]
        assert isinstance(resolved_images, dict)
        image = resolved_images["prometheus"]
        assert isinstance(image, dict)
        image["resolved_compose_reference"] = "prom/prometheus:v3.12.0"
    elif mutation == "container_config_image":
        runtime = evidence["runtime"]
        assert isinstance(runtime, dict)
        images = runtime["images"]
        assert isinstance(images, dict)
        image = images["prometheus"]
        assert isinstance(image, dict)
        image["container_config_reference"] = "prom/prometheus:v3.12.0"
    elif mutation == "repository_digest_missing":
        runtime = evidence["runtime"]
        assert isinstance(runtime, dict)
        images = runtime["images"]
        assert isinstance(images, dict)
        image = images["prometheus"]
        assert isinstance(image, dict)
        image["repository_digests"] = []
    elif mutation == "container_image_id_mismatch":
        runtime = evidence["runtime"]
        assert isinstance(runtime, dict)
        images = runtime["images"]
        assert isinstance(images, dict)
        image = images["prometheus"]
        assert isinstance(image, dict)
        image["local_image_id"] = "sha256:" + "f" * 64

    assert acceptance.evidence_errors(
        evidence,
        expected_sha=SHA,
        now=now,
    )


def test_atomic_evidence_manifest_rejects_tampering(tmp_path: Path) -> None:
    output = tmp_path / "evidence"
    payload = _valid_evidence(datetime.now(UTC))

    acceptance._seal_evidence(output, payload)
    raw = (output / "evidence.json").read_bytes()
    assert (output / "manifest.sha256").read_text(encoding="utf-8") == (
        f"{hashlib.sha256(raw).hexdigest()}  evidence.json\n"
    )
    loaded = acceptance._load_sealed_evidence(output)
    assert loaded["status"] == "candidate_passed"

    (output / "manifest.sha256").write_text("0" * 64, encoding="utf-8")
    with pytest.raises(acceptance.ObservabilityAcceptanceError):
        acceptance._load_sealed_evidence(output)


def test_evidence_mutations_do_not_alias_the_fixture() -> None:
    now = datetime.now(UTC)
    original = _valid_evidence(now)
    mutated = copy.deepcopy(original)
    mutated["status"] = "failed"

    assert original["status"] == "candidate_passed"


def _fresh_external_prefix(label: str) -> Path:
    base = (
        Path(os.environ.get("SYSTEMDRIVE", "C:") + os.sep) / "tmp"
        if os.name == "nt"
        else Path("/tmp")
    )
    return base / f"ku-{label}-pycache-{uuid4().hex}"


@pytest.mark.parametrize(
    "entry_name",
    ("run_observability_acceptance.py", "observability_acceptance.py"),
)
def test_observability_entries_require_trusted_launcher_and_clean_runtime(
    entry_name: str,
) -> None:
    root = Path(__file__).parents[2]
    script = root / "scripts" / entry_name
    launcher = root / "scripts" / "acceptance_launcher.py"
    unsafe = subprocess.run(
        [sys.executable, str(script), "--isolation-probe"],
        cwd=root,
        env=dict(os.environ),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert unsafe.returncode != 0
    assert "isolated mode (-I) is required" in unsafe.stderr

    direct_prefix = _fresh_external_prefix(f"{entry_name}-direct")
    direct_command = [
        sys.executable,
        "-I",
        "-S",
        "-X",
        "utf8",
        "-X",
        f"pycache_prefix={direct_prefix}",
        str(script),
        "--isolation-probe",
    ]
    direct = subprocess.run(
        direct_command,
        cwd=root,
        env=dict(os.environ),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert direct.returncode != 0
    assert "trusted acceptance launcher is required" in direct.stderr

    preseeded_prefix = _fresh_external_prefix(f"{entry_name}-preseeded")
    preseeded_prefix.mkdir(parents=True)
    (preseeded_prefix / "payload.pyc").write_bytes(b"untrusted")
    preseeded_command = direct_command.copy()
    preseeded_command[6] = f"pycache_prefix={preseeded_prefix}"
    preseeded = subprocess.run(
        preseeded_command,
        cwd=root,
        env=dict(os.environ),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert preseeded.returncode != 0
    assert "trusted acceptance launcher is required" in preseeded.stderr

    runtime_parent = _fresh_external_prefix(f"{entry_name}-launcher-parent")
    runtime_parent.mkdir(parents=True)
    environment = dict(os.environ)
    environment.update(
        {
            "TEMP": str(runtime_parent),
            "TMP": str(runtime_parent),
            "TMPDIR": str(runtime_parent),
        }
    )
    launched = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-X",
            "utf8",
            str(launcher),
            "observability",
            "--isolation-probe",
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert launched.returncode == 0, launched.stderr
    assert "Python isolation verified" in launched.stdout
    assert list(runtime_parent.iterdir()) == []
    runtime_parent.rmdir()
