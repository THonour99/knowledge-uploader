"""Run an isolated, evidence-bound Compose infrastructure and business E2E gate."""

from __future__ import annotations

import argparse
import base64
import binascii
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
from concurrent.futures import ThreadPoolExecutor
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
MINIO_MC_IMAGE = (
    "minio/mc:RELEASE.2024-04-18T16-45-29Z"
    "@sha256:5a84109d6b29bab96c3122e4a7ba888fbf48d4cdc83bc8bf88e3a7ac67b970b8"
)
MINIO_SERVER_IMAGE = (
    "minio/minio:RELEASE.2024-04-18T19-09-19Z"
    "@sha256:036a068d7d6b69400da6bc07a480bee1e241ef3c341c41d988ed11f520f85124"
)
GIT_SHA_PATTERN = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})")
SHA256_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
HEX_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
JWT_CANDIDATE_PATTERN = re.compile(
    rb"(?<![A-Za-z0-9_-])([A-Za-z0-9_-]{2,}\.[A-Za-z0-9_-]{2,}\.[A-Za-z0-9_-]{2,})(?![A-Za-z0-9_-])"
)
JWT_STRING_IDENTITY_CLAIMS = frozenset({"iss", "sub", "jti", "accessKey"})
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
    "minio-bootstrap",
    "minio-metrics-token-init",
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
EVIDENCE_CONTRACT_VERSION = 5
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


def _decode_base64url_bytes(segment: bytes) -> bytes:
    padding = b"=" * (-len(segment) % 4)
    return base64.b64decode(segment + padding, altchars=b"-_", validate=True)


def _nonempty_jwt_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _has_nonempty_jwt_identity(claims: dict[str, object]) -> bool:
    if any(_nonempty_jwt_string(claims.get(name)) for name in JWT_STRING_IDENTITY_CLAIMS):
        return True
    audience = claims.get("aud")
    if _nonempty_jwt_string(audience):
        return True
    return (
        isinstance(audience, list)
        and bool(audience)
        and all(_nonempty_jwt_string(item) for item in audience)
    )


def _contains_semantic_jwt(payload: str | bytes) -> bool:
    raw = payload.encode("utf-8", errors="ignore") if isinstance(payload, str) else payload
    for match in JWT_CANDIDATE_PATTERN.finditer(raw):
        segments = match.group(1).split(b".")
        try:
            header = json.loads(_decode_base64url_bytes(segments[0]).decode("utf-8"))
            claims = json.loads(_decode_base64url_bytes(segments[1]).decode("utf-8"))
            signature = _decode_base64url_bytes(segments[2])
        except (
            UnicodeDecodeError,
            binascii.Error,
            json.JSONDecodeError,
            ValueError,
        ):
            continue
        algorithm = header.get("alg") if isinstance(header, dict) else None
        if (
            isinstance(algorithm, str)
            and bool(algorithm.strip())
            and algorithm.strip().lower() != "none"
            and isinstance(claims, dict)
            and _has_nonempty_jwt_identity(claims)
            and bool(signature)
        ):
            return True
    return False


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

    def update_environment(self, values: dict[str, str]) -> None:
        self._environment.update(values)


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
        "--progress",
        "quiet",
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
    minio_root_secret = _random_token(40)
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
        "MINIO_ROOT_USER": "knowledgee2eroot",
        "MINIO_ROOT_PASSWORD": minio_root_secret,
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


def _service_has_volume(
    service: object,
    *,
    target: str,
    read_only: bool,
) -> bool:
    if not isinstance(service, dict):
        return False
    volumes = service.get("volumes")
    return isinstance(volumes, list) and any(
        isinstance(volume, dict)
        and volume.get("target") == target
        and isinstance(volume.get("read_only", False), bool)
        and volume.get("read_only", False) is read_only
        for volume in volumes
    )


