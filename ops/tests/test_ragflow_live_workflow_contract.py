from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]
WORKFLOW_PATH = ROOT / ".github/workflows/protected-ragflow-evidence.yml"
LOCK_PATH = ROOT / "ops/requirements-protected-ragflow-evidence.txt"

ACTION_PINS = {
    "actions/checkout": "11bd71901bbe5b1630ceea73d27597364c9af683",
    "actions/setup-python": "a26af69be951a213d495a4c3e4e4022e16d87065",
    "actions/upload-artifact": "ea165f8d65b6e75b540449e92b4886f43607fa02",
}


def _workflow() -> tuple[dict[str, object], str]:
    raw = WORKFLOW_PATH.read_text(encoding="utf-8")
    parsed = yaml.load(raw, Loader=yaml.BaseLoader)
    assert isinstance(parsed, dict)
    return parsed, raw


def test_workflow_is_manual_protected_and_least_privilege() -> None:
    workflow, _raw = _workflow()
    triggers = workflow["on"]
    assert isinstance(triggers, dict)
    assert set(triggers) == {"workflow_dispatch"}
    dispatch = triggers["workflow_dispatch"]
    assert isinstance(dispatch, dict)
    inputs = dispatch["inputs"]
    assert isinstance(inputs, dict)
    assert set(inputs) == {
        "environment",
        "main_ci_run_id",
        "main_ci_run_attempt",
        "nonce",
    }
    assert workflow["permissions"] == {"actions": "read", "contents": "read"}

    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    assert set(jobs) == {"collect-protected-ragflow-evidence"}
    job = jobs["collect-protected-ragflow-evidence"]
    assert isinstance(job, dict)
    assert job["runs-on"] == [
        "self-hosted",
        "Linux",
        "X64",
        "protected-ragflow-evidence",
    ]
    assert int(job["timeout-minutes"]) >= 30
    assert job["environment"] == {"name": "${{ inputs.environment }}"}
    global_env = job["env"]
    assert isinstance(global_env, dict)
    assert not any("secrets." in str(value) for value in global_env.values())


