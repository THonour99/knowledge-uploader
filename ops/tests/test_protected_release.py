from __future__ import annotations

import importlib.util
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

TEST_GIT_SHA = "a" * 40
MINIO_MC_IMAGE = (
    "minio/mc:RELEASE.2024-04-18T16-45-29Z"
    "@sha256:5a84109d6b29bab96c3122e4a7ba888fbf48d4cdc83bc8bf88e3a7ac67b970b8"
)
MINIO_MC_TAG_ONLY = "minio/mc:RELEASE.2024-04-18T16-45-29Z"
BACKEND_MC_ARG = f"ARG MINIO_MC_IMAGE={MINIO_MC_IMAGE}".encode()
OPS_MC_ARG = f"ARG MC_IMAGE={MINIO_MC_IMAGE}".encode()
WORKFLOW_MC_ARG = f'--build-arg MINIO_MC_IMAGE="{MINIO_MC_IMAGE}"'.encode()
WORKFLOW_MC_TAG_ONLY_ARG = f'--build-arg MINIO_MC_IMAGE="{MINIO_MC_TAG_ONLY}"'.encode()
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


def _load_gate() -> ModuleType:
    gate_path = Path(__file__).parents[2] / "scripts/check_protected_release.py"
    spec = importlib.util.spec_from_file_location("check_protected_release", gate_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load protected release gate")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _valid_dr_receipt(gate: ModuleType, *, now: datetime) -> dict[str, object]:
    policy_payload = (
        Path(__file__).parents[2] / "ops/policies/dr-release-policy.json"
    ).read_bytes()
    return {
        "backup_id": "20260716T000000Z-aabbccdd",
        "backup_manifest_sha256": "4" * 64,
        "restore_evidence_sha256": "5" * 64,
        "restore_started_at": (now - timedelta(minutes=20)).isoformat(),
        "restore_completed_at": (now - timedelta(minutes=18)).isoformat(),
        "rpo_seconds": 60,
        "rpo_target_seconds": 300,
        "rto_seconds": 120,
        "rto_target_seconds": 600,
        "policy_sha256": gate._sha256_bytes(policy_payload),
        "alembic_revision": "20260716o001",
        "database_tables_sha256": "6" * 64,
        "minio_missing_objects": 0,
        "minio_orphan_objects": 0,
        "minio_mismatched_objects": 0,
        "recovery_pair_id": "recovery-pair-001",
        "postgres_restore_point_sha256": "d" * 64,
        "minio_restore_point_sha256": "e" * 64,
        "postgres_pitr_enabled": True,
        "last_archived_at": (now - timedelta(minutes=15)).isoformat(),
        "full_backup_encrypted": True,
        "full_backup_immutable": True,
        "offsite_location_sha256": "7" * 64,
        "retention_until": (now + timedelta(days=31)).isoformat(),
        "minio_versioning_enabled": True,
        "minio_replication_enabled": True,
        "coordinated_snapshot": False,
        "key_version_sha256": "8" * 64,
        "decrypt_validation": "passed",
        "plaintext_emitted": False,
        "main_chain_smoke": "passed",
        "cleanup_validation": "passed",
    }


def test_dr_release_policy_allows_stricter_targets() -> None:
    gate = _load_gate()
    now = datetime.now(UTC)
    policy_payload = (
        Path(__file__).parents[2] / "ops/policies/dr-release-policy.json"
    ).read_bytes()
    receipt = _valid_dr_receipt(gate, now=now)
    receipt.update(
        {
            "rpo_target_seconds": 120,
            "rto_target_seconds": 300,
        }
    )

    assert (
        gate._dr_release_evidence_errors(
            receipt,
            now=now,
            policy=gate._load_dr_release_policy(policy_payload),
            policy_sha256=gate._sha256_bytes(policy_payload),
        )
        == []
    )


@pytest.mark.parametrize(
    ("field", "value", "expected_error"),
    (
        ("policy_sha256", "f" * 64, "policy digest"),
        ("rpo_target_seconds", 301, "RPO exceeds the repository policy"),
        ("rto_target_seconds", 601, "RTO exceeds the repository policy"),
        ("rpo_seconds", 301, "RPO exceeds the repository policy"),
        ("rto_seconds", 601, "RTO exceeds the repository policy"),
    ),
)
def test_dr_release_policy_rejects_digest_or_limit_widening(
    field: str,
    value: object,
    expected_error: str,
) -> None:
    gate = _load_gate()
    now = datetime.now(UTC)
    policy_payload = (
        Path(__file__).parents[2] / "ops/policies/dr-release-policy.json"
    ).read_bytes()
    receipt = _valid_dr_receipt(gate, now=now)
    receipt[field] = value

    errors = gate._dr_release_evidence_errors(
        receipt,
        now=now,
        policy=gate._load_dr_release_policy(policy_payload),
        policy_sha256=gate._sha256_bytes(policy_payload),
    )

    assert any(expected_error in error for error in errors)


@pytest.mark.parametrize(
    ("config", "expected_error"),
    (
        (
            "route:\n  receiver: ops\nreceivers:\n  - name: ops\n"
            "# webhook_configs:\n#   - url: https://example.invalid\n",
            "no valid delivery config",
        ),
        (
            "route:\n  receiver: ops\nreceivers:\n  - name: ops\n    webhook_configs: []\n",
            "no valid delivery config",
        ),
        (
            "route:\n  receiver: missing\nreceivers:\n  - name: ops\n"
            "    webhook_configs:\n      - url: https://alerts.example.test\n",
            "does not exist",
        ),
        (
            "route:\n  receiver: local-blackhole\nreceivers:\n"
            "  - name: local-blackhole\n    webhook_configs:\n"
            "      - url: https://alerts.example.test\n",
            "blackhole receiver",
        ),
    ),
)
def test_alertmanager_gate_rejects_false_receivers(
    tmp_path: Path,
    config: str,
    expected_error: str,
) -> None:
    gate = _load_gate()
    config_path = tmp_path / "alertmanager.yml"
    config_path.write_text(config, encoding="utf-8")

    errors = gate._alertmanager_receiver_errors(config_path)

    assert any(expected_error in error for error in errors)


def test_alertmanager_gate_accepts_routed_nonempty_receiver(tmp_path: Path) -> None:
    gate = _load_gate()
    config_path = tmp_path / "alertmanager.yml"
    config_path.write_text(
        "route:\n  receiver: incident-webhook\nreceivers:\n"
        "  - name: incident-webhook\n    webhook_configs:\n"
        "      - url_file: /run/secrets/alert-webhook-url\n",
        encoding="utf-8",
    )

    assert gate._alertmanager_receiver_errors(config_path) == []


def test_alertmanager_gate_rejects_blackhole_child_route(tmp_path: Path) -> None:
    gate = _load_gate()
    config_path = tmp_path / "alertmanager.yml"
    config_path.write_text(
        "route:\n  receiver: incident-webhook\n  routes:\n"
        "    - receiver: child-blackhole\n      matchers: [severity=warning]\n"
        "receivers:\n"
        "  - name: incident-webhook\n    webhook_configs:\n"
        "      - url_file: /run/secrets/alert-webhook-url\n"
        "  - name: child-blackhole\n    webhook_configs:\n"
        "      - url: https://alerts.example.test\n",
        encoding="utf-8",
    )

    errors = gate._alertmanager_receiver_errors(config_path)

    assert any("blackhole receiver" in error for error in errors)


def test_contract_does_not_accept_expected_strings_from_comments(tmp_path: Path) -> None:
    gate = _load_gate()
    (tmp_path / "ops/observability").mkdir(parents=True)
    (tmp_path / "ops/policies").mkdir(parents=True)
    (tmp_path / "backend/app/workers").mkdir(parents=True)
    (tmp_path / "docker-compose.yml").write_text(
        "# ${BACKEND_API_HOST:-127.0.0.1}:${BACKEND_API_PORT:-18000}:8000\n"
        "# target: ${BACKEND_BUILD_TARGET:-runtime}\n"
        "services:\n  backend-api:\n    build:\n      context: ./backend\n"
        '    ports: ["0.0.0.0:18000:8000"]\n',
        encoding="utf-8",
    )
    (tmp_path / "docker-compose.observability.yml").write_text(
        "# 127.0.0.1:${PROMETHEUS_HOST_PORT:-19090}:9090\n"
        'services:\n  prometheus:\n    ports: ["0.0.0.0:19090:9090"]\n',
        encoding="utf-8",
    )
    (tmp_path / "docker-compose.observability.protected.yml").write_text(
        "services:\n  prometheus:\n    volumes: []\n",
        encoding="utf-8",
    )
    (tmp_path / "ops/observability/prometheus.yml").write_text(
        "# alertmanager:9093\nalerting:\n  alertmanagers: []\n",
        encoding="utf-8",
    )
    (tmp_path / "ops/observability/prometheus.protected.yml").write_text(
        "scrape_configs: []\n",
        encoding="utf-8",
    )
    (tmp_path / "ops/observability/alerts.yml").write_text("{}\n", encoding="utf-8")
    (tmp_path / "ops/policies/dr-release-policy.json").write_bytes(
        (Path(__file__).parents[2] / "ops/policies/dr-release-policy.json").read_bytes()
    )
    (tmp_path / "backend/app/workers/rabbitmq_topology.py").write_text(
        "# document_queue ai_queue ragflow_queue notification_queue\n"
        "# x-dead-letter-exchange x-dead-letter-routing-key .dlq\n",
        encoding="utf-8",
    )
    source_root = gate.ROOT
    for relative in gate.CONTRACT_INPUT_PATHS:
        candidate = tmp_path / relative
        if candidate.exists():
            continue
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_bytes((source_root / relative).read_bytes())
    gate.ROOT = tmp_path

    errors = gate.check_contract()

    assert any("host binding" in error for error in errors)
    assert any("runtime target" in error for error in errors)
    assert any("Alertmanager target" in error for error in errors)
    assert any("task queue set" in error for error in errors)


def test_offsite_evidence_uri_rejects_embedded_credentials_or_tracking() -> None:
    gate = _load_gate()

    assert gate._safe_offsite_uri("s3://immutable-backups/knowledge-uploader") is True
    assert gate._safe_offsite_uri("https://backup.example.test/archive") is True
    assert gate._safe_offsite_uri("https://user:password@backup.example.test/archive") is False
    assert gate._safe_offsite_uri("https://backup.example.test/archive?token=secret") is False
    assert gate._safe_offsite_uri("s3://immutable-backups/archive#credential") is False


def test_resolved_compose_host_comes_from_docker_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = _load_gate()
    gate.ROOT = tmp_path
    resolved = {
        "services": {
            "backend-api": {
                "ports": [
                    {
                        "host_ip": "0.0.0.0",
                        "published": "18000",
                        "target": 8000,
                    }
                ]
            }
        }
    }
    monkeypatch.setattr(
        gate.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=json.dumps(resolved)),
    )

    assert gate._resolved_backend_api_hosts() == {"0.0.0.0"}


