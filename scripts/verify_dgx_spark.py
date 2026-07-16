"""Produce verifiable release evidence on a physical NVIDIA DGX Spark host."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

ARM64_NAMES = frozenset({"aarch64", "arm64"})
GIT_SHA_PATTERN = re.compile(r"[0-9a-fA-F]{7,64}")


@dataclass(frozen=True)
class DgxSparkEvidence:
    status: str
    generated_at: str
    git_sha: str
    environment: str
    architecture: str
    full_compose_e2e: str
    docker_architecture: str
    gpu_name: str
    gpu_driver: str
    backend_image: str
    backend_image_id: str
    frontend_image: str
    frontend_image_id: str
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
    if host_machine not in ARM64_NAMES:
        raise RuntimeError(f"physical ARM64 host required, got {host_machine}")

    docker_architecture = run(["docker", "info", "--format", "{{.Architecture}}"]).lower()
    if docker_architecture not in ARM64_NAMES:
        raise RuntimeError(f"native ARM64 Docker daemon required, got {docker_architecture}")

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
        ["docker", "image", "inspect", "--format", "{{.Architecture}}", backend_image]
    ).lower()
    frontend_arch = run(
        ["docker", "image", "inspect", "--format", "{{.Architecture}}", frontend_image]
    ).lower()
    if backend_arch not in ARM64_NAMES or frontend_arch not in ARM64_NAMES:
        raise RuntimeError("both release images must be native ARM64 images")

    run(
        [
            "docker",
            "run",
            "--rm",
            backend_image,
            "python",
            "-c",
            (
                "import platform, asyncpg, cryptography, prometheus_client, tiktoken; "
                "assert platform.machine().lower() in {'aarch64', 'arm64'}"
            ),
        ]
    )
    run(["docker", "run", "--rm", frontend_image, "nginx", "-t"])

    compose_e2e = _load_compose_e2e_evidence(
        compose_e2e_evidence,
        git_sha=git_sha,
        environment=environment,
        architecture=host_machine,
    )

    return DgxSparkEvidence(
        status="passed",
        generated_at=datetime.now(UTC).isoformat(),
        git_sha=git_sha,
        environment=environment,
        architecture=host_machine,
        full_compose_e2e=str(compose_e2e["full_compose_e2e"]),
        docker_architecture=docker_architecture,
        gpu_name=gpu_name,
        gpu_driver=gpu_driver,
        backend_image=backend_image,
        backend_image_id=run(["docker", "image", "inspect", "--format", "{{.Id}}", backend_image]),
        frontend_image=frontend_image,
        frontend_image_id=run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", frontend_image]
        ),
        compose_e2e_evidence_sha256=_sha256_file(compose_e2e_evidence),
    )


def _load_compose_e2e_evidence(
    path: Path,
    *,
    git_sha: str,
    environment: str,
    architecture: str,
) -> dict[str, object]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RuntimeError("Compose E2E evidence must be a JSON object")
    expected = {
        "status": "passed",
        "git_sha": git_sha,
        "environment": environment,
        "architecture": architecture,
        "full_compose_e2e": "passed",
    }
    for key, expected_value in expected.items():
        if raw.get(key) != expected_value:
            raise RuntimeError(f"Compose E2E evidence mismatch: {key}")
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
    required_results = (
        "compose_up",
        "alembic_head",
        "ready",
        "workers",
        "rabbitmq_topology",
        "upload_review_ragflow",
        "dlq_protocol",
    )
    results = raw.get("results")
    missing_results = not isinstance(results, dict) or any(
        results.get(key) != "passed" for key in required_results
    )
    if missing_results:
        raise RuntimeError("Compose E2E evidence is missing required passed results")
    return raw


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
