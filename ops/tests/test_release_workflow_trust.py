from __future__ import annotations

import copy
import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Protocol, cast

import pytest

TEST_SHA = "a" * 40
TEST_REPOSITORY = "example/knowledge-uploader"
NOW = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)


class EvidenceRequestLike(Protocol):
    @property
    def role(self) -> str: ...


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
        f"/repos/{TEST_REPOSITORY}/actions/runs/606": _run(
            run_id=606,
            attempt=2,
            path=module.LLM_LIVE_WORKFLOW,
            event="workflow_dispatch",
            conclusion=None,
            head_branch="main",
        ),
        f"/repos/{TEST_REPOSITORY}/actions/runs/707": _run(
            run_id=707,
            attempt=4,
            path=module.RAGFLOW_LIVE_WORKFLOW,
            event="workflow_dispatch",
            conclusion=None,
            head_branch="main",
        ),
        f"/repos/{TEST_REPOSITORY}/actions/runs/808": _run(
            run_id=808,
            attempt=2,
            path=module.LLM_LIVE_WORKFLOW,
            event="workflow_dispatch",
            conclusion="success",
            head_branch="main",
        ),
        f"/repos/{TEST_REPOSITORY}/actions/runs/808/artifacts?per_page=100": {
            "total_count": 1,
            "artifacts": [
                _artifact(
                    artifact_id=905,
                    name=f"protected-llm-evidence-{TEST_SHA}-808-2",
                    run_id=808,
                )
            ],
        },
        f"/repos/{TEST_REPOSITORY}/actions/runs/909": _run(
            run_id=909,
            attempt=5,
            path=module.RAGFLOW_LIVE_WORKFLOW,
            event="workflow_dispatch",
            conclusion="success",
            head_branch="main",
        ),
        f"/repos/{TEST_REPOSITORY}/actions/runs/909/artifacts?per_page=100": {
            "total_count": 1,
            "artifacts": [
                _artifact(
                    artifact_id=906,
                    name=f"protected-ragflow-evidence-{TEST_SHA}-909-5",
                    run_id=909,
                )
            ],
        },
    }


def _protected_evidence_requests(
    module: ModuleType,
    *,
    dgx_attempt: int = 3,
) -> list[EvidenceRequestLike]:
    return [
        cast(
            EvidenceRequestLike,
            module.EvidenceRunRequest("dgx", 404, dgx_attempt, module.DGX_WORKFLOW),
        ),
        cast(
            EvidenceRequestLike,
            module.EvidenceRunRequest("external", 505, 1, module.EXTERNAL_WORKFLOW),
        ),
        cast(
            EvidenceRequestLike,
            module.EvidenceRunRequest("llm_live", 808, 2, module.LLM_LIVE_WORKFLOW),
        ),
        cast(
            EvidenceRequestLike,
            module.EvidenceRunRequest("ragflow_live", 909, 5, module.RAGFLOW_LIVE_WORKFLOW),
        ),
    ]


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


def _build_live(
    module: ModuleType,
    responses: dict[str, dict[str, object]],
    role: str,
) -> dict[str, object]:
    current_runs = {
        "llm_live": (606, 2, module.LLM_LIVE_WORKFLOW),
        "ragflow_live": (707, 4, module.RAGFLOW_LIVE_WORKFLOW),
    }
    run_id, run_attempt, workflow = current_runs[role]
    summary = module.build_trust_summary(
        FakeClient(responses),
        repository=TEST_REPOSITORY,
        git_sha=TEST_SHA,
        ref="refs/heads/main",
        ref_protected=True,
        current_role=role,
        current_run_id=run_id,
        current_run_attempt=run_attempt,
        current_workflow=workflow,
        main_run_id=101,
        main_run_attempt=2,
        evidence_runs=[],
        now=NOW,
    )
    return dict(summary)


def _build_protected(
    module: ModuleType,
    responses: dict[str, dict[str, object]],
) -> dict[str, object]:
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
        evidence_runs=_protected_evidence_requests(module),
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


