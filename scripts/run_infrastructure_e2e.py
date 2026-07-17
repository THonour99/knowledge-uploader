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
import ssl
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

from infrastructure_e2e_probe import (
    BusinessProbeState,
    InfrastructureBusinessProbe,
    InfrastructureProbeError,
    ReplayTarget,
)

ROOT = Path(__file__).resolve().parents[1]
BASE_COMPOSE = ROOT / "docker-compose.yml"
E2E_COMPOSE = ROOT / "docker-compose.e2e.yml"
PROTECTED_PROMETHEUS_CONFIG = ROOT / "ops" / "observability" / "prometheus.protected.yml"
PROMETHEUS_IMAGE = (
    "prom/prometheus:v3.12.0"
    "@sha256:69f5241418838263316593f7274a304b095c40bcf22e57272865da91bd60a8ac"
)
GIT_SHA_PATTERN = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})")
SHA256_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
HEX_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
UUID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)
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
BACKEND_TLS_CLIENT_SERVICES = tuple(
    service for service in BACKEND_IMAGE_SERVICES if service not in {"mock-ragflow", "mock-smtp"}
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
EVIDENCE_CONTRACT_VERSION = 3
TLS_CERTIFICATE_NAMES = frozenset({"minio", "ragflow", "smtp", "gateway"})
TLS_VERIFIED_CHANNELS = (
    "gateway_https",
    "minio_https",
    "ragflow_https",
    "smtp_starttls",
)
FAULT_DEPENDENCIES = ("rabbitmq", "redis", "minio", "ragflow")
E2E_EMAIL_DOMAIN = "e2e.example.com"


class InfrastructureE2EError(RuntimeError):
    """A bounded gate failure that never includes command output or credentials."""

    def __init__(self, step: str, *, cleanup_status: str = "not_started") -> None:
        super().__init__(f"infrastructure E2E failed at step: {step}")
        self.step = step
        self.cleanup_status = cleanup_status


def _announce_step(step: str) -> None:
    sys.stderr.write(f"[infrastructure-e2e] step={step}\n")
    sys.stderr.flush()


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
        _announce_step(step)
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


def _compose_up_no_deps(
    runner: CommandRunner,
    project: str,
    service: str,
    *,
    step: str,
) -> CommandResult:
    return _compose(
        runner,
        project,
        [
            "up",
            "--detach",
            "--no-build",
            "--no-deps",
            service,
        ],
        step=step,
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
    if normalized in {"arm64", "aarch64"}:
        return "arm64"
    if normalized in {"amd64", "x86_64", "x64"}:
        return "amd64"
    return normalized


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
    prometheus_port: int,
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
        "APP_BASE_URL": f"https://127.0.0.1:{nginx_port}",
        "BACKEND_IMAGE": arguments.backend_image,
        "FRONTEND_IMAGE": arguments.frontend_image,
        "VCS_REF": arguments.git_sha,
        "BACKEND_BUILD_TARGET": "runtime",
        "BACKEND_API_HOST": "127.0.0.1",
        "BACKEND_API_PORT": str(backend_port),
        "NGINX_HTTPS_PORT": str(nginx_port),
        "E2E_RAGFLOW_HOST_PORT": str(ragflow_port),
        "E2E_SMTP_STATE_HOST_PORT": str(smtp_state_port),
        "E2E_PROMETHEUS_HOST_PORT": str(prometheus_port),
        "E2E_CERT_DIR": str(cert_dir),
        "PROMETHEUS_IMAGE": PROMETHEUS_IMAGE,
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
        "CACHE_REDIS_URL": f"redis://:{redis_password}@redis:6379/1",
        "MINIO_ENDPOINT": "minio:9000",
        "MINIO_ACCESS_KEY": "knowledgee2e",
        "MINIO_SECRET_KEY": minio_secret,
        "MINIO_BUCKET": "knowledge-e2e",
        "MINIO_SECURE": "true",
        "MINIO_CA_CERT_FILE": "/e2e-certs/ca.crt",
        "JWT_SECRET": _random_token(64),
        "ENCRYPTION_KEY": encryption_key,
        "ALLOWED_EMAIL_DOMAINS": E2E_EMAIL_DOMAIN,
        "REQUIRE_EMAIL_VERIFICATION": "true",
        "SMTP_HOST": "mock-smtp",
        "SMTP_PORT": "1025",
        "SMTP_TLS": "true",
        "SMTP_CA_CERT_FILE": "/e2e-certs/ca.crt",
        "SMTP_TIMEOUT_SECONDS": "10",
        "SMTP_FROM": f"noreply@{E2E_EMAIL_DOMAIN}",
        "AI_ANALYSIS_ENABLED": "false",
        "ALLOW_EXTERNAL_LLM": "false",
        "RAGFLOW_BASE_URL": "https://mock-ragflow:9380",
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
        "api_base_url": f"https://127.0.0.1:{nginx_port}",
        "backend_ready_url": f"http://127.0.0.1:{backend_port}/api/system/ready",
        "mock_state_url": f"https://127.0.0.1:{ragflow_port}/__e2e/state",
        "mock_smtp_state_url": f"https://127.0.0.1:{smtp_state_port}/__e2e/state",
        "prometheus_targets_url": f"http://127.0.0.1:{prometheus_port}/api/v1/targets",
        "ca_cert_file": str(cert_dir / "ca.crt"),
        "probe_token": probe_token,
        "admin_email": f"admin@{E2E_EMAIL_DOMAIN}",
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


def _tls_evidence(metadata: dict[str, Any]) -> dict[str, object]:
    certificates = metadata.get("certificates")
    ca_sha256 = metadata.get("ca_sha256")
    bundle_sha256 = metadata.get("certificate_bundle_sha256")
    if (
        not isinstance(certificates, dict)
        or set(certificates) != TLS_CERTIFICATE_NAMES
        or not all(
            isinstance(value, str) and HEX_SHA256_PATTERN.fullmatch(value) is not None
            for value in certificates.values()
        )
        or not isinstance(ca_sha256, str)
        or HEX_SHA256_PATTERN.fullmatch(ca_sha256) is None
        or not isinstance(bundle_sha256, str)
        or HEX_SHA256_PATTERN.fullmatch(bundle_sha256) is None
        or metadata.get("certificate_sha256") != certificates.get("minio")
    ):
        raise InfrastructureE2EError("tls_certificate_generation")
    return {
        "status": "passed",
        "ca_sha256": ca_sha256,
        "certificate_bundle_sha256": bundle_sha256,
        "certificates": dict(sorted(certificates.items())),
        "verified_channels": list(TLS_VERIFIED_CHANNELS),
    }


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
        if name in BACKEND_TLS_CLIENT_SERVICES:
            service_environment = service.get("environment")
            serialized_service = json.dumps(service, sort_keys=True)
            if (
                not isinstance(service_environment, dict)
                or service_environment.get("SSL_CERT_FILE") != "/e2e-certs/ca.crt"
                or service_environment.get("MINIO_CA_CERT_FILE") != "/e2e-certs/ca.crt"
                or "/e2e-certs/ca.crt" not in serialized_service
            ):
                raise InfrastructureE2EError("resolved_compose_contract")
    frontend = services.get("frontend")
    if not isinstance(frontend, dict) or frontend.get("image") != frontend_image:
        raise InfrastructureE2EError("resolved_compose_contract")
    backend = services.get("backend-api")
    backend_environment = backend.get("environment") if isinstance(backend, dict) else None
    if (
        not isinstance(backend_environment, dict)
        or str(backend_environment.get("MINIO_SECURE", "")).lower() != "true"
        or str(backend_environment.get("REQUIRE_EMAIL_VERIFICATION", "")).lower() != "true"
        or backend_environment.get("SMTP_HOST") != "mock-smtp"
        or str(backend_environment.get("SMTP_PORT")) != "1025"
        or str(backend_environment.get("SMTP_TLS", "")).lower() != "true"
        or backend_environment.get("SMTP_CA_CERT_FILE") != "/e2e-certs/ca.crt"
        or str(backend_environment.get("SMTP_TIMEOUT_SECONDS")) != "10"
        or backend_environment.get("RAGFLOW_BASE_URL") != "https://mock-ragflow:9380"
        or not str(backend_environment.get("SMTP_FROM", "")).strip()
    ):
        raise InfrastructureE2EError("resolved_compose_contract")
    serialized_backend = json.dumps(backend, sort_keys=True)
    serialized_minio = json.dumps(services.get("minio"), sort_keys=True)
    prometheus = services.get("prometheus")
    serialized_prometheus = json.dumps(prometheus, sort_keys=True)
    if "/e2e-certs/ca.crt" not in serialized_backend:
        raise InfrastructureE2EError("resolved_compose_contract")
    if (
        "public.crt" not in serialized_minio
        or "private.key" not in serialized_minio
        or "/root/.minio/certs/CAs/e2e-ca.crt" not in serialized_minio
        or "--cacert" not in serialized_minio
    ):
        raise InfrastructureE2EError("resolved_compose_contract")
    minio = services.get("minio")
    minio_environment = minio.get("environment") if isinstance(minio, dict) else None
    if (
        not isinstance(prometheus, dict)
        or prometheus.get("image") != PROMETHEUS_IMAGE
        or "/etc/prometheus/prometheus.yml" not in serialized_prometheus
        or "prometheus.protected.yml" not in serialized_prometheus
        or "/etc/prometheus/tls/ca.crt" not in serialized_prometheus
        or not isinstance(minio_environment, dict)
        or minio_environment.get("MINIO_PROMETHEUS_AUTH_TYPE") != "public"
    ):
        raise InfrastructureE2EError("resolved_compose_contract")
    for service_name in (
        "nginx",
        "backend-api",
        "mock-ragflow",
        "mock-smtp",
        "prometheus",
    ):
        service = services.get(service_name)
        ports = service.get("ports") if isinstance(service, dict) else None
        if not isinstance(ports, list) or not ports:
            raise InfrastructureE2EError("resolved_compose_contract")
        for port in ports:
            if (
                not isinstance(port, dict)
                or port.get("host_ip") != "127.0.0.1"
                or (service_name == "nginx" and port.get("target") not in {443, "443"})
                or (service_name == "prometheus" and port.get("target") not in {9090, "9090"})
            ):
                raise InfrastructureE2EError("resolved_compose_contract")
    nginx = services.get("nginx")
    mock_ragflow = services.get("mock-ragflow")
    mock_smtp = services.get("mock-smtp")
    serialized_nginx = json.dumps(nginx, sort_keys=True)
    serialized_ragflow = json.dumps(mock_ragflow, sort_keys=True)
    serialized_smtp = json.dumps(mock_smtp, sort_keys=True)
    if (
        "gateway.crt" not in serialized_nginx
        or "gateway.key" not in serialized_nginx
        or "nginx-tls.conf" not in serialized_nginx
        or "ragflow.crt" not in serialized_ragflow
        or "ragflow.key" not in serialized_ragflow
        or "https://127.0.0.1:9380/health" not in serialized_ragflow
        or "smtp.crt" not in serialized_smtp
        or "smtp.key" not in serialized_smtp
        or "https://127.0.0.1:8080/health" not in serialized_smtp
    ):
        raise InfrastructureE2EError("resolved_compose_contract")
    serialized_resolved = json.dumps(resolved, sort_keys=True).lower()
    forbidden_tls_bypasses = (
        "--insecure",
        "--no-check-certificate",
        "cert_none",
        '"verify": false',
    )
    if any(value in serialized_resolved for value in forbidden_tls_bypasses):
        raise InfrastructureE2EError("resolved_compose_contract")


def _verified_ssl_context(ca_cert_file: Path | None) -> ssl.SSLContext | None:
    if ca_cert_file is None:
        return None
    return ssl.create_default_context(cafile=str(ca_cert_file))


def _wait_ready(
    url: str,
    *,
    ca_cert_file: Path | None = None,
    timeout_seconds: float = 180.0,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    context = _verified_ssl_context(ca_cert_file)
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=5, context=context) as response:
                payload = json.loads(response.read().decode("utf-8"))
                status = response.status
            if status == 200 and isinstance(payload, dict) and payload.get("status") == "ok":
                return
        except (HTTPError, URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError):
            pass
        time.sleep(1)
    raise InfrastructureE2EError("ready")


def _expect_ready_503(url: str, *, ca_cert_file: Path) -> None:
    try:
        urlopen(
            url,
            timeout=10,
            context=_verified_ssl_context(ca_cert_file),
        ).close()
    except HTTPError as error:
        if error.code == 503:
            return
    except (URLError, TimeoutError):
        pass
    raise InfrastructureE2EError("dependency_outage_probe")


def _expect_tls_endpoint_unavailable(url: str, *, ca_cert_file: Path) -> None:
    try:
        urlopen(
            url,
            timeout=5,
            context=_verified_ssl_context(ca_cert_file),
        ).close()
    except (HTTPError, URLError, TimeoutError, OSError):
        return
    raise InfrastructureE2EError("dependency_outage_probe")


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


def _queue_delivery_snapshot(
    runner: CommandRunner,
    project: str,
) -> dict[str, tuple[int, int, int]]:
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
            "messages_unacknowledged",
        ],
        step="rabbitmq_queue_delivery_snapshot",
    )
    queues: dict[str, tuple[int, int, int]] = {}
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) != 4:
            continue
        try:
            consumers, messages_ready, messages_unacknowledged = map(int, fields[1:])
        except ValueError:
            continue
        queues[fields[0]] = (consumers, messages_ready, messages_unacknowledged)
    return queues