def test_resolved_compose_environment_is_structural_not_comment_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = _load_gate()
    gate.ROOT = tmp_path
    resolved = {
        "services": {
            "backend-api": {
                "environment": {
                    "SMTP_HOST": "smtp.company.test",
                    "SMTP_FROM": "noreply@company.test",
                    "REQUIRE_EMAIL_VERIFICATION": "true",
                }
            }
        }
    }
    monkeypatch.setattr(
        gate.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=json.dumps(resolved)),
    )

    backend = gate._resolved_backend_api_service()

    assert (
        gate._backend_api_environment(backend) == resolved["services"]["backend-api"]["environment"]
    )


def _resolved_external_llm_services() -> dict[str, object]:
    return {
        "backend-api": {"environment": {"ALLOW_EXTERNAL_LLM": "false"}},
        "worker-ai": {"environment": {"ALLOW_EXTERNAL_LLM": "false"}},
    }


def test_resolved_external_llm_contract_requires_explicit_false_for_api_and_worker() -> None:
    gate = _load_gate()

    assert gate._resolved_external_llm_errors(_resolved_external_llm_services()) == []


def test_resolved_external_llm_contract_rejects_missing_value() -> None:
    gate = _load_gate()
    services = _resolved_external_llm_services()
    backend = services["backend-api"]
    assert isinstance(backend, dict)
    environment = backend["environment"]
    assert isinstance(environment, dict)
    environment.pop("ALLOW_EXTERNAL_LLM")

    errors = gate._resolved_external_llm_errors(services)

    assert errors == ["resolved backend-api ALLOW_EXTERNAL_LLM is not explicitly false"]


