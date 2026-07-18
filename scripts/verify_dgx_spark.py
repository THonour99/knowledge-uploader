"""Produce verifiable release evidence on a physical NVIDIA DGX Spark host."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import stat
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

ARM64_NAMES = frozenset({"aarch64", "arm64"})
GIT_SHA_PATTERN = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
COMPOSE_PROJECT_PATTERN = re.compile(r"[a-z0-9][a-z0-9_.-]{0,62}")
REQUIRED_RESULTS = (
    "compose_up",
    "alembic_head",
    "ready",
    "gateway",
    "email_verification_floor",
    "gateway_tls",
    "workers",
    "smtp_starttls",
    "rabbitmq_topology",
    "minio_tls",
    "minio_metrics_auth",
    "prometheus_minio_tls",
    "upload_review_ragflow",
    "ragflow_tls",
    "dlq_protocol",
    "dependency_fault_recovery",
    "cleanup",
)
INFRASTRUCTURE_EVIDENCE_KEYS = frozenset(
    {
        "evidence_contract_version",
        "status",
        "generated_at",
        "git_sha",
        "environment",
        "run_id",
        "compose_project",
        "source_worktree_clean",
        "architecture",
        "docker_architecture",
        "full_compose_e2e",
        "resolved_compose_sha256",
        "backend_image",
        "backend_image_id",
        "backend_image_revision",
        "frontend_image",
        "frontend_image_id",
        "frontend_image_revision",
        "service_container_ids",
        "service_image_ids",
        "worker_queue_consumers",
        "business_probe",
        "fault_recovery",
        "prometheus_minio_tls",
        "minio_metrics_auth",
        "rabbitmq_probe_run_id",
        "rabbitmq_evidence_sha256",
        "tls",
        "tls_certificate_sha256",
        "cleanup_status",
        "results",
    }
)
REQUIRED_SERVICE_CONTAINERS = frozenset(
    {
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
    }
)
REQUIRED_WORKER_QUEUES = frozenset(
    {
        "document_queue",
        "ai_queue",
        "ragflow_queue",
        "notification_queue",
    }
)
MAX_EVIDENCE_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class DgxSparkEvidence:
    status: str
    generated_at: str
    git_sha: str
    environment: str
    run_id: str
    compose_project: str
    resolved_compose_sha256: str
    architecture: str
    full_compose_e2e: str
    docker_architecture: str
    gpu_name: str
    gpu_driver: str
    backend_image: str
    backend_image_id: str
    backend_image_revision: str
    frontend_image: str
    frontend_image_id: str
    frontend_image_revision: str
    compose_e2e_evidence_sha256: str


def run(command: list[str]) -> str:
    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def verify(
    *,
    backend_image: str,
    frontend_image: str,
    git_sha: str,
    environment: str,
    compose_e2e_evidence: Path,
) -> DgxSparkEvidence:
    if GIT_SHA_PATTERN.fullmatch(git_sha) is None:
        raise RuntimeError("a concrete hexadecimal git SHA is required")
    host_machine = platform.machine().lower()
    if _normalize_architecture(host_machine) != "arm64":
        raise RuntimeError(f"physical ARM64 host required, got {host_machine}")

    docker_architecture = run(["docker", "info", "--format", "{{.Architecture}}"]).lower()
    if _normalize_architecture(docker_architecture) != "arm64":
        raise RuntimeError(f"native ARM64 Docker daemon required, got {docker_architecture}")

    compose_e2e_payload = _read_stable_regular_file(
        compose_e2e_evidence,
        context="Compose E2E evidence",
    )
    compose_e2e = _load_compose_e2e_evidence(
        compose_e2e_evidence,
        git_sha=git_sha,
        environment=environment,
        architecture=host_machine,
        payload=compose_e2e_payload,
    )
    backend_image_id = _resolve_image_id(backend_image)
    frontend_image_id = _resolve_image_id(frontend_image)
    if (
        compose_e2e.get("backend_image") != backend_image
        or compose_e2e.get("frontend_image") != frontend_image
        or compose_e2e.get("backend_image_id") != backend_image_id
        or compose_e2e.get("frontend_image_id") != frontend_image_id
    ):
        raise RuntimeError("Compose E2E evidence is not bound to the verified image content")

    gpu_row = run(
        [
            "nvidia-smi",
            "--query-gpu=name,driver_version",
            "--format=csv,noheader",
        ]
    ).splitlines()
    if len(gpu_row) != 1:
        raise RuntimeError("exactly one integrated DGX Spark GPU is required")
    gpu_name, gpu_driver = (part.strip() for part in gpu_row[0].split(",", 1))
    if "GB10" not in gpu_name.upper() and "GRACE BLACKWELL" not in gpu_name.upper():
        raise RuntimeError(f"DGX Spark GB10 GPU required, got {gpu_name}")

    backend_arch = run(
        ["docker", "image", "inspect", "--format", "{{.Architecture}}", backend_image_id]
    ).lower()
    frontend_arch = run(
        ["docker", "image", "inspect", "--format", "{{.Architecture}}", frontend_image_id]
    ).lower()
    if (
        _normalize_architecture(backend_arch) != "arm64"
        or _normalize_architecture(frontend_arch) != "arm64"
    ):
        raise RuntimeError("both release images must be native ARM64 images")

    backend_revision = _image_revision(backend_image_id)
    frontend_revision = _image_revision(frontend_image_id)
    if backend_revision != git_sha or frontend_revision != git_sha:
        raise RuntimeError("both release image revision labels must match the exact git SHA")

    run(
        [
            "docker",
            "run",
            "--rm",
            backend_image_id,
            "python",
            "-c",
            (
                "import platform, asyncpg, cryptography, prometheus_client, tiktoken; "
                "assert platform.machine().lower() in {'aarch64', 'arm64'}"
            ),
        ]
    )
    run(["docker", "run", "--rm", frontend_image_id, "nginx", "-t"])

    return DgxSparkEvidence(
        status="passed",
        generated_at=datetime.now(UTC).isoformat(),
        git_sha=git_sha,
        environment=environment,
        run_id=str(compose_e2e["run_id"]),
        compose_project=str(compose_e2e["compose_project"]),
        resolved_compose_sha256=str(compose_e2e["resolved_compose_sha256"]),
        architecture=host_machine,
        full_compose_e2e="passed",
        docker_architecture=docker_architecture,
        gpu_name=gpu_name,
        gpu_driver=gpu_driver,
        backend_image=backend_image,
        backend_image_id=backend_image_id,
        backend_image_revision=backend_revision,
        frontend_image=frontend_image,
        frontend_image_id=frontend_image_id,
        frontend_image_revision=frontend_revision,
        compose_e2e_evidence_sha256=hashlib.sha256(compose_e2e_payload).hexdigest(),
    )


def _read_stable_regular_file(path: Path, *, context: str) -> bytes:
    descriptor = -1
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise RuntimeError(f"{context} is not a regular file")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            or opened.st_size > MAX_EVIDENCE_BYTES
        ):
            raise RuntimeError(f"{context} changed before it could be read")
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = -1
            payload = stream.read(MAX_EVIDENCE_BYTES + 1)
            after = os.fstat(stream.fileno())
        current = path.lstat()
    except OSError as error:
        raise RuntimeError(f"cannot read {context}") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if (
        len(payload) > MAX_EVIDENCE_BYTES
        or len(payload) != opened.st_size
        or (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
        or not stat.S_ISREG(current.st_mode)
    ):
        raise RuntimeError(f"{context} changed while it was read")
    return payload


def _load_compose_e2e_evidence(
    path: Path,
    *,
    git_sha: str,
    environment: str,
    architecture: str,
    payload: bytes | None = None,
) -> dict[str, object]:
    content = payload
    if content is None:
        content = _read_stable_regular_file(path, context="Compose E2E evidence")
    raw = json.loads(content.decode("utf-8"))
    if not isinstance(raw, dict):
        raise RuntimeError("Compose E2E evidence must be a JSON object")
    if set(raw) != INFRASTRUCTURE_EVIDENCE_KEYS:
        raise RuntimeError("Compose E2E evidence top-level schema mismatch")
    expected = {
        "evidence_contract_version": 5,
        "status": "development_passed",
        "git_sha": git_sha,
        "environment": environment,
        "full_compose_e2e": "development_passed",
    }
    for key, expected_value in expected.items():
        if raw.get(key) != expected_value:
            raise RuntimeError(f"Compose E2E evidence mismatch: {key}")
    if raw.get("source_worktree_clean") is not True:
        raise RuntimeError("Compose E2E evidence mismatch: source_worktree_clean")
    if raw.get("cleanup_status") != "passed":
        raise RuntimeError("Compose E2E evidence mismatch: cleanup_status")
    evidence_architecture = raw.get("architecture")
    if not isinstance(evidence_architecture, str) or (
        _normalize_architecture(evidence_architecture) != _normalize_architecture(architecture)
    ):
        raise RuntimeError("Compose E2E evidence mismatch: architecture")
    generated_at = raw.get("generated_at")
    if not isinstance(generated_at, str):
        raise RuntimeError("Compose E2E evidence generated_at is missing")
    try:
        parsed = datetime.fromisoformat(generated_at)
    except ValueError as error:
        raise RuntimeError("Compose E2E evidence generated_at is invalid") from error
    if parsed.tzinfo is None:
        raise RuntimeError("Compose E2E evidence generated_at has no timezone")
    age = datetime.now(UTC) - parsed.astimezone(UTC)
    if age.total_seconds() < -300 or age.total_seconds() > 7200:
        raise RuntimeError("Compose E2E evidence is stale or from the future")
    results = raw.get("results")
    missing_results = (
        not isinstance(results, dict)
        or set(results) != set(REQUIRED_RESULTS)
        or any(results.get(key) != "passed" for key in REQUIRED_RESULTS)
    )
    if missing_results:
        raise RuntimeError("Compose E2E evidence is missing required passed results")
    if raw.get("rabbitmq_probe_run_id") != raw.get("run_id"):
        raise RuntimeError("Compose E2E evidence RabbitMQ run identity is invalid")
    certificate_sha256 = raw.get("tls_certificate_sha256")
    if (
        not isinstance(certificate_sha256, str)
        or SHA256_PATTERN.fullmatch(certificate_sha256) is None
    ):
        raise RuntimeError("Compose E2E evidence TLS certificate digest is invalid")
    run_id = raw.get("run_id")
    try:
        uuid.UUID(str(run_id))
    except ValueError as error:
        raise RuntimeError("Compose E2E evidence run_id is invalid") from error
    compose_project = raw.get("compose_project")
    if (
        not isinstance(compose_project, str)
        or COMPOSE_PROJECT_PATTERN.fullmatch(compose_project) is None
    ):
        raise RuntimeError("Compose E2E evidence compose_project is invalid")
    resolved_compose_sha256 = raw.get("resolved_compose_sha256")
    if (
        not isinstance(resolved_compose_sha256, str)
        or SHA256_PATTERN.fullmatch(resolved_compose_sha256) is None
    ):
        raise RuntimeError("Compose E2E evidence resolved_compose_sha256 is invalid")
    for image_name in ("backend", "frontend"):
        if raw.get(f"{image_name}_image_revision") != git_sha:
            raise RuntimeError(f"Compose E2E evidence {image_name}_image_revision is invalid")
        image_id = raw.get(f"{image_name}_image_id")
        if (
            not isinstance(image_id, str)
            or not image_id.startswith("sha256:")
            or SHA256_PATTERN.fullmatch(image_id.removeprefix("sha256:")) is None
        ):
            raise RuntimeError(f"Compose E2E evidence {image_name}_image_id is invalid")
    service_container_ids = raw.get("service_container_ids")
    if not isinstance(service_container_ids, dict) or not REQUIRED_SERVICE_CONTAINERS <= set(
        service_container_ids
    ):
        raise RuntimeError("Compose E2E evidence service_container_ids is incomplete")
    if any(
        not isinstance(service_container_ids[name], str)
        or SHA256_PATTERN.fullmatch(service_container_ids[name]) is None
        for name in REQUIRED_SERVICE_CONTAINERS
    ):
        raise RuntimeError("Compose E2E evidence service_container_ids is invalid")
    worker_queue_consumers = raw.get("worker_queue_consumers")
    if not isinstance(worker_queue_consumers, dict) or any(
        not isinstance(worker_queue_consumers.get(queue), int)
        or isinstance(worker_queue_consumers.get(queue), bool)
        or int(worker_queue_consumers[queue]) < 1
        for queue in REQUIRED_WORKER_QUEUES
    ):
        raise RuntimeError("Compose E2E evidence worker_queue_consumers is incomplete")
    business_probe = raw.get("business_probe")
    if (
        not isinstance(business_probe, dict)
        or business_probe.get("status") != "passed"
        or business_probe.get("email_verification_floor") != "passed"
        or business_probe.get("mock_smtp_delivery") != "passed"
    ):
        raise RuntimeError("Compose E2E email verification behavior is incomplete")
    _validate_v5_evidence(raw)
    return raw


def _validate_v5_evidence(raw: dict[str, object]) -> None:
    run_id = str(raw.get("run_id"))
    tls = raw.get("tls")
    channels = tls.get("verified_channels") if isinstance(tls, dict) else None
    if (
        not isinstance(tls, dict)
        or tls.get("status") != "passed"
        or tls.get("certificate_bundle_sha256") != raw.get("tls_certificate_sha256")
        or not isinstance(channels, list)
        or set(channels) != {"gateway_https", "minio_https", "ragflow_https", "smtp_starttls"}
    ):
        raise RuntimeError("Compose E2E TLS evidence is incomplete")
    fault_recovery = raw.get("fault_recovery")
    if not isinstance(fault_recovery, dict) or set(fault_recovery) != {
        "rabbitmq",
        "redis",
        "minio",
        "ragflow",
    }:
        raise RuntimeError("Compose E2E fault recovery evidence is incomplete")
    targets: set[str] = set()
    for dependency in ("rabbitmq", "redis", "minio", "ragflow"):
        entry = fault_recovery.get(dependency)
        if (
            not isinstance(entry, dict)
            or entry.get("status") != "passed"
            or entry.get("run_id") != run_id
            or entry.get("event_loss_detected") is not False
            or entry.get("duplicate_remote_document") is not False
            or entry.get("remote_upload_delta") != 1
            or entry.get("remote_document_count") != 1
        ):
            raise RuntimeError("Compose E2E fault recovery evidence is incomplete")
        target = entry.get("target_file_id")
        try:
            targets.add(str(uuid.UUID(str(target))))
        except ValueError as error:
            raise RuntimeError("Compose E2E fault recovery target is invalid") from error
    if len(targets) != 4:
        raise RuntimeError("Compose E2E fault recovery targets are not unique")
    prometheus = raw.get("prometheus_minio_tls")
    if (
        not isinstance(prometheus, dict)
        or prometheus.get("status") != "passed"
        or prometheus.get("health") != "up"
        or prometheus.get("scrape_url") != "https://minio:9000/minio/v2/metrics/cluster"
        or prometheus.get("ca_file") != "/etc/prometheus/tls/ca.crt"
        or prometheus.get("server_name") != "minio"
        or prometheus.get("certificate_verification") != "required"
        or not isinstance(prometheus.get("config_sha256"), str)
        or SHA256_PATTERN.fullmatch(str(prometheus["config_sha256"])) is None
    ):
        raise RuntimeError("Compose E2E Prometheus MinIO TLS evidence is incomplete")
    minio_auth = raw.get("minio_metrics_auth")
    initializer = minio_auth.get("initializer") if isinstance(minio_auth, dict) else None
    anonymous = minio_auth.get("anonymous_access") if isinstance(minio_auth, dict) else None
    atomic = minio_auth.get("atomic_publish") if isinstance(minio_auth, dict) else None
    refresh = minio_auth.get("refresh") if isinstance(minio_auth, dict) else None
    emergency = minio_auth.get("emergency_revocation") if isinstance(minio_auth, dict) else None
    identity = minio_auth.get("identity_reconciliation") if isinstance(minio_auth, dict) else None
    collector = minio_auth.get("collector") if isinstance(minio_auth, dict) else None
    if (
        not isinstance(minio_auth, dict)
        or set(minio_auth)
        != {
            "status",
            "auth_mode",
            "initializer",
            "anonymous_access",
            "atomic_publish",
            "refresh",
            "emergency_revocation",
            "identity_reconciliation",
            "collector",
        }
        or minio_auth.get("status") != "passed"
        or minio_auth.get("auth_mode") != "jwt_bearer_file"
        or not isinstance(initializer, dict)
        or set(initializer)
        != {
            "status",
            "container_exit",
            "logs",
            "token_file",
            "mode",
            "uid",
            "gid",
        }
        or initializer.get("status") != "passed"
        or initializer.get("container_exit") != "exited_0"
        or initializer.get("logs") != "empty"
        or initializer.get("token_file") != "strict_semantic_jwt_single_lf"
        or initializer.get("mode") != "0440"
        or initializer.get("uid") != 65534
        or initializer.get("gid") != 65534
        or not isinstance(anonymous, dict)
        or set(anonymous) != {"status", "http_status"}
        or anonymous.get("status") != "denied"
        or anonymous.get("http_status") not in {401, 403}
        or not isinstance(atomic, dict)
        or set(atomic)
        != {
            "status",
            "concurrent_runs",
            "concurrent_successes",
            "term_exit_code",
            "term_cleanup",
            "sigkill_exit_code",
            "sigkill_orphan_observed",
            "post_sigkill_recovery",
            "cleanup_after_no_initializer",
            "final_temporary_file_count",
        }
        or atomic.get("status") != "passed"
        or atomic.get("concurrent_runs") != 2
        or atomic.get("concurrent_successes") != 2
        or atomic.get("term_exit_code") not in {1, 143}
        or atomic.get("term_cleanup") != "passed"
        or atomic.get("sigkill_exit_code") != 137
        or atomic.get("sigkill_orphan_observed") is not True
        or atomic.get("post_sigkill_recovery") != "passed"
        or atomic.get("cleanup_after_no_initializer") is not True
        or atomic.get("final_temporary_file_count") != 0
        or not isinstance(refresh, dict)
        or set(refresh)
        != {
            "status",
            "semantics",
            "credential_changed",
            "mtime_advanced",
            "previous_jwt_http_status",
            "refreshed_jwt_http_status",
            "consumer_processes_unchanged",
            "prometheus_health_before",
            "prometheus_health_after",
        }
        or refresh.get("status") != "passed"
        or refresh.get("semantics") != "consumer_refresh_not_revocation"
        or refresh.get("credential_changed") is not True
        or refresh.get("mtime_advanced") is not True
        or refresh.get("previous_jwt_http_status") != 200
        or refresh.get("refreshed_jwt_http_status") != 200
        or refresh.get("consumer_processes_unchanged") is not True
        or refresh.get("prometheus_health_before") != "up"
        or refresh.get("prometheus_health_after") != "up"
        or not isinstance(emergency, dict)
        or set(emergency)
        != {
            "status",
            "method",
            "previous_jwt_http_status_after_restart",
            "refreshed_jwt_http_status_after_restart",
            "replacement_jwt_http_status",
            "minio_recreated",
            "bootstrap_reconciled",
            "expected_minio_interruption",
            "consumer_processes_unchanged",
            "automatic_consumer_recovery",
            "prometheus_health_after_recovery",
        }
        or emergency.get("status") != "passed"
        or emergency.get("method") != "root_credential_rotation_and_minio_restart"
        or emergency.get("previous_jwt_http_status_after_restart") != 403
        or emergency.get("refreshed_jwt_http_status_after_restart") != 403
        or emergency.get("replacement_jwt_http_status") != 200
        or emergency.get("minio_recreated") is not True
        or emergency.get("bootstrap_reconciled") is not True
        or emergency.get("expected_minio_interruption") is not True
        or emergency.get("consumer_processes_unchanged") is not True
        or emergency.get("automatic_consumer_recovery") is not True
        or emergency.get("prometheus_health_after_recovery") != "up"
        or not isinstance(identity, dict)
        or set(identity)
        != {
            "status",
            "stale_direct_policy_removed",
            "stale_group_membership_removed",
            "intended_policy_attached",
            "intended_bucket_operations",
            "secondary_bucket_operations_denied",
            "admin_operations_denied",
        }
        or identity.get("status") != "passed"
        or identity.get("stale_direct_policy_removed") is not True
        or identity.get("stale_group_membership_removed") is not True
        or identity.get("intended_policy_attached") is not True
        or identity.get("intended_bucket_operations") != ["get", "put", "delete"]
        or identity.get("secondary_bucket_operations_denied") != ["list", "get", "put"]
        or identity.get("admin_operations_denied") != ["info", "user_list", "policy_list"]
        or not isinstance(collector, dict)
        or set(collector) != {"status", "component", "last_success_advanced"}
        or collector.get("status") != "passed"
        or collector.get("component") != "minio_capacity"
        or collector.get("last_success_advanced") is not True
    ):
        raise RuntimeError("Compose E2E MinIO metrics auth evidence is incomplete")


def _image_revision(image: str) -> str:
    return run(
        [
            "docker",
            "image",
            "inspect",
            "--format",
            '{{ index .Config.Labels "org.opencontainers.image.revision" }}',
            image,
        ]
    ).strip()


def _resolve_image_id(image: str) -> str:
    image_id = run(["docker", "image", "inspect", "--format", "{{.Id}}", image]).lower()
    if (
        not image_id.startswith("sha256:")
        or SHA256_PATTERN.fullmatch(image_id.removeprefix("sha256:")) is None
    ):
        raise RuntimeError(f"Docker returned an invalid immutable image ID for {image}")
    return image_id


def _normalize_architecture(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in ARM64_NAMES:
        return "arm64"
    return normalized


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-image", required=True)
    parser.add_argument("--frontend-image", required=True)
    parser.add_argument("--proof", type=Path, required=True)
    parser.add_argument("--git-sha", default=os.environ.get("GITHUB_SHA", "unknown"))
    parser.add_argument("--environment", default="staging", choices=("staging", "production"))
    parser.add_argument("--compose-e2e-evidence", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    proof = args.proof.resolve()
    if proof.suffix.lower() != ".json":
        raise RuntimeError("DGX proof path must use a .json suffix")
    if proof.exists():
        proof.unlink()
    evidence = verify(
        backend_image=args.backend_image,
        frontend_image=args.frontend_image,
        git_sha=args.git_sha,
        environment=args.environment,
        compose_e2e_evidence=args.compose_e2e_evidence,
    )
    proof.parent.mkdir(parents=True, exist_ok=True)
    temporary = proof.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(asdict(evidence), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(proof)
    sys.stdout.write(f"DGX Spark device verification passed: {proof}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