def _redis_rabbitmq_diagnostics(
    runner: CommandRunner,
    project: str,
) -> dict[str, object]:
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
            "messages_unacknowledged",
            "messages",
        ],
        step="fault_redis_diagnostic_rabbitmq",
        check=False,
    )
    if result.returncode != 0:
        return {"status": "command_failed"}
    queues: dict[str, dict[str, int]] = {}
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) != 5 or fields[0] not in {"ragflow_queue", "ragflow_queue.dlq"}:
            continue
        try:
            consumers, ready, unacknowledged, messages = map(int, fields[1:])
        except ValueError:
            return {"status": "invalid_output"}
        queues[fields[0]] = {
            "consumers": consumers,
            "messages_ready": ready,
            "messages_unacknowledged": unacknowledged,
            "messages_total": messages,
        }
    if set(queues) != {"ragflow_queue", "ragflow_queue.dlq"}:
        return {"status": "queue_missing"}
    return {"status": "observed", "queues": queues}


def _redis_retry_message_diagnostics(
    runner: CommandRunner,
    project: str,
    *,
    target_file_id: uuid.UUID,
) -> dict[str, object]:
    target = str(target_file_id)
    program = (
        "import json\n"
        "import sys\n"
        "import uuid\n"
        "sys.path.insert(0, 'scripts')\n"
        "from kombu import Connection\n"
        "from app.core.config import get_settings\n"
        "from exercise_rabbitmq_dlq import (\n"
        "    _get_message,\n"
        "    _queue_counts,\n"
        "    _validated_identity,\n"
        "    _validated_retry_identity,\n"
        ")\n"
        f"target = uuid.UUID('{target}')\n"
        "RETRY_ERROR_CODES = {\n"
        "    'RabbitMQ retry task identity is invalid': 'task_identity',\n"
        "    'RabbitMQ retry task id is invalid': 'task_id',\n"
        "    'RabbitMQ retry content type is invalid': 'content_type',\n"
        "    'RabbitMQ retry content encoding is invalid': 'content_encoding',\n"
        "    'RabbitMQ retry body is invalid': 'body_type_or_encoding',\n"
        "    'RabbitMQ retry body exceeds the observation limit': 'body_size',\n"
        "    'RabbitMQ retry JSON body is invalid': 'json_body',\n"
        "    'RabbitMQ retry JSON node limit exceeded': 'json_nodes',\n"
        "    'RabbitMQ retry JSON depth limit exceeded': 'json_depth',\n"
        "    'RabbitMQ retry JSON string limit exceeded': 'json_string',\n"
        "    'RabbitMQ retry JSON container limit exceeded': 'json_container',\n"
        "    'RabbitMQ retry JSON contains an unsupported value': 'json_value',\n"
        "    'RabbitMQ retry payload shape is invalid': 'payload_shape',\n"
        "    'RabbitMQ retry target is invalid': 'target',\n"
        "    'RabbitMQ retry keyword arguments are invalid': 'kwargs',\n"
        "    'RabbitMQ retry embedded options are invalid': 'embedded_options',\n"
        "    'RabbitMQ retry target identity changed': 'target_mismatch',\n"
        "    'RabbitMQ retry correlation id does not match task id': 'correlation',\n"
        "    'RabbitMQ retry task is not persistent': 'persistence',\n"
        "}\n"
        "def death_summary(value):\n"
        "    if not isinstance(value, list):\n"
        "        return []\n"
        "    output = []\n"
        "    for item in value[:4]:\n"
        "        if not isinstance(item, dict):\n"
        "            continue\n"
        "        queue = item.get('queue')\n"
        "        reason = item.get('reason')\n"
        "        count = item.get('count')\n"
        "        output.append({\n"
        "            'queue': queue.decode(errors='replace')[:80] if isinstance(queue, bytes) "
        "else str(queue)[:80],\n"
        "            'reason': reason.decode(errors='replace')[:40] "
        "if isinstance(reason, bytes) else str(reason)[:40],\n"
        "            'count': count if isinstance(count, int) and not isinstance(count, bool) "
        "else None,\n"
        "        })\n"
        "    return output\n"
        "def inspect(connection, observed_queue):\n"
        "    counts = _queue_counts(connection, observed_queue)\n"
        "    summary = {'messages': counts.messages, 'consumers': counts.consumers}\n"
        "    if counts.messages < 1:\n"
        "        return summary\n"
        "    message = _get_message(connection, observed_queue)\n"
        "    try:\n"
        "        headers = message.headers if isinstance(message.headers, dict) else {}\n"
        "        retries = headers.get('retries')\n"
        "        body_is_bytes = isinstance(message.body, (bytes, bytearray, memoryview))\n"
        "        body_is_text = isinstance(message.body, str)\n"
        "        try:\n"
        "            body_size = (\n"
        "                len(bytes(message.body)) if body_is_bytes\n"
        "                else len(message.body.encode('utf-8', errors='strict'))\n"
        "                if body_is_text else None\n"
        "            )\n"
        "        except UnicodeEncodeError:\n"
        "            body_size = None\n"
        "        summary.update({\n"
        "            'retry_count': retries if isinstance(retries, int) "
        "and not isinstance(retries, bool) else None,\n"
        "            'persistent_message': message.properties.get('delivery_mode') in {2, '2'},\n"
        "            'content_type_json': message.content_type == 'application/json',\n"
        "            'content_encoding_utf8': isinstance(message.content_encoding, str) "
        "and message.content_encoding.strip().lower().replace('_', '-') == 'utf-8',\n"
        "            'body_is_bytes': body_is_bytes,\n"
        "            'body_is_text': body_is_text,\n"
        "            'body_size': body_size if isinstance(body_size, int) and body_size <= 16384 "
        "else None,\n"
        "            'x_death': death_summary(headers.get('x-death')),\n"
        "        })\n"
        "        if (body_is_bytes or body_is_text) and isinstance(body_size, int) "
        "and body_size <= 16384:\n"
        "            try:\n"
        "                text_body = (bytes(message.body).decode('utf-8', errors='strict') "
        "if body_is_bytes else message.body)\n"
        "                payload = json.loads(text_body)\n"
        "            except (UnicodeDecodeError, json.JSONDecodeError, ValueError):\n"
        "                summary['json_envelope_valid'] = False\n"
        "            else:\n"
        "                summary['json_envelope_valid'] = True\n"
        "                summary['payload_shape_valid'] = "
        "isinstance(payload, list) and len(payload) == 3\n"
        "                if summary['payload_shape_valid']:\n"
        "                    args, kwargs, embedded = payload\n"
        "                    summary.update({\n"
        "                        'args_shape_valid': isinstance(args, list) and len(args) == 1,\n"
        "                        'kwargs_empty': isinstance(kwargs, dict) and not kwargs,\n"
        "                        'embedded_is_dict': isinstance(embedded, dict),\n"
        "                    })\n"
        "                    if isinstance(args, list) and len(args) == 1:\n"
        "                        try:\n"
        "                            decoded_target = uuid.UUID(args[0]) "
        "if isinstance(args[0], str) else None\n"
        "                        except ValueError:\n"
        "                            decoded_target = None\n"
        "                        summary['decoded_target_matches'] = decoded_target == target\n"
        "                    if isinstance(embedded, dict):\n"
        "                        allowed = {'callbacks', 'errbacks', 'chain', 'chord'}\n"
        "                        summary['embedded_keys_allowed'] = not (set(embedded) - allowed)\n"
        "                        summary['embedded_unknown_key_count'] = len(\n"
        "                            set(embedded) - allowed\n"
        "                        )\n"
        "                        summary['embedded_non_null_allowed_keys'] = [\n"
        "                            key\n"
        "                            for key in sorted(allowed)\n"
        "                            if embedded.get(key) is not None\n"
        "                        ]\n"
        "        if observed_queue == 'ragflow_queue':\n"
        "            try:\n"
        "                retry_id, retry_target, retry_correlation = "
        "_validated_retry_identity(\n"
        "                    message,\n"
        "                    queue_name='ragflow_queue',\n"
        "                    task_name='ragflow.create_upload_task',\n"
        "                    expected_target_id=target,\n"
        "                )\n"
        "            except Exception as error:\n"
        "                summary.update({\n"
        "                    'retry_identity_valid': False,\n"
        "                    'retry_identity_error_code': "
        "RETRY_ERROR_CODES.get(str(error), 'unknown'),\n"
        "                })\n"
        "            else:\n"
        "                summary.update({\n"
        "                    'retry_identity_valid': True,\n"
        "                    'retry_task_id': str(retry_id),\n"
        "                    'retry_target_matches': retry_target == target,\n"
        "                    'retry_correlation_matches': retry_correlation == str(retry_id),\n"
        "                })\n"
        "        try:\n"
        "            task_id, message_target, correlation_id = _validated_identity(\n"
        "                message,\n"
        "                queue_name='ragflow_queue',\n"
        "                task_name='ragflow.create_upload_task',\n"
        "                probe_run_id=uuid.UUID(int=0),\n"
        "                expected_target_id=None,\n"
        "                require_probe_header=False,\n"
        "            )\n"
        "        except Exception as error:\n"
        "            summary.update({'identity_valid': False, "
        "'identity_error_type': type(error).__name__})\n"
        "        else:\n"
        "            summary.update({\n"
        "                'identity_valid': True,\n"
        "                'task_id': str(task_id),\n"
        "                'correlation_id_matches': correlation_id == str(task_id),\n"
        "                'target_id': str(message_target),\n"
        "                'target_matches': message_target == target,\n"
        "            })\n"
        "    finally:\n"
        "        message.reject(requeue=True)\n"
        "    return summary\n"
        "with Connection(get_settings().celery_broker_url, connect_timeout=5) as connection:\n"
        "    payload = {\n"
        "        'main': inspect(connection, 'ragflow_queue'),\n"
        "        'dlq': inspect(connection, 'ragflow_queue.dlq'),\n"
        "    }\n"
        "print(json.dumps(payload, sort_keys=True))"
    )
    result = _compose(
        runner,
        project,
        ["exec", "--no-TTY", "backend-api", "python", "-c", program],
        step="fault_redis_diagnostic_retry_messages",
        timeout_seconds=30,
        check=False,
    )
    if result.returncode != 0:
        return {"status": "command_failed"}
    try:
        payload = _last_json_object(
            result.stdout,
            step="fault_redis_diagnostic_retry_messages",
        )
    except InfrastructureE2EError:
        return {"status": "invalid_output"}
    return {"status": "observed", **payload}


