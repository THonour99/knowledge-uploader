from __future__ import annotations

import copy
import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest

TEST_SHA = "a" * 40
TEST_REPOSITORY = "example/knowledge-uploader"
NOW = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)


def _load_module() -> ModuleType:
    path = Path(__file__).parents[2] / "scripts/release_workflow_trust.py"
    spec = importlib.util.spec_from_file_location("release_workflow_trust", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load release_workflow_trust")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _run(
    *,
    run_id: int,
    attempt: int,
    path: str,
    event: str,
    conclusion: str | None,
    head_branch: str,
    sha: str = TEST_SHA,
    repository: str = TEST_REPOSITORY,
    repository_id: int = 77,
) -> dict[str, object]:
    return {
        "id": run_id,
        "run_attempt": attempt,
        "path": path,
        "head_sha": sha,
        "head_branch": head_branch,
        "event": event,
        "status": "completed" if conclusion is not None else "in_progress",
        "conclusion": conclusion,
        "created_at": (NOW - timedelta(minutes=15)).isoformat(),
        "updated_at": (NOW - timedelta(minutes=1)).isoformat(),
        "repository": {"id": repository_id, "full_name": repository},
    }


def _artifact(*, artifact_id: int, name: str, run_id: int) -> dict[str, object]:
    return {
        "id": artifact_id,
        "name": name,
        "size_in_bytes": 4096,
        "digest": "sha256:" + f"{artifact_id:064x}",
        "expired": False,
        "created_at": (NOW - timedelta(minutes=5)).isoformat(),
        "expires_at": (NOW + timedelta(days=1)).isoformat(),
        "workflow_run": {"id": run_id},
    }


class FakeClient:
    def __init__(self, responses: dict[str, dict[str, object]]) -> None:
        self.responses = responses

    def get(self, path: str) -> dict[str, object]:
        if path not in self.responses:
            raise AssertionError(f"unexpected API request: {path}")
        return copy.deepcopy(self.responses[path])


def _responses(module: ModuleType) -> dict[str, dict[str, object]]:
    bundle_name = f"release-oci-bundle-{TEST_SHA}-101-2"
    provenance_name = f"release-oci-provenance-{TEST_SHA}-101-2"
    return {
        f"/repos/{TEST_REPOSITORY}": {
            "id": 77,
            "full_name": TEST_REPOSITORY,
            "default_branch": "main",
        },
        f"/repos/{TEST_REPOSITORY}/actions/runs/101": _run(
            run_id=101,
            attempt=2,
            path=module.MAIN_WORKFLOW,
            event="push",
            conclusion="success",
            head_branch="main",
        ),
        f"/repos/{TEST_REPOSITORY}/actions/runs/101/artifacts?per_page=100": {
            "total_count": 2,
            "artifacts": [
                _artifact(artifact_id=901, name=bundle_name, run_id=101),
                _artifact(artifact_id=902, name=provenance_name, run_id=101),
            ],
        },
        f"/repos/{TEST_REPOSITORY}/actions/runs/202": _run(
            run_id=202,
            attempt=1,
            path=module.DGX_WORKFLOW,
            event="workflow_dispatch",
            conclusion=None,
            head_branch="main",
        ),
        f"/repos/{TEST_REPOSITORY}/actions/runs/303": _run(
            run_id=303,
            attempt=1,
            path=module.PROTECTED_WORKFLOW,
            event="workflow_dispatch",
            conclusion=None,
            head_branch="main",
        ),
        f"/repos/{TEST_REPOSITORY}/actions/runs/404": _run(
            run_id=404,
            attempt=3,
            path=module.DGX_WORKFLOW,
            event="workflow_dispatch",
            conclusion="success",
            head_branch="main",
        ),
        f"/repos/{TEST_REPOSITORY}/actions/runs/404/artifacts?per_page=100": {
            "total_count": 1,
            "artifacts": [
                _artifact(
                    artifact_id=903,
                    name=f"dgx-spark-evidence-{TEST_SHA}-404-3",
                    run_id=404,
                )
            ],
        },
        f"/repos/{TEST_REPOSITORY}/actions/runs/505": _run(
            run_id=505,
            attempt=1,
            path=module.EXTERNAL_WORKFLOW,
            event="workflow_dispatch",
            conclusion="success",
            head_branch="main",
        ),
        f"/repos/{TEST_REPOSITORY}/actions/runs/505/artifacts?per_page=100": {
            "total_count": 1,
            "artifacts": [
                _artifact(
                    artifact_id=904,
                    name=f"protected-release-external-evidence-{TEST_SHA}-505-1",
                    run_id=505,
                )
            ],
        },
    }


def _build_dgx(module: ModuleType, responses: dict[str, dict[str, object]]) -> dict[str, object]:
    summary = module.build_trust_summary(
        FakeClient(responses),
        repository=TEST_REPOSITORY,
        git_sha=TEST_SHA,
        ref="refs/heads/main",
        ref_protected=True,
        current_role="dgx",
        current_run_id=202,
        current_run_attempt=1,
        current_workflow=module.DGX_WORKFLOW,
        main_run_id=101,
        main_run_attempt=2,
        evidence_runs=[],
        now=NOW,
    )
    return dict(summary)


