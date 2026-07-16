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
    "workers",
    "rabbitmq_topology",
    "minio_tls",
    "upload_review_ragflow",
    "dlq_protocol",
    "cleanup",
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
        full_compose_e2e=str(compose_e2e["full_compose_e2e"]),
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
    expected = {
        "status": "passed",
        "git_sha": git_sha,
        "environment": environment,
        "full_compose_e2e": "passed",
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
    missing_results = not isinstance(results, dict) or any(
        results.get(key) != "passed" for key in REQUIRED_RESULTS
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
    return raw


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
    if not image_id.startswith("sha256:") or SHA256_PATTERN.fullmatch(
        image_id.removeprefix("sha256:")
    ) is None:
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