def _redis_celery_diagnostics(
    runner: CommandRunner,
    project: str,
    *,
    target_file_id: uuid.UUID,
) -> dict[str, object]:
    target = str(target_file_id)
    program = (
        "import json\n"
        "from app.workers.celery_app import celery_app\n"
        f"target = '{target}'\n"
        "def summarize(response):\n"
        "    reply_count = len(response) if isinstance(response, dict) else 0\n"
        "    ragflow_reply = False\n"
        "    task_count = 0\n"
        "    name_matches = 0\n"
        "    target_matches = 0\n"
        "    for worker, tasks in response.items() if isinstance(response, dict) else []:\n"
        "        ragflow_reply = ragflow_reply or str(worker).startswith('worker-ragflow@')\n"
        "        for item in tasks if isinstance(tasks, list) else []:\n"
        "            request = item.get('request', item) if isinstance(item, dict) else None\n"
        "            if not isinstance(request, dict):\n"
        "                continue\n"
        "            task_count += 1\n"
        "            name = request.get('name') or request.get('type')\n"
        "            if name == 'ragflow.create_upload_task':\n"
        "                name_matches += 1\n"
        "                target_matches += int(target in str(request.get('args')))\n"
        "    return {'reply_count': reply_count, 'ragflow_worker_reply': ragflow_reply, "
        "'task_count': task_count, 'name_matches': name_matches, "
        "'target_matches': target_matches}\n"
        "inspector = celery_app.control.inspect(timeout=3)\n"
        "registered_task = celery_app.tasks.get('ragflow.create_upload_task')\n"
        "payload = {\n"
        "    'configuration': {\n"
        "        'global_task_ignore_result': bool(celery_app.conf.task_ignore_result),\n"
        "        'task_ignore_result': (\n"
        "            bool(registered_task.ignore_result) if registered_task else None\n"
        "        ),\n"
        "        'result_backend_configured': bool(celery_app.conf.result_backend),\n"
        "    },\n"
        "    'active': summarize(inspector.active() or {}),\n"
        "    'scheduled': summarize(inspector.scheduled() or {}),\n"
        "    'reserved': summarize(inspector.reserved() or {}),\n"
        "}\n"
        "print(json.dumps(payload, sort_keys=True))"
    )
    result = _compose(
        runner,
        project,
        ["exec", "--no-TTY", "backend-api", "python", "-c", program],
        step="fault_redis_diagnostic_celery",
        timeout_seconds=30,
        check=False,
    )
    if result.returncode != 0:
        return {"status": "command_failed"}
    try:
        payload = _last_json_object(result.stdout, step="fault_redis_diagnostic_celery")
    except InfrastructureE2EError:
        return {"status": "invalid_output"}
    return {"status": "observed", **payload}