def test_protected_release_binds_all_distinct_evidence_and_current_runs(
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
        evidence_runs=_protected_evidence_requests(module),
        now=NOW,
    )

    evidence = summary["evidence_runs"]
    assert isinstance(evidence, list)
    assert {row["role"] for row in evidence} == {
        "dgx",
        "external",
        "llm_live",
        "ragflow_live",
    }
    assert {row["artifact"]["workflow_run_id"] for row in evidence} == {404, 505, 808, 909}
    assert all(str(row["artifact"]["digest"]).startswith("sha256:") for row in evidence)
    github_output = tmp_path / "github-output"
    module._write_github_outputs(github_output, summary)
    assert github_output.read_text(encoding="utf-8").splitlines() == [
        "dgx_artifact_id=903",
        "external_artifact_id=904",
        "llm_live_artifact_id=905",
        "main_bundle_artifact_id=901",
        "main_provenance_artifact_id=902",
        "ragflow_live_artifact_id=906",
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
            evidence_runs=_protected_evidence_requests(module, dgx_attempt=2),
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
            evidence_runs=_protected_evidence_requests(module),
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
    responses[f"/repos/{TEST_REPOSITORY}/actions/runs/202"]["head_branch"] = "v1.2.3"
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


def test_summary_checksum_and_parser_use_the_same_captured_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module()
    summary = _build_dgx(module, _responses(module))
    summary_path = tmp_path / "release-workflow-trust.json"
    module._write_summary(summary_path, summary)
    original_payload = summary_path.read_bytes()
    forged_path = tmp_path / "forged-summary.json"
    forged_path.write_text('{"attacker_override":true}\n', encoding="utf-8", newline="\n")
    original_reader = module._read_stable_regular_file
    exchanged = False

    def exchange_after_summary_snapshot(
        path: Path,
        *,
        context: str,
        maximum: int,
    ) -> bytes:
        nonlocal exchanged
        payload = cast(bytes, original_reader(path, context=context, maximum=maximum))
        if path == summary_path and not exchanged:
            forged_path.replace(summary_path)
            exchanged = True
        return payload

    monkeypatch.setattr(module, "_read_stable_regular_file", exchange_after_summary_snapshot)

    assert module._load_summary(summary_path) == summary
    assert original_payload != summary_path.read_bytes()
    assert summary_path.read_text(encoding="utf-8") == '{"attacker_override":true}\n'


def test_summary_reader_rejects_symlink(tmp_path: Path) -> None:
    module = _load_module()
    target = tmp_path / "release-workflow-trust.json"
    module._write_summary(target, _build_dgx(module, _responses(module)))
    link = tmp_path / "linked-summary.json"
    try:
        link.symlink_to(target.name)
    except OSError:
        pytest.skip("symlink creation is unavailable on this runner")

    with pytest.raises(module.TrustError, match="not a regular file"):
        module._load_summary(link)


@pytest.mark.parametrize(
    ("role", "run_id", "run_attempt", "workflow_attribute"),
    [
        ("llm_live", 606, 2, "LLM_LIVE_WORKFLOW"),
        ("ragflow_live", 707, 4, "RAGFLOW_LIVE_WORKFLOW"),
    ],
)
def test_live_current_roles_bind_exact_run_and_main_artifacts(
    role: str,
    run_id: int,
    run_attempt: int,
    workflow_attribute: str,
) -> None:
    module = _load_module()
    summary = _build_live(module, _responses(module), role)

    current = summary["current"]
    assert isinstance(current, dict)
    assert current["role"] == role
    assert current["run_id"] == run_id
    assert current["run_attempt"] == run_attempt
    assert current["workflow_path"] == getattr(module, workflow_attribute)
    assert current["event"] == "workflow_dispatch"
    assert current["head_sha"] == TEST_SHA
    assert current["head_branch"] == "main"
    assert summary["evidence_runs"] == []
    main = summary["main_ci"]
    assert isinstance(main, dict)
    assert main["run_id"] == 101
    assert main["run_attempt"] == 2
    assert set(main["artifacts"]) == {"bundle", "provenance"}
    assert (
        module.validate_trust_summary(
            summary,
            expected_repository=TEST_REPOSITORY,
            expected_git_sha=TEST_SHA,
            expected_current_role=role,
            now=NOW + timedelta(minutes=1),
        )
        == summary
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("path", "__wrong_workflow__", "unexpected workflow"),
        ("event", "push", "event mismatch"),
        ("head_sha", "b" * 40, "Git SHA mismatch"),
        ("id", 607, "run ID mismatch"),
        ("run_attempt", 3, "attempt mismatch"),
        ("head_branch", "feature/unprotected", "branch mismatch"),
        (
            "repository",
            {"id": 78, "full_name": "attacker/fork"},
            "different repository",
        ),
    ],
)
def test_live_current_api_forgery_is_rejected(
    field: str,
    value: object,
    message: str,
) -> None:
    module = _load_module()
    responses = _responses(module)
    if value == "__wrong_workflow__":
        value = module.RAGFLOW_LIVE_WORKFLOW
    responses[f"/repos/{TEST_REPOSITORY}/actions/runs/606"][field] = value

    with pytest.raises(module.TrustError, match=message):
        _build_live(module, responses, "llm_live")


