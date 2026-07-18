from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]
RUNBOOK = ROOT / "ops/runbooks/protected-release.md"
RELEASE_WORKFLOW = ROOT / ".github/workflows/protected-release.yml"
WORKFLOWS = {
    "llm": ROOT / ".github/workflows/protected-llm-evidence.yml",
    "ragflow": ROOT / ".github/workflows/protected-ragflow-evidence.yml",
}


def _only_job(path: Path) -> tuple[dict[str, object], str]:
    text = path.read_text(encoding="utf-8")
    workflow = yaml.safe_load(text)
    assert isinstance(workflow, dict)
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict)
    assert len(jobs) == 1
    job = next(iter(jobs.values()))
    assert isinstance(job, dict)
    return job, text


def test_live_evidence_jobs_are_run_bound_behind_protected_environment_approval() -> None:
    for kind, path in WORKFLOWS.items():
        job, text = _only_job(path)
        labels = job.get("runs-on")
        environment = job.get("environment")
        assert isinstance(labels, list)
        assert "self-hosted" in labels
        assert isinstance(environment, dict)
        assert environment.get("name") == "${{ inputs.environment }}"
        run_flag = "workflow-run" if kind == "llm" else "run"
        assert f'--{run_flag}-id "${{GITHUB_RUN_ID}}"' in text
        assert f'--{run_flag}-attempt "${{GITHUB_RUN_ATTEMPT}}"' in text
        assert '--nonce "${EVIDENCE_NONCE}"' in text


def test_owner_proofs_are_delivered_only_through_protected_paths_or_secrets() -> None:
    _llm_job, llm = _only_job(WORKFLOWS["llm"])
    _ragflow_job, ragflow = _only_job(WORKFLOWS["ragflow"])

    assert "vars.PROTECTED_LLM_OWNER_ATTESTATION_PATH" in llm
    assert "vars.PROTECTED_LLM_OWNER_POLICY_PATH" in llm
    assert "secrets.APPLICATION_DEPLOYMENT_ATTESTATION_JSON" in ragflow
    assert "vars.RAGFLOW_OWNER_ATTESTATION_PATH" in ragflow
    assert "vars.RAGFLOW_OWNER_POLICY_PATH" in ragflow
    assert "vars.APPLICATION_DEPLOYMENT_OWNER_POLICY_PATH" in ragflow


def test_runbook_requires_post_dispatch_signing_before_job_approval() -> None:
    runbook = RUNBOOK.read_text(encoding="utf-8")

    for statement in (
        "生成 exact `run_id`/`run_attempt`",
        "在 environment approval 仍等待时由所有者签署短期证明",
        "最后批准 job",
        "不得复用旧 nonce/签名",
        "该 workflow 不可执行",
        "状态必须保持 **PENDING**",
    ):
        assert statement in runbook


def test_owner_policy_hashes_are_independently_anchored_at_live_and_release_gates() -> None:
    _llm_job, llm = _only_job(WORKFLOWS["llm"])
    _ragflow_job, ragflow = _only_job(WORKFLOWS["ragflow"])
    release = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    runbook = RUNBOOK.read_text(encoding="utf-8")

    anchors = (
        "PROTECTED_LLM_OWNER_POLICY_SHA256",
        "RAGFLOW_OWNER_POLICY_SHA256",
        "APPLICATION_DEPLOYMENT_OWNER_POLICY_SHA256",
    )
    assert f"vars.{anchors[0]}" in llm
    assert f"vars.{anchors[1]}" in ragflow
    assert f"vars.{anchors[2]}" in ragflow
    for anchor in anchors:
        assert f"vars.{anchor}" in release
        assert f"`{anchor}`" in runbook

    assert "--expected-owner-policy-sha256" in llm
    assert "--owner-policy-sha256" in ragflow
    assert "--deployment-policy-sha256" in ragflow
    assert "--llm-owner-policy-sha256" in release
    assert "--ragflow-owner-policy-sha256" in release
    assert "--application-deployment-policy-sha256" in release
    for flag in (
        "--llm-owner-policy-sha256",
        "--ragflow-owner-policy-sha256",
        "--application-deployment-policy-sha256",
    ):
        assert flag in runbook