def _service_dependency_is_completed(service: object, dependency: str) -> bool:
    if not isinstance(service, dict):
        return False
    dependencies = service.get("depends_on")
    dependency_config = dependencies.get(dependency) if isinstance(dependencies, dict) else None
    return (
        isinstance(dependency_config, dict)
        and dependency_config.get("condition") == "service_completed_successfully"
    )


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
    backend_build = backend.get("build") if isinstance(backend, dict) else None
    backend_build_args = backend_build.get("args") if isinstance(backend_build, dict) else None
    if (
        not isinstance(backend_build_args, dict)
        or backend_build_args.get("MINIO_MC_IMAGE") != MINIO_MC_IMAGE
    ):
        raise InfrastructureE2EError("resolved_compose_contract")
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
        or "/minio/health/cluster" not in serialized_minio
    ):
        raise InfrastructureE2EError("resolved_compose_contract")
    minio = services.get("minio")
    minio_environment = minio.get("environment") if isinstance(minio, dict) else None
    if not isinstance(minio, dict) or minio.get("image") != MINIO_SERVER_IMAGE:
        raise InfrastructureE2EError("resolved_compose_contract")
    bootstrap = services.get("minio-bootstrap")
    bootstrap_environment = bootstrap.get("environment") if isinstance(bootstrap, dict) else None
    bootstrap_dependencies = bootstrap.get("depends_on") if isinstance(bootstrap, dict) else None
    serialized_bootstrap = json.dumps(bootstrap, sort_keys=True)
    initializer = services.get("minio-metrics-token-init")
    initializer_environment = (
        initializer.get("environment") if isinstance(initializer, dict) else None
    )
    initializer_dependencies = (
        initializer.get("depends_on") if isinstance(initializer, dict) else None
    )
    serialized_initializer = json.dumps(initializer, sort_keys=True)
    operational = services.get("operational-metrics")
    operational_environment = (
        operational.get("environment") if isinstance(operational, dict) else None
    )
    token_file = "/run/secrets/minio-metrics/token"
    token_dir = "/run/secrets/minio-metrics"
    if (
        not isinstance(prometheus, dict)
        or prometheus.get("image") != PROMETHEUS_IMAGE
        or "/etc/prometheus/prometheus.yml" not in serialized_prometheus
        or "prometheus.protected.yml" not in serialized_prometheus
        or "/etc/prometheus/tls/ca.crt" not in serialized_prometheus
        or not _service_has_volume(prometheus, target=token_dir, read_only=True)
        or not _service_dependency_is_completed(prometheus, "minio-metrics-token-init")
        or not isinstance(minio_environment, dict)
        or minio_environment.get("MINIO_PROMETHEUS_AUTH_TYPE") != "jwt"
        or not str(minio_environment.get("MINIO_ROOT_USER", "")).strip()
        or not str(minio_environment.get("MINIO_ROOT_PASSWORD", "")).strip()
        or not isinstance(bootstrap, dict)
        or bootstrap.get("image") != backend_image
        or bootstrap.get("entrypoint") != ["python", "-m", "scripts.minio_bootstrap"]
        or bootstrap.get("command") is not None
        or bootstrap.get("restart") != "no"
        or not isinstance(bootstrap_environment, dict)
        or bootstrap_environment.get("MINIO_ENDPOINT") != "minio:9000"
        or str(bootstrap_environment.get("MINIO_SECURE", "")).lower() != "true"
        or bootstrap_environment.get("MINIO_CA_CERT_FILE") != "/e2e-certs/ca.crt"
        or bootstrap_environment.get("SSL_CERT_FILE") != "/e2e-certs/ca.crt"
        or not isinstance(bootstrap_dependencies, dict)
        or not isinstance(bootstrap_dependencies.get("minio"), dict)
        or bootstrap_dependencies["minio"].get("condition") != "service_healthy"
        or "/e2e-certs/ca.crt" not in serialized_bootstrap
        or not isinstance(initializer, dict)
        or initializer.get("image") != backend_image
        or initializer.get("entrypoint") != ["python", "-m", "scripts.minio_metrics_token_init"]
        or initializer.get("command") is not None
        or initializer.get("restart") != "no"
        or not isinstance(initializer_environment, dict)
        or initializer_environment.get("MINIO_ENDPOINT") != "minio:9000"
        or str(initializer_environment.get("MINIO_SECURE", "")).lower() != "true"
        or initializer_environment.get("MINIO_CA_CERT_FILE") != "/e2e-certs/ca.crt"
        or initializer_environment.get("SSL_CERT_FILE") != "/e2e-certs/ca.crt"
        or "MINIO_METRICS_TOKEN_ROTATE" in initializer_environment
        or "MINIO_ACCESS_KEY" in initializer_environment
        or "MINIO_SECRET_KEY" in initializer_environment
        or not isinstance(initializer_dependencies, dict)
        or not isinstance(initializer_dependencies.get("minio"), dict)
        or initializer_dependencies["minio"].get("condition") != "service_healthy"
        or not _service_dependency_is_completed(initializer, "minio-bootstrap")
        or not _service_has_volume(initializer, target=token_dir, read_only=False)
        or "/e2e-certs/ca.crt" not in serialized_initializer
        or not isinstance(operational_environment, dict)
        or operational_environment.get("MINIO_ACCESS_KEY") != "metrics-bearer-only-no-data-plane"
        or operational_environment.get("MINIO_SECRET_KEY") != "metrics-bearer-only-no-data-plane"
        or operational_environment.get("MINIO_METRICS_BEARER_TOKEN_FILE") != token_file
        or not _service_has_volume(operational, target=token_dir, read_only=True)
        or not _service_dependency_is_completed(operational, "minio-metrics-token-init")
    ):
        raise InfrastructureE2EError("resolved_compose_contract")

    root_pairs = {
        (
            str(environment.get("MINIO_ROOT_USER", "")).strip(),
            str(environment.get("MINIO_ROOT_PASSWORD", "")).strip(),
        )
        for environment in (
            minio_environment,
            bootstrap_environment,
            initializer_environment,
        )
    }
    if len(root_pairs) != 1:
        raise InfrastructureE2EError("resolved_compose_contract")
    root_user, root_password = next(iter(root_pairs))
    if (
        root_user in {"", "knowledge-root", "minioadmin"}
        or root_password in {"", "knowledge_root_password", "minioadmin"}
        or root_user == str(bootstrap_environment.get("MINIO_ACCESS_KEY", "")).strip()
        or root_password == str(bootstrap_environment.get("MINIO_SECRET_KEY", "")).strip()
    ):
        raise InfrastructureE2EError("resolved_compose_contract")
    for credential_name in (
        "MINIO_ROOT_USER",
        "MINIO_ROOT_PASSWORD",
        "MINIO_ACCESS_KEY",
        "MINIO_SECRET_KEY",
    ):
        if not str(bootstrap_environment.get(credential_name, "")).strip():
            raise InfrastructureE2EError("resolved_compose_contract")

    services_with_root = {
        service_name
        for service_name, service in services.items()
        if isinstance(service, dict)
        and isinstance(service.get("environment"), dict)
        and (
            "MINIO_ROOT_USER" in service["environment"]
            or "MINIO_ROOT_PASSWORD" in service["environment"]
        )
    }
    if services_with_root != {"minio", "minio-bootstrap", "minio-metrics-token-init"}:
        raise InfrastructureE2EError("resolved_compose_contract")

    for service_name, service in services.items():
        if service_name != "minio-metrics-token-init" and _service_has_volume(
            service,
            target=token_dir,
            read_only=False,
        ):
            raise InfrastructureE2EError("resolved_compose_contract")
        environment = service.get("environment") if isinstance(service, dict) else None
        if not isinstance(environment, dict):
            continue
        if "MINIO_METRICS_BEARER_TOKEN" in environment:
            raise InfrastructureE2EError("resolved_compose_contract")
        has_bearer_file = "MINIO_METRICS_BEARER_TOKEN_FILE" in environment
        if has_bearer_file != (service_name == "operational-metrics"):
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
        "'https://minio:9000/minio/health/cluster', context=context, timeout=5); "
        "assert response.status == 200"
    )
    _compose(
        runner,
        project,
        ["exec", "--no-TTY", "backend-api", "python", "-c", program],
        step="minio_tls",
    )