def test_resolved_external_llm_contract_rejects_true_value() -> None:
    gate = _load_gate()
    services = _resolved_external_llm_services()
    backend = services["backend-api"]
    assert isinstance(backend, dict)
    environment = backend["environment"]
    assert isinstance(environment, dict)
    environment["ALLOW_EXTERNAL_LLM"] = "true"

    errors = gate._resolved_external_llm_errors(services)

    assert errors == ["resolved backend-api ALLOW_EXTERNAL_LLM is not explicitly false"]


def test_resolved_external_llm_contract_rejects_worker_override() -> None:
    gate = _load_gate()
    services = _resolved_external_llm_services()
    worker = services["worker-ai"]
    assert isinstance(worker, dict)
    environment = worker["environment"]
    assert isinstance(environment, dict)
    environment["ALLOW_EXTERNAL_LLM"] = "true"

    errors = gate._resolved_external_llm_errors(services)

    assert errors == ["resolved worker-ai ALLOW_EXTERNAL_LLM is not explicitly false"]


def test_check_evidence_invokes_resolved_external_llm_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = _load_gate()

    class ExternalLlmContractCalled(RuntimeError):
        pass

    monkeypatch.setattr(
        gate,
        "_resolved_compose_services",
        lambda: {"backend-api": {}},
    )

    def raise_when_called(_services: object) -> list[str]:
        raise ExternalLlmContractCalled

    monkeypatch.setattr(gate, "_resolved_external_llm_errors", raise_when_called)

    with pytest.raises(ExternalLlmContractCalled):
        gate.check_evidence(
            evidence_root=tmp_path,
            alertmanager_config=tmp_path / "alertmanager.yml",
            backend_api_host="127.0.0.1",
            git_sha=TEST_GIT_SHA,
            environment="staging",
        )


def _resolved_minio_services() -> dict[str, object]:
    root_environment = {
        "MINIO_ROOT_USER": "protected-root-user",
        "MINIO_ROOT_PASSWORD": "protected-root-password-0123456789",
    }
    return {
        "minio": {
            "image": (
                "minio/minio:RELEASE.2024-04-18T19-09-19Z"
                "@sha256:036a068d7d6b69400da6bc07a480bee1e241ef3c341c41d988ed11f520f85124"
            ),
            "environment": dict(root_environment),
        },
        "minio-bootstrap": {
            "environment": {
                **root_environment,
                "MINIO_ENDPOINT": "minio:9000",
                "MINIO_ACCESS_KEY": "protected-data-user",
                "MINIO_SECRET_KEY": "protected-data-password-0123456789",
            }
        },
        "minio-metrics-token-init": {
            "environment": {**root_environment, "MINIO_ENDPOINT": "minio:9000"}
        },
        "backend-api": {
            "build": {
                "args": {
                    "MINIO_MC_IMAGE": (
                        "minio/mc:RELEASE.2024-04-18T16-45-29Z"
                        "@sha256:5a84109d6b29bab96c3122e4a7ba888fbf48d4cdc83bc8bf88e3a7ac67b970b8"
                    )
                }
            },
            "environment": {"MINIO_ACCESS_KEY": "protected-data-user"},
        },
    }


def test_resolved_minio_root_contract_requires_nondefault_consistent_isolation() -> None:
    gate = _load_gate()

    assert gate._resolved_minio_root_errors(_resolved_minio_services()) == []


@pytest.mark.parametrize(
    ("surface", "value", "expected"),
    (
        (
            "server",
            "minio/minio:RELEASE.2024-04-18T19-09-19Z",
            "server image is not the approved immutable digest",
        ),
        (
            "server",
            "minio/minio:RELEASE.2024-04-18T19-09-19Z@sha256:" + "f" * 64,
            "server image is not the approved immutable digest",
        ),
        (
            "mc",
            "minio/mc:RELEASE.2024-04-18T16-45-29Z",
            "backend mc image is not the approved immutable digest",
        ),
        (
            "mc",
            "minio/mc:RELEASE.2024-04-18T16-45-29Z@sha256:" + "f" * 64,
            "backend mc image is not the approved immutable digest",
        ),
    ),
)
def test_resolved_minio_image_contract_rejects_tag_or_alternate_digest(
    surface: str,
    value: str,
    expected: str,
) -> None:
    gate = _load_gate()
    services = _resolved_minio_services()
    if surface == "server":
        minio = services["minio"]
        assert isinstance(minio, dict)
        minio["image"] = value
    else:
        backend = services["backend-api"]
        assert isinstance(backend, dict)
        build = backend["build"]
        assert isinstance(build, dict)
        args = build["args"]
        assert isinstance(args, dict)
        args["MINIO_MC_IMAGE"] = value

    errors = gate._resolved_minio_root_errors(services)

    assert any(expected in error for error in errors)


