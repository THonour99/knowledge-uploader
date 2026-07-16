"""Run an isolated, evidence-bound Compose infrastructure and business E2E gate."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import platform
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from infrastructure_e2e_probe import InfrastructureBusinessProbe

ROOT = Path(__file__).resolve().parents[1]
BASE_COMPOSE = ROOT / "docker-compose.yml"
E2E_COMPOSE = ROOT / "docker-compose.e2e.yml"
GIT_SHA_PATTERN = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})")
SHA256_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
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
BACKEND_IMAGE_SERVICES = (
    "backend-api",
    "rabbitmq-topology",
    "outbox-dispatcher",
    "operational-metrics",
    "worker-document",
    "worker-ai",
    "worker-ragflow",
    "worker-notification",
    "scheduler",
    "mock-ragflow",
    "mock-smtp",
)
MAIN_QUEUES = (
    "document_queue",
    "ai_queue",
    "ragflow_queue",
    "notification_queue",
)
DLQ_NAMES = tuple(f"{queue}.dlq" for queue in MAIN_QUEUES)
RAGFLOW_TASK = "ragflow.create_upload_task"
RAGFLOW_CREATION_MAX_RETRIES = 3


class InfrastructureE2EError(RuntimeError):
    """A bounded gate failure that never includes command output or credentials."""

    def __init__(self, step: str, *, cleanup_status: str = "not_started") -> None:
        super().__init__(f"infrastructure E2E failed at step: {step}")
        self.step = step
        self.cleanup_status = cleanup_status


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class CommandRunner:
    def __init__(self, *, environment: dict[str, str]) -> None:
        self._environment = environment

    def run(
        self,
        command: list[str],
        *,
        step: str,
        timeout_seconds: float = 180.0,
        check: bool = True,
    ) -> CommandResult:
        try:
            completed = subprocess.run(
                command,
                cwd=ROOT,
                env=self._environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise InfrastructureE2EError(step) from error
        result = CommandResult(
            returncode=completed.returncode,
            stdout=completed.stdout.strip(),
            stderr=completed.stderr.strip(),
        )
        if check and result.returncode != 0:
            raise InfrastructureE2EError(step)
        return result


@dataclass(frozen=True)
class ImageIdentity:
    reference: str
    content_id: str
    revision: str
    architecture: str


@dataclass(frozen=True)
class GateArguments:
    backend_image: str
    frontend_image: str
    git_sha: str
    environment: str
    evidence_dir: Path
    allow_dirty_worktree: bool


def _compose_command(project: str, arguments: list[str]) -> list[str]:
    return [
        "docker",
        "compose",
        "--ansi",
        "never",
        "--project-name",
        project,
        "--file",
        str(BASE_COMPOSE),
        "--file",
        str(E2E_COMPOSE),
        *arguments,
    ]


def _compose(
    runner: CommandRunner,
    project: str,
    arguments: list[str],
    *,
    step: str,
    timeout_seconds: float = 180.0,
    check: bool = True,
) -> CommandResult:
    return runner.run(
        _compose_command(project, arguments),
        step=step,
        timeout_seconds=timeout_seconds,
        check=check,
    )


def _compose_up(
    runner: CommandRunner,
    project: str,
    services: list[str],
    *,
    step: str,
    wait_timeout_seconds: int,
    command_timeout_seconds: float,
) -> CommandResult:
    return _compose(
        runner,
        project,
        [
            "up",
            "--detach",
            "--no-build",
            "--wait",
            "--wait-timeout",
            str(wait_timeout_seconds),
            *services,
        ],
        step=step,
        timeout_seconds=command_timeout_seconds,
    )


def _json_object(raw: str, *, step: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise InfrastructureE2EError(step) from error
    if not isinstance(value, dict):
        raise InfrastructureE2EError(step)
    return value


def _last_json_object(raw: str, *, step: str) -> dict[str, Any]:
    for line in reversed(raw.splitlines()):
        if not line.lstrip().startswith("{"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise InfrastructureE2EError(step)


def _inspect_image(runner: CommandRunner, reference: str, *, step: str) -> ImageIdentity:
    result = runner.run(["docker", "image", "inspect", reference], step=step)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise InfrastructureE2EError(step) from error
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
        raise InfrastructureE2EError(step)
    image = payload[0]
    config = image.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    content_id = image.get("Id")
    architecture = image.get("Architecture")
    revision = labels.get("org.opencontainers.image.revision") if isinstance(labels, dict) else None
    if (
        not isinstance(content_id, str)
        or SHA256_PATTERN.fullmatch(content_id) is None
        or not isinstance(architecture, str)
        or not isinstance(revision, str)
    ):
        raise InfrastructureE2EError(step)
    return ImageIdentity(reference, content_id, revision, architecture.lower())


def _normalize_architecture(value: str) -> str:
    normalized = value.strip().lower()
    return "arm64" if normalized in {"arm64", "aarch64"} else normalized


def _source_identity(
    runner: CommandRunner,
    *,
    requested_sha: str,
    allow_dirty: bool,
) -> tuple[str, bool]:
    if GIT_SHA_PATTERN.fullmatch(requested_sha) is None:
        raise InfrastructureE2EError("git_identity")
    head = runner.run(["git", "rev-parse", "HEAD"], step="git_identity").stdout.lower()
    if head != requested_sha.lower():
        raise InfrastructureE2EError("git_identity")
    status = runner.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        step="git_worktree",
    ).stdout
    clean = not bool(status.strip())
    if not clean and not allow_dirty:
        raise InfrastructureE2EError("git_worktree")
    return head, clean


def _validate_isolated_image_reference(reference: str, *, git_sha: str) -> None:
    normalized = reference.strip().lower()
    if (
        not normalized
        or git_sha.lower() not in normalized
        or normalized.endswith(":dev")
        or normalized.endswith(":latest")
    ):
        raise InfrastructureE2EError("image_reference")


def _free_ports(count: int) -> tuple[int, ...]:
    sockets: list[socket.socket] = []
    ports: list[int] = []
    try:
        for _ in range(count):
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.bind(("127.0.0.1", 0))
            sockets.append(listener)
            ports.append(int(listener.getsockname()[1]))
    finally:
        for listener in sockets:
            listener.close()
    if len(set(ports)) != count:
        raise InfrastructureE2EError("host_ports")
    return tuple(ports)


def _random_token(length: int = 32) -> str:
    return secrets.token_hex(length // 2)


def _ephemeral_environment(
    *,
    arguments: GateArguments,
    cert_dir: Path,
    backend_port: int,
    nginx_port: int,
    ragflow_port: int,
    smtp_state_port: int,
) -> tuple[dict[str, str], dict[str, str]]:
    postgres_password = _random_token(32)
    rabbitmq_password = _random_token(32)
    redis_password = _random_token(32)
    minio_secret = _random_token(32)
    ragflow_api_key = f"sk-{_random_token(32)}"
    dataset_id = str(uuid.uuid4())
    probe_token = _random_token(48)
    admin_password = f"E2E-aA1!{_random_token(24)}"
    employee_password = f"E2E-bB2!{_random_token(24)}"
    encryption_key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
    values = {
        "APP_ENV": arguments.environment,
        "APP_BASE_URL": f"http://127.0.0.1:{nginx_port}",
        "BACKEND_IMAGE": arguments.backend_image,
        "FRONTEND_IMAGE": arguments.frontend_image,
        "VCS_REF": arguments.git_sha,
        "BACKEND_BUILD_TARGET": "runtime",
        "BACKEND_API_HOST": "127.0.0.1",
        "BACKEND_API_PORT": str(backend_port),
        "NGINX_HTTP_PORT": str(nginx_port),
        "E2E_RAGFLOW_HOST_PORT": str(ragflow_port),
        "E2E_SMTP_STATE_HOST_PORT": str(smtp_state_port),
        "E2E_CERT_DIR": str(cert_dir),
        "POSTGRES_DB": "knowledge_uploader_e2e",
        "POSTGRES_USER": "knowledge_e2e",
        "POSTGRES_PASSWORD": postgres_password,
        "DATABASE_URL": (
            "postgresql+asyncpg://knowledge_e2e:"
            f"{postgres_password}@postgres:5432/knowledge_uploader_e2e"
        ),
        "ALEMBIC_DATABASE_URL": (
            "postgresql+psycopg://knowledge_e2e:"
            f"{postgres_password}@postgres:5432/knowledge_uploader_e2e"
        ),
        "RABBITMQ_USER": "knowledge_e2e",
        "RABBITMQ_PASSWORD": rabbitmq_password,
        "CELERY_BROKER_URL": f"amqp://knowledge_e2e:{rabbitmq_password}@rabbitmq:5672//",
        "REDIS_PASSWORD": redis_password,
        "CELERY_RESULT_BACKEND": f"redis://:{redis_password}@redis:6379/0",
        "CACHE_REDIS_URL": f"redis://:{redis_password}@redis:6379/1",
        "MINIO_ENDPOINT": "minio:9000",
        "MINIO_ACCESS_KEY": "knowledgee2e",
        "MINIO_SECRET_KEY": minio_secret,
        "MINIO_BUCKET": "knowledge-e2e",
        "MINIO_SECURE": "true",
        "JWT_SECRET": _random_token(64),
        "ENCRYPTION_KEY": encryption_key,
        "ALLOWED_EMAIL_DOMAINS": "e2e.invalid",
        "REQUIRE_EMAIL_VERIFICATION": "true",
        "SMTP_HOST": "mock-smtp",
        "SMTP_PORT": "1025",
        "SMTP_TLS": "false",
        "SMTP_FROM": "noreply@e2e.invalid",
        "AI_ANALYSIS_ENABLED": "false",
        "ALLOW_EXTERNAL_LLM": "false",
        "RAGFLOW_BASE_URL": "http://mock-ragflow:9380",
        "RAGFLOW_API_KEY": ragflow_api_key,
        "RAGFLOW_ALLOWED_DATASET_IDS": dataset_id,
        "RAGFLOW_REQUEST_TIMEOUT": "15",
        "RAGFLOW_MAX_RETRY_COUNT": "1",
        "RAGFLOW_PARSE_POLL_TIMEOUT_SECONDS": "120",
        "E2E_PROBE_TOKEN": probe_token,
        "SEED_ADMIN_PASSWORD": admin_password,
    }
    environment = dict(os.environ)
    environment.update(values)
    probe_values = {
        "api_base_url": f"http://127.0.0.1:{nginx_port}",
        "backend_ready_url": f"http://127.0.0.1:{backend_port}/api/system/ready",
        "mock_state_url": f"http://127.0.0.1:{ragflow_port}/__e2e/state",
        "mock_smtp_state_url": f"http://127.0.0.1:{smtp_state_port}/__e2e/state",
        "probe_token": probe_token,
        "admin_email": "admin@e2e.invalid",
        "admin_password": admin_password,
        "employee_password": employee_password,
        "ragflow_api_key": ragflow_api_key,
        "dataset_id": dataset_id,
    }
    return environment, probe_values


def _generate_certificates(
    runner: CommandRunner,
    *,
    backend_image: str,
    cert_parent: Path,
) -> dict[str, Any]:
    cert_parent.mkdir(parents=True, exist_ok=False)
    result = runner.run(
        [
            "docker",
            "run",
            "--rm",
            "--volume",
            f"{cert_parent}:/e2e-certs",
            backend_image,
            "python",
            "scripts/generate_e2e_certificates.py",
            "--output",
            "/e2e-certs/generated",
        ],
        step="tls_certificate_generation",
    )
    metadata = _last_json_object(result.stdout, step="tls_certificate_generation")
    if metadata.get("status") != "generated":
        raise InfrastructureE2EError("tls_certificate_generation")
    return metadata


def _validate_resolved_compose(
    resolved: dict[str, Any],
    *,
    backend_image: str,
    frontend_image: str,
) -> None:
    services = resolved.get("services")
    if not isinstance(services, dict):
        raise InfrastructureE2EError("resolved_compose_contract")
    for name in BACKEND_IMAGE_SERVICES:
        service = services.get(name)
        if not isinstance(service, dict) or service.get("image") != backend_image:
            raise InfrastructureE2EError("resolved_compose_contract")
    frontend = services.get("frontend")
    if not isinstance(frontend, dict) or frontend.get("image") != frontend_image:
        raise InfrastructureE2EError("resolved_compose_contract")
    backend = services.get("backend-api")
    backend_environment = backend.get("environment") if isinstance(backend, dict) else None
    if (
        not isinstance(backend_environment, dict)
        or str(backend_environment.get("MINIO_SECURE", "")).lower() != "true"
        or str(backend_environment.get("REQUIRE_EMAIL_VERIFICATION", "")).lower()
        != "true"
        or backend_environment.get("SMTP_HOST") != "mock-smtp"
        or str(backend_environment.get("SMTP_PORT")) != "1025"
        or not str(backend_environment.get("SMTP_FROM", "")).strip()
    ):
        raise InfrastructureE2EError("resolved_compose_contract")
    serialized_backend = json.dumps(backend, sort_keys=True)
    serialized_minio = json.dumps(services.get("minio"), sort_keys=True)
    if "/e2e-certs/ca.crt" not in serialized_backend:
        raise InfrastructureE2EError("resolved_compose_contract")
    if "public.crt" not in serialized_minio or "private.key" not in serialized_minio:
        raise InfrastructureE2EError("resolved_compose_contract")
    for service_name in ("nginx", "backend-api", "mock-ragflow", "mock-smtp"):
        service = services.get(service_name)
        ports = service.get("ports") if isinstance(service, dict) else None
        if not isinstance(ports, list) or not ports:
            raise InfrastructureE2EError("resolved_compose_contract")
        for port in ports:
            if not isinstance(port, dict) or port.get("host_ip") != "127.0.0.1":
                raise InfrastructureE2EError("resolved_compose_contract")


def _wait_ready(url: str, *, timeout_seconds: float = 180.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
                status = response.status
            if status == 200 and isinstance(payload, dict) and payload.get("status") == "ok":
                return
        except (HTTPError, URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError):
            pass
        time.sleep(1)
    raise InfrastructureE2EError("ready")


def _queue_snapshot(
    runner: CommandRunner,
    project: str,
) -> dict[str, tuple[int, int]]:
    result = _compose(
        runner,
        project,
        [
            "exec",
            "--no-TTY",
            "rabbitmq",
            "rabbitmqctl",
            "-q",
            "list_queues",
            "name",
            "consumers",
            "messages_ready",
        ],
        step="rabbitmq_queue_snapshot",
    )
    queues: dict[str, tuple[int, int]] = {}
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) != 3:
            continue
        try:
            consumers = int(fields[1])
            messages_ready = int(fields[2])
        except ValueError:
            continue
        queues[fields[0]] = (consumers, messages_ready)
    return queues


def _wait_for_queue(
    runner: CommandRunner,
    project: str,
    *,
    queue: str,
    consumers: int | None = None,
    messages_ready: int | None = None,
    timeout_seconds: float = 60.0,
) -> dict[str, tuple[int, int]]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        snapshot = _queue_snapshot(runner, project)
        current = snapshot.get(queue)
        if current is not None:
            consumer_match = consumers is None or current[0] == consumers
            message_match = messages_ready is None or current[1] == messages_ready
            if consumer_match and message_match:
                return snapshot
        time.sleep(0.5)
    raise InfrastructureE2EError("rabbitmq_queue_state")


def _validate_service_containers(
    runner: CommandRunner,
    project: str,
) -> dict[str, str]:
    container_ids: dict[str, str] = {}
    for service in REQUIRED_SERVICES:
        container_id = _compose(
            runner,
            project,
            ["ps", "--quiet", service],
            step="service_container_identity",
        ).stdout
        if re.fullmatch(r"[0-9a-f]{64}", container_id) is None:
            raise InfrastructureE2EError("service_container_identity")
        state = runner.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}",
                container_id,
            ],
            step="service_container_health",
        ).stdout.split()
        if not state or state[0] != "running" or (len(state) > 1 and state[1] != "healthy"):
            raise InfrastructureE2EError("service_container_health")
        container_ids[service] = container_id
    topology_id = _compose(
        runner,
        project,
        ["ps", "--all", "--quiet", "rabbitmq-topology"],
        step="rabbitmq_topology",
    ).stdout
    if re.fullmatch(r"[0-9a-f]{64}", topology_id) is None:
        raise InfrastructureE2EError("rabbitmq_topology")
    topology_state = runner.run(
        [
            "docker",
            "inspect",
            "--format",
            "{{.State.Status}} {{.State.ExitCode}}",
            topology_id,
        ],
        step="rabbitmq_topology",
    ).stdout
    if topology_state != "exited 0":
        raise InfrastructureE2EError("rabbitmq_topology")
    return container_ids


def _verify_alembic_head(runner: CommandRunner, project: str) -> None:
    heads = _compose(
        runner,
        project,
        ["exec", "--no-TTY", "backend-api", "alembic", "heads"],
        step="alembic_head",
    ).stdout
    current = _compose(
        runner,
        project,
        ["exec", "--no-TTY", "backend-api", "alembic", "current"],
        step="alembic_head",
    ).stdout
    head_ids = re.findall(r"^([0-9a-z]+)\s+\(head\)", heads, flags=re.MULTILINE)
    current_ids = re.findall(r"^([0-9a-z]+)\s+\(head\)", current, flags=re.MULTILINE)
    if len(head_ids) != 1 or current_ids != head_ids:
        raise InfrastructureE2EError("alembic_head")


def _verify_minio_tls(runner: CommandRunner, project: str) -> None:
    program = (
        "import ssl, urllib.request; "
        "from app.core.config import get_settings; "
        "assert get_settings().minio_secure is True; "
        "context=ssl.create_default_context(); "
        "response=urllib.request.urlopen("
        "'https://minio:9000/minio/health/live', context=context, timeout=5); "
        "assert response.status == 200"
    )
    _compose(
        runner,
        project,
        ["exec", "--no-TTY", "backend-api", "python", "-c", program],
        step="minio_tls",
    )


def _set_ragflow_sync_lock(
    runner: CommandRunner,
    project: str,
    *,
    file_id: uuid.UUID,
    hold: bool,
) -> None:
    operation = (
        "result = await client.set(key, 'e2e-dlq-lock', nx=True, ex=600)\n"
        "        assert result is True"
        if hold
        else "result = await client.delete(key)\n        assert int(result) == 1"
    )
    program = (
        "import asyncio\n"
        "import uuid\n"
        "from redis.asyncio import from_url\n"
        "from app.core.config import get_settings\n"
        "from app.modules.ragflow.sync_locks import sync_lock_key\n"
        "async def main():\n"
        "    client = from_url(get_settings().cache_redis_url, decode_responses=True)\n"
        "    try:\n"
        f"        key = sync_lock_key(uuid.UUID('{file_id}'))\n"
        f"        {operation}\n"
        "    finally:\n"
        "        await client.aclose()\n"
        "asyncio.run(main())"
    )
    _compose(
        runner,
        project,
        ["exec", "--no-TTY", "backend-api", "python", "-c", program],
        step="ragflow_sync_lock_hold" if hold else "ragflow_sync_lock_release",
    )


def _exercise_rabbitmq(
    runner: CommandRunner,
    project: str,
    arguments: list[str],
    *,
    step: str,
) -> dict[str, Any]:
    result = _compose(
        runner,
        project,
        [
            "run",
            "--rm",
            "--no-deps",
            "backend-api",
            "python",
            "scripts/exercise_rabbitmq_dlq.py",
            *arguments,
        ],
        step=step,
    )
    return _last_json_object(result.stdout, step=step)


def _release_status(
    *,
    source_clean: bool,
    host_architecture: str,
    docker_architecture: str,
) -> str:
    if (
        source_clean
        and _normalize_architecture(host_architecture) == "arm64"
        and _normalize_architecture(docker_architecture) == "arm64"
    ):
        return "passed"
    return "development_passed"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> bytes:
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_bytes(encoded)
    temporary.replace(path)
    return encoded


def run_gate(arguments: GateArguments) -> tuple[Path, Path, str]:
    base_runner = CommandRunner(environment=dict(os.environ))
    git_sha, source_clean = _source_identity(
        base_runner,
        requested_sha=arguments.git_sha,
        allow_dirty=arguments.allow_dirty_worktree,
    )
    _validate_isolated_image_reference(arguments.backend_image, git_sha=git_sha)
    _validate_isolated_image_reference(arguments.frontend_image, git_sha=git_sha)
    backend = _inspect_image(base_runner, arguments.backend_image, step="backend_image_identity")
    frontend = _inspect_image(
        base_runner,
        arguments.frontend_image,
        step="frontend_image_identity",
    )
    if backend.revision != git_sha or frontend.revision != git_sha:
        raise InfrastructureE2EError("image_revision")
    docker_architecture = base_runner.run(
        ["docker", "info", "--format", "{{.Architecture}}"],
        step="docker_architecture",
    ).stdout.lower()
    if _normalize_architecture(backend.architecture) != _normalize_architecture(
        docker_architecture
    ) or _normalize_architecture(frontend.architecture) != _normalize_architecture(
        docker_architecture
    ):
        raise InfrastructureE2EError("image_architecture")

    host_architecture = platform.machine().lower()
    run_id = uuid.uuid4()
    project = f"ku-e2e-{git_sha[:12]}-{run_id.hex[:12]}"
    backend_port, nginx_port, ragflow_port, smtp_state_port = _free_ports(4)
    compose_active = False
    cleanup_status = "not_started"
    failure: InfrastructureE2EError | None = None
    infrastructure_payload: dict[str, Any] | None = None
    rabbit_payload: dict[str, Any] | None = None

    with tempfile.TemporaryDirectory(prefix=f"knowledge-uploader-{run_id.hex[:8]}-") as temporary:
        work_dir = Path(temporary).resolve()
        cert_parent = work_dir / "certificates"
        cert_dir = cert_parent / "generated"
        environment, probe_values = _ephemeral_environment(
            arguments=arguments,
            cert_dir=cert_dir,
            backend_port=backend_port,
            nginx_port=nginx_port,
            ragflow_port=ragflow_port,
            smtp_state_port=smtp_state_port,
        )
        runner = CommandRunner(environment=environment)
        try:
            certificate_metadata = _generate_certificates(
                runner,
                backend_image=arguments.backend_image,
                cert_parent=cert_parent,
            )
            resolved_result = _compose(
                runner,
                project,
                ["config", "--format", "json"],
                step="resolved_compose_contract",
            )
            resolved_compose = _json_object(
                resolved_result.stdout,
                step="resolved_compose_contract",
            )
            _validate_resolved_compose(
                resolved_compose,
                backend_image=arguments.backend_image,
                frontend_image=arguments.frontend_image,
            )
            resolved_compose_sha256 = hashlib.sha256(
                resolved_result.stdout.encode("utf-8")
            ).hexdigest()

            compose_active = True
            _compose_up(
                runner,
                project,
                [
                    "postgres",
                    "rabbitmq",
                    "redis",
                    "minio",
                    "mock-ragflow",
                    "mock-smtp",
                ],
                step="compose_core_up",
                wait_timeout_seconds=180,
                command_timeout_seconds=240,
            )
            _compose(
                runner,
                project,
                [
                    "run",
                    "--rm",
                    "--no-deps",
                    "backend-api",
                    "alembic",
                    "upgrade",
                    "head",
                ],
                step="alembic_upgrade",
            )
            _compose(
                runner,
                project,
                [
                    "run",
                    "--rm",
                    "--no-deps",
                    "--env",
                    "SEED_ADMIN_PASSWORD",
                    "backend-api",
                    "python",
                    "scripts/seed_admin.py",
                    "--email",
                    probe_values["admin_email"],
                    "--name",
                    "E2E System Admin",
                ],
                step="seed_admin",
            )
            _compose_up(
                runner,
                project,
                [],
                step="compose_up",
                wait_timeout_seconds=240,
                command_timeout_seconds=300,
            )
            _wait_ready(probe_values["backend_ready_url"])
            _wait_ready(f"{probe_values['api_base_url']}/api/system/ready")
            _verify_alembic_head(runner, project)
            _verify_minio_tls(runner, project)
            service_container_ids = _validate_service_containers(runner, project)
            initial_queues = _queue_snapshot(runner, project)
            if any(queue not in initial_queues for queue in (*MAIN_QUEUES, *DLQ_NAMES)):
                raise InfrastructureE2EError("rabbitmq_topology")
            if any(initial_queues[queue][0] < 1 for queue in MAIN_QUEUES):
                raise InfrastructureE2EError("workers")

            probe = InfrastructureBusinessProbe(
                api_base_url=probe_values["api_base_url"],
                mock_ragflow_state_url=probe_values["mock_state_url"],
                mock_smtp_state_url=probe_values["mock_smtp_state_url"],
                probe_token=probe_values["probe_token"],
                run_id=run_id,
                admin_email=probe_values["admin_email"],
                admin_password=probe_values["admin_password"],
                employee_password=probe_values["employee_password"],
                ragflow_internal_base_url="http://mock-ragflow:9380",
                ragflow_api_key=probe_values["ragflow_api_key"],
                dataset_id=probe_values["dataset_id"],
            )
            business_state, business_summary = probe.run_primary_flow()

            _compose(
                runner,
                project,
                ["stop", "--timeout", "30", "worker-ragflow"],
                step="stop_ragflow_worker",
            )
            _wait_for_queue(
                runner,
                project,
                queue="ragflow_queue",
                consumers=0,
                messages_ready=0,
            )
            baseline = _exercise_rabbitmq(
                runner,
                project,
                [
                    "--mode",
                    "baseline",
                    "--probe-run-id",
                    str(run_id),
                    "--queue",
                    "ragflow_queue",
                    "--task",
                    RAGFLOW_TASK,
                ],
                step="rabbitmq_baseline",
            )
            replay_target = probe.create_replay_target(business_state)
            _wait_for_queue(
                runner,
                project,
                queue="ragflow_queue",
                consumers=0,
                messages_ready=1,
            )
            _set_ragflow_sync_lock(
                runner,
                project,
                file_id=replay_target.file_id,
                hold=True,
            )
            _compose_up(
                runner,
                project,
                ["worker-ragflow"],
                step="restart_ragflow_worker_for_exhaustion",
                wait_timeout_seconds=120,
                command_timeout_seconds=180,
            )
            _wait_for_queue(
                runner,
                project,
                queue="ragflow_queue.dlq",
                messages_ready=1,
                timeout_seconds=360,
            )
            exhausted_snapshot = _queue_snapshot(runner, project)
            if exhausted_snapshot.get("ragflow_queue", (-1, -1))[1] != 0:
                raise InfrastructureE2EError("rabbitmq_exhaustion")
            _compose(
                runner,
                project,
                ["stop", "--timeout", "30", "worker-ragflow"],
                step="stop_ragflow_worker_after_exhaustion",
            )
            _wait_for_queue(
                runner,
                project,
                queue="ragflow_queue",
                consumers=0,
                messages_ready=0,
            )
            exhausted_output = _exercise_rabbitmq(
                runner,
                project,
                [
                    "--mode",
                    "observe-exhaustion",
                    "--probe-run-id",
                    str(run_id),
                    "--expected-target-id",
                    str(replay_target.file_id),
                    "--queue",
                    "ragflow_queue",
                    "--task",
                    RAGFLOW_TASK,
                    "--expected-retries",
                    str(RAGFLOW_CREATION_MAX_RETRIES),
                ],
                step="rabbitmq_exhaustion",
            )
            exhausted = exhausted_output.get("exhausted")
            if not isinstance(exhausted, dict):
                raise InfrastructureE2EError("rabbitmq_exhaustion")
            _set_ragflow_sync_lock(
                runner,
                project,
                file_id=replay_target.file_id,
                hold=False,
            )
            original_task_id = uuid.UUID(str(exhausted.get("task_id")))
            original_correlation_id = str(exhausted.get("correlation_id"))
            replay = probe.replay_next_dead_letter(
                business_state,
                original_task_id=original_task_id,
                original_correlation_id=original_correlation_id,
            )
            replay_task_id = uuid.UUID(str(replay.get("replay_task_id")))
            verified_output = _exercise_rabbitmq(
                runner,
                project,
                [
                    "--mode",
                    "verify-replay",
                    "--probe-run-id",
                    str(run_id),
                    "--expected-target-id",
                    str(replay_target.file_id),
                    "--expected-task-id",
                    str(replay_task_id),
                    "--queue",
                    "ragflow_queue",
                    "--task",
                    RAGFLOW_TASK,
                ],
                step="rabbitmq_replay_persistence",
            )
            if not isinstance(verified_output.get("verified_replay"), dict):
                raise InfrastructureE2EError("rabbitmq_replay_persistence")
            _compose_up(
                runner,
                project,
                ["worker-ragflow"],
                step="restart_ragflow_worker",
                wait_timeout_seconds=120,
                command_timeout_seconds=180,
            )
            _wait_for_queue(
                runner,
                project,
                queue="ragflow_queue",
                consumers=1,
            )
            restored = probe.verify_replay_restored(business_state, replay_target)
            final_queues = _wait_for_queue(
                runner,
                project,
                queue="ragflow_queue",
                messages_ready=0,
            )
            if final_queues.get("ragflow_queue.dlq", (-1, -1))[1] != 0:
                raise InfrastructureE2EError("rabbitmq_replay_resolution")
            worker_queue_consumers = {queue: final_queues[queue][0] for queue in MAIN_QUEUES}
            if any(value < 1 for value in worker_queue_consumers.values()):
                raise InfrastructureE2EError("workers")
            service_container_ids = _validate_service_containers(runner, project)

            release_status = _release_status(
                source_clean=source_clean,
                host_architecture=host_architecture,
                docker_architecture=docker_architecture,
            )
            generated_at = datetime.now(UTC).isoformat()
            success = baseline.get("success")
            intermediate_retry = baseline.get("intermediate_retry")
            if not isinstance(success, dict) or not isinstance(intermediate_retry, dict):
                raise InfrastructureE2EError("rabbitmq_baseline")
            resolved = {
                "queue_name": "ragflow_queue",
                "task_name": RAGFLOW_TASK,
                "probe_run_id": str(run_id),
                "original_task_id": str(original_task_id),
                "replay_task_id": str(replay_task_id),
                "replay_correlation_id": str(replay_task_id),
                "audit_log_id": replay.get("audit_log_id"),
                "result": "passed",
                "dlq_count_after": 0,
                "domain_state": restored.get("domain_state"),
                "ragflow_terminal_state": restored.get("ragflow_terminal_state"),
            }
            rabbit_payload = {
                "status": release_status,
                "generated_at": generated_at,
                "git_sha": git_sha,
                "environment": arguments.environment,
                "probe_run_id": str(run_id),
                "success": success,
                "intermediate_retry": intermediate_retry,
                "exhausted": exhausted,
                "replay": replay,
                "resolved": resolved,
            }
            infrastructure_payload = {
                "status": release_status,
                "generated_at": generated_at,
                "git_sha": git_sha,
                "environment": arguments.environment,
                "run_id": str(run_id),
                "compose_project": project,
                "source_worktree_clean": source_clean,
                "architecture": host_architecture,
                "docker_architecture": docker_architecture,
                "full_compose_e2e": release_status,
                "resolved_compose_sha256": resolved_compose_sha256,
                "backend_image": backend.reference,
                "backend_image_id": backend.content_id,
                "backend_image_revision": backend.revision,
                "frontend_image": frontend.reference,
                "frontend_image_id": frontend.content_id,
                "frontend_image_revision": frontend.revision,
                "service_container_ids": service_container_ids,
                "worker_queue_consumers": worker_queue_consumers,
                "business_probe": business_summary,
                "rabbitmq_probe_run_id": str(run_id),
                "tls_certificate_sha256": certificate_metadata.get("certificate_sha256"),
                "cleanup_status": "pending",
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
                    "cleanup": "pending",
                },
            }
        except InfrastructureE2EError as error:
            failure = error
        except (ValueError, KeyError, TypeError, OSError, RuntimeError, AssertionError) as error:
            failure = InfrastructureE2EError("unexpected_gate_state")
            failure.__cause__ = error
        finally:
            if compose_active:
                try:
                    cleanup = _compose(
                        runner,
                        project,
                        ["down", "--volumes", "--remove-orphans", "--timeout", "30"],
                        step="cleanup",
                        timeout_seconds=180,
                        check=False,
                    )
                except InfrastructureE2EError:
                    cleanup_status = "failed"
                else:
                    cleanup_status = "passed" if cleanup.returncode == 0 else "failed"
            else:
                cleanup_status = "not_required"

    if failure is not None:
        raise InfrastructureE2EError(
            failure.step,
            cleanup_status=cleanup_status,
        ) from failure
    if cleanup_status != "passed":
        raise InfrastructureE2EError("cleanup", cleanup_status=cleanup_status)
    if infrastructure_payload is None or rabbit_payload is None:
        raise InfrastructureE2EError("evidence_assembly", cleanup_status=cleanup_status)

    infrastructure_payload["cleanup_status"] = "passed"
    results = infrastructure_payload.get("results")
    if not isinstance(results, dict):
        raise InfrastructureE2EError("evidence_assembly", cleanup_status=cleanup_status)
    results["cleanup"] = "passed"
    generated_at = datetime.now(UTC).isoformat()
    infrastructure_payload["generated_at"] = generated_at
    rabbit_payload["generated_at"] = generated_at
    rabbit_path = arguments.evidence_dir / "rabbitmq-dlq-replay.json"
    rabbit_bytes = _atomic_write_json(rabbit_path, rabbit_payload)
    infrastructure_payload["rabbitmq_evidence_sha256"] = hashlib.sha256(rabbit_bytes).hexdigest()
    infrastructure_path = arguments.evidence_dir / "infrastructure-e2e.json"
    _atomic_write_json(infrastructure_path, infrastructure_payload)
    return infrastructure_path, rabbit_path, str(infrastructure_payload["status"])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-image", required=True)
    parser.add_argument("--frontend-image", required=True)
    parser.add_argument("--git-sha", default=os.environ.get("GITHUB_SHA", "unknown"))
    parser.add_argument("--environment", choices=("staging", "production"), default="staging")
    parser.add_argument("--evidence-dir", type=Path, default=ROOT / "artifacts")
    parser.add_argument(
        "--allow-dirty-worktree",
        action="store_true",
        help="Run a development-only drill; evidence cannot pass release verification.",
    )
    return parser


def main() -> int:
    parsed = build_parser().parse_args()
    evidence_dir = parsed.evidence_dir.resolve()
    infrastructure_path = evidence_dir / "infrastructure-e2e.json"
    rabbit_path = evidence_dir / "rabbitmq-dlq-replay.json"
    for stale_path in (infrastructure_path, rabbit_path):
        stale_path.unlink(missing_ok=True)
    arguments = GateArguments(
        backend_image=parsed.backend_image,
        frontend_image=parsed.frontend_image,
        git_sha=parsed.git_sha,
        environment=parsed.environment,
        evidence_dir=evidence_dir,
        allow_dirty_worktree=parsed.allow_dirty_worktree,
    )
    try:
        infrastructure_path, rabbit_path, status = run_gate(arguments)
    except InfrastructureE2EError as error:
        failed = {
            "status": "failed",
            "generated_at": datetime.now(UTC).isoformat(),
            "git_sha": parsed.git_sha,
            "environment": parsed.environment,
            "failed_step": error.step,
            "cleanup_status": error.cleanup_status,
        }
        _atomic_write_json(infrastructure_path, failed)
        sys.stderr.write(f"{error}\n")
        return 1
    sys.stdout.write(f"infrastructure E2E {status}: {infrastructure_path} {rabbit_path}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
