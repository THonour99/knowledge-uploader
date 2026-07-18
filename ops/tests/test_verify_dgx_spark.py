from __future__ import annotations

import hashlib
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
    "prometheus",
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
    fault_recovery = {
        dependency: {
            "status": "passed",
            "run_id": run_id,
            "target_file_id": str(uuid.uuid4()),
            "remote_upload_delta": 1,
            "remote_document_count": 1,
            "event_loss_detected": False,
            "duplicate_remote_document": False,
        }
        for dependency in ("rabbitmq", "redis", "minio", "ragflow")
    }
    return {
        "evidence_contract_version": 5,
        "status": "development_passed",
        "generated_at": (generated_at or datetime.now(UTC)).isoformat(),
        "git_sha": TEST_GIT_SHA,
        "environment": "staging",
        "run_id": run_id,
        "compose_project": "knowledge-uploader-dgx-test",
        "source_worktree_clean": True,
        "cleanup_status": "passed",
        "resolved_compose_sha256": "d" * 64,
        "architecture": "aarch64",
        "docker_architecture": "arm64",
        "full_compose_e2e": "development_passed",
        "backend_image": "backend:test",
        "backend_image_revision": TEST_GIT_SHA,
        "backend_image_id": TEST_BACKEND_IMAGE_ID,
        "frontend_image": "frontend:test",
        "frontend_image_revision": TEST_GIT_SHA,
        "frontend_image_id": TEST_FRONTEND_IMAGE_ID,
        "rabbitmq_probe_run_id": run_id,
        "rabbitmq_evidence_sha256": "9" * 64,
        "tls_certificate_sha256": "e" * 64,
        "tls": {
            "status": "passed",
            "certificate_bundle_sha256": "e" * 64,
            "verified_channels": [
                "gateway_https",
                "minio_https",
                "ragflow_https",
                "smtp_starttls",
            ],
        },
        "fault_recovery": fault_recovery,
        "minio_metrics_auth": {
            "status": "passed",
            "auth_mode": "jwt_bearer_file",
            "initializer": {
                "status": "passed",
                "container_exit": "exited_0",
                "logs": "empty",
                "token_file": "strict_semantic_jwt_single_lf",
                "mode": "0440",
                "uid": 65534,
                "gid": 65534,
            },
            "anonymous_access": {"status": "denied", "http_status": 403},
            "atomic_publish": {
                "status": "passed",
                "concurrent_runs": 2,
                "concurrent_successes": 2,
                "term_exit_code": 1,
                "term_cleanup": "passed",
                "sigkill_exit_code": 137,
                "sigkill_orphan_observed": True,
                "post_sigkill_recovery": "passed",
                "cleanup_after_no_initializer": True,
                "final_temporary_file_count": 0,
            },
            "refresh": {
                "status": "passed",
                "semantics": "consumer_refresh_not_revocation",
                "credential_changed": True,
                "mtime_advanced": True,
                "previous_jwt_http_status": 200,
                "refreshed_jwt_http_status": 200,
                "consumer_processes_unchanged": True,
                "prometheus_health_before": "up",
                "prometheus_health_after": "up",
            },
            "emergency_revocation": {
                "status": "passed",
                "method": "root_credential_rotation_and_minio_restart",
                "previous_jwt_http_status_after_restart": 403,
                "refreshed_jwt_http_status_after_restart": 403,
                "replacement_jwt_http_status": 200,
                "minio_recreated": True,
                "bootstrap_reconciled": True,
                "expected_minio_interruption": True,
                "consumer_processes_unchanged": True,
                "automatic_consumer_recovery": True,
                "prometheus_health_after_recovery": "up",
            },
            "identity_reconciliation": {
                "status": "passed",
                "stale_direct_policy_removed": True,
                "stale_group_membership_removed": True,
                "intended_policy_attached": True,
                "intended_bucket_operations": ["get", "put", "delete"],
                "secondary_bucket_operations_denied": ["list", "get", "put"],
                "admin_operations_denied": ["info", "user_list", "policy_list"],
            },
            "collector": {
                "status": "passed",
                "component": "minio_capacity",
                "last_success_advanced": True,
            },
        },
        "prometheus_minio_tls": {
            "status": "passed",
            "job": "minio",
            "health": "up",
            "scrape_url": "https://minio:9000/minio/v2/metrics/cluster",
            "config_sha256": "f" * 64,
            "ca_file": "/etc/prometheus/tls/ca.crt",
            "server_name": "minio",
            "certificate_verification": "required",
        },
        "service_container_ids": {
            service: f"{index:064x}" for index, service in enumerate(REQUIRED_SERVICES, 1)
        },
        "service_image_ids": {
            service: "sha256:" + f"{index:064x}"
            for index, service in enumerate(REQUIRED_SERVICES, 1)
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
            "gateway_tls": "passed",
            "workers": "passed",
            "smtp_starttls": "passed",
            "rabbitmq_topology": "passed",
            "minio_tls": "passed",
            "minio_metrics_auth": "passed",
            "prometheus_minio_tls": "passed",
            "upload_review_ragflow": "passed",
            "ragflow_tls": "passed",
            "dlq_protocol": "passed",
            "dependency_fault_recovery": "passed",
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
    evidence_path = tmp_path / "infrastructure-e2e.json"
    _write_evidence(evidence_path, _evidence())
    monkeypatch.setattr(verifier.platform, "machine", lambda: "aarch64")

    def fake_run(command: list[str]) -> str:
        joined = " ".join(command)
        if command[:2] == ["docker", "info"]:
            return "aarch64"
        if command[0] == "nvidia-smi":
            return "NVIDIA GB10, 999.0"
        if "{{.Id}}" in command:
            return (
                TEST_BACKEND_IMAGE_ID if command[-1] == "backend:test" else TEST_FRONTEND_IMAGE_ID
            )
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
            compose_e2e_evidence=evidence_path,
        )


def test_verifier_binds_device_proof_to_compose_run_and_image_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verifier = _load_verifier()
    evidence_path = tmp_path / "infrastructure-e2e.json"
    infrastructure = _evidence()
    _write_evidence(evidence_path, infrastructure)
    original_payload = evidence_path.read_bytes()
    replacement_payload = original_payload + b"\n"
    monkeypatch.setattr(verifier.platform, "machine", lambda: "aarch64")
    original_loader = verifier._load_compose_e2e_evidence
    commands: list[list[str]] = []

    def replace_after_validation(
        path: Path,
        *,
        git_sha: str,
        environment: str,
        architecture: str,
        payload: bytes | None = None,
    ) -> dict[str, object]:
        assert payload == original_payload
        loaded = original_loader(
            path,
            git_sha=git_sha,
            environment=environment,
            architecture=architecture,
            payload=payload,
        )
        evidence_path.write_bytes(replacement_payload)
        return loaded

    monkeypatch.setattr(verifier, "_load_compose_e2e_evidence", replace_after_validation)

    def fake_run(command: list[str]) -> str:
        commands.append(command)
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
    assert proof.status == "passed"
    assert proof.full_compose_e2e == "passed"
    assert proof.compose_e2e_evidence_sha256 == hashlib.sha256(original_payload).hexdigest()
    assert evidence_path.read_bytes() == replacement_payload
    tag_uses = [
        command for command in commands if "backend:test" in command or "frontend:test" in command
    ]
    assert tag_uses == [
        ["docker", "image", "inspect", "--format", "{{.Id}}", "backend:test"],
        ["docker", "image", "inspect", "--format", "{{.Id}}", "frontend:test"],
    ]
    image_commands = [
        command
        for command in commands
        if command[:3] == ["docker", "image", "inspect"] or command[:2] == ["docker", "run"]
    ]
    assert all(
        TEST_BACKEND_IMAGE_ID in command or TEST_FRONTEND_IMAGE_ID in command
        for command in image_commands[2:]
    )


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

    assert loaded["full_compose_e2e"] == "development_passed"


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("git_sha", "wrong"),
        ("environment", "production"),
        ("architecture", "amd64"),
        ("status", "passed"),
        ("status", "failed"),
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


@pytest.mark.parametrize(
    "result_name",
    (
        "gateway_tls",
        "smtp_starttls",
        "ragflow_tls",
        "dependency_fault_recovery",
        "prometheus_minio_tls",
    ),
)
def test_compose_e2e_evidence_rejects_missing_protocol_result(
    tmp_path: Path,
    result_name: str,
) -> None:
    verifier = _load_verifier()
    evidence_path = tmp_path / "infrastructure-e2e.json"
    evidence = _evidence()
    results = evidence["results"]
    assert isinstance(results, dict)
    del results[result_name]
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