def test_dgx_trust_requires_exact_successful_main_run_and_immutable_artifacts() -> None:
    module = _load_module()
    summary = _build_dgx(module, _responses(module))

    assert summary["release_ref"] == {
        "ref": "refs/heads/main",
        "kind": "protected_default_branch",
        "git_sha": TEST_SHA,
    }
    main = summary["main_ci"]
    assert isinstance(main, dict)
    assert main["run_id"] == 101
    artifacts = main["artifacts"]
    assert isinstance(artifacts, dict)
    assert artifacts["bundle"]["digest"].startswith("sha256:")
    assert artifacts["provenance"]["workflow_run_id"] == 101
    assert (
        module.validate_trust_summary(
            summary,
            expected_repository=TEST_REPOSITORY,
            expected_git_sha=TEST_SHA,
            expected_current_role="dgx",
            now=NOW + timedelta(minutes=1),
        )
        == summary
    )


def test_protected_release_binds_distinct_main_dgx_external_and_current_runs(
    tmp_path: Path,
) -> None:
    module = _load_module()
    responses = _responses(module)
    summary = module.build_trust_summary(
        FakeClient(responses),
        repository=TEST_REPOSITORY,
        git_sha=TEST_SHA,
        ref="refs/heads/main",
        ref_protected=True,
        current_role="protected_release",
        current_run_id=303,
        current_run_attempt=1,
        current_workflow=module.PROTECTED_WORKFLOW,
        main_run_id=101,
        main_run_attempt=2,
        evidence_runs=[
            module.EvidenceRunRequest("dgx", 404, 3, module.DGX_WORKFLOW),
            module.EvidenceRunRequest("external", 505, 1, module.EXTERNAL_WORKFLOW),
        ],
        now=NOW,
    )

    evidence = summary["evidence_runs"]
    assert isinstance(evidence, list)
    assert {row["role"] for row in evidence} == {"dgx", "external"}
    assert {row["artifact"]["workflow_run_id"] for row in evidence} == {404, 505}
    assert all(str(row["artifact"]["digest"]).startswith("sha256:") for row in evidence)
    github_output = tmp_path / "github-output"
    module._write_github_outputs(github_output, summary)
    assert github_output.read_text(encoding="utf-8").splitlines() == [
        "dgx_artifact_id=903",
        "external_artifact_id=904",
        "main_bundle_artifact_id=901",
        "main_provenance_artifact_id=902",
    ]


def test_protected_release_rejects_rerun_attempt_or_evidence_artifact_mismatch() -> None:
    module = _load_module()
    responses = _responses(module)

    with pytest.raises(module.TrustError, match="attempt mismatch"):
        module.build_trust_summary(
            FakeClient(responses),
            repository=TEST_REPOSITORY,
            git_sha=TEST_SHA,
            ref="refs/heads/main",
            ref_protected=True,
            current_role="protected_release",
            current_run_id=303,
            current_run_attempt=1,
            current_workflow=module.PROTECTED_WORKFLOW,
            main_run_id=101,
            main_run_attempt=2,
            evidence_runs=[
                module.EvidenceRunRequest("dgx", 404, 2, module.DGX_WORKFLOW),
                module.EvidenceRunRequest("external", 505, 1, module.EXTERNAL_WORKFLOW),
            ],
            now=NOW,
        )

    responses = _responses(module)
    artifact_key = f"/repos/{TEST_REPOSITORY}/actions/runs/404/artifacts?per_page=100"
    artifacts = responses[artifact_key]["artifacts"]
    assert isinstance(artifacts, list)
    artifacts[0]["name"] = f"dgx-spark-evidence-{TEST_SHA}-404-2"
    with pytest.raises(module.TrustError, match="exactly one immutable dgx artifact"):
        module.build_trust_summary(
            FakeClient(responses),
            repository=TEST_REPOSITORY,
            git_sha=TEST_SHA,
            ref="refs/heads/main",
            ref_protected=True,
            current_role="protected_release",
            current_run_id=303,
            current_run_attempt=1,
            current_workflow=module.PROTECTED_WORKFLOW,
            main_run_id=101,
            main_run_attempt=2,
            evidence_runs=[
                module.EvidenceRunRequest("dgx", 404, 3, module.DGX_WORKFLOW),
                module.EvidenceRunRequest("external", 505, 1, module.EXTERNAL_WORKFLOW),
            ],
            now=NOW,
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("conclusion", "failure", "did not complete successfully"),
        ("head_sha", "b" * 40, "Git SHA mismatch"),
        ("path", "__dgx_workflow__", "unexpected workflow"),
        (
            "repository",
            {"id": 78, "full_name": "attacker/fork"},
            "different repository",
        ),
        (
            "created_at",
            (NOW - timedelta(hours=9)).isoformat(),
            "stale or in the future",
        ),
    ],
)
def test_main_ci_forgery_is_rejected(field: str, value: object, message: str) -> None:
    module = _load_module()
    responses = _responses(module)
    if value == "__dgx_workflow__":
        value = module.DGX_WORKFLOW
    responses[f"/repos/{TEST_REPOSITORY}/actions/runs/101"][field] = value

    with pytest.raises(module.TrustError, match=message):
        _build_dgx(module, responses)