def test_evidence_entrypoints_support_package_tests_and_direct_workflow_execution() -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join((str(ROOT / "backend"), str(ROOT / "scripts")))
    for relative_path in (
        "scripts/run_ragflow_live_evidence.py",
        "scripts/collect_ragflow_live_evidence.py",
    ):
        result = subprocess.run(
            [sys.executable, str(ROOT / relative_path), "--help"],
            cwd=ROOT,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        assert "usage:" in result.stdout
        assert result.stderr == ""


def test_workflow_actions_are_full_sha_pinned_and_runtime_is_hash_locked() -> None:
    _workflow_document, raw = _workflow()
    uses = re.findall(r"(?m)^\s*uses:\s*([^\s#]+)", raw)
    assert uses
    for action in uses:
        name, pin = action.split("@", 1)
        assert pin == ACTION_PINS[name]
        assert re.fullmatch(r"[0-9a-f]{40}", pin)
    assert "--require-hashes --only-binary=:all:" in raw
    lock = LOCK_PATH.read_text(encoding="utf-8")
    assert "--hash=sha256:" in lock
    assert "x86_64" in lock


def test_workflow_fails_closed_on_signed_trust_and_real_service_only() -> None:
    _workflow_document, raw = _workflow()
    lowered = raw.lower()
    assert "--current-role ragflow_live" in raw
    assert "--ref-protected" in raw
    assert "release-workflow-trust.json.sha256" in raw
    assert "vars.RAGFLOW_OWNER_ATTESTATION_PATH" in raw
    assert "vars.RAGFLOW_OWNER_POLICY_PATH" in raw
    assert "vars.RAGFLOW_OWNER_POLICY_SHA256" in raw
    assert raw.count('--owner-attestation "${RAGFLOW_OWNER_ATTESTATION_PATH}"') == 3
    assert raw.count('--owner-policy "${RAGFLOW_OWNER_POLICY_PATH}"') == 3
    assert raw.count('--owner-policy-sha256 "${RAGFLOW_OWNER_POLICY_SHA256}"') == 3
    assert "vars.APPLICATION_DEPLOYMENT_OWNER_POLICY_PATH" in raw
    assert "vars.APPLICATION_DEPLOYMENT_OWNER_POLICY_SHA256" in raw
    assert raw.count('--deployment-policy "${APPLICATION_DEPLOYMENT_OWNER_POLICY_PATH}"') == 3
    assert (
        raw.count('--deployment-policy-sha256 "${APPLICATION_DEPLOYMENT_OWNER_POLICY_SHA256}"') == 3
    )
    assert "ops/policies/application-deployment-owner-policy.v1.json" not in raw
    assert "application-deployment-owner-policy.v1.example.json" not in raw
    assert "endpoint-owner-attestation-policy.v1.example.json" not in raw
    assert "scripts/run_ragflow_live_evidence.py probe" in raw
    assert "scripts/run_ragflow_live_evidence.py janitor" in raw
    assert "scripts/collect_ragflow_live_evidence.py" in raw
    for forbidden in (
        "protected_evidence_source_dir",
        "mock ragflow",
        "fault injection",
        "timeout simulation",
        "openssl",
        "curl -k",
        "verify=false",
        "self-sign",
        "self_sign",
        "hmac",
        "private_key",
    ):
        assert forbidden not in lowered


def test_secrets_are_step_scoped_and_final_upload_requires_cleanup() -> None:
    workflow, raw = _workflow()
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    job = jobs["collect-protected-ragflow-evidence"]
    assert isinstance(job, dict)
    steps = job["steps"]
    assert isinstance(steps, list)
    by_id = {
        step["id"]: step
        for step in steps
        if isinstance(step, dict) and isinstance(step.get("id"), str)
    }
    assert "always()" in str(by_id["janitor"]["if"])
    assert by_id["collect"]["if"] == "${{ success() }}"
    assert by_id["scrub"]["if"] == "${{ always() }}"

    upload = next(
        step
        for step in steps
        if isinstance(step, dict)
        and str(step.get("uses", "")).startswith("actions/upload-artifact@")
    )
    assert "success()" in str(upload["if"])
    assert "steps.collect.outcome == 'success'" in str(upload["if"])
    assert "steps.scrub.outcome == 'success'" in str(upload["if"])
    assert upload["with"]["name"] == (
        "protected-ragflow-evidence-${{ github.sha }}-"
        "${{ github.run_id }}-${{ github.run_attempt }}"
    )
    assert upload["with"]["path"] == "artifacts/ragflow"
    assert "artifacts/private" not in str(upload["with"])

    probe_env = by_id["probe"]["env"]
    janitor_env = by_id["janitor"]["env"]
    assert isinstance(probe_env, dict)
    assert isinstance(janitor_env, dict)
    for field in (
        "KU_APP_BASE_URL",
        "KU_EMPLOYEE_PASSWORD",
        "KU_ADMIN_PASSWORD",
        "KU_RAGFLOW_BASE_URL",
        "KU_RAGFLOW_API_KEY",
    ):
        assert "secrets." in str(probe_env[field])
        assert "secrets." in str(janitor_env[field])
    assert "APPLICATION_DEPLOYMENT_ATTESTATION_JSON" in raw


def test_collector_receives_every_independently_verifiable_source() -> None:
    _workflow_document, raw = _workflow()
    for argument in (
        "--owner-attestation",
        "--owner-policy",
        "--deployment-attestation",
        "--deployment-policy",
        "--deployment-identity-sha256",
        "--workflow-trust",
        "--workflow-trust-checksum",
        "--nonce",
    ):
        assert argument in raw
    assert "--output-dir artifacts/ragflow" in raw
    assert "artifacts/private" in raw
    assert "unexpected private artifact entry" in raw
