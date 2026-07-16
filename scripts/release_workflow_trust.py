"""Verify GitHub workflow, protected-ref and immutable artifact provenance.

The online ``fetch`` command talks only to the GitHub REST API and emits a
strict, short-lived JSON summary.  The pure validation functions are exercised
offline with fixtures so release decisions do not depend on live-network tests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final, Protocol

SCHEMA: Final = "knowledge-uploader.release-workflow-trust.v1"
MAIN_WORKFLOW: Final = ".github/workflows/knowledge-uploader.yml"
DGX_WORKFLOW: Final = ".github/workflows/dgx-spark-device.yml"
PROTECTED_WORKFLOW: Final = ".github/workflows/protected-release.yml"
EXTERNAL_WORKFLOW: Final = ".github/workflows/protected-external-evidence.yml"
EXPECTED_EVIDENCE_ROLES: Final = {
    "dgx": DGX_WORKFLOW,
    "external": EXTERNAL_WORKFLOW,
}
SHA256_PATTERN: Final = re.compile(r"sha256:[0-9a-f]{64}")
GIT_SHA_PATTERN: Final = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
REPOSITORY_PATTERN: Final = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
RELEASE_TAG_PATTERN: Final = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?")
MAX_CLOCK_SKEW: Final = timedelta(minutes=5)
MAX_RUN_AGE: Final = timedelta(hours=8)
SUMMARY_TTL: Final = timedelta(hours=2)


class TrustError(RuntimeError):
    """Raised when a workflow or artifact fails a trust-boundary check."""


class GitHubApi(Protocol):
    def get(self, path: str) -> Mapping[str, object]: ...


@dataclass(frozen=True)
class EvidenceRunRequest:
    role: str
    run_id: int
    run_attempt: int
    workflow_path: str


class GitHubClient:
    def __init__(self, *, token: str, api_url: str = "https://api.github.com") -> None:
        if not token:
            raise TrustError("GitHub API token is required")
        self._token = token
        self._api_url = api_url.rstrip("/")

    def get(self, path: str) -> Mapping[str, object]:
        request = urllib.request.Request(
            self._api_url + path,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "knowledge-uploader-release-gate",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                if response.status != 200:
                    raise TrustError(f"GitHub API returned HTTP {response.status}")
                content = response.read(16 * 1024 * 1024 + 1)
        except (OSError, urllib.error.URLError) as error:
            raise TrustError("GitHub API request failed") from error
        if len(content) > 16 * 1024 * 1024:
            raise TrustError("GitHub API response exceeds the safety limit")
        try:
            parsed: object = json.loads(content.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise TrustError("GitHub API returned invalid JSON") from error
        return _mapping(parsed, "GitHub API response")


def _mapping(value: object, context: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise TrustError(f"{context} must be a JSON object")
    return value


def _sequence(value: object, context: str) -> Sequence[object]:
    if not isinstance(value, list):
        raise TrustError(f"{context} must be a JSON array")
    return value


def _exact_keys(value: Mapping[str, object], expected: set[str], context: str) -> None:
    actual = set(value)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing or extra:
        raise TrustError(f"{context} schema mismatch: missing={missing}, extra={extra}")


def _text(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise TrustError(f"{context} must be a non-empty string")
    return value


def _positive_integer(value: object, context: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise TrustError(f"{context} must be a positive integer")
    return value


def _git_sha(value: object, context: str) -> str:
    text = _text(value, context).lower()
    if GIT_SHA_PATTERN.fullmatch(text) is None:
        raise TrustError(f"{context} must be a full hexadecimal Git SHA")
    return text


def _timestamp(value: object, context: str) -> datetime:
    text = _text(value, context)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise TrustError(f"{context} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise TrustError(f"{context} must include a timezone")
    return parsed.astimezone(UTC)


def _fresh_timestamp(value: object, context: str, *, now: datetime) -> datetime:
    parsed = _timestamp(value, context)
    if parsed > now + MAX_CLOCK_SKEW or now - parsed > MAX_RUN_AGE:
        raise TrustError(f"{context} is stale or in the future")
    return parsed


def _workflow_path(value: object) -> str:
    return _text(value, "workflow run path").split("@", 1)[0]


def _repository_identity(value: object, *, expected_id: int, expected_name: str) -> None:
    repository = _mapping(value, "workflow run repository")
    if repository.get("id") != expected_id or repository.get("full_name") != expected_name:
        raise TrustError("workflow run belongs to a different repository")


def _run_record(
    raw: Mapping[str, object],
    *,
    role: str,
    expected_run_id: int,
    expected_run_attempt: int | None,
    expected_workflow: str,
    expected_repository_id: int,
    expected_repository: str,
    expected_sha: str,
    expected_event: str,
    require_success: bool,
    expected_head_branch: str | None,
    now: datetime,
) -> dict[str, object]:
    if raw.get("id") != expected_run_id:
        raise TrustError(f"{role} workflow run ID mismatch")
    attempt = _positive_integer(raw.get("run_attempt"), f"{role}.run_attempt")
    if expected_run_attempt is not None and attempt != expected_run_attempt:
        raise TrustError(f"{role} workflow run attempt mismatch")
    path = _workflow_path(raw.get("path"))
    if path != expected_workflow:
        raise TrustError(f"{role} came from an unexpected workflow")
    sha = _git_sha(raw.get("head_sha"), f"{role}.head_sha")
    if sha != expected_sha:
        raise TrustError(f"{role} workflow Git SHA mismatch")
    if raw.get("event") != expected_event:
        raise TrustError(f"{role} workflow event mismatch")
    _repository_identity(
        raw.get("repository"),
        expected_id=expected_repository_id,
        expected_name=expected_repository,
    )
    if expected_head_branch is not None and raw.get("head_branch") != expected_head_branch:
        raise TrustError(f"{role} workflow branch mismatch")
    status = _text(raw.get("status"), f"{role}.status")
    conclusion = raw.get("conclusion")
    if require_success:
        if status != "completed" or conclusion != "success":
            raise TrustError(f"{role} workflow did not complete successfully")
    elif status not in {"queued", "in_progress", "completed"}:
        raise TrustError(f"{role} workflow status is invalid")
    created_at = _fresh_timestamp(raw.get("created_at"), f"{role}.created_at", now=now)
    updated_at = _fresh_timestamp(raw.get("updated_at"), f"{role}.updated_at", now=now)
    if updated_at < created_at:
        raise TrustError(f"{role} workflow timestamps are inconsistent")
    return {
        "role": role,
        "run_id": expected_run_id,
        "run_attempt": attempt,
        "workflow_path": path,
        "event": expected_event,
        "head_sha": sha,
        "head_branch": raw.get("head_branch"),
        "status": status,
        "conclusion": conclusion,
        "created_at": created_at.isoformat(),
        "updated_at": updated_at.isoformat(),
    }


def _artifact_record(raw: Mapping[str, object], *, name: str, now: datetime) -> dict[str, object]:
    if raw.get("name") != name:
        raise TrustError("artifact name mismatch")
    if raw.get("expired") is not False:
        raise TrustError(f"artifact {name} is expired")
    artifact_id = _positive_integer(raw.get("id"), f"artifact {name}.id")
    size = _positive_integer(raw.get("size_in_bytes"), f"artifact {name}.size_in_bytes")
    digest = _text(raw.get("digest"), f"artifact {name}.digest")
    if SHA256_PATTERN.fullmatch(digest) is None:
        raise TrustError(f"artifact {name} lacks an immutable SHA-256 digest")
    created_at = _fresh_timestamp(raw.get("created_at"), f"artifact {name}.created_at", now=now)
    expires_at = _timestamp(raw.get("expires_at"), f"artifact {name}.expires_at")
    if expires_at <= now:
        raise TrustError(f"artifact {name} has expired")
    workflow_run = _mapping(raw.get("workflow_run"), f"artifact {name}.workflow_run")
    workflow_run_id = _positive_integer(workflow_run.get("id"), f"artifact {name}.workflow_run.id")
    return {
        "id": artifact_id,
        "name": name,
        "digest": digest,
        "size_in_bytes": size,
        "workflow_run_id": workflow_run_id,
        "created_at": created_at.isoformat(),
        "expires_at": expires_at.isoformat(),
    }


def _main_artifacts(
    raw: Mapping[str, object],
    *,
    sha: str,
    run_id: int,
    run_attempt: int,
    now: datetime,
) -> dict[str, object]:
    names = {
        "bundle": f"release-oci-bundle-{sha}-{run_id}-{run_attempt}",
        "provenance": f"release-oci-provenance-{sha}-{run_id}-{run_attempt}",
    }
    artifacts = _sequence(raw.get("artifacts"), "main CI artifacts")
    records: dict[str, object] = {}
    for role, name in names.items():
        matches = [
            _mapping(item, f"main CI artifact {name}")
            for item in artifacts
            if isinstance(item, dict) and item.get("name") == name
        ]
        if len(matches) != 1:
            raise TrustError(f"expected exactly one immutable main CI artifact named {name}")
        record = _artifact_record(matches[0], name=name, now=now)
        if record["workflow_run_id"] != run_id:
            raise TrustError(f"artifact {name} is bound to a different workflow run")
        records[role] = record
    return records


def _evidence_artifact_name(
    *,
    role: str,
    sha: str,
    run_id: int,
    run_attempt: int,
) -> str:
    if role == "dgx":
        prefix = "dgx-spark-evidence"
    elif role == "external":
        prefix = "protected-release-external-evidence"
    else:
        raise TrustError(f"unsupported evidence artifact role: {role}")
    return f"{prefix}-{sha}-{run_id}-{run_attempt}"


def _evidence_artifact(
    raw: Mapping[str, object],
    *,
    role: str,
    sha: str,
    run_id: int,
    run_attempt: int,
    now: datetime,
) -> dict[str, object]:
    name = _evidence_artifact_name(
        role=role,
        sha=sha,
        run_id=run_id,
        run_attempt=run_attempt,
    )
    artifacts = _sequence(raw.get("artifacts"), f"{role} artifacts")
    matches = [
        _mapping(item, f"{role} artifact {name}")
        for item in artifacts
        if isinstance(item, dict) and item.get("name") == name
    ]
    if len(matches) != 1:
        raise TrustError(f"expected exactly one immutable {role} artifact named {name}")
    record = _artifact_record(matches[0], name=name, now=now)
    if record["workflow_run_id"] != run_id:
        raise TrustError(f"artifact {name} is bound to a different workflow run")
    return record


def _release_ref(
    client: GitHubApi,
    *,
    repository: str,
    default_branch: str,
    ref: str,
    ref_protected: bool,
    sha: str,
) -> dict[str, str]:
    if not ref_protected:
        raise TrustError("release workflow ref is not protected by GitHub rules")
    default_ref = f"refs/heads/{default_branch}"
    if ref == default_ref:
        return {"ref": ref, "kind": "protected_default_branch", "git_sha": sha}
    prefix = "refs/tags/"
    if not ref.startswith(prefix):
        raise TrustError("release ref is neither the default branch nor a signed release tag")
    tag_name = ref.removeprefix(prefix)
    if RELEASE_TAG_PATTERN.fullmatch(tag_name) is None:
        raise TrustError("release tag does not follow the signed release tag policy")
    encoded_tag = urllib.parse.quote(tag_name, safe="")
    ref_payload = client.get(f"/repos/{repository}/git/ref/tags/{encoded_tag}")
    ref_object = _mapping(ref_payload.get("object"), "release tag ref.object")
    if ref_object.get("type") != "tag":
        raise TrustError("lightweight release tags are not trusted")
    tag_object_sha = _git_sha(ref_object.get("sha"), "release tag object SHA")
    tag_payload = client.get(f"/repos/{repository}/git/tags/{tag_object_sha}")
    verification = _mapping(tag_payload.get("verification"), "release tag verification")
    if verification.get("verified") is not True:
        raise TrustError("release tag signature is not verified by GitHub")
    target = _mapping(tag_payload.get("object"), "release tag target")
    if target.get("type") != "commit" or _git_sha(target.get("sha"), "release tag commit") != sha:
        raise TrustError("signed release tag does not resolve to the release commit")
    return {"ref": ref, "kind": "protected_signed_tag", "git_sha": sha}


def build_trust_summary(
    client: GitHubApi,
    *,
    repository: str,
    git_sha: str,
    ref: str,
    ref_protected: bool,
    current_role: str,
    current_run_id: int,
    current_run_attempt: int,
    current_workflow: str,
    main_run_id: int,
    main_run_attempt: int,
    evidence_runs: Sequence[EvidenceRunRequest],
    now: datetime | None = None,
) -> Mapping[str, object]:
    timestamp = (now or datetime.now(UTC)).astimezone(UTC)
    sha = _git_sha(git_sha, "git_sha")
    if REPOSITORY_PATTERN.fullmatch(repository) is None:
        raise TrustError("repository must use the owner/name form")
    if current_role not in {"dgx", "protected_release"}:
        raise TrustError("current_role is invalid")
    expected_current_workflow = DGX_WORKFLOW if current_role == "dgx" else PROTECTED_WORKFLOW
    if current_workflow != expected_current_workflow:
        raise TrustError("current workflow path does not match its declared role")
    repository_raw = client.get(f"/repos/{repository}")
    repository_id = _positive_integer(repository_raw.get("id"), "repository.id")
    if repository_raw.get("full_name") != repository:
        raise TrustError("GitHub repository identity mismatch")
    default_branch = _text(repository_raw.get("default_branch"), "repository.default_branch")
    release_ref = _release_ref(
        client,
        repository=repository,
        default_branch=default_branch,
        ref=ref,
        ref_protected=ref_protected,
        sha=sha,
    )
    current = _run_record(
        client.get(f"/repos/{repository}/actions/runs/{current_run_id}"),
        role=current_role,
        expected_run_id=current_run_id,
        expected_run_attempt=current_run_attempt,
        expected_workflow=current_workflow,
        expected_repository_id=repository_id,
        expected_repository=repository,
        expected_sha=sha,
        expected_event="workflow_dispatch",
        require_success=False,
        expected_head_branch=None,
        now=timestamp,
    )
    main = _run_record(
        client.get(f"/repos/{repository}/actions/runs/{main_run_id}"),
        role="main_ci",
        expected_run_id=main_run_id,
        expected_run_attempt=main_run_attempt,
        expected_workflow=MAIN_WORKFLOW,
        expected_repository_id=repository_id,
        expected_repository=repository,
        expected_sha=sha,
        expected_event="push",
        require_success=True,
        expected_head_branch=default_branch,
        now=timestamp,
    )
    main["artifacts"] = _main_artifacts(
        client.get(f"/repos/{repository}/actions/runs/{main_run_id}/artifacts?per_page=100"),
        sha=sha,
        run_id=main_run_id,
        run_attempt=main_run_attempt,
        now=timestamp,
    )

    expected_roles = set() if current_role == "dgx" else set(EXPECTED_EVIDENCE_ROLES)
    actual_roles = {request.role for request in evidence_runs}
    if actual_roles != expected_roles or len(actual_roles) != len(evidence_runs):
        raise TrustError("evidence workflow role inventory is incomplete or duplicated")
    evidence_records: list[dict[str, object]] = []
    for request in sorted(evidence_runs, key=lambda item: item.role):
        expected_path = EXPECTED_EVIDENCE_ROLES.get(request.role)
        if expected_path is None or request.workflow_path != expected_path:
            raise TrustError(f"unexpected workflow path for evidence role {request.role}")
        record = _run_record(
            client.get(f"/repos/{repository}/actions/runs/{request.run_id}"),
            role=request.role,
            expected_run_id=request.run_id,
            expected_run_attempt=request.run_attempt,
            expected_workflow=expected_path,
            expected_repository_id=repository_id,
            expected_repository=repository,
            expected_sha=sha,
            expected_event="workflow_dispatch",
            require_success=True,
            expected_head_branch=None,
            now=timestamp,
        )
        record["artifact"] = _evidence_artifact(
            client.get(f"/repos/{repository}/actions/runs/{request.run_id}/artifacts?per_page=100"),
            role=request.role,
            sha=sha,
            run_id=request.run_id,
            run_attempt=request.run_attempt,
            now=timestamp,
        )
        evidence_records.append(record)
    all_run_ids = [current_run_id, main_run_id, *(request.run_id for request in evidence_runs)]
    if len(set(all_run_ids)) != len(all_run_ids):
        raise TrustError("workflow run IDs must be unique across all trust roles")
    summary: Mapping[str, object] = {
        "schema": SCHEMA,
        "generated_at": timestamp.isoformat(),
        "expires_at": (timestamp + SUMMARY_TTL).isoformat(),
        "repository": {
            "id": repository_id,
            "full_name": repository,
            "default_branch": default_branch,
        },
        "release_ref": release_ref,
        "current": current,
        "main_ci": main,
        "evidence_runs": evidence_records,
    }
    validate_trust_summary(
        summary,
        expected_repository=repository,
        expected_git_sha=sha,
        expected_current_role=current_role,
        now=timestamp,
    )
    return summary


def _validate_run_summary(value: object, context: str, *, git_sha: str) -> Mapping[str, object]:
    run = _mapping(value, context)
    _exact_keys(
        run,
        {
            "role",
            "run_id",
            "run_attempt",
            "workflow_path",
            "event",
            "head_sha",
            "head_branch",
            "status",
            "conclusion",
            "created_at",
            "updated_at",
        },
        context,
    )
    _positive_integer(run.get("run_id"), f"{context}.run_id")
    _positive_integer(run.get("run_attempt"), f"{context}.run_attempt")
    if _git_sha(run.get("head_sha"), f"{context}.head_sha") != git_sha:
        raise TrustError(f"{context}.head_sha mismatch")
    _text(run.get("workflow_path"), f"{context}.workflow_path")
    _text(run.get("event"), f"{context}.event")
    _text(run.get("status"), f"{context}.status")
    _timestamp(run.get("created_at"), f"{context}.created_at")
    _timestamp(run.get("updated_at"), f"{context}.updated_at")
    return run


def _validate_artifact_summary(value: object, context: str, *, run_id: int) -> None:
    artifact = _mapping(value, context)
    _exact_keys(
        artifact,
        {
            "id",
            "name",
            "digest",
            "size_in_bytes",
            "workflow_run_id",
            "created_at",
            "expires_at",
        },
        context,
    )
    _positive_integer(artifact.get("id"), f"{context}.id")
    _positive_integer(artifact.get("size_in_bytes"), f"{context}.size_in_bytes")
    if artifact.get("workflow_run_id") != run_id:
        raise TrustError(f"{context} workflow run mismatch")
    digest = _text(artifact.get("digest"), f"{context}.digest")
    if SHA256_PATTERN.fullmatch(digest) is None:
        raise TrustError(f"{context}.digest is invalid")
    _timestamp(artifact.get("created_at"), f"{context}.created_at")
    _timestamp(artifact.get("expires_at"), f"{context}.expires_at")


def validate_trust_summary(
    value: object,
    *,
    expected_repository: str,
    expected_git_sha: str,
    expected_current_role: str,
    now: datetime | None = None,
) -> Mapping[str, object]:
    summary = _mapping(value, "workflow trust summary")
    _exact_keys(
        summary,
        {
            "schema",
            "generated_at",
            "expires_at",
            "repository",
            "release_ref",
            "current",
            "main_ci",
            "evidence_runs",
        },
        "workflow trust summary",
    )
    if summary.get("schema") != SCHEMA:
        raise TrustError("unsupported workflow trust summary schema")
    timestamp = (now or datetime.now(UTC)).astimezone(UTC)
    generated_at = _timestamp(summary.get("generated_at"), "workflow trust generated_at")
    expires_at = _timestamp(summary.get("expires_at"), "workflow trust expires_at")
    if (
        generated_at > timestamp + MAX_CLOCK_SKEW
        or expires_at <= generated_at
        or expires_at - generated_at > SUMMARY_TTL
        or timestamp > expires_at
    ):
        raise TrustError("workflow trust summary is stale or has an invalid validity window")
    repository = _mapping(summary.get("repository"), "workflow trust repository")
    _exact_keys(repository, {"id", "full_name", "default_branch"}, "workflow trust repository")
    _positive_integer(repository.get("id"), "workflow trust repository.id")
    if repository.get("full_name") != expected_repository:
        raise TrustError("workflow trust repository mismatch")
    _text(repository.get("default_branch"), "workflow trust repository.default_branch")
    sha = _git_sha(expected_git_sha, "expected_git_sha")
    release_ref = _mapping(summary.get("release_ref"), "workflow trust release_ref")
    _exact_keys(release_ref, {"ref", "kind", "git_sha"}, "workflow trust release_ref")
    if release_ref.get("kind") not in {"protected_default_branch", "protected_signed_tag"}:
        raise TrustError("workflow trust release_ref kind is invalid")
    if _git_sha(release_ref.get("git_sha"), "workflow trust release_ref.git_sha") != sha:
        raise TrustError("workflow trust release_ref Git SHA mismatch")
    current = _validate_run_summary(summary.get("current"), "workflow trust current", git_sha=sha)
    if current.get("role") != expected_current_role:
        raise TrustError("workflow trust current role mismatch")
    expected_current_path = DGX_WORKFLOW if expected_current_role == "dgx" else PROTECTED_WORKFLOW
    if current.get("workflow_path") != expected_current_path:
        raise TrustError("workflow trust current workflow mismatch")
    main = _mapping(summary.get("main_ci"), "workflow trust main_ci")
    main_without_artifacts = {key: item for key, item in main.items() if key != "artifacts"}
    main_run = _validate_run_summary(main_without_artifacts, "workflow trust main_ci", git_sha=sha)
    if (
        main_run.get("role") != "main_ci"
        or main_run.get("workflow_path") != MAIN_WORKFLOW
        or main_run.get("event") != "push"
        or main_run.get("status") != "completed"
        or main_run.get("conclusion") != "success"
    ):
        raise TrustError("workflow trust main CI identity is invalid")
    _exact_keys(
        main,
        set(main_without_artifacts) | {"artifacts"},
        "workflow trust main_ci",
    )
    artifacts = _mapping(main.get("artifacts"), "workflow trust main_ci.artifacts")
    _exact_keys(artifacts, {"bundle", "provenance"}, "workflow trust main_ci.artifacts")
    main_run_id = _positive_integer(main.get("run_id"), "workflow trust main_ci.run_id")
    _validate_artifact_summary(artifacts.get("bundle"), "main artifact bundle", run_id=main_run_id)
    _validate_artifact_summary(
        artifacts.get("provenance"), "main artifact provenance", run_id=main_run_id
    )
    evidence_runs = _sequence(summary.get("evidence_runs"), "workflow trust evidence_runs")
    expected_roles = set() if expected_current_role == "dgx" else set(EXPECTED_EVIDENCE_ROLES)
    seen_roles: set[str] = set()
    run_ids = {_positive_integer(current.get("run_id"), "current.run_id"), main_run_id}
    for index, raw in enumerate(evidence_runs):
        evidence_record = _mapping(raw, f"workflow trust evidence_runs[{index}]")
        artifact = evidence_record.get("artifact")
        evidence_without_artifact = {
            key: item for key, item in evidence_record.items() if key != "artifact"
        }
        evidence = _validate_run_summary(
            evidence_without_artifact,
            f"workflow trust evidence_runs[{index}]",
            git_sha=sha,
        )
        _exact_keys(
            evidence_record,
            set(evidence_without_artifact) | {"artifact"},
            f"workflow trust evidence_runs[{index}]",
        )
        role = _text(evidence.get("role"), f"evidence_runs[{index}].role")
        if role in seen_roles or role not in expected_roles:
            raise TrustError("workflow trust evidence roles are duplicated or unexpected")
        seen_roles.add(role)
        if evidence.get("workflow_path") != EXPECTED_EVIDENCE_ROLES[role]:
            raise TrustError(f"workflow trust {role} path mismatch")
        if (
            evidence.get("event") != "workflow_dispatch"
            or evidence.get("status") != "completed"
            or evidence.get("conclusion") != "success"
        ):
            raise TrustError(f"workflow trust {role} did not complete successfully")
        run_id = _positive_integer(evidence.get("run_id"), f"evidence_runs[{index}].run_id")
        run_attempt = _positive_integer(
            evidence.get("run_attempt"),
            f"evidence_runs[{index}].run_attempt",
        )
        artifact_record = _mapping(
            artifact,
            f"workflow trust evidence_runs[{index}].artifact",
        )
        expected_artifact_name = _evidence_artifact_name(
            role=role,
            sha=sha,
            run_id=run_id,
            run_attempt=run_attempt,
        )
        if artifact_record.get("name") != expected_artifact_name:
            raise TrustError(f"workflow trust {role} artifact name mismatch")
        _validate_artifact_summary(
            artifact_record,
            f"workflow trust {role} artifact",
            run_id=run_id,
        )
        if run_id in run_ids:
            raise TrustError("workflow trust reuses a run ID across roles")
        run_ids.add(run_id)
    if seen_roles != expected_roles:
        raise TrustError("workflow trust evidence role inventory is incomplete")
    return summary


def _write_summary(path: Path, summary: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(summary, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    path.write_text(content, encoding="utf-8", newline="\n")
    checksum_path = path.with_suffix(path.suffix + ".sha256")
    checksum_path.write_text(
        f"{hashlib.sha256(content.encode('utf-8')).hexdigest()}  {path.name}\n",
        encoding="utf-8",
        newline="\n",
    )


def _write_github_outputs(path: Path, summary: Mapping[str, object]) -> None:
    main = _mapping(summary.get("main_ci"), "workflow trust main_ci")
    main_artifacts = _mapping(main.get("artifacts"), "workflow trust main artifacts")
    output_ids = {
        "main_bundle_artifact_id": _positive_integer(
            _mapping(main_artifacts.get("bundle"), "main bundle artifact").get("id"),
            "main bundle artifact id",
        ),
        "main_provenance_artifact_id": _positive_integer(
            _mapping(
                main_artifacts.get("provenance"),
                "main provenance artifact",
            ).get("id"),
            "main provenance artifact id",
        ),
    }
    for raw in _sequence(summary.get("evidence_runs"), "workflow trust evidence_runs"):
        record = _mapping(raw, "workflow trust evidence run")
        role = _text(record.get("role"), "workflow trust evidence role")
        if role not in EXPECTED_EVIDENCE_ROLES:
            raise TrustError("workflow trust output contains an unexpected evidence role")
        artifact = _mapping(record.get("artifact"), f"workflow trust {role} artifact")
        output_ids[f"{role}_artifact_id"] = _positive_integer(
            artifact.get("id"),
            f"workflow trust {role} artifact id",
        )
    content = "".join(f"{name}={value}\n" for name, value in sorted(output_ids.items()))
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(content)


def _load_summary(path: Path) -> Mapping[str, object]:
    if path.stat().st_size > 4 * 1024 * 1024:
        raise TrustError("workflow trust summary is too large")
    try:
        parsed: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise TrustError("cannot read workflow trust summary") from error
    checksum_path = path.with_suffix(path.suffix + ".sha256")
    expected_line = checksum_path.read_text(encoding="utf-8")
    match = re.fullmatch(rf"([0-9a-f]{{64}})  {re.escape(path.name)}\n", expected_line)
    if match is None:
        raise TrustError("workflow trust checksum file is malformed")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != match.group(1):
        raise TrustError("workflow trust summary checksum mismatch")
    return _mapping(parsed, "workflow trust summary")


def _parse_evidence_request(value: str) -> EvidenceRunRequest:
    parts = value.split(":", 3)
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(
            "evidence run must use role:run_id:run_attempt:workflow_path"
        )
    role, run_id_raw, run_attempt_raw, workflow_path = parts
    try:
        run_id = int(run_id_raw)
        run_attempt = int(run_attempt_raw)
    except ValueError as error:
        raise argparse.ArgumentTypeError("evidence run ID and attempt must be numeric") from error
    if run_id < 1 or run_attempt < 1:
        raise argparse.ArgumentTypeError("evidence run ID and attempt must be positive")
    return EvidenceRunRequest(
        role=role,
        run_id=run_id,
        run_attempt=run_attempt,
        workflow_path=workflow_path,
    )


def _boolean(value: str) -> bool:
    if value == "true":
        return True
    if value == "false":
        return False
    raise argparse.ArgumentTypeError("value must be true or false")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    fetch = subparsers.add_parser(
        "fetch",
        help="Fetch API metadata and write a strict trust summary",
    )
    fetch.add_argument("--repository", required=True)
    fetch.add_argument("--git-sha", required=True)
    fetch.add_argument("--ref", required=True)
    fetch.add_argument("--ref-protected", required=True, type=_boolean)
    fetch.add_argument("--current-role", choices=("dgx", "protected_release"), required=True)
    fetch.add_argument("--current-run-id", required=True, type=int)
    fetch.add_argument("--current-run-attempt", required=True, type=int)
    fetch.add_argument("--current-workflow", required=True)
    fetch.add_argument("--main-run-id", required=True, type=int)
    fetch.add_argument("--main-run-attempt", required=True, type=int)
    fetch.add_argument("--evidence-run", action="append", type=_parse_evidence_request, default=[])
    fetch.add_argument("--output", required=True, type=Path)
    fetch.add_argument("--github-output", type=Path)

    verify = subparsers.add_parser("verify", help="Verify an existing strict trust summary")
    verify.add_argument("--summary", required=True, type=Path)
    verify.add_argument("--repository", required=True)
    verify.add_argument("--git-sha", required=True)
    verify.add_argument("--current-role", choices=("dgx", "protected_release"), required=True)
    return parser


def main() -> int:
    arguments = _build_parser().parse_args()
    try:
        if arguments.command == "fetch":
            token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
            summary = build_trust_summary(
                GitHubClient(token=token),
                repository=arguments.repository,
                git_sha=arguments.git_sha,
                ref=arguments.ref,
                ref_protected=arguments.ref_protected,
                current_role=arguments.current_role,
                current_run_id=arguments.current_run_id,
                current_run_attempt=arguments.current_run_attempt,
                current_workflow=arguments.current_workflow,
                main_run_id=arguments.main_run_id,
                main_run_attempt=arguments.main_run_attempt,
                evidence_runs=arguments.evidence_run,
            )
            _write_summary(arguments.output, summary)
            if arguments.github_output is not None:
                _write_github_outputs(arguments.github_output, summary)
            sys.stdout.write("GitHub release workflow trust verified\n")
        elif arguments.command == "verify":
            validate_trust_summary(
                _load_summary(arguments.summary),
                expected_repository=arguments.repository,
                expected_git_sha=arguments.git_sha,
                expected_current_role=arguments.current_role,
            )
            sys.stdout.write("release workflow trust summary verified\n")
        else:  # pragma: no cover - argparse enforces the command choices
            raise TrustError("unsupported command")
    except (TrustError, OSError) as error:
        sys.stderr.write(f"release workflow trust failed: {error}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
