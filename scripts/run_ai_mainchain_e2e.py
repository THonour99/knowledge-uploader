from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
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
COMPOSE_SOURCE_ENVIRONMENT_KEYS = frozenset(
    {
        "COMPOSE_DISABLE_ENV_FILE",
        "COMPOSE_ENV_FILES",
        "COMPOSE_FILE",
        "COMPOSE_PATH_SEPARATOR",
        "COMPOSE_PROFILES",
        "COMPOSE_PROJECT_DIRECTORY",
        "COMPOSE_PROJECT_NAME",
    }
)


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


def compose_command(candidate_root: Path, project: str, *args: str) -> list[str]:
    return [
        "docker",
        "compose",
        "--project-name",
        project,
        "--project-directory",
        str(candidate_root),
        "--file",
        str(candidate_root / BASE_COMPOSE.name),
        "--file",
        str(candidate_root / AI_COMPOSE.name),
        *args,
    ]


def sanitized_environment(source: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    environment = dict(source)
    removed = sorted(
        {key.upper() for key in environment if key.upper() in COMPOSE_SOURCE_ENVIRONMENT_KEYS}
    )
    for key in tuple(environment):
        if key.upper() in COMPOSE_SOURCE_ENVIRONMENT_KEYS:
            environment.pop(key)
    return environment, removed


def free_host_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def random_token(byte_count: int = 24) -> str:
    return secrets.token_hex(byte_count)


def resolve_candidate_revision(
    environment: dict[str, str],
    requested_revision: str,
    *,
    repo_root: Path = ROOT,
    identity_step: str = "git_identity",
    status_step: str = "candidate_worktree",
) -> str:
    git_prefix = ["git"] if repo_root.resolve() == ROOT.resolve() else ["git", "-C", str(repo_root)]
    result = run_command(
        [*git_prefix, "rev-parse", "HEAD"],
        environment=environment,
        step=identity_step,
        timeout_seconds=30,
    )
    head_revision = result.stdout.strip().lower()
    normalized_requested = requested_revision.strip().lower()
    if (
        re.fullmatch(r"[0-9a-f]{40}", head_revision) is None
        or re.fullmatch(r"[0-9a-f]{40}", normalized_requested) is None
        or normalized_requested != head_revision
    ):
        raise AiMainchainE2EError(identity_step)
    worktree_status = run_command(
        [*git_prefix, "status", "--porcelain=v1", "--untracked-files=all"],
        environment=environment,
        step=status_step,
        timeout_seconds=30,
    )
    if worktree_status.stdout.strip():
        raise AiMainchainE2EError(status_step)
    return head_revision


def resolve_tree_revision(
    environment: dict[str, str],
    revision: str,
    *,
    repo_root: Path,
    step: str,
) -> str:
    git_prefix = ["git"] if repo_root.resolve() == ROOT.resolve() else ["git", "-C", str(repo_root)]
    result = run_command(
        [*git_prefix, "rev-parse", f"{revision}^{{tree}}"],
        environment=environment,
        step=step,
        timeout_seconds=30,
    )
    tree_revision = result.stdout.strip().lower()
    if re.fullmatch(r"[0-9a-f]{40}", tree_revision) is None:
        raise AiMainchainE2EError(step)
    return tree_revision


def source_fingerprint(root: Path = ROOT) -> str:
    resolved_root = root.resolve()
    digest = hashlib.sha256()
    candidates = [
        root / "docker-compose.yml",
        root / AI_COMPOSE.name,
        root / "ops" / "e2e" / "mock_llm.py",
        root / "scripts" / "ai_mainchain_probe.py",
        root / "scripts" / "run_ai_mainchain_e2e.py",
        root / "backend" / "Dockerfile",
        root / "backend" / "requirements.txt",
    ]
    candidates.extend((root / "backend" / "app").rglob("*.py"))
    for path in sorted({candidate.resolve() for candidate in candidates}):
        relative = path.relative_to(resolved_root).as_posix()
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
    candidate_root: Path,
    project: str,
    environment: dict[str, str],
    expected_image_id: str,
) -> list[str]:
    verified: list[str] = []
    for service in BACKEND_CANDIDATE_SERVICES:
        container_result = run_command(
            compose_command(candidate_root, project, "ps", "--quiet", service),
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
    candidate_root: Path,
    project: str,
    environment: dict[str, str],
    expected_image_id: str,
) -> dict[str, object]:
    services_result = run_command(
        compose_command(candidate_root, project, "ps", "--status", "running", "--services"),
        environment=environment,
        step="running_services",
        timeout_seconds=60,
    )
    running_services = frozenset(services_result.stdout.split())
    if not REQUIRED_RUNNING_SERVICES.issubset(running_services):
        raise AiMainchainE2EError("running_services")
    candidate_services = verify_candidate_containers(
        candidate_root=candidate_root,
        project=project,
        environment=environment,
        expected_image_id=expected_image_id,
    )

    queue_result = run_command(
        compose_command(
            candidate_root,
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
        compose_command(candidate_root, project, "logs", "--no-color", "worker-ai"),
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


def worktree_registered(output: str, candidate_root: Path) -> bool:
    expected = candidate_root.resolve()
    for line in output.splitlines():
        if not line.startswith("worktree "):
            continue
        if Path(line.removeprefix("worktree ")).resolve() == expected:
            return True
    return False


def remove_detached_worktree(
    *,
    candidate_root: Path,
    runtime_root: Path,
    environment: dict[str, str],
) -> None:
    try:
        run_command(
            ["git", "worktree", "remove", "--force", str(candidate_root)],
            environment=environment,
            step="candidate_worktree_remove",
            timeout_seconds=300,
        )
    except AiMainchainE2EError:
        pass

    try:
        if candidate_root.exists():
            shutil.rmtree(candidate_root)
        run_command(
            ["git", "worktree", "prune", "--expire", "now"],
            environment=environment,
            step="candidate_worktree_prune",
            timeout_seconds=60,
        )
        listed = run_command(
            ["git", "worktree", "list", "--porcelain"],
            environment=environment,
            step="candidate_worktree_cleanup_check",
            timeout_seconds=60,
        )
        if candidate_root.exists() or worktree_registered(listed.stdout, candidate_root):
            raise AiMainchainE2EError("candidate_worktree_cleanup_check")
        shutil.rmtree(runtime_root)
    except AiMainchainE2EError:
        raise
    except OSError as exc:
        raise AiMainchainE2EError("candidate_worktree_cleanup") from exc


def execute(
    *,
    output_path: Path | None,
    requested_revision: str,
) -> Path:
    if output_path is not None and output_path.exists():
        raise AiMainchainE2EError("evidence_output_exists")
    base_environment, removed_compose_environment = sanitized_environment(dict(os.environ))
    revision = resolve_candidate_revision(base_environment, requested_revision)
    resolved_output = output_path or default_output_path(revision)
    if resolved_output.exists():
        raise AiMainchainE2EError("evidence_output_exists")

    run_id = uuid.uuid4()
    project = f"ku-ai-mainchain-{run_id.hex[:12]}"
    runtime_root = Path(tempfile.mkdtemp(prefix=f"{project}-"))
    candidate_root = runtime_root / "candidate"
    environment = ephemeral_environment(
        base_environment=base_environment,
        run_id=run_id,
        revision=revision,
    )
    cleanup_required = False
    worktree_added = False
    cleanup_failures: list[AiMainchainE2EError] = []
    primary_failure: Exception | None = None
    source_before = ""
    candidate_tree = ""
    candidate_image: dict[str, str] = {}
    evidence: dict[str, Any] = {}
    detached_unchanged = False
    compose_sha256: dict[str, str] = {}
    try:
        run_command(
            ["git", "worktree", "add", "--detach", str(candidate_root), revision],
            environment=base_environment,
            step="candidate_worktree_add",
            timeout_seconds=180,
        )
        worktree_added = True
        detached_revision = resolve_candidate_revision(
            base_environment,
            revision,
            repo_root=candidate_root,
            identity_step="detached_git_identity",
            status_step="detached_worktree",
        )
        expected_tree = resolve_tree_revision(
            base_environment,
            revision,
            repo_root=ROOT,
            step="expected_tree_identity",
        )
        candidate_tree = resolve_tree_revision(
            base_environment,
            detached_revision,
            repo_root=candidate_root,
            step="detached_tree_identity",
        )
        if candidate_tree != expected_tree:
            raise AiMainchainE2EError("detached_tree_identity")
        source_before = source_fingerprint(candidate_root)
        compose_sha256 = {
            name: hashlib.sha256((candidate_root / name).read_bytes()).hexdigest()
            for name in (BASE_COMPOSE.name, AI_COMPOSE.name)
        }

        cleanup_required = True
        run_command(
            compose_command(candidate_root, project, "build", "backend-api"),
            environment=environment,
            step="build_backend_image",
            timeout_seconds=900,
        )
        candidate_image = inspect_candidate_image(
            image_reference=environment["BACKEND_IMAGE"],
            revision=revision,
            environment=environment,
        )
        run_command(
            compose_command(
                candidate_root,
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
                candidate_root,
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
                candidate_root,
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
                candidate_root,
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
                candidate_root,
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
            candidate_root=candidate_root,
            project=project,
            environment=environment,
            expected_image_id=candidate_image["image_id"],
        )
        evidence["runtime_evidence"] = runtime
    except Exception as exc:
        primary_failure = exc
    finally:
        if cleanup_required:
            try:
                run_command(
                    compose_command(
                        candidate_root,
                        project,
                        "down",
                        "--volumes",
                        "--remove-orphans",
                    ),
                    environment=environment,
                    step="cleanup_compose",
                    timeout_seconds=180,
                )
            except AiMainchainE2EError as exc:
                cleanup_failures.append(exc)
            try:
                run_command(
                    ["docker", "image", "rm", "--force", environment["BACKEND_IMAGE"]],
                    environment=environment,
                    step="cleanup_candidate_image",
                    timeout_seconds=180,
                )
            except AiMainchainE2EError:
                pass
            try:
                image_check = run_command(
                    ["docker", "image", "ls", "--quiet", environment["BACKEND_IMAGE"]],
                    environment=environment,
                    step="cleanup_candidate_image_check",
                    timeout_seconds=60,
                )
                if image_check.stdout.strip():
                    raise AiMainchainE2EError("cleanup_candidate_image_check")
            except AiMainchainE2EError as exc:
                cleanup_failures.append(exc)
        if worktree_added and candidate_root.is_dir():
            try:
                final_revision = resolve_candidate_revision(
                    base_environment,
                    revision,
                    repo_root=candidate_root,
                    identity_step="detached_git_identity_after",
                    status_step="detached_worktree_after",
                )
                source_after = source_fingerprint(candidate_root)
                final_tree = resolve_tree_revision(
                    base_environment,
                    final_revision,
                    repo_root=candidate_root,
                    step="detached_tree_identity_after",
                )
                detached_unchanged = (
                    final_revision == revision
                    and final_tree == candidate_tree
                    and source_after == source_before
                )
                if not detached_unchanged:
                    raise AiMainchainE2EError("candidate_changed")
            except AiMainchainE2EError as exc:
                if primary_failure is None:
                    primary_failure = exc
        try:
            remove_detached_worktree(
                candidate_root=candidate_root,
                runtime_root=runtime_root,
                environment=base_environment,
            )
        except AiMainchainE2EError as exc:
            cleanup_failures.append(exc)

    if primary_failure is not None:
        raise primary_failure
    if cleanup_failures:
        raise cleanup_failures[0]
    if not detached_unchanged:
        raise AiMainchainE2EError("candidate_changed")

    evidence["candidate"] = {
        "bound": True,
        **candidate_image,
        "git_sha": revision,
        "git_tree_sha": candidate_tree,
        "source_sha256": source_before,
        "compose_file_sha256": compose_sha256,
        "compose_files": [BASE_COMPOSE.name, AI_COMPOSE.name],
        "compose_environment_keys_removed": removed_compose_environment,
        "generated_at": datetime.now(UTC).isoformat(),
        "compose_project_isolated": True,
        "database_name_suffix_guard": "_test",
        "worktree_clean": True,
        "detached_worktree_bound": True,
        "detached_worktree_removed": True,
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