def test_unprotected_or_arbitrary_workflow_dispatch_ref_is_rejected() -> None:
    module = _load_module()
    responses = _responses(module)

    with pytest.raises(module.TrustError, match="not protected"):
        module.build_trust_summary(
            FakeClient(responses),
            repository=TEST_REPOSITORY,
            git_sha=TEST_SHA,
            ref="refs/heads/main",
            ref_protected=False,
            current_role="dgx",
            current_run_id=202,
            current_run_attempt=1,
            current_workflow=module.DGX_WORKFLOW,
            main_run_id=101,
            main_run_attempt=2,
            evidence_runs=[],
            now=NOW,
        )
    with pytest.raises(module.TrustError, match="neither the default branch"):
        module.build_trust_summary(
            FakeClient(responses),
            repository=TEST_REPOSITORY,
            git_sha=TEST_SHA,
            ref="refs/heads/feature/attacker",
            ref_protected=True,
            current_role="dgx",
            current_run_id=202,
            current_run_attempt=1,
            current_workflow=module.DGX_WORKFLOW,
            main_run_id=101,
            main_run_attempt=2,
            evidence_runs=[],
            now=NOW,
        )


def test_signed_release_tag_must_be_annotated_verified_and_point_to_sha() -> None:
    module = _load_module()
    responses = _responses(module)
    responses[f"/repos/{TEST_REPOSITORY}/git/ref/tags/v1.2.3"] = {
        "object": {"type": "tag", "sha": "c" * 40}
    }
    responses[f"/repos/{TEST_REPOSITORY}/git/tags/{'c' * 40}"] = {
        "verification": {"verified": True},
        "object": {"type": "commit", "sha": TEST_SHA},
    }

    summary = module.build_trust_summary(
        FakeClient(responses),
        repository=TEST_REPOSITORY,
        git_sha=TEST_SHA,
        ref="refs/tags/v1.2.3",
        ref_protected=True,
        current_role="dgx",
        current_run_id=202,
        current_run_attempt=1,
        current_workflow=module.DGX_WORKFLOW,
        main_run_id=101,
        main_run_attempt=2,
        evidence_runs=[],
        now=NOW,
    )
    assert summary["release_ref"]["kind"] == "protected_signed_tag"

    responses[f"/repos/{TEST_REPOSITORY}/git/ref/tags/v1.2.3"] = {
        "object": {"type": "commit", "sha": TEST_SHA}
    }
    with pytest.raises(module.TrustError, match="lightweight"):
        module.build_trust_summary(
            FakeClient(responses),
            repository=TEST_REPOSITORY,
            git_sha=TEST_SHA,
            ref="refs/tags/v1.2.3",
            ref_protected=True,
            current_role="dgx",
            current_run_id=202,
            current_run_attempt=1,
            current_workflow=module.DGX_WORKFLOW,
            main_run_id=101,
            main_run_attempt=2,
            evidence_runs=[],
            now=NOW,
        )


def test_artifact_replay_and_mutability_checks_fail_closed() -> None:
    module = _load_module()
    responses = _responses(module)
    artifacts_key = f"/repos/{TEST_REPOSITORY}/actions/runs/101/artifacts?per_page=100"
    artifacts = responses[artifacts_key]["artifacts"]
    assert isinstance(artifacts, list)
    artifacts[0]["digest"] = "missing"

    with pytest.raises(module.TrustError, match="immutable SHA-256"):
        _build_dgx(module, responses)

    responses = _responses(module)
    artifacts = responses[artifacts_key]["artifacts"]
    assert isinstance(artifacts, list)
    artifacts.append(copy.deepcopy(artifacts[0]))
    with pytest.raises(module.TrustError, match="exactly one"):
        _build_dgx(module, responses)


def test_summary_extra_fields_expiry_and_duplicate_run_ids_are_rejected() -> None:
    module = _load_module()
    summary = _build_dgx(module, _responses(module))
    summary["attacker_override"] = True
    with pytest.raises(module.TrustError, match="schema mismatch"):
        module.validate_trust_summary(
            summary,
            expected_repository=TEST_REPOSITORY,
            expected_git_sha=TEST_SHA,
            expected_current_role="dgx",
            now=NOW,
        )

    summary = _build_dgx(module, _responses(module))
    with pytest.raises(module.TrustError, match="stale"):
        module.validate_trust_summary(
            summary,
            expected_repository=TEST_REPOSITORY,
            expected_git_sha=TEST_SHA,
            expected_current_role="dgx",
            now=NOW + timedelta(hours=3),
        )

    responses = _responses(module)
    responses[f"/repos/{TEST_REPOSITORY}/actions/runs/101"] = _run(
        run_id=303,
        attempt=2,
        path=module.MAIN_WORKFLOW,
        event="push",
        conclusion="success",
        head_branch="main",
    )
    with pytest.raises(module.TrustError, match="run ID mismatch"):
        _build_dgx(module, responses)