@pytest.mark.parametrize(
    ("service", "field", "value", "expected"),
    (
        (
            "minio",
            "MINIO_ROOT_USER",
            "other-root",
            "disagree across server and init services",
        ),
        (
            "minio-bootstrap",
            "MINIO_ROOT_PASSWORD",
            "knowledge_root_password",
            "disagree across server and init services",
        ),
        (
            "minio",
            "MINIO_ROOT_USER",
            "knowledge-root",
            "disagree across server and init services",
        ),
        (
            "minio-bootstrap",
            "MINIO_ACCESS_KEY",
            "protected-root-user",
            "root and data-plane credentials are not isolated",
        ),
    ),
)
def test_resolved_minio_root_contract_rejects_drift_or_reuse(
    service: str,
    field: str,
    value: str,
    expected: str,
) -> None:
    gate = _load_gate()
    services = _resolved_minio_services()
    target = services[service]
    assert isinstance(target, dict)
    environment = target["environment"]
    assert isinstance(environment, dict)
    environment[field] = value

    errors = gate._resolved_minio_root_errors(services)

    assert any(expected in error for error in errors)


def test_resolved_minio_root_contract_rejects_default_pair() -> None:
    gate = _load_gate()
    services = _resolved_minio_services()
    for name in ("minio", "minio-bootstrap", "minio-metrics-token-init"):
        service = services[name]
        assert isinstance(service, dict)
        environment = service["environment"]
        assert isinstance(environment, dict)
        environment["MINIO_ROOT_USER"] = "knowledge-root"
        environment["MINIO_ROOT_PASSWORD"] = "knowledge_root_password"

    errors = gate._resolved_minio_root_errors(services)

    assert any("known default" in error for error in errors)


def test_resolved_minio_root_contract_rejects_root_escape() -> None:
    gate = _load_gate()
    services = _resolved_minio_services()
    backend = services["backend-api"]
    assert isinstance(backend, dict)
    environment = backend["environment"]
    assert isinstance(environment, dict)
    environment["MINIO_ROOT_USER"] = "protected-root-user"

    errors = gate._resolved_minio_root_errors(services)

    assert any("escaped" in error for error in errors)


def test_email_delivery_evidence_rejects_plaintext_or_missing_real_delivery() -> None:
    gate = _load_gate()
    delivered_at = datetime.now(UTC).isoformat()
    evidence = {
        "registration_delivery": "passed",
        "password_reset_delivery": "passed",
        "registration_message_id_sha256": "1" * 64,
        "password_reset_message_id_sha256": "2" * 64,
        "registration_smtp_receipt_sha256": "3" * 64,
        "password_reset_smtp_receipt_sha256": "4" * 64,
        "registration_smtp_result": "accepted",
        "password_reset_smtp_result": "accepted",
        "registration_delivered_at": delivered_at,
        "password_reset_delivered_at": delivered_at,
        "persistent_message": True,
        "broker_expiry_at_or_before_token_expiry": True,
        "publisher_confirm": "passed",
        "encrypted_envelope_observed": True,
        "plaintext_token_observed": False,
        "dlq_plaintext_token_observed": False,
        "publish_failure_public_response_indistinguishable": True,
        "publish_failure_public_statuses": {
            "register": 201,
            "resend_verification": 200,
            "forgot_password": 200,
        },
        "publish_failure_metric_recorded": True,
        "retry_issued_fresh_token": True,
        "smtp_delivery_semantics": "at_most_once_attempt",
    }

    assert gate._email_delivery_evidence_errors(evidence) == []

    evidence["plaintext_token_observed"] = True
    evidence["token"] = "must-never-be-evidence"
    errors = gate._email_delivery_evidence_errors(evidence)

    assert any("plaintext_token_observed" in error for error in errors)
    assert any("schema mismatch" in error for error in errors)


def test_rabbitmq_replay_evidence_is_bound_to_queue_and_deterministic_id() -> None:
    gate = _load_gate()
    original_task_id = uuid.uuid4()
    replay_task_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"rabbitmq-replay:ragflow_queue:{original_task_id}",
    )
    exhausted = {
        "task_name": "ragflow.create_upload_task",
        "task_id": str(original_task_id),
        "queue_name": "ragflow_queue",
    }
    replayed = {
        "queue_name": "ragflow_queue",
        "replay_task_id": str(replay_task_id),
        "persistent_message": True,
        "replay_policy": "clean_room_allowlist_only",
    }
    resolved = {"queue_name": "ragflow_queue"}

    assert (
        gate._rabbitmq_replay_binding_errors(
            exhausted=exhausted,
            replayed=replayed,
            resolved=resolved,
        )
        == []
    )

    replayed["queue_name"] = "document_queue"
    replayed["replay_task_id"] = str(uuid.uuid4())
    errors = gate._rabbitmq_replay_binding_errors(
        exhausted=exhausted,
        replayed=replayed,
        resolved=resolved,
    )

    assert any("replay queue" in error for error in errors)
    assert any("deterministic" in error for error in errors)


def test_protected_gate_requires_full_git_identity() -> None:
    gate = _load_gate()

    assert gate._is_release_git_sha(TEST_GIT_SHA) is True
    assert gate._is_release_git_sha("b" * 64) is True
    assert gate._is_release_git_sha("abcdef123456") is False
    assert gate._is_release_git_sha("unknown") is False