def test_live_current_rejects_wrong_declared_role_path_and_any_evidence_input() -> None:
    module = _load_module()
    responses = _responses(module)
    common = {
        "repository": TEST_REPOSITORY,
        "git_sha": TEST_SHA,
        "ref": "refs/heads/main",
        "ref_protected": True,
        "current_run_id": 606,
        "current_run_attempt": 2,
        "main_run_id": 101,
        "main_run_attempt": 2,
        "now": NOW,
    }

    with pytest.raises(module.TrustError, match="current_role is invalid"):
        module.build_trust_summary(
            FakeClient(responses),
            current_role="external",
            current_workflow=module.EXTERNAL_WORKFLOW,
            evidence_runs=[],
            **common,
        )
    with pytest.raises(module.TrustError, match="does not match its declared role"):
        module.build_trust_summary(
            FakeClient(responses),
            current_role="llm_live",
            current_workflow=module.RAGFLOW_LIVE_WORKFLOW,
            evidence_runs=[],
            **common,
        )
    with pytest.raises(module.TrustError, match="inventory is incomplete or duplicated"):
        module.build_trust_summary(
            FakeClient(responses),
            current_role="llm_live",
            current_workflow=module.LLM_LIVE_WORKFLOW,
            evidence_runs=[module.EvidenceRunRequest("dgx", 404, 3, module.DGX_WORKFLOW)],
            **common,
        )


@pytest.mark.parametrize("missing_role", ["llm_live", "ragflow_live"])
def test_protected_release_rejects_missing_or_duplicate_live_role(missing_role: str) -> None:
    module = _load_module()
    responses = _responses(module)
    common = {
        "repository": TEST_REPOSITORY,
        "git_sha": TEST_SHA,
        "ref": "refs/heads/main",
        "ref_protected": True,
        "current_role": "protected_release",
        "current_run_id": 303,
        "current_run_attempt": 1,
        "current_workflow": module.PROTECTED_WORKFLOW,
        "main_run_id": 101,
        "main_run_attempt": 2,
        "now": NOW,
    }
    requests = [
        request for request in _protected_evidence_requests(module) if request.role != missing_role
    ]
    with pytest.raises(module.TrustError, match="inventory is incomplete or duplicated"):
        module.build_trust_summary(
            FakeClient(responses),
            evidence_runs=requests,
            **common,
        )

    requests = _protected_evidence_requests(module)
    requests.append(next(request for request in requests if request.role == missing_role))
    with pytest.raises(module.TrustError, match="inventory is incomplete or duplicated"):
        module.build_trust_summary(
            FakeClient(responses),
            evidence_runs=requests,
            **common,
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("name", "protected-llm-evidence-forged", "exactly one immutable llm_live artifact"),
        ("digest", "sha256:invalid", "immutable SHA-256"),
        ("workflow_run", {"id": 999}, "bound to a different workflow run"),
    ],
)
def test_protected_release_rejects_live_artifact_api_forgery(
    field: str,
    value: object,
    message: str,
) -> None:
    module = _load_module()
    responses = _responses(module)
    artifacts = responses[f"/repos/{TEST_REPOSITORY}/actions/runs/808/artifacts?per_page=100"][
        "artifacts"
    ]
    assert isinstance(artifacts, list)
    artifacts[0][field] = value

    with pytest.raises(module.TrustError, match=message):
        _build_protected(module, responses)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("name", "protected-llm-evidence-forged", "artifact name mismatch"),
        ("digest", "sha256:invalid", "digest is invalid"),
        ("workflow_run_id", 999, "workflow run mismatch"),
    ],
)
def test_summary_validation_rejects_live_artifact_tampering(
    field: str,
    value: object,
    message: str,
) -> None:
    module = _load_module()
    summary = _build_protected(module, _responses(module))
    evidence = summary["evidence_runs"]
    assert isinstance(evidence, list)
    llm_record = next(record for record in evidence if record["role"] == "llm_live")
    llm_record["artifact"][field] = value

    with pytest.raises(module.TrustError, match=message):
        module.validate_trust_summary(
            summary,
            expected_repository=TEST_REPOSITORY,
            expected_git_sha=TEST_SHA,
            expected_current_role="protected_release",
            now=NOW,
        )


