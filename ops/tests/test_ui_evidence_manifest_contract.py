from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[2]
ACCEPTANCE_SCRIPT = ROOT / "frontend/e2e/acceptance.mjs"
CI_WORKFLOW = ROOT / ".github/workflows/knowledge-uploader.yml"
MANIFEST_NAME = "evidence-manifest.json"


def _run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _git(repo: Path, *args: str) -> str:
    result = _run(["git", *args], cwd=repo, check=True)
    return result.stdout.strip()


def _contract_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    script = repo / "frontend/e2e/acceptance.mjs"
    script.parent.mkdir(parents=True)
    shutil.copy2(ACCEPTANCE_SCRIPT, script)
    (repo / "tracked.txt").write_text("clean\n", encoding="utf-8")

    _git(repo, "init", "--quiet")
    _git(repo, "config", "user.name", "UI Evidence Contract")
    _git(repo, "config", "user.email", "ui-evidence@example.invalid")
    _git(repo, "add", "frontend/e2e/acceptance.mjs", "tracked.txt")
    _git(repo, "commit", "--quiet", "-m", "test fixture")
    return repo


def _run_protected(
    repo: Path,
    artifact_dir: Path,
    git_sha: str,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "E2E_ACCEPTANCE_MODE": "protected",
            "E2E_ARTIFACT_DIR": str(artifact_dir),
            "E2E_BASE_URL": "http://127.0.0.1:9",
            "E2E_GIT_SHA": git_sha,
        }
    )
    return _run(["node", "frontend/e2e/acceptance.mjs"], cwd=repo, env=env)


@pytest.mark.parametrize(
    ("git_sha", "expected_error"),
    [
        ("deadbeef", "exact lowercase 40-hex E2E_GIT_SHA"),
        ("0" * 40, "E2E_GIT_SHA does not match HEAD"),
    ],
)
def test_protected_acceptance_rejects_short_or_forged_git_sha(
    tmp_path: Path,
    git_sha: str,
    expected_error: str,
) -> None:
    repo = _contract_repo(tmp_path)
    artifact_dir = tmp_path / "evidence"

    result = _run_protected(repo, artifact_dir, git_sha)

    assert result.returncode != 0
    assert expected_error in result.stderr + result.stdout
    assert not artifact_dir.exists()


@pytest.mark.parametrize("dirty_kind", ["tracked", "untracked"])
def test_protected_acceptance_rejects_every_nonignored_dirty_path(
    tmp_path: Path,
    dirty_kind: str,
) -> None:
    repo = _contract_repo(tmp_path)
    git_sha = _git(repo, "rev-parse", "HEAD")
    dirty_path = repo / ("tracked.txt" if dirty_kind == "tracked" else "untracked.txt")
    dirty_path.write_text("dirty\n", encoding="utf-8")
    artifact_dir = tmp_path / "evidence"

    result = _run_protected(repo, artifact_dir, git_sha)

    assert result.returncode != 0
    assert "clean non-ignored worktree" in result.stderr + result.stdout
    assert not artifact_dir.exists()


def test_protected_acceptance_rejects_preexisting_evidence_directory(tmp_path: Path) -> None:
    repo = _contract_repo(tmp_path)
    git_sha = _git(repo, "rev-parse", "HEAD")
    artifact_dir = tmp_path / "evidence"
    artifact_dir.mkdir()
    stale_manifest = artifact_dir / MANIFEST_NAME
    stale_manifest.write_text('{"status":"passed"}\n', encoding="utf-8")

    result = _run_protected(repo, artifact_dir, git_sha)

    assert result.returncode != 0
    assert "stale evidence is forbidden" in result.stderr + result.stdout
    assert stale_manifest.read_text(encoding="utf-8") == '{"status":"passed"}\n'


def test_missing_screenshot_runtime_issue_or_source_drift_precedes_passed_manifest() -> None:
    source = ACCEPTANCE_SCRIPT.read_text(encoding="utf-8")
    seal_start = source.index("async function sealProtectedEvidence()")
    seal_end = source.index("\nasync function focusAndActivate", seal_start)
    seal = source[seal_start:seal_end]
    passed_index = seal.index('status: "passed"')

    for guard in (
        "const finalSourceIdentity = await validateProtectedSourceIdentity();",
        "finalSourceIdentity.gitSha === protectedSourceIdentity.gitSha",
        "finalSourceIdentity.gitTree === protectedSourceIdentity.gitTree",
        "validateProtectedArtifacts();",
        "runtimeWarnings.length === 0",
        "runtimeErrors.length === 0",
        "const screenshots = await buildProtectedScreenshotEvidence();",
    ):
        assert seal.index(guard) < passed_index

    assert "Required screenshot was not captured" in source
    assert "capturedArtifacts.size === requiredArtifactNames.length" in source
    assert 'contents.subarray(0, 8).toString("hex") === "89504e470d0a1a0a"' in source
    assert 'createHash("sha256").update(contents).digest("hex")' in source


def test_manifest_is_self_describing_and_atomically_publishes_only_after_validation() -> None:
    source = ACCEPTANCE_SCRIPT.read_text(encoding="utf-8")
    required_fields = (
        'schema: "knowledge-uploader.protected-ui-evidence/v1"',
        'status: "passed"',
        "generated_at:",
        "git_sha:",
        "git_tree:",
        "base_url:",
        "browser:",
        "scenarios:",
        "screenshots,",
        "runtime_boundary:",
        "maximum_errors: 0",
        "maximum_warnings: 0",
    )
    for field in required_fields:
        assert field in source

    assert "await mkdtemp(" in source
    assert "E2E_ARTIFACT_DIR must not already exist; stale evidence is forbidden" in source
    manifest_rename = source.index("await rename(manifestTempPath, manifestPath);")
    publish_validation = source.index(
        "const publishSourceIdentity = await validateProtectedSourceIdentity();"
    )
    directory_rename = source.index("await rename(stagingDir, protectedArtifactDestination);")
    assert manifest_rename < publish_validation < directory_rename
    assert source.index("await browser.close();") < source.index(
        "const evidenceManifest = await sealProtectedEvidence();"
    )


def test_main_ci_injects_exact_source_sha_and_uploads_only_sealed_evidence() -> None:
    workflow = yaml.load(CI_WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(workflow, dict)
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict)
    job = jobs.get("lint-test-arm64")
    assert isinstance(job, dict)
    steps = job.get("steps")
    assert isinstance(steps, list)
    by_name = {step["name"]: step for step in steps if isinstance(step, dict) and "name" in step}

    run_step = by_name["Run protected UI acceptance"]
    environment = run_step.get("env")
    assert isinstance(environment, dict)
    assert environment.get("E2E_ACCEPTANCE_MODE") == "protected"
    assert environment.get("E2E_GIT_SHA") == "${{ github.sha }}"
    run = str(run_step.get("run"))
    assert 'test ! -e "${E2E_ARTIFACT_DIR}"' in run
    assert 'test -f "${E2E_ARTIFACT_DIR}/evidence-manifest.json"' in run
    assert 'mkdir -p "${E2E_ARTIFACT_DIR}"' not in run

    upload_step = by_name["Upload UI acceptance evidence"]
    assert upload_step.get("if") == "${{ success() && !env.ACT }}"
    options = upload_step.get("with")
    assert isinstance(options, dict)
    assert "knowledge-uploader-ui-${{ github.sha }}" in str(options.get("path"))
    assert options.get("if-no-files-found") == "error"