def _classify_redis_worker_logs(raw_logs: str) -> dict[str, object]:
    lines = raw_logs.lower().splitlines()
    task_lines = [line for line in lines if "ragflow.create_upload_task" in line]
    return {
        "task_received": any("received" in line for line in task_lines),
        "task_retry_logged": any("retry" in line for line in task_lines),
        "task_failure_logged": any("failed" in line for line in task_lines),
        "redis_connection_error": any(
            marker in raw_logs.lower()
            for marker in (
                "redis.exceptions.connectionerror",
                "error 111 connecting",
                "connection refused",
            )
        ),
        "redis_timeout_error": "redis.exceptions.timeouterror" in raw_logs.lower(),
        "result_backend_error": any(
            marker in raw_logs.lower()
            for marker in (
                "backendstoreerror",
                "on_task_retry",
                "mark_as_retry",
            )
        ),
        "task_rejected": any("reject" in line for line in task_lines),
        "worker_lost": "workerlosterror" in raw_logs.lower(),
        "warm_shutdown": "warm shutdown" in raw_logs.lower(),
    }


def _redis_worker_diagnostics(
    runner: CommandRunner,
    project: str,
) -> dict[str, object]:
    logs = _compose(
        runner,
        project,
        ["logs", "--no-color", "--tail", "300", "worker-ragflow"],
        step="fault_redis_diagnostic_worker_logs",
        check=False,
    )
    log_summary = (
        _classify_redis_worker_logs(f"{logs.stdout}\n{logs.stderr}")
        if logs.returncode == 0
        else {"log_status": "command_failed"}
    )
    process_program = (
        "import json\n"
        "from pathlib import Path\n"
        "states = {}\n"
        "count = 0\n"
        "for entry in Path('/proc').iterdir():\n"
        "    if not entry.name.isdigit():\n"
        "        continue\n"
        "    try:\n"
        "        command = (entry / 'cmdline').read_bytes().replace(b'\\x00', b' ')\n"
        "        status = (entry / 'status').read_text(encoding='utf-8')\n"
        "    except (FileNotFoundError, PermissionError, ProcessLookupError):\n"
        "        continue\n"
        "    if b'celery' not in command or b'worker' not in command:\n"
        "        continue\n"
        "    state_lines = [line for line in status.splitlines() if line.startswith('State:')]\n"
        "    state_line = state_lines[0] if state_lines else ''\n"
        "    state = state_line.split()[1][:1] if len(state_line.split()) > 1 else '?'\n"
        "    states[state] = states.get(state, 0) + 1\n"
        "    count += 1\n"
        "payload = {'celery_process_count': count, 'process_states': states}\n"
        "print(json.dumps(payload, sort_keys=True))"
    )
    processes = _compose(
        runner,
        project,
        ["exec", "--no-TTY", "worker-ragflow", "python", "-c", process_program],
        step="fault_redis_diagnostic_worker_processes",
        timeout_seconds=15,
        check=False,
    )
    if processes.returncode != 0:
        process_summary: dict[str, object] = {"status": "command_failed"}
    else:
        try:
            process_summary = {
                "status": "observed",
                **_last_json_object(
                    processes.stdout,
                    step="fault_redis_diagnostic_worker_processes",
                ),
            }
        except InfrastructureE2EError:
            process_summary = {"status": "invalid_output"}
    return {"logs": log_summary, "processes": process_summary}