@pytest.mark.parametrize(
    ("filename", "needle", "replacement"),
    (
        (
            "docker-compose.yml",
            b'image: "${MINIO_SERVER_IMAGE:-minio/minio:RELEASE.2024-04-18T19-09-19Z'
            b'@sha256:036a068d7d6b69400da6bc07a480bee1e241ef3c341c41d988ed11f520f85124}"',
            b'image: "${MINIO_SERVER_IMAGE:-minio/minio:RELEASE.2024-04-18T19-09-19Z}"',
        ),
        (
            "docker-compose.yml",
            b'MINIO_MC_IMAGE: "${MINIO_MC_IMAGE:-minio/mc:RELEASE.2024-04-18T16-45-29Z'
            b'@sha256:5a84109d6b29bab96c3122e4a7ba888fbf48d4cdc83bc8bf88e3a7ac67b970b8}"',
            b'MINIO_MC_IMAGE: "${MINIO_MC_IMAGE:-minio/mc:RELEASE.2024-04-18T16-45-29Z'
            b'@sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff}"',
        ),
        (
            "backend/Dockerfile",
            BACKEND_MC_ARG,
            f"ARG MINIO_MC_IMAGE={MINIO_MC_TAG_ONLY}".encode(),
        ),
        (
            "ops/Dockerfile",
            OPS_MC_ARG,
            f"ARG MC_IMAGE={MINIO_MC_TAG_ONLY}@sha256:{'f' * 64}".encode(),
        ),
        (
            ".github/workflows/knowledge-uploader.yml",
            WORKFLOW_MC_ARG,
            WORKFLOW_MC_TAG_ONLY_ARG,
        ),
        (
            "backend/Dockerfile",
            BACKEND_MC_ARG,
            BACKEND_MC_ARG + f"\nARG MINIO_MC_IMAGE={MINIO_MC_TAG_ONLY}".encode(),
        ),
        (
            "backend/Dockerfile",
            BACKEND_MC_ARG,
            b"# " + BACKEND_MC_ARG + f"\nARG MINIO_MC_IMAGE={MINIO_MC_TAG_ONLY}".encode(),
        ),
        (
            "ops/Dockerfile",
            OPS_MC_ARG,
            OPS_MC_ARG + f"\nARG MC_IMAGE={MINIO_MC_TAG_ONLY}".encode(),
        ),
        (
            "ops/Dockerfile",
            OPS_MC_ARG,
            b"# " + OPS_MC_ARG + f"\nARG MC_IMAGE={MINIO_MC_TAG_ONLY}".encode(),
        ),
        (
            ".github/workflows/knowledge-uploader.yml",
            WORKFLOW_MC_ARG,
            WORKFLOW_MC_ARG + b" --build-arg=MINIO_MC_IMAGE=" + MINIO_MC_TAG_ONLY.encode(),
        ),
        (
            "docker-compose.yml",
            b"MINIO_PROMETHEUS_AUTH_TYPE: jwt",
            b"MINIO_PROMETHEUS_AUTH_TYPE: public",
        ),
        (
            "docker-compose.yml",
            b"\n  minio-bootstrap:\n    image:",
            b"\n  minio-bootstrap-disabled:\n    image:",
        ),
        (
            "docker-compose.yml",
            b"  MINIO_CA_CERT_FILE:",
            b"  MINIO_METRICS_BEARER_TOKEN_FILE: /global/token\n  MINIO_CA_CERT_FILE:",
        ),
        (
            "docker-compose.yml",
            b'MINIO_ACCESS_KEY: "metrics-bearer-only-no-data-plane"',
            b'MINIO_ACCESS_KEY: "knowledge"',
        ),
        (
            "docker-compose.observability.protected.yml",
            b"https://minio:9000/minio/health/cluster",
            b"http://minio:9000/minio/health/cluster",
        ),
        (
            "docker-compose.observability.protected.yml",
            b"/ca.crt:/run/secrets/minio-ca/ca.crt:ro",
            b"/missing.crt:/run/secrets/minio-ca/ca.crt:ro",
        ),
        (
            "docker-compose.observability.protected.yml",
            b"https://minio:9000/minio/health/cluster",
            b"https://127.0.0.1:9000/minio/health/cluster",
        ),
        (
            "docker-compose.observability.protected.yml",
            b"curl --fail",
            b"curl --insecure --fail",
        ),
    ),
)
def test_protected_contract_rejects_minio_auth_and_tls_downgrades(
    filename: str,
    needle: bytes,
    replacement: bytes,
) -> None:
    gate = _load_gate()
    contracts = dict(gate.snapshot_contract_payloads())
    assert gate.check_contract(contracts) == []
    assert needle in contracts[filename]
    contracts[filename] = contracts[filename].replace(needle, replacement, 1)

    assert gate.check_contract(contracts)


@pytest.mark.parametrize("mutation", ("comment_decoy", "step_env"))
def test_protected_contract_rejects_workflow_decoy_or_step_override(
    mutation: str,
) -> None:
    gate = _load_gate()
    contracts = dict(gate.snapshot_contract_payloads())
    workflow = contracts[".github/workflows/knowledge-uploader.yml"]
    if mutation == "comment_decoy":
        workflow = workflow.replace(WORKFLOW_MC_ARG, WORKFLOW_MC_TAG_ONLY_ARG, 1)
        build_line = b"            docker buildx build "
        workflow = workflow.replace(
            build_line,
            b"            # " + WORKFLOW_MC_ARG + b"\n" + build_line,
            1,
        )
    else:
        marker = (
            b"      - name: Build backend OCI layout once with SBOM and provenance\n"
            b"        run: |"
        )
        replacement = (
            b"      - name: Build backend OCI layout once with SBOM and provenance\n"
            b"        env:\n"
            b"          MINIO_MC_IMAGE: minio/mc:RELEASE.2024-04-18T16-45-29Z\n"
            b"        run: |"
        )
        assert marker in workflow
        workflow = workflow.replace(marker, replacement, 1)
    contracts[".github/workflows/knowledge-uploader.yml"] = workflow

    assert gate.check_contract(contracts)