def _seed_minio_identity_drift(runner: CommandRunner, project: str) -> None:
    program = r"""
set -eu
scheme=http
case "$(printf '%s' "$MINIO_SECURE" | tr '[:upper:]' '[:lower:]')" in
  true|1) scheme=https ;;
  false|0) ;;
  *) exit 1 ;;
esac
[ "$MINIO_ENDPOINT" = "minio:9000" ]
secondary_bucket="${MINIO_BUCKET}-isolation"
mc alias set drift \
  "$scheme://$MINIO_ENDPOINT" \
  "$MINIO_ROOT_USER" \
  "$MINIO_ROOT_PASSWORD" >/dev/null 2>&1
mc mb --ignore-existing "drift/$secondary_bucket" >/dev/null 2>&1
printf '%s' 'drift' | mc pipe "drift/$secondary_bucket/drift-object" >/dev/null 2>&1
mc admin user remove drift "$MINIO_ACCESS_KEY" >/dev/null 2>&1 || true
mc admin user add drift "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY" >/dev/null 2>&1
mc admin policy attach drift readwrite --user "$MINIO_ACCESS_KEY" >/dev/null 2>&1
mc admin group add drift knowledge-uploader-drift "$MINIO_ACCESS_KEY" >/dev/null 2>&1
mc admin policy attach drift readwrite --group knowledge-uploader-drift >/dev/null 2>&1
""".strip()
    result = _compose(
        runner,
        project,
        [
            "run",
            "--rm",
            "--no-deps",
            "--no-TTY",
            "--entrypoint",
            "/bin/sh",
            "minio-bootstrap",
            "-c",
            program,
        ],
        step="minio_identity_drift_seed",
    )
    if result.stdout or result.stderr:
        raise InfrastructureE2EError("minio_identity_drift_seed")


def _verify_minio_identity_reconciliation(
    runner: CommandRunner,
    project: str,
) -> dict[str, object]:
    program = r"""
import shutil
import tempfile
from pathlib import Path

from scripts import minio_bootstrap as bootstrap


def require_denied(arguments: list[str], *, environment: dict[str, str]) -> None:
    try:
        bootstrap._run_mc(arguments, environment=environment)
    except bootstrap.CommandRejected:
        return
    raise bootstrap.BootstrapError


def verify() -> None:
    (
        base_url,
        root_user,
        root_password,
        access_key,
        secret_key,
        bucket,
        secure,
    ) = bootstrap._validate_environment()
    working_directory = Path(
        tempfile.mkdtemp(prefix="knowledge-uploader-identity-verify.", dir="/tmp")
    )
    try:
        working_directory.chmod(0o700)
        environment = bootstrap._client_environment(
            working_directory=working_directory,
            secure=secure,
        )
        bootstrap._run_mc(
            ["alias", "set", "bootstrap", base_url, root_user, root_password],
            environment=environment,
        )
        bootstrap._run_mc(
            ["alias", "set", "data", base_url, access_key, secret_key],
            environment=environment,
        )

        probe_payload = b"target-data"
        probe_path = working_directory / "data-probe"
        probe_path.write_bytes(probe_payload)
        target_object = f"data/{bucket}/e2e-reconciliation-probe"
        bootstrap._run_mc(
            ["cp", str(probe_path), target_object],
            environment=environment,
        )
        downloaded = bootstrap._run_mc(
            ["cat", target_object],
            environment=environment,
        )
        if downloaded.stdout != probe_payload:
            raise bootstrap.BootstrapError
        bootstrap._run_mc(["rm", target_object], environment=environment)

        secondary_bucket = f"{bucket}-isolation"
        bootstrap._run_mc(
            ["ls", f"bootstrap/{secondary_bucket}"],
            environment=environment,
        )
        drift_object = bootstrap._run_mc(
            ["cat", f"bootstrap/{secondary_bucket}/drift-object"],
            environment=environment,
        )
        if drift_object.stdout != b"drift":
            raise bootstrap.BootstrapError
        secondary_probe = working_directory / "secondary-probe"
        secondary_probe.write_bytes(b"root-probe")
        secondary_object = f"bootstrap/{secondary_bucket}/root-probe"
        bootstrap._run_mc(
            ["cp", str(secondary_probe), secondary_object],
            environment=environment,
        )
        copied_probe = bootstrap._run_mc(
            ["cat", secondary_object],
            environment=environment,
        )
        if copied_probe.stdout != b"root-probe":
            raise bootstrap.BootstrapError
        bootstrap._run_mc(["rm", secondary_object], environment=environment)

        require_denied(
            ["ls", f"data/{secondary_bucket}"],
            environment=environment,
        )
        require_denied(
            ["cat", f"data/{secondary_bucket}/drift-object"],
            environment=environment,
        )
        denied_path = working_directory / "denied-probe"
        denied_path.write_bytes(b"denied")
        require_denied(
            ["cp", str(denied_path), f"data/{secondary_bucket}/denied-object"],
            environment=environment,
        )
        for root_arguments, data_arguments in (
            (["admin", "info", "bootstrap"], ["admin", "info", "data"]),
            (
                ["admin", "user", "list", "bootstrap"],
                ["admin", "user", "list", "data"],
            ),
            (
                ["admin", "policy", "list", "bootstrap"],
                ["admin", "policy", "list", "data"],
            ),
        ):
            bootstrap._run_mc(root_arguments, environment=environment)
            require_denied(data_arguments, environment=environment)

        if access_key not in bootstrap._user_names(environment=environment):
            raise bootstrap.BootstrapError
        for group in bootstrap._group_names(environment=environment):
            if access_key in bootstrap._group_members(group, environment=environment):
                raise bootstrap.BootstrapError
        policies = bootstrap._user_policies(access_key, environment=environment)
        if policies != {bootstrap.POLICY_NAME} or policies.intersection(
            bootstrap.BROAD_POLICIES
        ):
            raise bootstrap.BootstrapError
        users, groups = bootstrap._policy_entities(
            bootstrap.POLICY_NAME,
            environment=environment,
        )
        if users != {access_key} or groups:
            raise bootstrap.BootstrapError

        verified_policy = working_directory / "verified-policy.json"
        bootstrap._run_mc(
            [
                "admin",
                "policy",
                "info",
                "bootstrap",
                bootstrap.POLICY_NAME,
                "--policy-file",
                str(verified_policy),
            ],
            environment=environment,
        )
        bootstrap._verify_exact_bucket_policy(
            bootstrap._read_policy(verified_policy),
            bucket=bucket,
        )
    finally:
        shutil.rmtree(working_directory)


try:
    verify()
except BaseException:
    raise SystemExit(1) from None
""".strip()
    result = _compose(
        runner,
        project,
        [
            "run",
            "--rm",
            "--no-deps",
            "--no-TTY",
            "--entrypoint",
            "python",
            "minio-bootstrap",
            "-c",
            program,
        ],
        step="minio_identity_reconciliation",
    )
    if result.stdout or result.stderr:
        raise InfrastructureE2EError("minio_identity_reconciliation")
    return {
        "status": "passed",
        "stale_direct_policy_removed": True,
        "stale_group_membership_removed": True,
        "intended_policy_attached": True,
        "intended_bucket_operations": ["get", "put", "delete"],
        "secondary_bucket_operations_denied": ["list", "get", "put"],
        "admin_operations_denied": ["info", "user_list", "policy_list"],
    }