def _emit_redis_fault_diagnostics(
    runner: CommandRunner,
    project: str,
    *,
    probe: InfrastructureBusinessProbe,
    business_state: BusinessProbeState,
    target: ReplayTarget,
) -> None:
    try:
        database = probe.fault_database_diagnostics(business_state, target)
    except InfrastructureProbeError:
        database = {"status": "probe_failed"}
    payload = {
        "rabbitmq": _redis_rabbitmq_diagnostics(runner, project),
        "messages": _redis_retry_message_diagnostics(
            runner,
            project,
            target_file_id=target.file_id,
        ),
        "celery": _redis_celery_diagnostics(
            runner,
            project,
            target_file_id=target.file_id,
        ),
        "worker": _redis_worker_diagnostics(runner, project),
        "database": database,
    }
    sys.stderr.write(
        "[infrastructure-e2e] redis_diagnostics="
        f"{json.dumps(payload, sort_keys=True, separators=(',', ':'))}\n"
    )
    sys.stderr.flush()


def _require_service_stopped(
    runner: CommandRunner,
    project: str,
    *,
    service: str,
    step: str,
) -> None:
    result = _compose(
        runner,
        project,
        ["ps", "--status", "running", "--quiet", service],
        step=step,
        timeout_seconds=15,
        check=False,
    )
    if result.returncode != 0 or result.stdout.strip():
        raise InfrastructureE2EError(step)


