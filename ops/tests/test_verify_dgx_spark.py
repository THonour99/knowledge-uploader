from __future__ import annotations

import importlib.util
import json
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest

TEST_GIT_SHA = "a" * 40
TEST_BACKEND_IMAGE_ID = "sha256:" + "b" * 64
TEST_FRONTEND_IMAGE_ID = "sha256:" + "c" * 64
REQUIRED_SERVICES = (
    "nginx",
    "frontend",
    "backend-api",
    "outbox-dispatcher",
    "operational-metrics",
    "worker-document",
    "worker-ai",
    "worker-ragflow",
    "worker-notification",
    "scheduler",
    "mock-ragflow",
    "mock-smtp",
    "postgres",
    "rabbitmq",
    "redis",
    "minio",
)


def _load_verifier() -> ModuleType:
    verifier_path = Path(__file__).parents[2] / "scripts/verify_dgx_spark.py"
    spec = importlib.util.spec_from_file_location("verify_dgx_spark", verifier_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load DGX Spark verifier")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _evidence(*, generated_at: datetime | None = None) -> dict[str, object]:
    run_id = str(uuid.uuid4())
    return {
        "status": "passed",
        "generated_at": (generated_at or datetime.now(UTC)).isoformat(),
        "git_sha": TEST_GIT_SHA,
        "environment": "staging",
        "run_id": run_id,
        "compose_project": "knowledge-uploader-dgx-test",
        "source_worktree_clean": True,
        "cleanup_status": "passed",
        "resolved_compose_sha256": "d" * 64,
        "architecture": "aarch64",
        "full_compose_e2e": "passed",
        "backend_image": "backend:test",
        "backend_image_revision": TEST_GIT_SHA,
        "backend_image_id": TEST_BACKEND_IMAGE_ID,
        "frontend_image": "frontend:test",
        "frontend_image_revision": TEST_GIT_SHA,
        "frontend_image_id": TEST_FRONTEND_IMAGE_ID,
        "rabbitmq_probe_run_id": run_id,
        "tls_certificate_sha256": "e" * 64,
        "service_container_ids": {
            service: f"{index:064x}" for index, service in enumerate(REQUIRED_SERVICES, 1)
        },
        "worker_queue_consumers": {
            "document_queue": 1,
            "ai_queue": 1,
            "ragflow_queue": 1,
            "notification_queue": 1,
        },
        "business_probe": {
            "status": "passed",
            "email_verification_floor": "passed",
            "mock_smtp_delivery": "passed",
        },
        "results": {
            "compose_up": "passed",
            "alembic_head": "passed",
            "ready": "passed",
            "gateway": "passed",
            "email_verification_floor": "passed",
            "workers": "passed",
            "rabbitmq_topology": "passed",
            "minio_tls": "passed",
            "upload_review_ragflow": "passed",
            "dlq_protocol": "passed",
            "cleanup": "passed",
        },
    }


def _write_evidence(path: Path, evidence: dict[str, object]) -> None:
    path.write_text(json.dumps(evidence), encoding="utf-8")


@pytest.mark.parametrize("git_sha", ["unknown", "abcdef123456"])
def test_verifier_rejects_incomplete_git_identity_before_host_checks(
    tmp_path: Path,
    git_sha: str,
) -> None:
    verifier = _load_verifier()

    with pytest.raises(RuntimeError, match="git SHA"):
        verifier.verify(
            backend_image="backend:test",
            frontend_image="frontend:test",
            git_sha=git_sha,
            environment="staging",
            compose_e2e_evidence=tmp_path / "missing.json",
        )


def test_verifier_rejects_images_built_from_another_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verifier = _load_verifier()
    monkeypatch.setattr(verifier.platform, "machine", lambda: "aarch64")

    def fake_run(command: list[str]) -> str:
        joined = " ".join(command)
        if command[:2] == ["docker", "info"]:
            return "aarch64"
        if command[0] == "nvidia-smi":
            return "NVIDIA GB10, 999.0"
        if "{{.Architecture}}" in command:
            return "arm64"
        if "org.opencontainers.image.revision" in joined:
            return "deadbee"
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(verifier, "run", fake_run)

    with pytest.raises(RuntimeError, match="revision labels"):
        verifier.verify(
            backend_image="backend:test",
            frontend_image="frontend:test",
            git_sha=TEST_GIT_SHA,
            environment="staging",
            compose_e2e_evidence=tmp_path / "missing.json",
        )


def test_verifier_binds_device_proof_to_compose_run_and_image_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verifier = _load_verifier()
    evidence_path = tmp_path / "infrastructure-e2e.json"
    infrastructure = _evidence()
    _write_evidence(evidence_path, infrastructure)
    monkeypatch.setattr(verifier.platform, "machine", lambda: "aarch64")

    def fake_run(command: list[str]) -> str:
        joined = " ".join(command)
        if command[:2] == ["docker", "info"]:
            return "aarch64"
        if command[0] == "nvidia-smi":
            return "NVIDIA GB10, 999.0"
        if "{{.Architecture}}" in command:
            return "arm64"
        if "org.opencontainers.image.revision" in joined:
            return TEST_GIT_SHA
        if "{{.Id}}" in command:
            return TEST_BACKEND_IMAGE_ID if "backend:test" in command else TEST_FRONTEND_IMAGE_ID
        if command[:2] == ["docker", "run"]:
            return ""
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(verifier, "run", fake_run)

    proof = verifier.verify(
        backend_image="backend:test",
        frontend_image="frontend:test",
        git_sha=TEST_GIT_SHA,
        environment="staging",
        compose_e2e_evidence=evidence_path,
    )

    assert proof.run_id == infrastructure["run_id"]
    assert proof.compose_project == infrastructure["compose_project"]
    assert proof.resolved_compose_sha256 == infrastructure["resolved_compose_sha256"]
    assert proof.backend_image_id == infrastructure["backend_image_id"]
    assert proof.frontend_image_id == infrastructure["frontend_image_id"]


def test_compose_e2e_evidence_accepts_matching_complete_proof(tmp_path: Path) -> None:
    verifier = _load_verifier()
    evidence_path = tmp_path / "infrastructure-e2e.json"
    _write_evidence(evidence_path, _evidence())

    loaded = verifier._load_compose_e2e_evidence(
        evidence_path,
        git_sha=TEST_GIT_SHA,
        environment="staging",
        architecture="arm64",
    )

    assert loaded["full_compose_e2e"] == "passed"


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("git_sha", "wrong"),
        ("environment", "production"),
        ("architecture", "amd64"),
        ("full_compose_e2e", "skipped"),
        ("source_worktree_clean", False),
        ("cleanup_status", "failed"),
    ),
)
def test_compose_e2e_evidence_rejects_identity_or_scope_mismatch(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    verifier = _load_verifier()
    evidence_path = tmp_path / "infrastructure-e2e.json"
    evidence = _evidence()
    evidence[field] = value
    _write_evidence(evidence_path, evidence)

    with pytest.raises(RuntimeError, match=field):
        verifier._load_compose_e2e_evidence(
            evidence_path,
            git_sha=TEST_GIT_SHA,
            environment="staging",
            architecture="aarch64",
        )


def test_verifier_rejects_compose_evidence_for_different_image_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verifier = _load_verifier()
    evidence_path = tmp_path / "infrastructure-e2e.json"
    infrastructure = _evidence()
    infrastructure["backend_image_id"] = "sha256:" + "f" * 64
    _write_evidence(evidence_path, infrastructure)
    monkeypatch.setattr(verifier.platform, "machine", lambda: "aarch64")

    def fake_run(command: list[str]) -> str:
        joined = " ".join(command)
        if command[:2] == ["docker", "info"]:
            return "aarch64"
        if command[0] == "nvidia-smi":
            return "NVIDIA GB10, 999.0"
        if "{{.Architecture}}" in command:
            return "arm64"
        if "org.opencontainers.image.revision" in joined:
            return TEST_GIT_SHA
        if "{{.Id}}" in command:
            return TEST_BACKEND_IMAGE_ID if "backend:test" in command else TEST_FRONTEND_IMAGE_ID
        if command[:2] == ["docker", "run"]:
            return ""
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(verifier, "run", fake_run)

    with pytest.raises(RuntimeError, match="image content"):
        verifier.verify(
            backend_image="backend:test",
            frontend_image="frontend:test",
            git_sha=TEST_GIT_SHA,
            environment="staging",
            compose_e2e_evidence=evidence_path,
        )


def test_compose_e2e_evidence_rejects_missing_protocol_result(tmp_path: Path) -> None:
    verifier = _load_verifier()
    evidence_path = tmp_path / "infrastructure-e2e.json"
    evidence = _evidence()
    results = evidence["results"]
    assert isinstance(results, dict)
    results["dlq_protocol"] = "skipped"
    _write_evidence(evidence_path, evidence)

    with pytest.raises(RuntimeError, match="required passed results"):
        verifier._load_compose_e2e_evidence(
            evidence_path,
            git_sha=TEST_GIT_SHA,
            environment="staging",
            architecture="aarch64",
        )


def test_compose_e2e_evidence_rejects_stale_proof(tmp_path: Path) -> None:
    verifier = _load_verifier()
    evidence_path = tmp_path / "infrastructure-e2e.json"
    _write_evidence(
        evidence_path,
        _evidence(generated_at=datetime.now(UTC) - timedelta(hours=3)),
    )

    with pytest.raises(RuntimeError, match="stale"):
        verifier._load_compose_e2e_evidence(
            evidence_path,
            git_sha=TEST_GIT_SHA,
            environment="staging",
            architecture="aarch64",
        )
