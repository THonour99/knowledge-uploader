"""Fail closed unless protected-release infrastructure evidence is complete."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_MAX_AGE = timedelta(hours=2)
DELIVERY_REQUIRED_FIELDS: dict[str, frozenset[str]] = {
    "webhook_configs": frozenset({"url", "url_file"}),
    "email_configs": frozenset({"to"}),
    "slack_configs": frozenset({"api_url", "api_url_file"}),
    "msteams_configs": frozenset({"webhook_url"}),
    "pagerduty_configs": frozenset({"routing_key", "routing_key_file", "service_key"}),
    "wechat_configs": frozenset({"corp_id", "api_secret", "api_secret_file"}),
}
INLINE_ALERTMANAGER_SECRET_FIELDS = frozenset(
    {
        "api_key",
        "api_secret",
        "api_url",
        "auth_password",
        "auth_secret",
        "bearer_token",
        "bot_token",
        "client_secret",
        "credentials",
        "password",
        "routing_key",
        "secret_key",
        "service_key",
        "slack_api_url",
        "token",
        "token_id",
        "token_secret",
        "url",
        "webhook_url",
    }
)
SAFE_REPLAY_TASK_QUEUES = {
    "ragflow.create_upload_task": "ragflow_queue",
    "ragflow.create_delete_task": "ragflow_queue",
}
SAFE_REPLAY_TASKS = frozenset(SAFE_REPLAY_TASK_QUEUES)
RELEASE_GIT_SHA_PATTERN = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
PROMETHEUS_VALIDATOR_IMAGE = (
    "prom/prometheus:v3.12.0"
    "@sha256:69f5241418838263316593f7274a304b095c40bcf22e57272865da91bd60a8ac"
)
ALERTMANAGER_VALIDATOR_IMAGE = (
    "prom/alertmanager:v0.28.1"
    "@sha256:27c475db5fb156cab31d5c18a4251ac7ed567746a2483ff264516437a39b15ba"
)
SUPPORTED_VALIDATOR_ARCHITECTURES = frozenset({"amd64", "arm64"})
COMPOSE_PROJECT_PATTERN = re.compile(r"[a-z0-9][a-z0-9_.-]{0,62}")
REQUIRED_INFRASTRUCTURE_RESULTS = frozenset(
    {
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


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be a JSON object")
    return value


def _load_evidence(root: Path, filename: str) -> dict[str, Any]:
    path = (root / filename).resolve()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise RuntimeError(f"evidence path escapes root: {filename}") from error
    return _mapping(json.loads(path.read_text(encoding="utf-8")), filename)


def _timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise RuntimeError(f"{label} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise RuntimeError(f"{label} must be an ISO timestamp") from error
    if parsed.tzinfo is None:
        raise RuntimeError(f"{label} must include a timezone")
    return parsed.astimezone(UTC)


def _fresh(timestamp: datetime, *, now: datetime) -> bool:
    return now - EVIDENCE_MAX_AGE <= timestamp <= now + timedelta(minutes=5)


def _is_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def _is_release_git_sha(value: object) -> bool:
    return isinstance(value, str) and RELEASE_GIT_SHA_PATTERN.fullmatch(value) is not None


def _is_sha256_image_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and value.startswith("sha256:")
        and SHA256_PATTERN.fullmatch(value.removeprefix("sha256:")) is not None
    )


def _infrastructure_e2e_errors(
    evidence: dict[str, Any],
    *,
    git_sha: str,
) -> list[str]:
    errors: list[str] = []
    _require(evidence.get("status") == "passed", "infrastructure E2E did not pass", errors)
    _require(
        evidence.get("full_compose_e2e") == "passed",
        "infrastructure full Compose E2E is missing",
        errors,
    )
    _require(
        evidence.get("source_worktree_clean") is True,
        "infrastructure E2E source worktree was not clean",
        errors,
    )
    _require(
        evidence.get("cleanup_status") == "passed",
        "infrastructure E2E cleanup did not pass",
        errors,
    )
    _require(
        str(evidence.get("architecture", "")).strip().lower() in {"arm64", "aarch64"},
        "infrastructure E2E did not run on ARM64",
        errors,
    )
    _require(_is_uuid(evidence.get("run_id")), "infrastructure run_id is invalid", errors)
    compose_project = evidence.get("compose_project")
    _require(
        isinstance(compose_project, str)
        and COMPOSE_PROJECT_PATTERN.fullmatch(compose_project) is not None,
        "infrastructure Compose project identity is invalid",
        errors,
    )
    _require(
        isinstance(evidence.get("resolved_compose_sha256"), str)
        and SHA256_PATTERN.fullmatch(str(evidence["resolved_compose_sha256"])) is not None,
        "resolved Compose digest is invalid",
        errors,
    )
    _require(
        isinstance(evidence.get("tls_certificate_sha256"), str)
        and SHA256_PATTERN.fullmatch(str(evidence["tls_certificate_sha256"])) is not None,
        "infrastructure TLS certificate digest is invalid",
        errors,
    )
    _require(
        evidence.get("rabbitmq_probe_run_id") == evidence.get("run_id"),
        "infrastructure RabbitMQ run identity is invalid",
        errors,
    )
    for image_name in ("backend", "frontend"):
        _require(
            evidence.get(f"{image_name}_image_revision") == git_sha,
            f"infrastructure {image_name} image revision mismatch",
            errors,
        )
        _require(
            _is_sha256_image_id(evidence.get(f"{image_name}_image_id")),
            f"infrastructure {image_name} image content id is invalid",
            errors,
        )
    results = evidence.get("results")
    _require(
        isinstance(results, dict)
        and all(results.get(name) == "passed" for name in REQUIRED_INFRASTRUCTURE_RESULTS),
        "infrastructure E2E is missing detailed passed results",
        errors,
    )
    service_container_ids = evidence.get("service_container_ids")
    _require(
        isinstance(service_container_ids, dict)
        and REQUIRED_SERVICE_CONTAINERS <= set(service_container_ids)
        and all(
            isinstance(service_container_ids.get(name), str)
            and SHA256_PATTERN.fullmatch(str(service_container_ids[name])) is not None
            for name in REQUIRED_SERVICE_CONTAINERS
        ),
        "infrastructure service container identities are incomplete",
        errors,
    )
    worker_queue_consumers = evidence.get("worker_queue_consumers")
    _require(
        isinstance(worker_queue_consumers, dict)
        and all(
            isinstance(worker_queue_consumers.get(queue), int)
            and not isinstance(worker_queue_consumers.get(queue), bool)
            and int(worker_queue_consumers[queue]) >= 1
            for queue in REQUIRED_WORKER_QUEUES
        ),
        "infrastructure worker queue consumers are incomplete",
        errors,
    )
    business_probe = evidence.get("business_probe")
    _require(
        isinstance(business_probe, dict)
        and business_probe.get("status") == "passed"
        and business_probe.get("email_verification_floor") == "passed"
        and business_probe.get("mock_smtp_delivery") == "passed",
        "infrastructure email verification behavior is missing",
        errors,
    )
    return errors


def _rabbitmq_replay_binding_errors(
    *,
    exhausted: dict[str, Any],
    replayed: dict[str, Any],
    resolved: dict[str, Any],
) -> list[str]:
    """Bind replay evidence to the exact safe queue and deterministic task id."""
    errors: list[str] = []
    task_name = exhausted.get("task_name")
    exhausted_queue = exhausted.get("queue_name")
    expected_queue = SAFE_REPLAY_TASK_QUEUES.get(task_name) if isinstance(task_name, str) else None
    _require(
        expected_queue is not None and exhausted_queue == expected_queue,
        "RabbitMQ exhausted task is not bound to its allowlisted queue",
        errors,
    )
    _require(
        replayed.get("queue_name") == exhausted_queue,
        "RabbitMQ replay queue does not match the exhausted queue",
        errors,
    )
    _require(
        resolved.get("queue_name") == exhausted_queue,
        "RabbitMQ resolved queue does not match the exhausted queue",
        errors,
    )

    original_task_id = exhausted.get("task_id")
    replay_task_id = replayed.get("replay_task_id")
    if (
        isinstance(exhausted_queue, str)
        and isinstance(original_task_id, str)
        and _is_uuid(original_task_id)
    ):
        expected_replay_task_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"rabbitmq-replay:{exhausted_queue}:{uuid.UUID(original_task_id)}",
            )
        )
        _require(
            replay_task_id == expected_replay_task_id,
            "RabbitMQ replay task id is not the deterministic queue/original binding",
            errors,
        )
    else:
        errors.append("RabbitMQ replay identity inputs are invalid")
    return errors


def _rabbitmq_exhaustion_errors(exhausted: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    _require(exhausted.get("result") == "dead_lettered", "exhausted task missed DLQ", errors)
    _require(
        isinstance(exhausted.get("attempts"), int)
        and not isinstance(exhausted.get("attempts"), bool)
        and exhausted.get("attempts") == 4
        and exhausted.get("retry_count") == 3
        and exhausted.get("dead_letter_reason") == "rejected"
        and exhausted.get("delivery_path") == "celery_worker_retry_exhaustion",
        "RabbitMQ exhaustion did not prove the worker's final rejected attempt",
        errors,
    )
    _require(exhausted.get("dlq_count_after") == 1, "exhausted DLQ count is invalid", errors)
    _require(
        exhausted.get("task_name") in SAFE_REPLAY_TASKS,
        "exhausted RabbitMQ task is not safely reconstructable",
        errors,
    )
    return errors


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validator_image_evidence_errors(
    evidence: dict[str, Any],
    *,
    name: str,
    expected_reference: str,
) -> list[str]:
    errors: list[str] = []
    expected_digest = expected_reference.rsplit("@", maxsplit=1)[1]
    prefix = f"{name}_"
    _require(
        evidence.get(f"{prefix}image") == expected_reference,
        f"{name} validator image reference is not the approved manifest-list digest",
        errors,
    )
    _require(
        evidence.get(f"{prefix}manifest_list_digest") == expected_digest,
        f"{name} validator manifest-list digest does not match the approved digest",
        errors,
    )
    _require(
        _is_sha256_image_id(evidence.get(f"{prefix}image_id")),
        f"{name} validator image id is invalid",
        errors,
    )
    _require(
        evidence.get(f"{prefix}image_os") == "linux",
        f"{name} validator image is not Linux",
        errors,
    )
    image_architecture = evidence.get(f"{prefix}image_architecture")
    docker_architecture = evidence.get(f"{prefix}docker_architecture")
    _require(
        image_architecture in SUPPORTED_VALIDATOR_ARCHITECTURES,
        f"{name} validator image architecture is unsupported",
        errors,
    )
    _require(
        docker_architecture == image_architecture,
        f"{name} validator image architecture does not match the Docker daemon",
        errors,
    )
    return errors


def _promtool_evidence_errors(
    evidence: dict[str, Any],
    *,
    alertmanager_config: Path,
) -> list[str]:
    errors: list[str] = []
    expected_hashes = {
        "prometheus_config_sha256": _sha256_path(ROOT / "ops" / "observability" / "prometheus.yml"),
        "prometheus_rules_sha256": _sha256_path(ROOT / "ops" / "observability" / "alerts.yml"),
        "alertmanager_config_sha256": _sha256_path(alertmanager_config),
    }
    _require(evidence.get("prometheus_config") == "passed", "promtool config missing", errors)
    _require(evidence.get("prometheus_rules") == "passed", "promtool rules missing", errors)
    _require(evidence.get("alertmanager_config") == "passed", "amtool config missing", errors)
    for field, expected in expected_hashes.items():
        _require(
            evidence.get(field) == expected,
            f"promtool evidence {field} does not match the validated file",
            errors,
        )
    errors.extend(
        _validator_image_evidence_errors(
            evidence,
            name="prometheus",
            expected_reference=PROMETHEUS_VALIDATOR_IMAGE,
        )
    )
    errors.extend(
        _validator_image_evidence_errors(
            evidence,
            name="alertmanager",
            expected_reference=ALERTMANAGER_VALIDATOR_IMAGE,
        )
    )
    _require(
        evidence.get("prometheus_docker_architecture")
        == evidence.get("alertmanager_docker_architecture"),
        "validator images were not executed on one Docker architecture",
        errors,
    )
    return errors


def _safe_offsite_uri(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    return (
        parsed.scheme in {"s3", "https"}
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
    )


def _resolved_backend_api_service() -> dict[str, Any]:
    try:
        completed = subprocess.run(
            ["docker", "compose", "config", "--format", "json"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        config = _mapping(json.loads(completed.stdout), "resolved Compose config")
        services = _mapping(config.get("services"), "resolved Compose services")
        return _mapping(services.get("backend-api"), "resolved backend-api service")
    except (OSError, subprocess.SubprocessError, ValueError, RuntimeError) as error:
        raise RuntimeError("could not resolve Docker Compose backend service") from error


def _backend_api_hosts(backend: dict[str, Any]) -> set[str]:
    ports = backend.get("ports")
    if not isinstance(ports, list):
        return set()
    return {
        str(port.get("host_ip", ""))
        for port in ports
        if isinstance(port, dict) and port.get("target") in {8000, "8000"}
    }


def _resolved_backend_api_hosts() -> set[str]:
    return _backend_api_hosts(_resolved_backend_api_service())


def _backend_api_environment(backend: dict[str, Any]) -> dict[str, str]:
    raw_environment = backend.get("environment")
    if not isinstance(raw_environment, dict):
        raise RuntimeError("resolved backend-api environment is invalid")
    return {str(key): str(value) for key, value in raw_environment.items() if value is not None}


def _email_delivery_evidence_errors(evidence: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    _require(evidence.get("status") == "passed", "email delivery test did not pass", errors)
    _require(
        evidence.get("registration_delivery") == "passed",
        "registration email was not delivered to the test inbox",
        errors,
    )
    _require(
        evidence.get("password_reset_delivery") == "passed",
        "password reset email was not delivered to the test inbox",
        errors,
    )
    registration_message_id = evidence.get("registration_message_id")
    reset_message_id = evidence.get("password_reset_message_id")
    _require(
        isinstance(registration_message_id, str) and bool(registration_message_id.strip()),
        "registration email message id is missing",
        errors,
    )
    _require(
        isinstance(reset_message_id, str) and bool(reset_message_id.strip()),
        "password reset email message id is missing",
        errors,
    )
    _require(
        registration_message_id != reset_message_id,
        "registration/password reset evidence reused one message id",
        errors,
    )
    _require(
        evidence.get("persistent_message") is True,
        "email broker message was not persistent",
        errors,
    )
    _require(
        evidence.get("broker_expiry_at_or_before_token_expiry") is True,
        "email broker expiry exceeded the auth token expiry",
        errors,
    )
    _require(
        evidence.get("publisher_confirm") == "passed",
        "email publisher confirm was not observed",
        errors,
    )
    _require(
        evidence.get("encrypted_envelope_observed") is True,
        "email broker envelope encryption was not observed",
        errors,
    )
    _require(
        evidence.get("plaintext_token_observed") is False,
        "plaintext auth token was observed in broker evidence",
        errors,
    )
    _require(
        evidence.get("dlq_plaintext_token_observed") is False,
        "plaintext auth token was observed in a DLQ",
        errors,
    )
    _require(
        evidence.get("publish_failure_public_response_indistinguishable") is True,
        "email publisher outage leaked account state through a public response",
        errors,
    )
    _require(
        evidence.get("publish_failure_public_statuses")
        == {
            "register": 201,
            "resend_verification": 200,
            "forgot_password": 200,
        },
        "email publisher outage public statuses do not match the generic accepted contract",
        errors,
    )
    _require(
        evidence.get("publish_failure_metric_recorded") is True,
        "email publisher outage was not recorded in the bounded operational metric",
        errors,
    )
    _require(
        evidence.get("retry_issued_fresh_token") is True,
        "email publisher recovery did not issue a fresh token",
        errors,
    )
    _require(
        evidence.get("smtp_delivery_semantics") == "at_most_once_attempt",
        "SMTP ambiguity policy is not the reviewed early-ack contract",
        errors,
    )
    forbidden_fields = {"token", "raw_token", "email_body", "broker_payload", "ciphertext"}
    _require(
        not any(str(key).strip().lower() in forbidden_fields for key in evidence),
        "email delivery evidence contains a forbidden sensitive field",
        errors,
    )
    return errors


def _require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def _alertmanager_receiver_errors(config_path: Path) -> list[str]:
    errors: list[str] = []
    loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config = _mapping(loaded, "Alertmanager config")
    route = _mapping(config.get("route"), "Alertmanager route")
    receivers = config.get("receivers")
    if not isinstance(receivers, list):
        return [*errors, "Alertmanager receivers must be a list"]
    receivers_by_name: dict[str, dict[str, Any]] = {}
    for raw_receiver in receivers:
        receiver = _mapping(raw_receiver, "Alertmanager receiver")
        name = receiver.get("name")
        if isinstance(name, str) and name.strip():
            receivers_by_name[name] = receiver

    route_receivers = _collect_route_receivers(route, inherited_receiver=None, errors=errors)
    for route_receiver in route_receivers:
        selected = receivers_by_name.get(route_receiver)
        if selected is None:
            errors.append(f"Alertmanager route receiver does not exist: {route_receiver}")
            continue
        normalized_name = route_receiver.strip().lower()
        _require(
            all(marker not in normalized_name for marker in ("blackhole", "null", "discard")),
            f"Alertmanager route points to a blackhole receiver: {route_receiver}",
            errors,
        )
        _require(
            _receiver_has_delivery(selected),
            f"Alertmanager route receiver has no valid delivery config: {route_receiver}",
            errors,
        )
    return errors


def _alertmanager_inline_secret_errors(config_path: Path) -> list[str]:
    loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    errors: list[str] = []

    def walk(value: object, path: tuple[str, ...]) -> None:
        if isinstance(value, dict):
            for raw_key, child in value.items():
                key = str(raw_key).strip().lower()
                child_path = (*path, key)
                if (
                    key in INLINE_ALERTMANAGER_SECRET_FIELDS
                    and not key.endswith("_file")
                    and child not in (None, "")
                ):
                    errors.append(
                        "Alertmanager evidence config contains inline secret field: "
                        + ".".join(child_path)
                    )
                walk(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, (*path, str(index)))

    walk(loaded, ())
    return errors


def _receiver_has_delivery(receiver: dict[str, Any]) -> bool:
    for config_name, required_fields in DELIVERY_REQUIRED_FIELDS.items():
        deliveries = receiver.get(config_name)
        if not isinstance(deliveries, list):
            continue
        for delivery in deliveries:
            if not isinstance(delivery, dict):
                continue
            if any(bool(delivery.get(field)) for field in required_fields):
                return True
    return False


def _collect_route_receivers(
    route: dict[str, Any],
    *,
    inherited_receiver: str | None,
    errors: list[str],
) -> set[str]:
    raw_receiver = route.get("receiver", inherited_receiver)
    if not isinstance(raw_receiver, str) or not raw_receiver.strip():
        errors.append("Alertmanager route has no receiver and cannot inherit one")
        receiver = inherited_receiver
    else:
        receiver = raw_receiver.strip()
    selected = {receiver} if receiver is not None else set()
    child_routes = route.get("routes", [])
    if child_routes is None:
        child_routes = []
    if not isinstance(child_routes, list):
        errors.append("Alertmanager child routes must be a list")
        return selected
    for child in child_routes:
        if not isinstance(child, dict):
            errors.append("Alertmanager child route must be an object")
            continue
        selected.update(
            _collect_route_receivers(
                child,
                inherited_receiver=receiver,
                errors=errors,
            )
        )
    return selected


def _validate_evidence_identity(
    evidence: dict[str, Any],
    *,
    filename: str,
    git_sha: str,
    environment: str,
    now: datetime,
    errors: list[str],
) -> None:
    _require(evidence.get("git_sha") == git_sha, f"{filename} git_sha mismatch", errors)
    _require(
        evidence.get("environment") == environment,
        f"{filename} environment mismatch",
        errors,
    )
    generated_at = _timestamp(evidence.get("generated_at"), f"{filename} generated_at")
    _require(
        _fresh(generated_at, now=now),
        f"{filename} evidence is stale or from the future",
        errors,
    )


def check_contract() -> list[str]:
    errors: list[str] = []
    compose = _mapping(
        yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8")),
        "Compose config",
    )
    observability = _mapping(
        yaml.safe_load((ROOT / "docker-compose.observability.yml").read_text(encoding="utf-8")),
        "observability Compose config",
    )
    prometheus = _mapping(
        yaml.safe_load((ROOT / "ops/observability/prometheus.yml").read_text(encoding="utf-8")),
        "Prometheus config",
    )
    compose_services = _mapping(compose.get("services"), "Compose services")
    backend_api = _mapping(compose_services.get("backend-api"), "backend-api service")
    backend_build = _mapping(backend_api.get("build"), "backend-api build")
    backend_ports = backend_api.get("ports")
    observability_services = _mapping(
        observability.get("services"),
        "observability services",
    )
    prometheus_service = _mapping(
        observability_services.get("prometheus"),
        "Prometheus service",
    )
    prometheus_ports = prometheus_service.get("ports")
    _require(
        isinstance(backend_ports, list)
        and "${BACKEND_API_HOST:-127.0.0.1}:${BACKEND_API_PORT:-18000}:8000" in backend_ports,
        "backend API default host binding is not loopback",
        errors,
    )
    _require(
        backend_build.get("target") == "${BACKEND_BUILD_TARGET:-runtime}",
        "backend image does not default to the runtime target",
        errors,
    )
    _require(
        isinstance(prometheus_ports, list)
        and "127.0.0.1:${PROMETHEUS_HOST_PORT:-19090}:9090" in prometheus_ports,
        "Prometheus host binding is not loopback",
        errors,
    )
    _require(
        "alertmanager:9093" in _prometheus_alertmanager_targets(prometheus),
        "Prometheus has no Alertmanager target",
        errors,
    )
    errors.extend(_topology_contract_errors(ROOT / "backend/app/workers/rabbitmq_topology.py"))
    return errors


def _prometheus_alertmanager_targets(config: dict[str, Any]) -> set[str]:
    alerting = config.get("alerting")
    if not isinstance(alerting, dict):
        return set()
    managers = alerting.get("alertmanagers")
    if not isinstance(managers, list):
        return set()
    targets: set[str] = set()
    for manager in managers:
        if not isinstance(manager, dict):
            continue
        static_configs = manager.get("static_configs")
        if not isinstance(static_configs, list):
            continue
        for static_config in static_configs:
            if not isinstance(static_config, dict):
                continue
            raw_targets = static_config.get("targets")
            if isinstance(raw_targets, list):
                targets.update(target for target in raw_targets if isinstance(target, str))
    return targets


def _topology_contract_errors(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    expected_queues = {
        "document_queue",
        "ai_queue",
        "ragflow_queue",
        "notification_queue",
    }
    declared_queues: set[str] = set()
    has_dead_letter_exchange = False
    has_dead_letter_routing = False
    has_dlq_suffix = False
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "TASK_QUEUE_NAMES"
            and isinstance(node.value, ast.Tuple)
        ):
            declared_queues = {
                element.value
                for element in node.value.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            }
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "TASK_QUEUE_NAMES":
                    if isinstance(node.value, ast.Tuple):
                        declared_queues = {
                            element.value
                            for element in node.value.elts
                            if isinstance(element, ast.Constant) and isinstance(element.value, str)
                        }
        if isinstance(node, ast.Dict):
            keys = {
                key.value
                for key in node.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
            has_dead_letter_exchange |= "x-dead-letter-exchange" in keys
            has_dead_letter_routing |= "x-dead-letter-routing-key" in keys
        if isinstance(node, ast.JoinedStr):
            has_dlq_suffix |= any(
                isinstance(value, ast.Constant)
                and isinstance(value.value, str)
                and ".dlq" in value.value
                for value in node.values
            )
    errors: list[str] = []
    _require(declared_queues == expected_queues, "RabbitMQ task queue set is incomplete", errors)
    _require(
        has_dead_letter_exchange and has_dead_letter_routing,
        "RabbitMQ task queues have no dead-letter exchange/routing contract",
        errors,
    )
    _require(has_dlq_suffix, "RabbitMQ dead-letter queues are not declared", errors)
    return errors


def check_evidence(
    *,
    evidence_root: Path,
    alertmanager_config: Path,
    backend_api_host: str,
    git_sha: str,
    environment: str,
) -> list[str]:
    errors = check_contract()
    now = datetime.now(UTC)
    _require(
        _is_release_git_sha(git_sha),
        "protected release requires a full 40- or 64-character git SHA",
        errors,
    )
    _require(
        backend_api_host in {"127.0.0.1", "::1", "localhost"},
        "BACKEND_API_HOST must be loopback in a protected environment",
        errors,
    )
    resolved_backend = _resolved_backend_api_service()
    resolved_backend_hosts = _backend_api_hosts(resolved_backend)
    _require(
        bool(resolved_backend_hosts)
        and resolved_backend_hosts <= {"127.0.0.1", "::1", "localhost"},
        "resolved backend API port is not bound exclusively to loopback",
        errors,
    )
    _require(
        backend_api_host in resolved_backend_hosts,
        "BACKEND_API_HOST does not match the resolved Compose binding",
        errors,
    )
    backend_environment = _backend_api_environment(resolved_backend)
    _require(
        bool(backend_environment.get("SMTP_HOST", "").strip()),
        "SMTP_HOST is empty in the resolved protected Compose service",
        errors,
    )
    _require(
        bool(
            backend_environment.get("SMTP_FROM", "").strip()
            or backend_environment.get("SMTP_USER", "").strip()
        ),
        "SMTP_FROM and SMTP_USER are both empty in the resolved protected Compose service",
        errors,
    )
    _require(
        backend_environment.get("REQUIRE_EMAIL_VERIFICATION", "").strip().lower()
        in {"1", "true", "yes", "on"},
        "REQUIRE_EMAIL_VERIFICATION is not enabled in protected Compose",
        errors,
    )
    config_path = alertmanager_config.resolve()
    expected_config_path = (evidence_root / "alertmanager.yml").resolve()
    _require(
        config_path == expected_config_path,
        "Alertmanager config must be the copy inside the evidence bundle",
        errors,
    )
    _require(
        config_path.name != "alertmanager.example.yml",
        "example Alertmanager blackhole config is forbidden",
        errors,
    )
    errors.extend(_alertmanager_receiver_errors(config_path))
    errors.extend(_alertmanager_inline_secret_errors(config_path))

    evidence_filenames = (
        "alertmanager-notification.json",
        "dr-release.json",
        "rabbitmq-dlq-replay.json",
        "email-delivery.json",
        "promtool.json",
        "infrastructure-e2e.json",
        "dgx-spark-evidence.json",
    )
    evidence_by_name = {
        filename: _load_evidence(evidence_root, filename) for filename in evidence_filenames
    }
    for filename, evidence in evidence_by_name.items():
        _validate_evidence_identity(
            evidence,
            filename=filename,
            git_sha=git_sha,
            environment=environment,
            now=now,
            errors=errors,
        )

    alert = evidence_by_name["alertmanager-notification.json"]
    _require(alert.get("status") == "passed", "Alertmanager test did not pass", errors)
    _require(bool(alert.get("delivery_id")), "Alertmanager delivery id is missing", errors)
    firing_at = _timestamp(alert.get("firing_at"), "alert firing_at")
    resolved_at = _timestamp(alert.get("resolved_at"), "alert resolved_at")
    _require(resolved_at >= firing_at, "Alertmanager resolved precedes firing", errors)
    _require(_fresh(firing_at, now=now), "Alertmanager firing event is stale", errors)
    _require(_fresh(resolved_at, now=now), "Alertmanager resolved event is stale", errors)

    dr = evidence_by_name["dr-release.json"]
    pitr = _mapping(dr.get("postgres_pitr"), "postgres_pitr")
    full = _mapping(dr.get("full_backup"), "full_backup")
    object_store = _mapping(dr.get("object_store"), "object_store")
    key = _mapping(dr.get("key_recovery"), "key_recovery")
    drill = _mapping(dr.get("restore_drill"), "restore_drill")
    _require(pitr.get("enabled") is True, "continuous WAL/PITR is not enabled", errors)
    _require(
        now - timedelta(hours=1)
        <= _timestamp(pitr.get("last_archived_at"), "last_archived_at")
        <= now + timedelta(minutes=5),
        "last archived WAL is outside the one-hour/future tolerance",
        errors,
    )
    _require(full.get("encrypted") is True, "full backup is not encrypted", errors)
    _require(full.get("immutable") is True, "off-site backup is not immutable", errors)
    _require(
        _safe_offsite_uri(full.get("offsite_uri")),
        "off-site URI is invalid",
        errors,
    )
    _require(
        _timestamp(full.get("retention_until"), "retention_until") >= now + timedelta(days=30),
        "backup retention is shorter than 30 days",
        errors,
    )
    _require(object_store.get("versioning_enabled") is True, "MinIO versioning is off", errors)
    _require(
        object_store.get("replication_enabled") is True
        or object_store.get("coordinated_snapshot") is True,
        "MinIO has neither replication nor a coordinated snapshot",
        errors,
    )
    _require(
        dr.get("database_restore_point") == dr.get("object_restore_point")
        and bool(dr.get("database_restore_point")),
        "database/object restore points are not paired",
        errors,
    )
    _require(bool(key.get("key_version")), "encryption key version is missing", errors)
    _require(key.get("decrypt_validation") == "passed", "secret decrypt validation failed", errors)
    _require(key.get("plaintext_emitted") is False, "secret validation emitted plaintext", errors)
    _require(drill.get("status") == "passed", "isolated restore drill did not pass", errors)
    _require(drill.get("main_chain_smoke") == "passed", "restore main-chain smoke missing", errors)
    _require(int(drill.get("missing_objects", -1)) == 0, "restore has missing objects", errors)
    _require(int(drill.get("orphaned_objects", -1)) == 0, "restore has orphaned objects", errors)
    _require(
        float(drill.get("rpo_seconds", float("inf"))) <= float(drill.get("rpo_target_seconds", -1)),
        "restore RPO target missed",
        errors,
    )
    _require(
        float(drill.get("rto_seconds", float("inf"))) <= float(drill.get("rto_target_seconds", -1)),
        "restore RTO target missed",
        errors,
    )
    _require(
        now - timedelta(days=90)
        <= _timestamp(drill.get("completed_at"), "restore completed_at")
        <= now + timedelta(minutes=5),
        "restore drill is outside the quarterly/future tolerance",
        errors,
    )

    replay = evidence_by_name["rabbitmq-dlq-replay.json"]
    _require(replay.get("status") == "passed", "RabbitMQ safe replay test did not pass", errors)
    success = _mapping(replay.get("success"), "RabbitMQ success probe")
    intermediate = _mapping(
        replay.get("intermediate_retry"),
        "RabbitMQ intermediate retry probe",
    )
    exhausted = _mapping(replay.get("exhausted"), "RabbitMQ exhausted probe")
    replayed = _mapping(replay.get("replay"), "RabbitMQ replay stage")
    resolved = _mapping(replay.get("resolved"), "RabbitMQ resolved stage")
    probe_run_id = replay.get("probe_run_id")
    _require(_is_uuid(probe_run_id), "RabbitMQ probe run id is invalid", errors)
    for stage_name, stage in (
        ("success", success),
        ("intermediate_retry", intermediate),
        ("exhausted", exhausted),
    ):
        _require(_is_uuid(stage.get("task_id")), f"{stage_name} task id is invalid", errors)
        _require(
            stage.get("correlation_id") == stage.get("task_id"),
            f"{stage_name} correlation id does not match task id",
            errors,
        )
        _require(
            stage.get("probe_run_id") == probe_run_id,
            f"{stage_name} does not belong to the same RabbitMQ probe run",
            errors,
        )
        _require(
            isinstance(stage.get("task_name"), str) and bool(str(stage["task_name"]).strip()),
            f"{stage_name} task name is missing",
            errors,
        )
    task_ids = {
        success.get("task_id"),
        intermediate.get("task_id"),
        exhausted.get("task_id"),
    }
    _require(len(task_ids) == 3, "RabbitMQ probe stage task IDs are not unique", errors)
    _require(success.get("result") == "passed", "RabbitMQ success probe failed", errors)
    _require(
        success.get("dlq_count_after") == 0,
        "successful RabbitMQ task entered a DLQ",
        errors,
    )
    _require(
        intermediate.get("result") == "passed"
        and isinstance(intermediate.get("retries_observed"), int)
        and not isinstance(intermediate.get("retries_observed"), bool)
        and int(intermediate["retries_observed"]) >= 1,
        "RabbitMQ intermediate retry was not observed",
        errors,
    )
    _require(
        intermediate.get("dlq_count_during_retry") == 0,
        "RabbitMQ intermediate retry entered a DLQ",
        errors,
    )
    errors.extend(_rabbitmq_exhaustion_errors(exhausted))
    _require(
        replayed.get("task_name") in SAFE_REPLAY_TASKS,
        "RabbitMQ replay task is not whitelisted",
        errors,
    )
    _require(
        replayed.get("probe_run_id") == probe_run_id
        and replayed.get("task_name") == exhausted.get("task_name"),
        "RabbitMQ replay task/run identity does not match exhaustion",
        errors,
    )
    _require(
        replayed.get("original_task_id") == exhausted.get("task_id")
        and replayed.get("original_correlation_id") == exhausted.get("correlation_id"),
        "RabbitMQ replay is not bound to the exhausted task",
        errors,
    )
    _require(_is_uuid(replayed.get("replay_task_id")), "replay task id is invalid", errors)
    _require(
        replayed.get("replay_correlation_id") == replayed.get("replay_task_id"),
        "replay correlation id does not match replay task id",
        errors,
    )
    _require(
        replayed.get("raw_payload_copied") is False,
        "RabbitMQ replay copied raw payload",
        errors,
    )
    _require(
        replayed.get("persistent_message") is True,
        "RabbitMQ clean-room replay was not published persistently",
        errors,
    )
    _require(
        replayed.get("replay_policy") == "clean_room_allowlist_only",
        "RabbitMQ replay policy is not the reviewed clean-room allowlist",
        errors,
    )
    _require(_is_uuid(replayed.get("audit_log_id")), "RabbitMQ replay audit id missing", errors)
    _require(replayed.get("result") == "queued", "RabbitMQ replay was not queued", errors)
    _require(
        replayed.get("replay_task_id") not in task_ids,
        "RabbitMQ replay task id reused a probe stage id",
        errors,
    )
    errors.extend(
        _rabbitmq_replay_binding_errors(
            exhausted=exhausted,
            replayed=replayed,
            resolved=resolved,
        )
    )
    _require(
        resolved.get("original_task_id") == exhausted.get("task_id")
        and resolved.get("replay_task_id") == replayed.get("replay_task_id")
        and resolved.get("replay_correlation_id") == replayed.get("replay_correlation_id")
        and resolved.get("audit_log_id") == replayed.get("audit_log_id"),
        "RabbitMQ resolved stage identity does not match replay",
        errors,
    )
    _require(
        resolved.get("probe_run_id") == probe_run_id,
        "RabbitMQ resolved stage does not belong to the probe run",
        errors,
    )
    _require(resolved.get("result") == "passed", "RabbitMQ replay did not resolve", errors)
    _require(resolved.get("dlq_count_after") == 0, "RabbitMQ DLQ was not drained", errors)
    _require(
        resolved.get("domain_state") == "passed",
        "RabbitMQ replay did not restore domain state",
        errors,
    )

    errors.extend(_email_delivery_evidence_errors(evidence_by_name["email-delivery.json"]))

    for filename in ("promtool.json", "infrastructure-e2e.json", "dgx-spark-evidence.json"):
        evidence = evidence_by_name[filename]
        _require(evidence.get("status") == "passed", f"{filename} did not pass", errors)
    promtool = evidence_by_name["promtool.json"]
    errors.extend(_promtool_evidence_errors(promtool, alertmanager_config=config_path))
    infrastructure = evidence_by_name["infrastructure-e2e.json"]
    errors.extend(_infrastructure_e2e_errors(infrastructure, git_sha=git_sha))
    _require(
        infrastructure.get("rabbitmq_probe_run_id") == replay.get("probe_run_id"),
        "infrastructure and RabbitMQ evidence do not share one run identity",
        errors,
    )
    _require(
        infrastructure.get("rabbitmq_evidence_sha256")
        == _sha256_path(evidence_root / "rabbitmq-dlq-replay.json"),
        "infrastructure evidence is not bound to rabbitmq-dlq-replay.json",
        errors,
    )
    dgx = evidence_by_name["dgx-spark-evidence.json"]
    _require(dgx.get("architecture") in {"arm64", "aarch64"}, "DGX evidence is not ARM64", errors)
    _require(
        dgx.get("docker_architecture") in {"arm64", "aarch64"},
        "DGX Docker daemon evidence is not ARM64",
        errors,
    )
    _require(
        dgx.get("backend_image_revision") == git_sha
        and dgx.get("frontend_image_revision") == git_sha,
        "DGX image revision labels do not match the release SHA",
        errors,
    )
    _require(
        isinstance(dgx.get("backend_image_id"), str)
        and str(dgx["backend_image_id"]).startswith("sha256:")
        and isinstance(dgx.get("frontend_image_id"), str)
        and str(dgx["frontend_image_id"]).startswith("sha256:"),
        "DGX image content IDs are missing",
        errors,
    )
    _require(dgx.get("full_compose_e2e") == "passed", "DGX full Compose E2E missing", errors)
    _require(
        dgx.get("run_id") == infrastructure.get("run_id")
        and dgx.get("compose_project") == infrastructure.get("compose_project")
        and dgx.get("resolved_compose_sha256") == infrastructure.get("resolved_compose_sha256"),
        "DGX proof does not share the infrastructure run/Compose identity",
        errors,
    )
    _require(
        dgx.get("backend_image_id") == infrastructure.get("backend_image_id")
        and dgx.get("frontend_image_id") == infrastructure.get("frontend_image_id"),
        "DGX proof image content IDs do not match infrastructure E2E",
        errors,
    )
    infrastructure_path = evidence_root / "infrastructure-e2e.json"
    _require(
        dgx.get("compose_e2e_evidence_sha256") == _sha256_path(infrastructure_path),
        "DGX proof is not bound to infrastructure-e2e.json",
        errors,
    )
    return errors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract-only", action="store_true")
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument("--alertmanager-config", type=Path)
    parser.add_argument("--backend-api-host", default="127.0.0.1")
    parser.add_argument("--git-sha")
    parser.add_argument("--environment", choices=("staging", "production"))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.contract_only:
        errors = check_contract()
    else:
        if (
            args.evidence_dir is None
            or args.alertmanager_config is None
            or args.git_sha is None
            or args.environment is None
        ):
            sys.stderr.write(
                "ERROR: protected gate requires evidence dir, Alertmanager config, "
                "git SHA, and environment\n"
            )
            return 2
        errors = check_evidence(
            evidence_root=args.evidence_dir.resolve(),
            alertmanager_config=args.alertmanager_config,
            backend_api_host=args.backend_api_host,
            git_sha=args.git_sha,
            environment=args.environment,
        )
    if errors:
        sys.stderr.write("\n".join(f"ERROR: {error}" for error in errors) + "\n")
        return 1
    sys.stdout.write("protected release gate passed\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