def _verify_minio_metrics_initializer(
    runner: CommandRunner,
    project: str,
) -> dict[str, object]:
    container_id = _compose(
        runner,
        project,
        ["ps", "--all", "--quiet", "minio-metrics-token-init"],
        step="minio_metrics_token_init",
    ).stdout
    if re.fullmatch(r"[0-9a-f]{64}", container_id) is None:
        raise InfrastructureE2EError("minio_metrics_token_init")
    state = runner.run(
        [
            "docker",
            "inspect",
            "--format",
            "{{.State.Status}} {{.State.ExitCode}}",
            container_id,
        ],
        step="minio_metrics_token_init",
    ).stdout
    if state != "exited 0":
        raise InfrastructureE2EError("minio_metrics_token_init")
    logs = runner.run(
        ["docker", "logs", container_id],
        step="minio_metrics_token_init_logs",
        check=False,
    )
    if (
        logs.returncode != 0
        or logs.stdout
        or logs.stderr
        or _contains_semantic_jwt(logs.stdout)
        or _contains_semantic_jwt(logs.stderr)
    ):
        raise InfrastructureE2EError("minio_metrics_token_init_logs")
    return {
        "status": "passed",
        "container_exit": "exited_0",
        "logs": "empty",
        "token_file": "strict_semantic_jwt_single_lf",
        "mode": "0440",
        "uid": 65534,
        "gid": 65534,
    }


def _run_token_volume_program(
    runner: CommandRunner,
    project: str,
    *,
    program: str,
    step: str,
    check: bool = True,
) -> CommandResult:
    result = _compose(
        runner,
        project,
        [
            "run",
            "--rm",
            "--no-deps",
            "--no-TTY",
            "--entrypoint",
            "python",
            "minio-metrics-token-init",
            "-c",
            program,
        ],
        step=step,
        check=check,
    )
    if _contains_semantic_jwt(result.stdout) or _contains_semantic_jwt(result.stderr):
        raise InfrastructureE2EError(step)
    return result


def _minio_metrics_token_metadata(
    runner: CommandRunner,
    project: str,
) -> dict[str, object]:
    program = r"""
import json
import stat
from pathlib import Path

from app.core.jwt_validation import is_semantic_time_bound_jwt

path = Path("/run/secrets/minio-metrics/token")
raw = path.read_bytes()
metadata = path.stat()
assert 0 < len(raw) <= 16384
assert raw.endswith(b"\n") and raw.count(b"\n") == 1
token = raw[:-1].decode("ascii")
assert is_semantic_time_bound_jwt(token)
assert stat.S_IMODE(metadata.st_mode) == 0o440
assert metadata.st_uid == 65534 and metadata.st_gid == 65534
print(json.dumps({"mtime_ns": metadata.st_mtime_ns}))
""".strip()
    result = _compose(
        runner,
        project,
        ["exec", "--no-TTY", "operational-metrics", "python", "-c", program],
        step="minio_metrics_token_file",
    )
    metadata = _json_object(result.stdout, step="minio_metrics_token_file")
    if (
        set(metadata) != {"mtime_ns"}
        or not isinstance(metadata.get("mtime_ns"), int)
        or int(metadata["mtime_ns"]) <= 0
    ):
        raise InfrastructureE2EError("minio_metrics_token_file")
    return metadata


def _copy_minio_metrics_token(
    runner: CommandRunner,
    project: str,
    *,
    destination_name: str,
    step: str,
) -> None:
    if re.fullmatch(r"\.[a-z0-9-]+", destination_name) is None:
        raise InfrastructureE2EError(step)
    program = f"""
import os
import stat
from pathlib import Path

from app.core.jwt_validation import is_semantic_time_bound_jwt

source = Path("/run/secrets/minio-metrics/token")
destination = source.parent / {destination_name!r}
raw = source.read_bytes()
assert raw.endswith(b"\\n") and raw.count(b"\\n") == 1
assert is_semantic_time_bound_jwt(raw[:-1].decode("ascii"))
destination.unlink(missing_ok=True)
destination.write_bytes(raw)
os.chown(destination, 65534, 65534)
os.chmod(destination, 0o440)
metadata = destination.stat()
assert stat.S_IMODE(metadata.st_mode) == 0o440
assert metadata.st_uid == 65534 and metadata.st_gid == 65534
""".strip()
    result = _run_token_volume_program(
        runner,
        project,
        program=program,
        step=step,
    )
    if result.stdout or result.stderr:
        raise InfrastructureE2EError(step)