def test_release_evidence_scanner_rejects_semantic_jwt_but_not_dotted_noise() -> None:
    gate = _load_gate()
    semantic_jwt = b"eyJhbGciOiJIUzI1NiJ9." b"eyJzdWIiOiJtaW5pby1tZXRyaWNzIn0." b"eA"

    def token(claims: dict[str, object]) -> bytes:
        def segment(value: dict[str, object]) -> bytes:
            payload = gate.json.dumps(value, separators=(",", ":")).encode("utf-8")
            return gate.base64.urlsafe_b64encode(payload).rstrip(b"=")

        return b".".join((segment({"alg": "HS256"}), segment(claims), b"eA"))

    assert not gate._contains_semantic_jwt(b"foo.bar.baz")
    assert gate._contains_semantic_jwt(semantic_jwt)
    assert gate._contains_semantic_jwt(token({"aud": ["minio"]}))
    assert not gate._contains_semantic_jwt(token({"exp": 9999999999}))
    assert not gate._contains_semantic_jwt(token({"accessKey": 42}))
    assert not gate._contains_semantic_jwt(token({"sub": ""}))
    with pytest.raises(RuntimeError, match="bearer credential material"):
        gate.reject_semantic_jwts({"infrastructure-e2e.json": semantic_jwt})


def test_infrastructure_e2e_requires_run_compose_image_and_worker_identity() -> None:
    gate = _load_gate()
    run_id = str(uuid.uuid4())

    def fault_receipt(
        service: str,
        outage: str,
        observation: str,
        anchor: str,
        **extra: object,
    ) -> dict[str, object]:
        return {
            "status": "passed",
            "run_id": run_id,
            "service": service,
            "target_file_id": str(uuid.uuid4()),
            "outage_observed": outage,
            "failure_observation": observation,
            "durability_anchor": anchor,
            "queue_messages_before": 1,
            "queue_messages_after_restore": 1,
            "remote_upload_delta": 1,
            "remote_document_count": 1,
            "terminal_state": "parsed",
            "event_loss_detected": False,
            "duplicate_remote_document": False,
            **extra,
        }

    fault_recovery: dict[str, dict[str, object]] = {
        "rabbitmq": fault_receipt(
            "rabbitmq",
            "ready_503",
            "persistent_message_held_while_broker_unavailable",
            "rabbitmq_durable_queue",
            broker_message_persisted=True,
        ),
        "redis": fault_receipt(
            "redis",
            "ready_503",
            "celery_retry_requeued_while_cache_unavailable",
            "celery_retry_message",
            retry_task_id=str(uuid.uuid4()),
            retry_task_name="ragflow.create_upload_task",
            retry_queue="ragflow_queue",
            retry_count_observed=1,
            retry_status_before_restore="requeued",
        ),
    }
    for dependency, service, outage in (
        ("minio", "minio", "ready_503"),
        ("ragflow", "mock-ragflow", "tls_endpoint_unreachable"),
    ):
        fault_recovery[dependency] = fault_receipt(
            service,
            outage,
            "postgres_failed_sync_task_before_remote_upload",
            "postgres_sync_task",
            failed_task_id=str(uuid.uuid4()),
            retry_status_before="failed",
            retry_status_after="queued",
        )

    evidence = {
        "evidence_contract_version": 5,
        "status": "development_passed",
        "generated_at": "2026-07-17T00:00:00+00:00",
        "git_sha": TEST_GIT_SHA,
        "environment": "staging",
        "full_compose_e2e": "development_passed",
        "architecture": "aarch64",
        "docker_architecture": "arm64",
        "run_id": run_id,
        "compose_project": "knowledge-uploader-dgx-test",
        "source_worktree_clean": True,
        "cleanup_status": "passed",
        "resolved_compose_sha256": "b" * 64,
        "tls_certificate_sha256": "e" * 64,
        "tls": {
            "status": "passed",
            "ca_sha256": "1" * 64,
            "certificate_bundle_sha256": "e" * 64,
            "certificates": {
                "minio": "2" * 64,
                "ragflow": "3" * 64,
                "smtp": "4" * 64,
                "gateway": "5" * 64,
            },
            "verified_channels": [
                "gateway_https",
                "minio_https",
                "ragflow_https",
                "smtp_starttls",
            ],
        },
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
            "config_sha256": "6" * 64,
            "ca_file": "/etc/prometheus/tls/ca.crt",
            "server_name": "minio",
            "certificate_verification": "required",
        },
        "rabbitmq_probe_run_id": run_id,
        "rabbitmq_evidence_sha256": "9" * 64,
        "backend_image": "backend:test",
        "backend_image_revision": TEST_GIT_SHA,
        "backend_image_id": "sha256:" + "c" * 64,
        "frontend_image": "frontend:test",
        "frontend_image_revision": TEST_GIT_SHA,
        "frontend_image_id": "sha256:" + "d" * 64,
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
            "cleanup": "passed",
        },
        "service_container_ids": {
            service: f"{index:064x}" for index, service in enumerate(REQUIRED_SERVICES, 1)
        },
        "service_image_ids": {
            service: "sha256:" + f"{index + 100:064x}"
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
        "fault_recovery": fault_recovery,
    }

    assert gate._infrastructure_e2e_errors(evidence, git_sha=TEST_GIT_SHA) == []

    unexpected = json.loads(json.dumps(evidence))
    unexpected["minio_token"] = "forbidden-field"
    unexpected_errors = gate._infrastructure_e2e_errors(
        unexpected,
        git_sha=TEST_GIT_SHA,
    )
    assert any("top-level schema mismatch" in error for error in unexpected_errors)

    evidence["resolved_compose_sha256"] = "short"
    evidence["worker_queue_consumers"] = {"ragflow_queue": 1}
    errors = gate._infrastructure_e2e_errors(evidence, git_sha=TEST_GIT_SHA)

    assert any("Compose digest" in error for error in errors)
    assert any("worker queue consumers" in error for error in errors)


