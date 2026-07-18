from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASE_COMPOSE = ROOT / "docker-compose.yml"
AI_COMPOSE = ROOT / "docker-compose.ai-mainchain.yml"
DATABASE_NAME = "knowledge_uploader_ai_probe_test"
PROBE_MARKER = "AI_MAINCHAIN_EVIDENCE="
REQUIRED_RUNNING_SERVICES = frozenset(
    {
        "backend-api",
        "mock-llm",
        "outbox-dispatcher",
        "worker-ai",
        "postgres",
        "rabbitmq",
        "redis",
        "minio",
    }
)
BACKEND_CANDIDATE_SERVICES = (
    "backend-api",
    "outbox-dispatcher",
    "worker-ai",
    "mock-llm",
)
TASK_SUCCESS_PATTERN = re.compile(r"Task ai\.analyze_file\[[^\]]+\] succeeded")


class AiMainchainE2EError(RuntimeError):
    def __init__(self, step: str) -> None:
        super().__init__(step)
        self.step = step


@dataclass(frozen=True)
class RunResult:
    stdout: str
    stderr: str


def announce(step: str) -> None:
    sys.stderr.write(f"[ai-mainchain-e2e] step={step}\n")
    sys.stderr.flush()


def run_command(
    args: Sequence[str],
    *,
    environment: dict[str, str],
    step: str,
    timeout_seconds: int,
) -> RunResult:
    announce(step)
    try:
        completed = subprocess.run(
            list(args),
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AiMainchainE2EError(step) from exc
    if completed.returncode != 0:
        raise AiMainchainE2EError(step)
    return RunResult(stdout=completed.stdout, stderr=completed.stderr)


def compose_command(project: str, *args: str) -> list[str]:
    return [
        "docker",
        "compose",
        "--project-name",
        project,
        "--file",
        str(BASE_COMPOSE),
        "--file",
        str(AI_COMPOSE),
        *args,
    ]


def free_host_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def random_token(byte_count: int = 24) -> str:
    return secrets.token_hex(byte_count)


def resolve_candidate_revision(
    environment: dict[str, str],
    requested_revision: str,
) -> str:
    result = run_command(
        ["git", "rev-parse", "HEAD"],
        environment=environment,
        step="git_identity",
        timeout_seconds=30,
    )
    head_revision = result.stdout.strip().lower()
    normalized_requested = requested_revision.strip().lower()
    if (
        re.fullmatch(r"[0-9a-f]{40}", head_revision) is None
        or re.fullmatch(r"[0-9a-f]{40}", normalized_requested) is None
        or normalized_requested != head_revision
    ):
        raise AiMainchainE2EError("git_identity")
    worktree_status = run_command(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        environment=environment,
        step="candidate_worktree",
        timeout_seconds=30,
    )
    if worktree_status.stdout.strip():
        raise AiMainchainE2EError("candidate_worktree")
    return head_revision


def source_fingerprint() -> str:
    digest = hashlib.sha256()
    candidates = [
        ROOT / "docker-compose.yml",
        AI_COMPOSE,
        ROOT / "ops" / "e2e" / "mock_llm.py",
        ROOT / "scripts" / "ai_mainchain_probe.py",
        ROOT / "scripts" / "run_ai_mainchain_e2e.py",
        ROOT / "backend" / "Dockerfile",
        ROOT / "backend" / "requirements.txt",
    ]
    candidates.extend((ROOT / "backend" / "app").rglob("*.py"))
    for path in sorted({candidate.resolve() for candidate in candidates}):
        relative = path.relative_to(ROOT).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def ephemeral_environment(
    *,
    base_environment: dict[str, str],
    run_id: uuid.UUID,
    revision: str,
) -> dict[str, str]:
    suffix = run_id.hex[:12]
    postgres_password = random_token(24)
    rabbitmq_password = random_token(24)
    redis_password = random_token(24)
    minio_root_password = random_token(28)
    minio_secret = random_token(24)
    llm_api_key = f"sk-probe-{random_token(18)}"
    admin_password = f"Probe-aA1!{random_token(16)}"
    employee_password = f"Probe-bB2!{random_token(16)}"
    state_token = random_token(24)
    encryption_key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
    backend_image = f"knowledge-uploader-ai-mainchain:{revision[:12]}-{suffix}"

    values = {
        "APP_ENV": "test",
        "APP_BASE_URL": "http://backend-api:8000",
        "BACKEND_API_HOST": "127.0.0.1",
        "BACKEND_API_PORT": str(free_host_port()),
        "BACKEND_BUILD_TARGET": "runtime",
        "BACKEND_IMAGE": backend_image,
        "VCS_REF": revision,
        "POSTGRES_DB": DATABASE_NAME,
        "POSTGRES_USER": "knowledge_ai_probe",
        "POSTGRES_PASSWORD": postgres_password,
        "DATABASE_URL": (
            "postgresql+asyncpg://knowledge_ai_probe:"
            f"{postgres_password}@postgres:5432/{DATABASE_NAME}"
        ),
        "ALEMBIC_DATABASE_URL": (
            "postgresql+psycopg://knowledge_ai_probe:"
            f"{postgres_password}@postgres:5432/{DATABASE_NAME}"
        ),
        "RABBITMQ_USER": "knowledge_ai_probe",
        "RABBITMQ_PASSWORD": rabbitmq_password,
        "CELERY_BROKER_URL": (f"amqp://knowledge_ai_probe:{rabbitmq_password}@rabbitmq:5672//"),
        "REDIS_PASSWORD": redis_password,
        "CACHE_REDIS_URL": f"redis://:{redis_password}@redis:6379/1",
        "MINIO_ENDPOINT": "minio:9000",
        "MINIO_ROOT_USER": "knowledgeaiproberoot",
        "MINIO_ROOT_PASSWORD": minio_root_password,
        "MINIO_ACCESS_KEY": "knowledgeaiprobe",
        "MINIO_SECRET_KEY": minio_secret,
        "MINIO_BUCKET": "knowledge-ai-probe",
        "MINIO_SECURE": "false",
        "JWT_SECRET": random_token(40),
        "ENCRYPTION_KEY": encryption_key,
        "ALLOW_REGISTER": "true",
        "REQUIRE_EMAIL_VERIFICATION": "false",
        "ALLOWED_EMAIL_DOMAINS": "ai-probe.example.com",
        "AI_ANALYSIS_ENABLED": "true",
        "ALLOW_EXTERNAL_LLM": "false",
        "LLM_PROVIDER": "local_openai_compatible",
        "LLM_BASE_URL": "http://mock-llm:8081/v1",
        "LLM_ALLOWED_BASE_URLS": "http://mock-llm:8081/v1",
        "LLM_API_KEY": llm_api_key,
        "LLM_MODEL": "ai-mainchain-probe-model",
        "AI_REQUEST_TIMEOUT": "15",
        "AI_MAX_RETRY_COUNT": "0",
        "AI_PROBE_ADMIN_EMAIL": "admin@ai-probe.example.com",
        "AI_PROBE_ADMIN_PASSWORD": admin_password,
        "AI_PROBE_DATABASE_NAME": DATABASE_NAME,
        "AI_PROBE_EMPLOYEE_PASSWORD": employee_password,
        "AI_PROBE_LLM_API_KEY": llm_api_key,
        "AI_PROBE_LLM_DELAY_MS": "500",
        "AI_PROBE_LLM_MODEL": "ai-mainchain-probe-model",
        "AI_PROBE_RUN_ID": suffix,
        "AI_PROBE_STATE_TOKEN": state_token,
        "SEED_ADMIN_PASSWORD": admin_password,
        "PYTHONUTF8": "1",
    }
    environment = dict(base_environment)
    environment.update(values)
    return environment


def parse_probe_evidence(output: str) -> dict[str, Any]:
    matching_lines = [
        line[len(PROBE_MARKER) :] for line in output.splitlines() if line.startswith(PROBE_MARKER)
    ]
    if len(matching_lines) != 1:
        raise AiMainchainE2EError("probe_evidence")
    try:
        payload: object = json.loads(matching_lines[0])
    except json.JSONDecodeError as exc:
        raise AiMainchainE2EError("probe_evidence") from exc
    if not isinstance(payload, dict) or payload.get("status") != "passed":
        raise AiMainchainE2EError("probe_evidence")
    boundary = payload.get("provider_boundary")
    if not isinstance(boundary, dict) or boundary.get("external_provider_verified") is not False:
        raise AiMainchainE2EError("provider_boundary")
    if payload.get("database_name") != DATABASE_NAME:
        raise AiMainchainE2EError("probe_database")
    return payload


def parse_queue_snapshot(output: str) -> dict[str, tuple[int, int]]:
    queues: dict[str, tuple[int, int]] = {}
    for raw_line in output.splitlines():
        fields = raw_line.split()
        if len(fields) != 3:
            continue
        name, messages_raw, consumers_raw = fields
        try:
            queues[name] = (int(messages_raw), int(consumers_raw))
        except ValueError:
            continue
    return queues


def inspect_candidate_image(
    *,
    image_reference: str,
    revision: str,
    environment: dict[str, str],
) -> dict[str, str]:
    labels_result = run_command(
        [
            "docker",
            "image",
            "inspect",
            image_reference,
            "--format",
            "{{json .Config.Labels}}",
        ],
        environment=environment,
        step="candidate_image_labels",
        timeout_seconds=60,
    )
    try:
        labels: object = json.loads(labels_result.stdout)
    except json.JSONDecodeError as exc:
        raise AiMainchainE2EError("candidate_image_labels") from exc
    if not isinstance(labels, dict) or labels.get("org.opencontainers.image.revision") != revision:
        raise AiMainchainE2EError("candidate_image_revision")
    image_id_result = run_command(
        ["docker", "image", "inspect", image_reference, "--format", "{{.Id}}"],
        environment=environment,
        step="candidate_image_id",
        timeout_seconds=60,
    )
    image_id = image_id_result.stdout.strip()
    if re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None:
        raise AiMainchainE2EError("candidate_image_id")
    return {
        "image_reference": image_reference,
        "image_id": image_id,
        "oci_revision": revision,
    }


def verify_candidate_containers(
    *,
    project: str,
    environment: dict[str, str],
    expected_image_id: str,
) -> list[str]:
    verified: list[str] = []
    for service in BACKEND_CANDIDATE_SERVICES:
        container_result = run_command(
            compose_command(project, "ps", "--quiet", service),
            environment=environment,
            step=f"{service}_container_identity",
            timeout_seconds=60,
        )
        container_id = container_result.stdout.strip()
        if not container_id:
            raise AiMainchainE2EError(f"{service}_container_identity")
        image_result = run_command(
            ["docker", "container", "inspect", container_id, "--format", "{{.Image}}"],
            environment=environment,
            step=f"{service}_image_identity",
            timeout_seconds=60,
        )
        if image_result.stdout.strip() != expected_image_id:
            raise AiMainchainE2EError(f"{service}_image_identity")
        verified.append(service)
    return verified


def verify_runtime(
    *,
    project: str,
    environment: dict[str, str],
    expected_image_id: str,
) -> dict[str, object]:
    services_result = run_command(
        compose_command(project, "ps", "--status", "running", "--services"),
        environment=environment,
        step="running_services",
        timeout_seconds=60,
    )
    running_services = frozenset(services_result.stdout.split())
    if not REQUIRED_RUNNING_SERVICES.issubset(running_services):
        raise AiMainchainE2EError("running_services")
    candidate_services = verify_candidate_containers(
        project=project,
        environment=environment,
        expected_image_id=expected_image_id,
    )

    queue_result = run_command(
        compose_command(
            project,
            "exec",
            "-T",
            "rabbitmq",
            "rabbitmqctl",
            "-q",
            "list_queues",
            "name",
            "messages",
            "consumers",
        ),
        environment=environment,
        step="rabbitmq_queue_evidence",
        timeout_seconds=60,
    )
    queues = parse_queue_snapshot(queue_result.stdout)
    ai_queue = queues.get("ai_queue")
    if ai_queue is None or ai_queue[0] != 0 or ai_queue[1] < 1:
        raise AiMainchainE2EError("rabbitmq_queue_evidence")

    logs_result = run_command(
        compose_command(project, "logs", "--no-color", "worker-ai"),
        environment=environment,
        step="worker_ai_log_evidence",
        timeout_seconds=60,
    )
    task_success_count = len(TASK_SUCCESS_PATTERN.findall(logs_result.stdout))
    if task_success_count < 2:
        raise AiMainchainE2EError("worker_ai_log_evidence")
    return {
        "running_services": sorted(REQUIRED_RUNNING_SERVICES),
        "ai_queue_messages": ai_queue[0],
        "ai_queue_consumers": ai_queue[1],
        "worker_ai_success_count": task_success_count,
        "candidate_image_services": candidate_services,
    }


def atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise AiMainchainE2EError("evidence_output_exists")
    temporary = path.with_suffix(f"{path.suffix}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise AiMainchainE2EError("evidence_output_exists") from exc
        except OSError as exc:
            raise AiMainchainE2EError("evidence_write") from exc
    except AiMainchainE2EError:
        raise
    except OSError as exc:
        raise AiMainchainE2EError("evidence_write") from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def default_output_path(revision: str) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return ROOT / "artifacts" / "ai-mainchain-e2e" / f"{revision}-{stamp}.json"


def execute(
    *,
    output_path: Path | None,
    requested_revision: str,
) -> Path:
    if output_path is not None and output_path.exists():
        raise AiMainchainE2EError("evidence_output_exists")
    base_environment = dict(os.environ)
    revision = resolve_candidate_revision(base_environment, requested_revision)
    resolved_output = output_path or default_output_path(revision)
    if resolved_output.exists():
        raise AiMainchainE2EError("evidence_output_exists")
    source_before = source_fingerprint()
    run_id = uuid.uuid4()
    project = f"ku-ai-mainchain-{run_id.hex[:12]}"
    environment = ephemeral_environment(
        base_environment=base_environment,
        run_id=run_id,
        revision=revision,
    )
    cleanup_required = False
    try:
        run_command(
            compose_command(project, "build", "backend-api"),
            environment=environment,
            step="build_backend_image",
            timeout_seconds=900,
        )
        cleanup_required = True
        candidate_image = inspect_candidate_image(
            image_reference=environment["BACKEND_IMAGE"],
            revision=revision,
            environment=environment,
        )
        run_command(
            compose_command(
                project,
                "up",
                "--detach",
                "--wait",
                "--wait-timeout",
                "240",
                "postgres",
                "rabbitmq",
                "redis",
                "minio",
                "mock-llm",
            ),
            environment=environment,
            step="start_core_infrastructure",
            timeout_seconds=300,
        )
        run_command(
            compose_command(
                project,
                "run",
                "--rm",
                "--no-deps",
                "backend-api",
                "alembic",
                "upgrade",
                "head",
            ),
            environment=environment,
            step="alembic_upgrade",
            timeout_seconds=180,
        )
        run_command(
            compose_command(
                project,
                "run",
                "--rm",
                "--no-deps",
                "--env",
                "SEED_ADMIN_PASSWORD",
                "backend-api",
                "python",
                "scripts/seed_admin.py",
                "--email",
                environment["AI_PROBE_ADMIN_EMAIL"],
                "--name",
                "AI Mainchain Probe Admin",
            ),
            environment=environment,
            step="seed_admin",
            timeout_seconds=120,
        )
        run_command(
            compose_command(
                project,
                "up",
                "--detach",
                "--wait",
                "--wait-timeout",
                "300",
                "backend-api",
                "outbox-dispatcher",
                "worker-ai",
            ),
            environment=environment,
            step="start_application_chain",
            timeout_seconds=360,
        )
        probe_result = run_command(
            compose_command(
                project,
                "exec",
                "-T",
                "backend-api",
                "python",
                "/ai-probe/ai_mainchain_probe.py",
            ),
            environment=environment,
            step="public_api_and_database_probe",
            timeout_seconds=240,
        )
        evidence = parse_probe_evidence(probe_result.stdout)
        runtime = verify_runtime(
            project=project,
            environment=environment,
            expected_image_id=candidate_image["image_id"],
        )
        evidence["runtime_evidence"] = runtime
    finally:
        if cleanup_required:
            run_command(
                compose_command(project, "down", "--volumes", "--remove-orphans"),
                environment=environment,
                step="cleanup_compose",
                timeout_seconds=180,
            )
            run_command(
                ["docker", "image", "rm", environment["BACKEND_IMAGE"]],
                environment=environment,
                step="cleanup_candidate_image",
                timeout_seconds=180,
            )
    final_revision = resolve_candidate_revision(base_environment, requested_revision)
    source_after = source_fingerprint()
    if final_revision != revision or source_after != source_before:
        raise AiMainchainE2EError("candidate_changed")

    evidence["candidate"] = {
        "bound": True,
        **candidate_image,
        "git_sha": revision,
        "source_sha256": source_before,
        "generated_at": datetime.now(UTC).isoformat(),
        "compose_project_isolated": True,
        "database_name_suffix_guard": "_test",
        "worktree_clean": True,
        "cleanup_completed": True,
    }
    atomic_write_json(resolved_output, evidence)
    announce("passed")
    return resolved_output


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the isolated DOC-002/DOC-003 real-infrastructure AI mainchain probe."
    )
    parser.add_argument(
        "--git-sha",
        required=True,
        help="Exact 40-character candidate commit SHA; it must equal a clean HEAD.",
    )
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_args(argv)
    try:
        output = execute(
            output_path=arguments.output,
            requested_revision=str(arguments.git_sha),
        )
    except AiMainchainE2EError as exc:
        sys.stderr.write(f"[ai-mainchain-e2e] failed_step={exc.step}\n")
        return 1
    sys.stdout.write(f"{output}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