def _minio_metrics_token_files_differ(
    runner: CommandRunner,
    project: str,
    *,
    first_name: str,
    second_name: str,
) -> bool:
    for name in (first_name, second_name):
        if re.fullmatch(r"(?:token|\.[a-z0-9-]+)", name) is None:
            raise InfrastructureE2EError("minio_metrics_token_compare")
    program = f"""
import hmac
from pathlib import Path

root = Path("/run/secrets/minio-metrics")
first = (root / {first_name!r}).read_bytes()
second = (root / {second_name!r}).read_bytes()
print("same" if hmac.compare_digest(first, second) else "changed")
""".strip()
    result = _compose(
        runner,
        project,
        ["exec", "--no-TTY", "operational-metrics", "python", "-c", program],
        step="minio_metrics_token_compare",
    )
    if result.stdout not in {"same", "changed"} or result.stderr:
        raise InfrastructureE2EError("minio_metrics_token_compare")
    return result.stdout == "changed"


def _minio_metrics_http_status(
    runner: CommandRunner,
    project: str,
    *,
    token_name: str,
    step: str,
) -> int:
    if re.fullmatch(r"(?:token|\.[a-z0-9-]+)", token_name) is None:
        raise InfrastructureE2EError(step)
    program = f"""
import ssl
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from app.core.jwt_validation import is_semantic_time_bound_jwt

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None

url = "https://minio:9000/minio/v2/metrics/cluster"
raw = (Path("/run/secrets/minio-metrics") / {token_name!r}).read_bytes()
assert raw.endswith(b"\\n") and raw.count(b"\\n") == 1
token = raw[:-1].decode("ascii")
assert is_semantic_time_bound_jwt(token)
context = ssl.create_default_context(cafile="/e2e-certs/ca.crt")
opener = build_opener(NoRedirect(), HTTPSHandler(context=context))
request = Request(url, headers={{"Authorization": f"Bearer {{token}}"}})
try:
    with opener.open(request, timeout=10) as response:
        assert response.geturl() == url
        status = response.status
        response.read(1)
except HTTPError as error:
    assert error.geturl() == url
    status = error.code
print(status)
""".strip()
    result = _compose(
        runner,
        project,
        ["exec", "--no-TTY", "operational-metrics", "python", "-c", program],
        step=step,
    )
    if result.stderr or result.stdout not in {"200", "401", "403"}:
        raise InfrastructureE2EError(step)
    return int(result.stdout)


def _delete_private_token_snapshots(
    runner: CommandRunner,
    project: str,
    *,
    names: tuple[str, ...],
) -> None:
    if not names or any(re.fullmatch(r"\.[a-z0-9-]+", name) is None for name in names):
        raise InfrastructureE2EError("minio_metrics_snapshot_cleanup")
    program = "\n".join(
        (
            "from pathlib import Path",
            'root = Path("/run/secrets/minio-metrics")',
            *(f"(root / {name!r}).unlink(missing_ok=True)" for name in names),
        )
    )
    result = _run_token_volume_program(
        runner,
        project,
        program=program,
        step="minio_metrics_snapshot_cleanup",
    )
    if result.stdout or result.stderr:
        raise InfrastructureE2EError("minio_metrics_snapshot_cleanup")


def _replace_invalid_minio_metrics_token(
    runner: CommandRunner,
    project: str,
) -> dict[str, object]:
    invalid_tokens = (
        ("lexical", "a.b.c"),
        (
            "decoded_non_object",
            "cHJlZml4ImFsZyI6IkhTMjU2InN1ZmZpeA." "cHJlZml4InN1YiI6Im1pbmlvInN1ZmZpeA.eA",
        ),
    )
    recovered: dict[str, object] = {}
    for case_name, invalid_token in invalid_tokens:
        corrupt_program = f"""
from pathlib import Path
import os
path = Path("/run/secrets/minio-metrics/token")
path.write_text({invalid_token!r} + "\\n", encoding="ascii")
os.chown(path, 65534, 65534)
os.chmod(path, 0o440)
""".strip()
        corruption = _run_token_volume_program(
            runner,
            project,
            program=corrupt_program,
            step=f"minio_metrics_invalid_token_injected_{case_name}",
        )
        recovery = _compose(
            runner,
            project,
            ["run", "--rm", "--no-deps", "--no-TTY", "minio-metrics-token-init"],
            step=f"minio_metrics_invalid_token_recovery_{case_name}",
        )
        for result in (corruption, recovery):
            if _contains_semantic_jwt(result.stdout) or _contains_semantic_jwt(result.stderr):
                raise InfrastructureE2EError("minio_metrics_invalid_token_recovery")
        recovered = _minio_metrics_token_metadata(runner, project)
    return recovered


def _verify_minio_metrics_anonymous_access_is_denied(
    runner: CommandRunner,
    project: str,
) -> dict[str, object]:
    program = """
import ssl
import urllib.request
from urllib.error import HTTPError

context = ssl.create_default_context(cafile="/e2e-certs/ca.crt")
try:
    urllib.request.urlopen(
        "https://minio:9000/minio/v2/metrics/cluster",
        context=context,
        timeout=5,
    ).close()
except HTTPError as error:
    assert error.code in {401, 403}
    print(error.code)
else:
    raise AssertionError("anonymous MinIO metrics access unexpectedly succeeded")
""".strip()
    result = _compose(
        runner,
        project,
        ["exec", "--no-TTY", "backend-api", "python", "-c", program],
        step="minio_metrics_anonymous_denied",
    )
    if result.stdout not in {"401", "403"}:
        raise InfrastructureE2EError("minio_metrics_anonymous_denied")
    return {"status": "denied", "http_status": int(result.stdout)}


def _wait_for_minio_capacity_collector(
    runner: CommandRunner,
    project: str,
    *,
    minimum_timestamp: float = 0.0,
    timeout_seconds: float = 90.0,
) -> float:
    program = """
import urllib.request

body = urllib.request.urlopen(
    "http://127.0.0.1:9102/metrics",
    timeout=5,
).read().decode("utf-8")
prefix = (
    'knowledge_uploader_operational_collector_component_'
    'last_success_timestamp_seconds{component="minio_capacity"} '
)
value = next((line[len(prefix):] for line in body.splitlines() if line.startswith(prefix)), "")
assert value and float(value) > 0
print(value)
""".strip()
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        result = _compose(
            runner,
            project,
            ["exec", "--no-TTY", "operational-metrics", "python", "-c", program],
            step="minio_capacity_collector",
            check=False,
        )
        if result.returncode == 0:
            try:
                timestamp = float(result.stdout)
            except ValueError:
                timestamp = 0.0
            if timestamp > minimum_timestamp:
                return timestamp
        time.sleep(1)
    raise InfrastructureE2EError("minio_capacity_collector")