def test_promtool_evidence_is_bound_to_exact_configs(tmp_path: Path) -> None:
    gate = _load_gate()
    contract_payloads = gate.snapshot_contract_payloads()
    alertmanager = tmp_path / "alertmanager.yml"
    alertmanager.write_text(
        "route:\n  receiver: ops\nreceivers:\n  - name: ops\n",
        encoding="utf-8",
    )
    evidence = {
        "prometheus_config": "passed",
        "prometheus_rules": "passed",
        "alertmanager_config": "passed",
        "prometheus_config_sha256": gate._sha256_path(
            Path(__file__).parents[2] / "ops/observability/prometheus.protected.yml"
        ),
        "prometheus_rules_sha256": gate._sha256_path(
            Path(__file__).parents[2] / "ops/observability/alerts.yml"
        ),
        "alertmanager_config_sha256": gate._sha256_path(alertmanager),
        "prometheus_image": gate.PROMETHEUS_VALIDATOR_IMAGE,
        "prometheus_manifest_list_digest": gate.PROMETHEUS_VALIDATOR_IMAGE.rsplit(
            "@",
            maxsplit=1,
        )[1],
        "prometheus_image_id": "sha256:" + "a" * 64,
        "prometheus_image_os": "linux",
        "prometheus_image_architecture": "amd64",
        "prometheus_docker_architecture": "amd64",
        "alertmanager_image": gate.ALERTMANAGER_VALIDATOR_IMAGE,
        "alertmanager_manifest_list_digest": gate.ALERTMANAGER_VALIDATOR_IMAGE.rsplit(
            "@",
            maxsplit=1,
        )[1],
        "alertmanager_image_id": "sha256:" + "b" * 64,
        "alertmanager_image_os": "linux",
        "alertmanager_image_architecture": "amd64",
        "alertmanager_docker_architecture": "amd64",
    }

    assert gate.check_contract(contract_payloads) == []
    gate.ROOT = tmp_path / "missing-root"
    assert (
        gate._promtool_evidence_errors(
            evidence,
            alertmanager_config=alertmanager,
            contract_payloads=contract_payloads,
        )
        == []
    )

    bad_config = dict(evidence)
    bad_config["alertmanager_config_sha256"] = "0" * 64
    config_errors = gate._promtool_evidence_errors(
        bad_config,
        alertmanager_config=alertmanager,
        contract_payloads=contract_payloads,
    )
    assert any("alertmanager_config_sha256" in error for error in config_errors)

    mutable_validator = dict(evidence)
    mutable_validator["prometheus_image"] = "prom/prometheus:v3.12.0"
    reference_errors = gate._promtool_evidence_errors(
        mutable_validator,
        alertmanager_config=alertmanager,
        contract_payloads=contract_payloads,
    )
    assert any("approved manifest-list digest" in error for error in reference_errors)

    wrong_digest = dict(evidence)
    wrong_digest["prometheus_manifest_list_digest"] = (
        "sha256:dd4bced05dfaddf23a7ec50f87334993a4149f7fcfbf58456d1c8bafce91cd13"
    )
    digest_errors = gate._promtool_evidence_errors(
        wrong_digest,
        alertmanager_config=alertmanager,
        contract_payloads=contract_payloads,
    )
    assert any("approved digest" in error for error in digest_errors)

    wrong_architecture = dict(evidence)
    wrong_architecture["prometheus_image_architecture"] = "arm64"
    architecture_errors = gate._promtool_evidence_errors(
        wrong_architecture,
        alertmanager_config=alertmanager,
        contract_payloads=contract_payloads,
    )
    assert any("Docker daemon" in error for error in architecture_errors)


def test_alertmanager_evidence_config_rejects_inline_delivery_secret(tmp_path: Path) -> None:
    gate = _load_gate()
    config = tmp_path / "alertmanager.yml"
    config.write_text(
        (
            "route:\n  receiver: ops\nreceivers:\n  - name: ops\n"
            "    webhook_configs:\n      - url: https://hooks.example.test/private-token\n"
        ),
        encoding="utf-8",
    )

    errors = gate._alertmanager_inline_secret_errors(config)

    assert len(errors) == 1
    assert "webhook_configs.0.url" in errors[0]


@pytest.mark.parametrize(
    ("header_name", "field_name"),
    (
        ("Authorization", "values"),
        ("Proxy-Authorization", "values"),
        ("X-API-Key", "values"),
        ("X-Auth-Token", "values"),
        ("X-Correlation-ID", "secrets"),
    ),
)
def test_alertmanager_evidence_rejects_inline_http_header_secrets(
    tmp_path: Path,
    header_name: str,
    field_name: str,
) -> None:
    gate = _load_gate()
    config = tmp_path / "alertmanager.yml"
    config.write_text(
        (
            "route:\n  receiver: ops\nreceivers:\n  - name: ops\n"
            "    webhook_configs:\n      - url_file: /run/secrets/webhook\n"
            "        http_config:\n          http_headers:\n"
            f"            {header_name}:\n              {field_name}: [opaque-marker]\n"
        ),
        encoding="utf-8",
    )

    errors = gate._alertmanager_inline_secret_errors(config)

    assert len(errors) == 1
    assert f"{header_name}.{field_name}" in errors[0]
    assert "opaque-marker" not in errors[0]


def test_alertmanager_evidence_allows_http_header_files(tmp_path: Path) -> None:
    gate = _load_gate()
    config = tmp_path / "alertmanager.yml"
    config.write_text(
        (
            "route:\n  receiver: ops\nreceivers:\n  - name: ops\n"
            "    webhook_configs:\n      - url_file: /run/secrets/webhook\n"
            "        http_config:\n          http_headers:\n"
            "            Authorization:\n"
            "              files: [/run/secrets/authorization-header]\n"
        ),
        encoding="utf-8",
    )

    assert gate._alertmanager_inline_secret_errors(config) == []