def test_summary_rejects_duplicate_run_artifact_id_and_digest_across_roles() -> None:
    module = _load_module()
    summary = _build_live(module, _responses(module), "llm_live")
    current = summary["current"]
    assert isinstance(current, dict)
    current["run_id"] = 101
    with pytest.raises(module.TrustError, match="reuses a run ID"):
        module.validate_trust_summary(
            summary,
            expected_repository=TEST_REPOSITORY,
            expected_git_sha=TEST_SHA,
            expected_current_role="llm_live",
            now=NOW,
        )

    summary = _build_protected(module, _responses(module))
    evidence = summary["evidence_runs"]
    assert isinstance(evidence, list)
    llm_artifact = next(record["artifact"] for record in evidence if record["role"] == "llm_live")
    ragflow_artifact = next(
        record["artifact"] for record in evidence if record["role"] == "ragflow_live"
    )
    ragflow_artifact["id"] = llm_artifact["id"]
    with pytest.raises(module.TrustError, match="reuses an artifact ID"):
        module.validate_trust_summary(
            summary,
            expected_repository=TEST_REPOSITORY,
            expected_git_sha=TEST_SHA,
            expected_current_role="protected_release",
            now=NOW,
        )

    summary = _build_protected(module, _responses(module))
    evidence = summary["evidence_runs"]
    assert isinstance(evidence, list)
    llm_artifact = next(record["artifact"] for record in evidence if record["role"] == "llm_live")
    ragflow_artifact = next(
        record["artifact"] for record in evidence if record["role"] == "ragflow_live"
    )
    ragflow_artifact["digest"] = llm_artifact["digest"]
    with pytest.raises(module.TrustError, match="reuses an artifact digest"):
        module.validate_trust_summary(
            summary,
            expected_repository=TEST_REPOSITORY,
            expected_git_sha=TEST_SHA,
            expected_current_role="protected_release",
            now=NOW,
        )


@pytest.mark.parametrize("role", ["llm_live", "ragflow_live"])
def test_cli_fetch_and_verify_accept_live_current_roles(role: str, tmp_path: Path) -> None:
    module = _load_module()
    summary_path = tmp_path / "trust.json"
    fetch = module._build_parser().parse_args(
        [
            "fetch",
            "--repository",
            TEST_REPOSITORY,
            "--git-sha",
            TEST_SHA,
            "--ref",
            "refs/heads/main",
            "--ref-protected",
            "true",
            "--current-role",
            role,
            "--current-run-id",
            "606",
            "--current-run-attempt",
            "2",
            "--current-workflow",
            getattr(module, "LLM_LIVE_WORKFLOW" if role == "llm_live" else "RAGFLOW_LIVE_WORKFLOW"),
            "--main-run-id",
            "101",
            "--main-run-attempt",
            "2",
            "--output",
            str(summary_path),
        ]
    )
    assert fetch.current_role == role
    assert fetch.evidence_run == []

    verify = module._build_parser().parse_args(
        [
            "verify",
            "--summary",
            str(summary_path),
            "--repository",
            TEST_REPOSITORY,
            "--git-sha",
            TEST_SHA,
            "--current-role",
            role,
        ]
    )
    assert verify.current_role == role