def _temporary_token_file_count(runner: CommandRunner, project: str, *, step: str) -> int:
    program = """
from pathlib import Path
root = Path("/run/secrets/minio-metrics")
paths = list(root.glob(".token.tmp.*"))
assert all(path.is_file() and not path.is_symlink() for path in paths)
print(len(paths))
""".strip()
    result = _run_token_volume_program(runner, project, program=program, step=step)
    try:
        count = int(result.stdout)
    except ValueError as error:
        raise InfrastructureE2EError(step) from error
    if count < 0 or result.stderr:
        raise InfrastructureE2EError(step)
    return count


def _wait_for_temporary_token_file(
    runner: CommandRunner,
    project: str,
    *,
    step: str,
    timeout_seconds: float = 20.0,
) -> int:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        count = _temporary_token_file_count(runner, project, step=step)
        if count > 0:
            return count
        time.sleep(0.2)
    raise InfrastructureE2EError(step)


def _atomic_publish_probe_program() -> str:
    return """
import signal
import time
from scripts import minio_metrics_token_init as module

real_fsync = module.os.fsync

def blocking_fsync(descriptor):
    time.sleep(60)
    real_fsync(descriptor)

def terminate(_signum, _frame):
    raise module.TokenInitializationInterrupted

previous_handlers = {}
module.os.fsync = blocking_fsync
try:
    for current in module.TERMINATION_SIGNALS:
        previous_handlers[current] = signal.signal(current, terminate)
    module._write_atomic("atomic-publish-probe")
except BaseException:
    raise SystemExit(1)
finally:
    for current, previous in previous_handlers.items():
        signal.signal(current, previous)
""".strip()


def _start_atomic_publish_probe(
    runner: CommandRunner,
    project: str,
    *,
    container_name: str,
    step: str,
) -> None:
    result = _compose(
        runner,
        project,
        [
            "run",
            "--detach",
            "--no-deps",
            "--name",
            container_name,
            "--entrypoint",
            "python",
            "minio-metrics-token-init",
            "-c",
            _atomic_publish_probe_program(),
        ],
        step=step,
    )
    if _contains_semantic_jwt(result.stdout) or _contains_semantic_jwt(result.stderr):
        raise InfrastructureE2EError(step)


def _stop_atomic_publish_probe(
    runner: CommandRunner,
    *,
    container_name: str,
    signal_name: str,
    expected_exit_codes: set[int],
    step: str,
) -> int:
    kill = runner.run(
        ["docker", "kill", "--signal", signal_name, container_name],
        step=f"{step}_signal",
    )
    if _contains_semantic_jwt(kill.stdout) or _contains_semantic_jwt(kill.stderr):
        raise InfrastructureE2EError(step)
    waited = runner.run(["docker", "wait", container_name], step=f"{step}_wait")
    try:
        exit_code = int(waited.stdout)
    except ValueError as error:
        raise InfrastructureE2EError(step) from error
    logs = runner.run(["docker", "logs", container_name], step=f"{step}_logs", check=False)
    if (
        exit_code not in expected_exit_codes
        or logs.returncode != 0
        or logs.stdout
        or logs.stderr
        or _contains_semantic_jwt(logs.stdout)
        or _contains_semantic_jwt(logs.stderr)
    ):
        raise InfrastructureE2EError(step)
    runner.run(
        ["docker", "rm", "--force", container_name],
        step=f"{step}_remove",
    )
    return exit_code


