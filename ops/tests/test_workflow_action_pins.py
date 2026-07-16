from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[2]
TRUSTED_WORKFLOWS = (
    ROOT / ".github/workflows/knowledge-uploader.yml",
    ROOT / ".github/workflows/dgx-spark-device.yml",
    ROOT / ".github/workflows/protected-release.yml",
    ROOT / ".github/workflows/protected-external-evidence.yml",
)
ACTION_ALLOWLIST = {
    "actions/checkout": ("11bd71901bbe5b1630ceea73d27597364c9af683", "v4.2.2"),
    "actions/setup-python": ("a26af69be951a213d495a4c3e4e4022e16d87065", "v5.6.0"),
    "actions/setup-node": ("49933ea5288caeca8642d1e84afbd3f7d6820020", "v4.4.0"),
    "actions/upload-artifact": ("ea165f8d65b6e75b540449e92b4886f43607fa02", "v4.6.2"),
    "actions/download-artifact": ("d3f86a106a0bac45b974a628896c90dbdf5c8093", "v4.3.0"),
    "docker/setup-qemu-action": ("29109295f81e9208d7d86ff1c6c12d2833863392", "v3.6.0"),
    "docker/setup-buildx-action": ("e468171a9de216ec08956ac3ada2f0791b6bd435", "v3.11.1"),
}
USES_PATTERN = re.compile(
    r"^\s*uses:\s*([^@\s]+)@([^\s#]+)(?:\s+#\s*(\S+))?\s*$",
    re.MULTILINE,
)


@pytest.mark.parametrize("workflow", TRUSTED_WORKFLOWS, ids=lambda path: path.name)
def test_release_trust_workflow_actions_are_full_sha_allowlisted(workflow: Path) -> None:
    content = workflow.read_text(encoding="utf-8")
    uses_lines = USES_PATTERN.findall(content)
    assert uses_lines, f"no action references found in {workflow}"

    for action, revision, tag_comment in uses_lines:
        assert action in ACTION_ALLOWLIST, f"unreviewed action owner/name: {action}"
        expected_revision, expected_tag = ACTION_ALLOWLIST[action]
        assert re.fullmatch(r"[0-9a-f]{40}", revision), (
            f"{action} must use a full lowercase 40-hex commit, not {revision}"
        )
        assert revision == expected_revision, f"{action} commit is outside the reviewed allowlist"
        assert tag_comment == expected_tag, f"{action} must retain its reviewed tag annotation"


def test_action_allowlist_has_no_dead_or_unexercised_entry() -> None:
    observed = {
        action
        for workflow in TRUSTED_WORKFLOWS
        for action, _revision, _tag in USES_PATTERN.findall(workflow.read_text(encoding="utf-8"))
    }

    assert observed == set(ACTION_ALLOWLIST)


@pytest.mark.parametrize("workflow", TRUSTED_WORKFLOWS, ids=lambda path: path.name)
def test_release_checkouts_pin_sha_and_do_not_persist_credentials(workflow: Path) -> None:
    payload = yaml.load(workflow.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(payload, dict)
    jobs = payload.get("jobs")
    assert isinstance(jobs, dict)
    checkouts = []
    for job in jobs.values():
        assert isinstance(job, dict)
        steps = job.get("steps", [])
        assert isinstance(steps, list)
        for step in steps:
            assert isinstance(step, dict)
            if str(step.get("uses", "")).startswith("actions/checkout@"):
                checkouts.append(step)
    assert checkouts
    for checkout in checkouts:
        options = checkout.get("with")
        assert isinstance(options, dict)
        assert options.get("ref") == "${{ github.sha }}"
        assert options.get("persist-credentials") == "false"


@pytest.mark.parametrize("workflow", TRUSTED_WORKFLOWS, ids=lambda path: path.name)
def test_release_trust_workflows_do_not_grant_write_or_oidc_permissions(workflow: Path) -> None:
    payload = yaml.load(workflow.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(payload, dict)
    permissions = payload.get("permissions")
    assert isinstance(permissions, dict)
    assert permissions.get("contents") == "read"
    assert "id-token" not in permissions
    assert "packages" not in permissions
    assert "attestations" not in permissions
    assert "write" not in permissions.values()

    jobs = payload.get("jobs")
    assert isinstance(jobs, dict)
    for job in jobs.values():
        assert isinstance(job, dict)
        job_permissions = job.get("permissions", {})
        assert isinstance(job_permissions, dict)
        assert "id-token" not in job_permissions
        assert "packages" not in job_permissions
        assert "attestations" not in job_permissions
        assert "write" not in job_permissions.values()
