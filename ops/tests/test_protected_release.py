from __future__ import annotations

import importlib.util
import json
import uuid
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

TEST_GIT_SHA = "a" * 40
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


def _load_gate() -> ModuleType:
    gate_path = Path(__file__).parents[2] / "scripts/check_protected_release.py"
    spec = importlib.util.spec_from_file_location("check_protected_release", gate_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load protected release gate")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    (tmp_path / "ops/observability/prometheus.yml").write_text(
        "# alertmanager:9093\nalerting:\n  alertmanagers: []\n",
        encoding="utf-8",
    )
    (tmp_path / "backend/app/workers/rabbitmq_topology.py").write_text(
        "# document_queue ai_queue ragflow_queue notification_queue\n"
        "# x-dead-letter-exchange x-dead-letter-routing-key .dlq\n",
        encoding="utf-8",
    )
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


def test_email_delivery_evidence_rejects_plaintext_or_missing_real_delivery() -> None:
    gate = _load_gate()
    evidence = {
        "status": "passed",
        "registration_delivery": "passed",
        "password_reset_delivery": "passed",
        "registration_message_id": "message-1",
        "password_reset_message_id": "message-2",
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

    assert any("plaintext auth token" in error for error in errors)
    assert any("forbidden sensitive field" in error for error in errors)


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


def test_infrastructure_e2e_requires_run_compose_image_and_worker_identity() -> None:
    gate = _load_gate()
    run_id = str(uuid.uuid4())
    evidence = {
        "status": "passed",
        "full_compose_e2e": "passed",
        "architecture": "aarch64",
        "run_id": run_id,
        "compose_project": "knowledge-uploader-dgx-test",
        "source_worktree_clean": True,
        "cleanup_status": "passed",
        "resolved_compose_sha256": "b" * 64,
        "tls_certificate_sha256": "e" * 64,
        "rabbitmq_probe_run_id": run_id,
        "backend_image_revision": TEST_GIT_SHA,
        "backend_image_id": "sha256:" + "c" * 64,
        "frontend_image_revision": TEST_GIT_SHA,
        "frontend_image_id": "sha256:" + "d" * 64,
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
            "cleanup": "passed",
        },
        "service_container_ids": {
            service: f"{index:064x}" for index, service in enumerate(REQUIRED_SERVICES, 1)
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
    }

    assert gate._infrastructure_e2e_errors(evidence, git_sha=TEST_GIT_SHA) == []

    evidence["resolved_compose_sha256"] = "short"
    evidence["worker_queue_consumers"] = {"ragflow_queue": 1}
    errors = gate._infrastructure_e2e_errors(evidence, git_sha=TEST_GIT_SHA)

    assert any("Compose digest" in error for error in errors)
    assert any("worker queue consumers" in error for error in errors)


def test_promtool_evidence_is_bound_to_exact_configs(tmp_path: Path) -> None:
    gate = _load_gate()
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
            Path(__file__).parents[2] / "ops/observability/prometheus.yml"
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

    assert gate._promtool_evidence_errors(evidence, alertmanager_config=alertmanager) == []

    bad_config = dict(evidence)
    bad_config["alertmanager_config_sha256"] = "0" * 64
    config_errors = gate._promtool_evidence_errors(
        bad_config,
        alertmanager_config=alertmanager,
    )
    assert any("alertmanager_config_sha256" in error for error in config_errors)

    mutable_validator = dict(evidence)
    mutable_validator["prometheus_image"] = "prom/prometheus:v3.12.0"
    reference_errors = gate._promtool_evidence_errors(
        mutable_validator,
        alertmanager_config=alertmanager,
    )
    assert any("approved manifest-list digest" in error for error in reference_errors)

    wrong_digest = dict(evidence)
    wrong_digest["prometheus_manifest_list_digest"] = (
        "sha256:dd4bced05dfaddf23a7ec50f87334993a4149f7fcfbf58456d1c8bafce91cd13"
    )
    digest_errors = gate._promtool_evidence_errors(
        wrong_digest,
        alertmanager_config=alertmanager,
    )
    assert any("approved digest" in error for error in digest_errors)

    wrong_architecture = dict(evidence)
    wrong_architecture["prometheus_image_architecture"] = "arm64"
    architecture_errors = gate._promtool_evidence_errors(
        wrong_architecture,
        alertmanager_config=alertmanager,
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