def _exercise_minio_atomic_publish(
    runner: CommandRunner,
    project: str,
) -> dict[str, object]:
    def concurrent_init(index: int) -> CommandResult:
        return _compose(
            runner,
            project,
            ["run", "--rm", "--no-deps", "--no-TTY", "minio-metrics-token-init"],
            step=f"minio_metrics_concurrent_init_{index}",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        concurrent_results = list(executor.map(concurrent_init, (1, 2)))
    if any(
        result.stdout
        or result.stderr
        or _contains_semantic_jwt(result.stdout)
        or _contains_semantic_jwt(result.stderr)
        for result in concurrent_results
    ):
        raise InfrastructureE2EError("minio_metrics_concurrent_init")
    _minio_metrics_token_metadata(runner, project)

    before_term = _minio_metrics_token_metadata(runner, project)
    if (
        _temporary_token_file_count(
            runner,
            project,
            step="minio_metrics_term_precondition",
        )
        != 0
    ):
        raise InfrastructureE2EError("minio_metrics_term_precondition")
    term_name = f"{project}-token-term"
    _start_atomic_publish_probe(
        runner,
        project,
        container_name=term_name,
        step="minio_metrics_term_probe_start",
    )
    _wait_for_temporary_token_file(runner, project, step="minio_metrics_term_temp")
    term_exit = _stop_atomic_publish_probe(
        runner,
        container_name=term_name,
        signal_name="TERM",
        expected_exit_codes={1, 143},
        step="minio_metrics_term_probe",
    )
    if (
        _temporary_token_file_count(
            runner,
            project,
            step="minio_metrics_term_cleanup",
        )
        != 0
    ):
        raise InfrastructureE2EError("minio_metrics_term_cleanup")
    after_term = _minio_metrics_token_metadata(runner, project)
    if before_term != after_term:
        raise InfrastructureE2EError("minio_metrics_term_cleanup")

    if (
        _temporary_token_file_count(
            runner,
            project,
            step="minio_metrics_sigkill_precondition",
        )
        != 0
    ):
        raise InfrastructureE2EError("minio_metrics_sigkill_precondition")
    kill_name = f"{project}-token-kill"
    _start_atomic_publish_probe(
        runner,
        project,
        container_name=kill_name,
        step="minio_metrics_sigkill_probe_start",
    )
    _wait_for_temporary_token_file(runner, project, step="minio_metrics_sigkill_temp")
    kill_exit = _stop_atomic_publish_probe(
        runner,
        container_name=kill_name,
        signal_name="KILL",
        expected_exit_codes={137},
        step="minio_metrics_sigkill_probe",
    )
    orphan_count = _temporary_token_file_count(
        runner,
        project,
        step="minio_metrics_sigkill_orphan",
    )
    if orphan_count < 1:
        raise InfrastructureE2EError("minio_metrics_sigkill_orphan")
    recovery = _compose(
        runner,
        project,
        ["run", "--rm", "--no-deps", "--no-TTY", "minio-metrics-token-init"],
        step="minio_metrics_sigkill_recovery",
    )
    if (
        recovery.stdout
        or recovery.stderr
        or _contains_semantic_jwt(recovery.stdout)
        or _contains_semantic_jwt(recovery.stderr)
    ):
        raise InfrastructureE2EError("minio_metrics_sigkill_recovery")
    _minio_metrics_token_metadata(runner, project)
    running = _compose(
        runner,
        project,
        ["ps", "--status", "running", "--quiet", "minio-metrics-token-init"],
        step="minio_metrics_no_initializer_before_cleanup",
        check=False,
    )
    if running.returncode != 0 or running.stdout or running.stderr:
        raise InfrastructureE2EError("minio_metrics_no_initializer_before_cleanup")
    cleanup_program = """
from pathlib import Path
root = Path("/run/secrets/minio-metrics")
for path in root.glob(".token.tmp.*"):
    assert path.is_file() and not path.is_symlink()
    path.unlink()
""".strip()
    cleanup = _run_token_volume_program(
        runner,
        project,
        program=cleanup_program,
        step="minio_metrics_sigkill_orphan_cleanup",
    )
    if cleanup.stdout or cleanup.stderr:
        raise InfrastructureE2EError("minio_metrics_sigkill_orphan_cleanup")
    if (
        _temporary_token_file_count(
            runner,
            project,
            step="minio_metrics_final_temp_count",
        )
        != 0
    ):
        raise InfrastructureE2EError("minio_metrics_final_temp_count")
    return {
        "status": "passed",
        "concurrent_runs": 2,
        "concurrent_successes": 2,
        "term_exit_code": term_exit,
        "term_cleanup": "passed",
        "sigkill_exit_code": kill_exit,
        "sigkill_orphan_observed": True,
        "post_sigkill_recovery": "passed",
        "cleanup_after_no_initializer": True,
        "final_temporary_file_count": 0,
    }


def _minio_metrics_consumer_container_ids(
    runner: CommandRunner,
    project: str,
) -> dict[str, str]:
    identities: dict[str, str] = {}
    for service in ("operational-metrics", "prometheus"):
        result = _compose(
            runner,
            project,
            ["ps", "--quiet", service],
            step=f"minio_metrics_consumer_identity_{service}",
        )
        if re.fullmatch(r"[0-9a-f]{64}", result.stdout) is None or result.stderr:
            raise InfrastructureE2EError("minio_metrics_consumer_identity")
        identities[service] = result.stdout
    return identities


def _wait_for_prometheus_minio_target_up(
    runner: CommandRunner,
    project: str,
    *,
    step: str,
    timeout_seconds: float = 90.0,
) -> None:
    program = """
import json
import urllib.request

payload = json.loads(
    urllib.request.urlopen(
        "http://prometheus:9090/api/v1/targets",
        timeout=5,
    ).read().decode("utf-8")
)
targets = payload.get("data", {}).get("activeTargets", [])
matched = [
    target
    for target in targets
    if isinstance(target, dict)
    and isinstance(target.get("labels"), dict)
    and target["labels"].get("job") == "minio"
]
assert len(matched) == 1
assert matched[0].get("health") == "up"
assert matched[0].get("scrapeUrl") == "https://minio:9000/minio/v2/metrics/cluster"
print("up")
""".strip()
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        result = _compose(
            runner,
            project,
            ["exec", "--no-TTY", "backend-api", "python", "-c", program],
            step=step,
            check=False,
        )
        if result.returncode == 0 and result.stdout == "up" and not result.stderr:
            return
        time.sleep(1)
    raise InfrastructureE2EError(step)


def _refresh_minio_metrics_token(
    runner: CommandRunner,
    project: str,
    *,
    previous_metadata: dict[str, object],
    previous_collector_timestamp: float,
) -> tuple[dict[str, object], float]:
    consumer_ids_before = _minio_metrics_consumer_container_ids(runner, project)
    _wait_for_prometheus_minio_target_up(
        runner,
        project,
        step="minio_metrics_refresh_prometheus_before",
    )
    _copy_minio_metrics_token(
        runner,
        project,
        destination_name=".previous-token",
        step="minio_metrics_refresh_snapshot",
    )
    time.sleep(1.1)
    result = _compose(
        runner,
        project,
        ["run", "--rm", "--no-deps", "--no-TTY", "minio-metrics-token-init"],
        step="minio_metrics_token_refresh",
    )
    if (
        result.stdout
        or result.stderr
        or _contains_semantic_jwt(result.stdout)
        or _contains_semantic_jwt(result.stderr)
    ):
        raise InfrastructureE2EError("minio_metrics_token_refresh")
    refreshed = _minio_metrics_token_metadata(runner, project)
    credential_changed = _minio_metrics_token_files_differ(
        runner,
        project,
        first_name=".previous-token",
        second_name="token",
    )
    mtime_advanced = int(refreshed["mtime_ns"]) > int(previous_metadata["mtime_ns"])
    previous_status = _minio_metrics_http_status(
        runner,
        project,
        token_name=".previous-token",
        step="minio_metrics_refresh_previous_status",
    )
    refreshed_status = _minio_metrics_http_status(
        runner,
        project,
        token_name="token",
        step="minio_metrics_refresh_current_status",
    )
    if (
        not credential_changed
        or not mtime_advanced
        or previous_status != 200
        or refreshed_status != 200
    ):
        raise InfrastructureE2EError("minio_metrics_token_refresh")
    collector_timestamp = _wait_for_minio_capacity_collector(
        runner,
        project,
        minimum_timestamp=previous_collector_timestamp,
    )
    _wait_for_prometheus_minio_target_up(
        runner,
        project,
        step="minio_metrics_refresh_prometheus_after",
    )
    consumer_ids_after = _minio_metrics_consumer_container_ids(runner, project)
    if consumer_ids_after != consumer_ids_before:
        raise InfrastructureE2EError("minio_metrics_refresh_consumer_restart")
    return (
        {
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
        collector_timestamp,
    )


def _rotated_minio_root_credentials() -> dict[str, str]:
    return {
        "MINIO_ROOT_USER": f"e2eroot{secrets.token_hex(4)}",
        "MINIO_ROOT_PASSWORD": _random_token(40),
    }


def _emergency_revoke_minio_metrics_tokens(
    runner: CommandRunner,
    project: str,
    *,
    previous_collector_timestamp: float,
) -> tuple[dict[str, object], float]:
    consumer_ids_before = _minio_metrics_consumer_container_ids(runner, project)
    _copy_minio_metrics_token(
        runner,
        project,
        destination_name=".refreshed-before-root-rotation",
        step="minio_metrics_emergency_snapshot",
    )
    runner.update_environment(_rotated_minio_root_credentials())
    _compose(
        runner,
        project,
        [
            "up",
            "--detach",
            "--no-build",
            "--no-deps",
            "--force-recreate",
            "--wait",
            "--wait-timeout",
            "180",
            "minio",
        ],
        step="minio_metrics_emergency_minio_recreate",
        timeout_seconds=240,
    )
    bootstrap = _compose(
        runner,
        project,
        ["run", "--rm", "--no-deps", "--no-TTY", "minio-bootstrap"],
        step="minio_metrics_emergency_bootstrap",
    )
    if (
        bootstrap.stdout
        or bootstrap.stderr
        or _contains_semantic_jwt(bootstrap.stdout)
        or _contains_semantic_jwt(bootstrap.stderr)
    ):
        raise InfrastructureE2EError("minio_metrics_emergency_bootstrap")
    _verify_minio_identity_reconciliation(runner, project)
    previous_status = _minio_metrics_http_status(
        runner,
        project,
        token_name=".previous-token",
        step="minio_metrics_emergency_previous_status",
    )
    refreshed_status = _minio_metrics_http_status(
        runner,
        project,
        token_name=".refreshed-before-root-rotation",
        step="minio_metrics_emergency_refreshed_status",
    )
    if previous_status != 403 or refreshed_status != 403:
        raise InfrastructureE2EError("minio_metrics_emergency_revocation")
    replacement = _compose(
        runner,
        project,
        ["run", "--rm", "--no-deps", "--no-TTY", "minio-metrics-token-init"],
        step="minio_metrics_emergency_replacement",
    )
    if (
        replacement.stdout
        or replacement.stderr
        or _contains_semantic_jwt(replacement.stdout)
        or _contains_semantic_jwt(replacement.stderr)
    ):
        raise InfrastructureE2EError("minio_metrics_emergency_replacement")
    replacement_status = _minio_metrics_http_status(
        runner,
        project,
        token_name="token",
        step="minio_metrics_emergency_replacement_status",
    )
    if replacement_status != 200:
        raise InfrastructureE2EError("minio_metrics_emergency_replacement")
    collector_timestamp = _wait_for_minio_capacity_collector(
        runner,
        project,
        minimum_timestamp=previous_collector_timestamp,
    )
    _wait_for_prometheus_minio_target_up(
        runner,
        project,
        step="minio_metrics_emergency_prometheus_recovered",
    )
    consumer_ids_after = _minio_metrics_consumer_container_ids(runner, project)
    if consumer_ids_after != consumer_ids_before:
        raise InfrastructureE2EError("minio_metrics_emergency_consumer_restart")
    _delete_private_token_snapshots(
        runner,
        project,
        names=(".previous-token", ".refreshed-before-root-rotation"),
    )
    return (
        {
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
        collector_timestamp,
    )


def _verify_minio_metrics_auth(
    runner: CommandRunner,
    project: str,
    *,
    identity_reconciliation: dict[str, object],
) -> dict[str, object]:
    initializer = _verify_minio_metrics_initializer(runner, project)
    anonymous = _verify_minio_metrics_anonymous_access_is_denied(runner, project)
    _minio_metrics_token_metadata(runner, project)
    recovered = _replace_invalid_minio_metrics_token(runner, project)
    atomic_publish = _exercise_minio_atomic_publish(runner, project)
    previous_collector_timestamp = _wait_for_minio_capacity_collector(runner, project)
    refresh, refresh_collector_timestamp = _refresh_minio_metrics_token(
        runner,
        project,
        previous_metadata=recovered,
        previous_collector_timestamp=previous_collector_timestamp,
    )
    emergency_revocation, _ = _emergency_revoke_minio_metrics_tokens(
        runner,
        project,
        previous_collector_timestamp=refresh_collector_timestamp,
    )
    return {
        "status": "passed",
        "auth_mode": "jwt_bearer_file",
        "initializer": initializer,
        "anonymous_access": anonymous,
        "atomic_publish": atomic_publish,
        "refresh": refresh,
        "emergency_revocation": emergency_revocation,
        "identity_reconciliation": identity_reconciliation,
        "collector": {
            "status": "passed",
            "component": "minio_capacity",
            "last_success_advanced": True,
        },
    }


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
            _seed_minio_identity_drift(runner, project)
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
            identity_reconciliation = _verify_minio_identity_reconciliation(
                runner,
                project,
            )
            minio_metrics_auth = _verify_minio_metrics_auth(
                runner,
                project,
                identity_reconciliation=identity_reconciliation,
            )
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
                "minio_metrics_auth": minio_metrics_auth,
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
                    "minio_metrics_auth": "passed",
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