def _require_service_state(
    runner: CommandRunner,
    project: str,
    *,
    service: str,
    status: str,
    step: str,
) -> None:
    result = _compose(
        runner,
        project,
        ["ps", "--status", status, "--quiet", service],
        step=step,
        timeout_seconds=15,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise InfrastructureE2EError(step)


def _require_ragflow_worker_ping(
    runner: CommandRunner,
    project: str,
    *,
    step: str,
) -> None:
    program = (
        "from app.workers.celery_app import celery_app\n"
        "replies = celery_app.control.ping(timeout=5) or []\n"
        "matched = any(\n"
        "    isinstance(reply, dict)\n"
        "    and any(str(name).startswith('worker-ragflow@') for name in reply)\n"
        "    for reply in replies\n"
        ")\n"
        "raise SystemExit(0 if matched else 1)"
    )
    result = _compose(
        runner,
        project,
        ["exec", "--no-TTY", "backend-api", "python", "-c", program],
        step=step,
        timeout_seconds=15,
        check=False,
    )
    if result.returncode != 0:
        raise InfrastructureE2EError(step)


def _reconfirm_dependency_outage(
    runner: CommandRunner,
    project: str,
    *,
    dependency: str,
    service: str,
    api_ready_url: str,
    ragflow_health_url: str,
    ca_cert_file: Path,
) -> str:
    _require_service_stopped(
        runner,
        project,
        service=service,
        step=f"fault_{dependency}_dependency_still_stopped",
    )
    if dependency == "ragflow":
        _expect_tls_endpoint_unavailable(
            ragflow_health_url,
            ca_cert_file=ca_cert_file,
        )
        return "tls_endpoint_unreachable"
    _expect_ready_503(api_ready_url, ca_cert_file=ca_cert_file)
    return "ready_503"


def _start_worker_during_dependency_outage(
    runner: CommandRunner,
    project: str,
    *,
    dependency: str,
    service: str,
    api_ready_url: str,
    ragflow_health_url: str,
    ca_cert_file: Path,
) -> str:
    _compose_up_no_deps(
        runner,
        project,
        "worker-ragflow",
        step=f"fault_{dependency}_start_worker_during_outage",
    )
    return _reconfirm_dependency_outage(
        runner,
        project,
        dependency=dependency,
        service=service,
        api_ready_url=api_ready_url,
        ragflow_health_url=ragflow_health_url,
        ca_cert_file=ca_cert_file,
    )


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


def _wait_for_queue_ready_below(
    runner: CommandRunner,
    project: str,
    *,
    queue: str,
    threshold: int,
    timeout_seconds: float = 60.0,
) -> dict[str, tuple[int, int]]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        snapshot = _queue_snapshot(runner, project)
        current = snapshot.get(queue)
        if current is not None and current[1] < threshold:
            return snapshot
        time.sleep(0.5)
    raise InfrastructureE2EError("rabbitmq_queue_consumption")


def _wait_for_queue_delivery(
    runner: CommandRunner,
    project: str,
    *,
    queue: str,
    consumers: int | None = None,
    messages_total: int | None = None,
    timeout_seconds: float = 60.0,
) -> dict[str, tuple[int, int, int]]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        snapshot = _queue_delivery_snapshot(runner, project)
        current = snapshot.get(queue)
        if current is not None:
            consumer_match = consumers is None or current[0] == consumers
            total = current[1] + current[2]
            message_match = messages_total is None or total == messages_total
            if consumer_match and message_match:
                return snapshot
        time.sleep(0.5)
    raise InfrastructureE2EError("rabbitmq_queue_delivery_state")


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


def _service_image_ids(
    runner: CommandRunner,
    container_ids: dict[str, str],
) -> dict[str, str]:
    image_ids: dict[str, str] = {}
    for service, container_id in sorted(container_ids.items()):
        image_id = runner.run(
            ["docker", "inspect", "--format", "{{.Image}}", container_id],
            step="service_image_identity",
        ).stdout
        if SHA256_PATTERN.fullmatch(image_id) is None:
            raise InfrastructureE2EError("service_image_identity")
        image_ids[service] = image_id
    return image_ids


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
        "settings=get_settings(); "
        "assert settings.minio_secure is True; "
        "assert settings.minio_ca_cert_file == '/e2e-certs/ca.crt'; "
        "context=ssl.create_default_context(cafile=settings.minio_ca_cert_file); "
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


def _verify_prometheus_minio_tls(targets_url: str) -> dict[str, object]:
    _announce_step("prometheus_minio_tls")
    expected_scrape_url = "https://minio:9000/minio/v2/metrics/cluster"
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            with urlopen(targets_url, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError):
            time.sleep(1)
            continue
        data = payload.get("data") if isinstance(payload, dict) else None
        active_targets = data.get("activeTargets") if isinstance(data, dict) else None
        if not isinstance(active_targets, list):
            time.sleep(1)
            continue
        for target in active_targets:
            if not isinstance(target, dict):
                continue
            labels = target.get("labels")
            if (
                isinstance(labels, dict)
                and labels.get("job") == "minio"
                and target.get("health") == "up"
                and target.get("scrapeUrl") == expected_scrape_url
                and target.get("lastError") in {"", None}
            ):
                return {
                    "status": "passed",
                    "job": "minio",
                    "health": "up",
                    "scrape_url": expected_scrape_url,
                    "config_sha256": hashlib.sha256(
                        PROTECTED_PROMETHEUS_CONFIG.read_bytes()
                    ).hexdigest(),
                    "ca_file": "/etc/prometheus/tls/ca.crt",
                    "server_name": "minio",
                    "certificate_verification": "required",
                }
        time.sleep(1)
    raise InfrastructureE2EError("prometheus_minio_tls")


def _wait_for_started_redis_retry_attempt(
    runner: CommandRunner,
    project: str,
    *,
    target_file_id: uuid.UUID,
) -> None:
    target = str(target_file_id)
    program = (
        "from app.workers.celery_app import celery_app\n"
        f"target = '{target}'\n"
        "inspector = celery_app.control.inspect(timeout=2)\n"
        "responses = (inspector.active() or {}, inspector.scheduled() or {})\n"
        "for response in responses:\n"
        "    for tasks in response.values():\n"
        "        for item in tasks if isinstance(tasks, list) else []:\n"
        "            request = item.get('request', item) if isinstance(item, dict) else None\n"
        "            if not isinstance(request, dict):\n"
        "                continue\n"
        "            name = request.get('name') or request.get('type')\n"
        "            if name != 'ragflow.create_upload_task':\n"
        "                continue\n"
        "            if target in str(request.get('args')):\n"
        "                raise SystemExit(0)\n"
        "raise SystemExit(1)"
    )
    _announce_step("fault_redis_wait_retry_activity")
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        inspection = _compose(
            runner,
            project,
            ["exec", "--no-TTY", "backend-api", "python", "-c", program],
            step="fault_redis_inspect_retry_activity",
            timeout_seconds=15,
            check=False,
        )
        if inspection.returncode == 0:
            return
        snapshot = _queue_snapshot(runner, project)
        if snapshot["ragflow_queue"][1] >= 1:
            return
        time.sleep(0.5)
    raise InfrastructureE2EError("redis_retry_activity")


def _observe_redis_retry_message(
    runner: CommandRunner,
    project: str,
    *,
    probe: InfrastructureBusinessProbe,
    business_state: BusinessProbeState,
    target: ReplayTarget,
    run_id: uuid.UUID,
) -> dict[str, object]:
    try:
        retry_output = _exercise_rabbitmq(
            runner,
            project,
            [
                "--mode",
                "observe-retry",
                "--queue",
                "ragflow_queue",
                "--task",
                RAGFLOW_TASK,
                "--probe-run-id",
                str(run_id),
                "--expected-target-id",
                str(target.file_id),
                "--expected-retries",
                "1",
            ],
            step="fault_redis_observe_retry",
        )
    except InfrastructureE2EError:
        _emit_redis_fault_diagnostics(
            runner,
            project,
            probe=probe,
            business_state=business_state,
            target=target,
        )
        raise
    retry_message = retry_output.get("retry_message")
    if not isinstance(retry_message, dict):
        raise InfrastructureE2EError("redis_retry_observation")
    retry_task_id = retry_message.get("task_id")
    retry_count = retry_message.get("retry_count")
    if (
        not isinstance(retry_task_id, str)
        or UUID_PATTERN.fullmatch(retry_task_id) is None
        or retry_count != 1
        or retry_message.get("target_id") != str(target.file_id)
        or retry_message.get("task_name") != RAGFLOW_TASK
        or retry_message.get("queue_name") != "ragflow_queue"
        or retry_message.get("persistent_message") is not True
        or retry_message.get("result") != "retry_requeued"
    ):
        raise InfrastructureE2EError("redis_retry_observation")
    return {
        "retry_task_id": retry_task_id,
        "retry_task_name": RAGFLOW_TASK,
        "retry_queue": "ragflow_queue",
        "retry_count_observed": retry_count,
        "retry_status_before_restore": "requeued",
    }


def _exercise_redis_dependency_fault(
    runner: CommandRunner,
    project: str,
    *,
    probe: InfrastructureBusinessProbe,
    business_state: BusinessProbeState,
    run_id: uuid.UUID,
    api_ready_url: str,
    ragflow_health_url: str,
    ca_cert_file: Path,
) -> dict[str, object]:
    _wait_for_queue(
        runner,
        project,
        queue="ragflow_queue",
        consumers=1,
        messages_ready=0,
    )
    _wait_for_queue_delivery(
        runner,
        project,
        queue="ragflow_queue.dlq",
        consumers=0,
        messages_total=0,
    )
    _require_ragflow_worker_ping(
        runner,
        project,
        step="fault_redis_worker_ping_before_pause",
    )
    baseline_upload_count = probe.ragflow_upload_count()
    _compose(
        runner,
        project,
        ["pause", "worker-ragflow"],
        step="fault_redis_pause_running_worker",
    )
    _require_service_state(
        runner,
        project,
        service="worker-ragflow",
        status="paused",
        step="fault_redis_worker_paused",
    )
    target = probe.create_fault_target(business_state, dependency="redis")
    before_snapshot = _wait_for_queue_delivery(
        runner,
        project,
        queue="ragflow_queue",
        consumers=1,
        messages_total=1,
    )
    before_delivery = before_snapshot["ragflow_queue"]
    queue_messages_before = before_delivery[1] + before_delivery[2]
    probe.require_remote_unchanged(
        target,
        baseline_upload_count=baseline_upload_count,
    )

    _compose(
        runner,
        project,
        ["stop", "--timeout", "30", "redis"],
        step="fault_redis_stop_dependency",
    )
    outage_observed = _reconfirm_dependency_outage(
        runner,
        project,
        dependency="redis",
        service="redis",
        api_ready_url=api_ready_url,
        ragflow_health_url=ragflow_health_url,
        ca_cert_file=ca_cert_file,
    )
    _compose(
        runner,
        project,
        ["unpause", "worker-ragflow"],
        step="fault_redis_unpause_running_worker",
    )
    _require_service_state(
        runner,
        project,
        service="worker-ragflow",
        status="running",
        step="fault_redis_worker_running_after_unpause",
    )
    if (
        _reconfirm_dependency_outage(
            runner,
            project,
            dependency="redis",
            service="redis",
            api_ready_url=api_ready_url,
            ragflow_health_url=ragflow_health_url,
            ca_cert_file=ca_cert_file,
        )
        != outage_observed
    ):
        raise InfrastructureE2EError("fault_redis_outage_reconfirmation")
    _require_ragflow_worker_ping(
        runner,
        project,
        step="fault_redis_worker_ping_after_unpause",
    )
    try:
        _wait_for_started_redis_retry_attempt(
            runner,
            project,
            target_file_id=target.file_id,
        )
    except InfrastructureE2EError:
        _emit_redis_fault_diagnostics(
            runner,
            project,
            probe=probe,
            business_state=business_state,
            target=target,
        )
        raise

    _compose(
        runner,
        project,
        ["kill", "--signal", "SIGKILL", "worker-ragflow"],
        step="fault_redis_kill_worker_for_retry_inspection",
    )
    _wait_for_queue(
        runner,
        project,
        queue="ragflow_queue",
        consumers=0,
        messages_ready=1,
        timeout_seconds=60,
    )
    _wait_for_queue_delivery(
        runner,
        project,
        queue="ragflow_queue.dlq",
        consumers=0,
        messages_total=0,
        timeout_seconds=60,
    )
    failure_details = _observe_redis_retry_message(
        runner,
        project,
        probe=probe,
        business_state=business_state,
        target=target,
        run_id=run_id,
    )
    _wait_for_queue(
        runner,
        project,
        queue="ragflow_queue",
        consumers=0,
        messages_ready=1,
        timeout_seconds=60,
    )
    _wait_for_queue_delivery(
        runner,
        project,
        queue="ragflow_queue.dlq",
        consumers=0,
        messages_total=0,
        timeout_seconds=60,
    )

    _compose_up(
        runner,
        project,
        ["redis"],
        step="fault_redis_restore_dependency",
        wait_timeout_seconds=120,
        command_timeout_seconds=180,
    )
    _wait_ready(api_ready_url, ca_cert_file=ca_cert_file)
    probe.require_remote_unchanged(
        target,
        baseline_upload_count=baseline_upload_count,
    )
    after_snapshot = _wait_for_queue(
        runner,
        project,
        queue="ragflow_queue",
        consumers=0,
        messages_ready=1,
        timeout_seconds=60,
    )
    queue_messages_after_restore = after_snapshot["ragflow_queue"][1]
    _compose_up_no_deps(
        runner,
        project,
        "worker-ragflow",
        step="fault_redis_restart_worker",
    )
    _wait_for_queue(
        runner,
        project,
        queue="ragflow_queue",
        consumers=1,
    )
    _require_ragflow_worker_ping(
        runner,
        project,
        step="fault_redis_worker_ping_after_restore",
    )
    restored = probe.verify_fault_restored(
        business_state,
        target,
        baseline_upload_count=baseline_upload_count,
    )
    _wait_for_queue(
        runner,
        project,
        queue="ragflow_queue",
        messages_ready=0,
    )
    return {
        "status": "passed",
        "run_id": str(run_id),
        "service": "redis",
        "outage_observed": outage_observed,
        "failure_observation": "celery_retry_requeued_while_cache_unavailable",
        "durability_anchor": "celery_retry_message",
        "queue_messages_before": queue_messages_before,
        "queue_messages_after_restore": queue_messages_after_restore,
        **failure_details,
        **restored,
    }


def _exercise_dependency_fault(
    runner: CommandRunner,
    project: str,
    *,
    probe: InfrastructureBusinessProbe,
    business_state: BusinessProbeState,
    dependency: str,
    run_id: uuid.UUID,
    api_ready_url: str,
    ragflow_health_url: str,
    ca_cert_file: Path,
) -> dict[str, object]:
    if dependency == "redis":
        return _exercise_redis_dependency_fault(
            runner,
            project,
            probe=probe,
            business_state=business_state,
            run_id=run_id,
            api_ready_url=api_ready_url,
            ragflow_health_url=ragflow_health_url,
            ca_cert_file=ca_cert_file,
        )
    service = "mock-ragflow" if dependency == "ragflow" else dependency
    _compose(
        runner,
        project,
        ["stop", "--timeout", "30", "worker-ragflow"],
        step=f"fault_{dependency}_stop_worker",
    )
    _wait_for_queue(
        runner,
        project,
        queue="ragflow_queue",
        consumers=0,
        messages_ready=0,
    )
    target = probe.create_fault_target(business_state, dependency=dependency)
    before_snapshot = _wait_for_queue(
        runner,
        project,
        queue="ragflow_queue",
        consumers=0,
        messages_ready=1,
    )
    queue_messages_before = before_snapshot["ragflow_queue"][1]
    baseline_upload_count = probe.ragflow_upload_count()

    _compose(
        runner,
        project,
        ["stop", "--timeout", "30", service],
        step=f"fault_{dependency}_stop_dependency",
    )
    if dependency == "ragflow":
        _expect_tls_endpoint_unavailable(
            ragflow_health_url,
            ca_cert_file=ca_cert_file,
        )
        outage_observed = "tls_endpoint_unreachable"
    else:
        _expect_ready_503(api_ready_url, ca_cert_file=ca_cert_file)
        outage_observed = "ready_503"

    failure_observation: str
    durability_anchor: str
    failed_task_id: uuid.UUID | None = None
    failure_details: dict[str, object] = {}
    if dependency == "rabbitmq":
        failure_observation = "persistent_message_held_while_broker_unavailable"
        durability_anchor = "rabbitmq_durable_queue"
        failure_details["broker_message_persisted"] = True
    else:
        outage_reconfirmed = _start_worker_during_dependency_outage(
            runner,
            project,
            dependency=dependency,
            service=service,
            api_ready_url=api_ready_url,
            ragflow_health_url=ragflow_health_url,
            ca_cert_file=ca_cert_file,
        )
        if outage_reconfirmed != outage_observed:
            raise InfrastructureE2EError(f"fault_{dependency}_outage_reconfirmation")
        _wait_for_queue_ready_below(
            runner,
            project,
            queue="ragflow_queue",
            threshold=queue_messages_before,
            timeout_seconds=120,
        )
        failed_task_id = probe.wait_for_failed_sync_task(business_state, target)
        failure_observation = "postgres_failed_sync_task_before_remote_upload"
        durability_anchor = "postgres_sync_task"
        failure_details.update(
            {
                "failed_task_id": str(failed_task_id),
                "retry_status_before": "failed",
            }
        )
        _compose(
            runner,
            project,
            [
                "stop",
                "--timeout",
                "30",
                "worker-ragflow",
            ],
            step=f"fault_{dependency}_stop_worker_after_failure",
        )

    _compose_up(
        runner,
        project,
        [service],
        step=f"fault_{dependency}_restore_dependency",
        wait_timeout_seconds=120,
        command_timeout_seconds=180,
    )
    if dependency == "ragflow":
        _wait_ready(ragflow_health_url, ca_cert_file=ca_cert_file)
    else:
        _wait_ready(api_ready_url, ca_cert_file=ca_cert_file)
    probe.require_remote_unchanged(
        target,
        baseline_upload_count=baseline_upload_count,
    )
    if failed_task_id is not None:
        failure_details["retry_status_after"] = probe.retry_failed_sync_task(
            business_state,
            task_id=failed_task_id,
        )
    after_snapshot = _wait_for_queue(
        runner,
        project,
        queue="ragflow_queue",
        consumers=0,
        messages_ready=1,
        timeout_seconds=180,
    )
    queue_messages_after_restore = after_snapshot["ragflow_queue"][1]

    _compose_up_no_deps(
        runner,
        project,
        "worker-ragflow",
        step=f"fault_{dependency}_restart_worker",
    )
    _wait_for_queue(
        runner,
        project,
        queue="ragflow_queue",
        consumers=1,
    )
    restored = probe.verify_fault_restored(
        business_state,
        target,
        baseline_upload_count=baseline_upload_count,
    )
    _wait_for_queue(
        runner,
        project,
        queue="ragflow_queue",
        messages_ready=0,
    )
    return {
        "status": "passed",
        "run_id": str(run_id),
        "service": service,
        "outage_observed": outage_observed,
        "failure_observation": failure_observation,
        "durability_anchor": durability_anchor,
        "queue_messages_before": queue_messages_before,
        "queue_messages_after_restore": queue_messages_after_restore,
        **failure_details,
        **restored,
    }


def _exercise_dependency_faults(
    runner: CommandRunner,
    project: str,
    *,
    probe: InfrastructureBusinessProbe,
    business_state: BusinessProbeState,
    run_id: uuid.UUID,
    api_ready_url: str,
    ragflow_health_url: str,
    ca_cert_file: Path,
) -> dict[str, dict[str, object]]:
    results: dict[str, dict[str, object]] = {}
    for dependency in FAULT_DEPENDENCIES:
        _announce_step(f"fault_{dependency}_begin")
        results[dependency] = _exercise_dependency_fault(
            runner,
            project,
            probe=probe,
            business_state=business_state,
            dependency=dependency,
            run_id=run_id,
            api_ready_url=api_ready_url,
            ragflow_health_url=ragflow_health_url,
            ca_cert_file=ca_cert_file,
        )
    target_ids = {str(result["target_file_id"]) for result in results.values()}
    if len(target_ids) != len(FAULT_DEPENDENCIES):
        raise InfrastructureE2EError("dependency_fault_identity")
    return results


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
            "-m",
            "scripts.exercise_rabbitmq_dlq",
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
    # This local runner proves only the raw Compose execution. A protected
    # external DGX verifier must bind host identity, immutable OCI provenance,
    # and the complete release evidence set before any release-level pass exists.
    del source_clean, host_architecture, docker_architecture
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
    backend_port, nginx_port, ragflow_port, smtp_state_port, prometheus_port = _free_ports(5)
    compose_active = False
    cleanup_status = "not_started"
    failure: InfrastructureE2EError | None = None
    infrastructure_payload: dict[str, Any] | None = None
    rabbit_payload: dict[str, Any] | None = None
    current_phase = "tls_setup"

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
            prometheus_port=prometheus_port,
        )
        runner = CommandRunner(environment=environment)
        try:
            _announce_step(current_phase)
            certificate_metadata = _generate_certificates(
                runner,
                backend_image=arguments.backend_image,
                cert_parent=cert_parent,
            )
            tls_evidence = _tls_evidence(certificate_metadata)
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
            current_phase = "compose_bootstrap"
            _announce_step(current_phase)
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
            ca_cert_file = Path(probe_values["ca_cert_file"])
            _wait_ready(probe_values["backend_ready_url"])
            _wait_ready(
                f"{probe_values['api_base_url']}/api/system/ready",
                ca_cert_file=ca_cert_file,
            )
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
                ragflow_internal_base_url="https://mock-ragflow:9380",
                ragflow_api_key=probe_values["ragflow_api_key"],
                dataset_id=probe_values["dataset_id"],
                ca_cert_file=probe_values["ca_cert_file"],
            )
            current_phase = "business_probe"
            _announce_step(current_phase)
            business_state, business_summary = probe.run_primary_flow()
            current_phase = "dependency_fault_recovery"
            _announce_step(current_phase)
            fault_recovery = _exercise_dependency_faults(
                runner,
                project,
                probe=probe,
                business_state=business_state,
                run_id=run_id,
                api_ready_url=f"{probe_values['api_base_url']}/api/system/ready",
                ragflow_health_url=probe_values["mock_state_url"].replace(
                    "/__e2e/state",
                    "/health",
                ),
                ca_cert_file=ca_cert_file,
            )

            current_phase = "dlq_protocol"
            _announce_step(current_phase)
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
            prometheus_minio_tls = _verify_prometheus_minio_tls(
                probe_values["prometheus_targets_url"]
            )
            service_container_ids = _validate_service_containers(runner, project)
            service_image_ids = _service_image_ids(runner, service_container_ids)

            current_phase = "evidence_assembly"
            _announce_step(current_phase)
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
                "status": "passed",
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
                "evidence_contract_version": EVIDENCE_CONTRACT_VERSION,
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
                "service_image_ids": service_image_ids,
                "worker_queue_consumers": worker_queue_consumers,
                "business_probe": business_summary,
                "fault_recovery": fault_recovery,
                "prometheus_minio_tls": prometheus_minio_tls,
                "rabbitmq_probe_run_id": str(run_id),
                "tls": tls_evidence,
                "tls_certificate_sha256": tls_evidence["certificate_bundle_sha256"],
                "cleanup_status": "pending",
                "results": {
                    "compose_up": "passed",
                    "alembic_head": "passed",
                    "ready": "passed",
                    "gateway": "passed",
                    "gateway_tls": "passed",
                    "email_verification_floor": "passed",
                    "smtp_starttls": "passed",
                    "workers": "passed",
                    "rabbitmq_topology": "passed",
                    "minio_tls": "passed",
                    "prometheus_minio_tls": "passed",
                    "ragflow_tls": "passed",
                    "upload_review_ragflow": "passed",
                    "dependency_fault_recovery": "passed",
                    "dlq_protocol": "passed",
                    "cleanup": "pending",
                },
            }
        except InfrastructureE2EError as error:
            failure = error
        except InfrastructureProbeError as error:
            sys.stderr.write(f"[infrastructure-e2e] probe={current_phase} detail={error}\n")
            sys.stderr.flush()
            failure = InfrastructureE2EError(current_phase)
            failure.__cause__ = error
        except (ValueError, KeyError, TypeError, OSError, RuntimeError, AssertionError) as error:
            sys.stderr.write(
                f"[infrastructure-e2e] unexpected={current_phase} " f"type={type(error).__name__}\n"
            )
            sys.stderr.flush()
            failure = InfrastructureE2EError(f"{current_phase}_unexpected")
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