def test_rabbitmq_exhaustion_requires_real_worker_final_reject() -> None:
    gate = _load_gate()
    exhausted = {
        "result": "dead_lettered",
        "attempts": 4,
        "retry_count": 3,
        "dead_letter_reason": "rejected",
        "delivery_path": "celery_worker_retry_exhaustion",
        "dlq_count_after": 1,
        "task_name": "ragflow.create_upload_task",
    }

    assert gate._rabbitmq_exhaustion_errors(exhausted) == []

    exhausted["delivery_path"] = "manual_basic_reject"
    exhausted["attempts"] = 2
    errors = gate._rabbitmq_exhaustion_errors(exhausted)
    assert any("worker's final rejected attempt" in error for error in errors)


def test_external_projection_rejects_unknown_checksum_and_run_mix() -> None:
    gate = _load_gate()
    now = datetime.now(UTC)
    source_run_id = str(uuid.uuid4())
    receipt = {
        "alert_name": "KnowledgeUploaderProtectedReleaseProbe",
        "alert_fingerprint": "1" * 64,
        "receiver_name": "protected-webhook",
        "receiver_type": "webhook",
        "webhook_delivery_id_sha256": "2" * 64,
        "webhook_receipt_sha256": "3" * 64,
        "webhook_status_code": 202,
        "firing_at": now.isoformat(),
        "delivered_at": now.isoformat(),
        "resolved_at": now.isoformat(),
    }
    source_evidence = {
        "schema": gate.SOURCE_SCHEMAS["alertmanager-notification.json"],
        "generated_at": now.isoformat(),
        "git_sha": TEST_GIT_SHA,
        "environment": "staging",
        "source_run_id": source_run_id,
        "source_run_attempt": 1,
        "source_tool": gate.SOURCE_TOOLS["alertmanager-notification.json"],
        "status": "passed",
        "receipt": receipt,
    }
    projection = {
        "schema": gate.OUTPUT_SCHEMAS["alertmanager-notification.json"],
        "generated_at": now.isoformat(),
        "git_sha": TEST_GIT_SHA,
        "environment": "staging",
        "collector_run_id": 505,
        "collector_run_attempt": 1,
        "status": "passed",
        "source": {
            "schema": source_evidence["schema"],
            "generated_at": source_evidence["generated_at"],
            "run_id": source_run_id,
            "run_attempt": 1,
            "tool": source_evidence["source_tool"],
            "file_sha256": "4" * 64,
            "canonical_sha256": gate._canonical_sha256(source_evidence),
        },
        "receipt": receipt,
    }

    assert gate._validate_external_projection(
        projection,
        filename="alertmanager-notification.json",
        git_sha=TEST_GIT_SHA,
        environment="staging",
        collector_run_id=505,
        collector_run_attempt=1,
        now=now,
    ) == (source_run_id, 1, receipt)

    unknown = json.loads(json.dumps(projection))
    unknown["unexpected"] = True
    with pytest.raises(RuntimeError, match="schema mismatch"):
        gate._validate_external_projection(
            unknown,
            filename="alertmanager-notification.json",
            git_sha=TEST_GIT_SHA,
            environment="staging",
            collector_run_id=505,
            collector_run_attempt=1,
            now=now,
        )

    forged = json.loads(json.dumps(projection))
    forged["source"]["canonical_sha256"] = "0" * 64
    with pytest.raises(RuntimeError, match="canonical checksum mismatch"):
        gate._validate_external_projection(
            forged,
            filename="alertmanager-notification.json",
            git_sha=TEST_GIT_SHA,
            environment="staging",
            collector_run_id=505,
            collector_run_attempt=1,
            now=now,
        )

    with pytest.raises(RuntimeError, match="collector identity mismatch"):
        gate._validate_external_projection(
            projection,
            filename="alertmanager-notification.json",
            git_sha=TEST_GIT_SHA,
            environment="staging",
            collector_run_id=506,
            collector_run_attempt=1,
            now=now,
        )


def _write_protected_gate_inventory(gate: ModuleType, root: Path) -> None:
    root.mkdir()
    for name in gate.REQUIRED_RELEASE_GATE_EVIDENCE:
        (root / name).write_bytes(f"fixture:{name}\n".encode())


def test_protected_gate_directory_requires_exact_regular_inventory(tmp_path: Path) -> None:
    gate = _load_gate()
    evidence = tmp_path / "protected-gate"
    _write_protected_gate_inventory(gate, evidence)

    assert set(
        gate._snapshot_exact_evidence_directory(
            evidence,
            gate.REQUIRED_RELEASE_GATE_EVIDENCE,
        )
    ) == set(gate.REQUIRED_RELEASE_GATE_EVIDENCE)

    (evidence / "unexpected-token.txt").write_text(
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJsZWFrIn0.c2lnbmF0dXJl",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="inventory mismatch"):
        gate._snapshot_exact_evidence_directory(
            evidence,
            gate.REQUIRED_RELEASE_GATE_EVIDENCE,
        )
    (evidence / "unexpected-token.txt").unlink()

    (evidence / "unexpected-directory").mkdir()
    with pytest.raises(RuntimeError, match="inventory mismatch"):
        gate._snapshot_exact_evidence_directory(
            evidence,
            gate.REQUIRED_RELEASE_GATE_EVIDENCE,
        )


def test_protected_gate_directory_rejects_required_symlink(tmp_path: Path) -> None:
    gate = _load_gate()
    evidence = tmp_path / "protected-gate"
    _write_protected_gate_inventory(gate, evidence)
    victim = evidence / sorted(gate.REQUIRED_RELEASE_GATE_EVIDENCE)[0]
    target = tmp_path / "target"
    target.write_bytes(victim.read_bytes())
    victim.unlink()
    try:
        victim.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable on this runner")

    with pytest.raises(RuntimeError, match="inventory mismatch"):
        gate._snapshot_exact_evidence_directory(
            evidence,
            gate.REQUIRED_RELEASE_GATE_EVIDENCE,
        )
