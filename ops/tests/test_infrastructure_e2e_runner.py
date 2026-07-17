from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


def _load_runner() -> ModuleType:
    scripts_dir = Path(__file__).parents[2] / "scripts"
    runner_path = scripts_dir / "run_infrastructure_e2e.py"
    sys.path.insert(0, str(scripts_dir))
    try:
        spec = importlib.util.spec_from_file_location("run_infrastructure_e2e", runner_path)
        if spec is None or spec.loader is None:
            raise RuntimeError("could not load infrastructure E2E runner")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(scripts_dir))


def _resolved_compose(runner: ModuleType) -> dict[str, object]:
    backend_image = "backend:test"
    services: dict[str, object] = {
        name: {
            "image": backend_image,
            "environment": {
                "SSL_CERT_FILE": "/e2e-certs/ca.crt",
                "MINIO_CA_CERT_FILE": "/e2e-certs/ca.crt",
            },
            "volumes": [{"target": "/e2e-certs/ca.crt"}],
        }
        for name in runner.BACKEND_IMAGE_SERVICES
    }
    services["frontend"] = {"image": "frontend:test"}
    services["backend-api"] = {
        "image": backend_image,
        "environment": {
            "MINIO_SECURE": "true",
            "REQUIRE_EMAIL_VERIFICATION": "true",
            "SMTP_HOST": "mock-smtp",
            "SMTP_PORT": "1025",
            "SMTP_FROM": "noreply@e2e.example.com",
            "SMTP_TLS": "true",
            "SMTP_CA_CERT_FILE": "/e2e-certs/ca.crt",
            "SMTP_TIMEOUT_SECONDS": "10",
            "SSL_CERT_FILE": "/e2e-certs/ca.crt",
            "MINIO_CA_CERT_FILE": "/e2e-certs/ca.crt",
            "RAGFLOW_BASE_URL": "https://mock-ragflow:9380",
        },
        "volumes": [{"target": "/e2e-certs/ca.crt"}],
        "ports": [{"host_ip": "127.0.0.1"}],
    }
    services["mock-ragflow"] = {
        "image": backend_image,
        "environment": {
            "SSL_CERT_FILE": "/e2e-certs/ca.crt",
            "MINIO_CA_CERT_FILE": "/e2e-certs/ca.crt",
        },
        "ports": [{"host_ip": "127.0.0.1"}],
        "volumes": [
            {"source": "ca.crt", "target": "/e2e-certs/ca.crt"},
            {"source": "ragflow.crt", "target": "/e2e-certs/ragflow.crt"},
            {"source": "ragflow.key", "target": "/e2e-certs/ragflow.key"},
        ],
        "healthcheck": {"test": ["https://127.0.0.1:9380/health"]},
    }
    services["mock-smtp"] = {
        "image": backend_image,
        "environment": {
            "SSL_CERT_FILE": "/e2e-certs/ca.crt",
            "MINIO_CA_CERT_FILE": "/e2e-certs/ca.crt",
        },
        "ports": [{"host_ip": "127.0.0.1"}],
        "volumes": [
            {"source": "ca.crt", "target": "/e2e-certs/ca.crt"},
            {"source": "smtp.crt", "target": "/e2e-certs/smtp.crt"},
            {"source": "smtp.key", "target": "/e2e-certs/smtp.key"},
        ],
        "healthcheck": {"test": ["https://127.0.0.1:8080/health"]},
    }
    services["nginx"] = {
        "ports": [{"host_ip": "127.0.0.1", "target": 443}],
        "volumes": [
            {"source": "gateway.crt", "target": "/e2e-certs/gateway.crt"},
            {"source": "gateway.key", "target": "/e2e-certs/gateway.key"},
            {"source": "nginx-tls.conf", "target": "/etc/nginx/conf.d/default.conf"},
        ],
    }
    services["minio"] = {
        "environment": {"MINIO_PROMETHEUS_AUTH_TYPE": "public"},
        "volumes": [
            {"target": "/root/.minio/certs/public.crt"},
            {"target": "/root/.minio/certs/private.key"},
            {"target": "/root/.minio/certs/CAs/e2e-ca.crt"},
        ],
        "healthcheck": {"test": ["--cacert"]},
    }
    services["prometheus"] = {
        "image": runner.PROMETHEUS_IMAGE,
        "ports": [{"host_ip": "127.0.0.1", "target": 9090}],
        "volumes": [
            {
                "source": "prometheus.protected.yml",
                "target": "/etc/prometheus/prometheus.yml",
            },
            {"source": "ca.crt", "target": "/etc/prometheus/tls/ca.crt"},
        ],
    }
    return {"services": services}


def test_local_runner_never_self_promotes_clean_arm64_run() -> None:
    runner = _load_runner()

    assert (
        runner._release_status(
            source_clean=True,
            host_architecture="aarch64",
            docker_architecture="arm64",
        )
        == "development_passed"
    )
    assert (
        runner._release_status(
            source_clean=False,
            host_architecture="aarch64",
            docker_architecture="arm64",
        )
        == "development_passed"
    )


def test_architecture_normalization_matches_docker_and_platform_names() -> None:
    runner = _load_runner()

    assert runner._normalize_architecture("aarch64") == "arm64"
    assert runner._normalize_architecture("arm64") == "arm64"
    assert runner._normalize_architecture("x86_64") == "amd64"
    assert runner._normalize_architecture("amd64") == "amd64"
    assert (
        runner._release_status(
            source_clean=True,
            host_architecture="x86_64",
            docker_architecture="amd64",
        )
        == "development_passed"
    )


def test_runner_rejects_shared_image_tags_and_uses_sha_scoped_project() -> None:
    runner = _load_runner()
    git_sha = "a" * 40

    runner._validate_isolated_image_reference(
        f"knowledge-uploader-backend:dgx-{git_sha}-123",
        git_sha=git_sha,
    )
    with pytest.raises(runner.InfrastructureE2EError, match="image_reference"):
        runner._validate_isolated_image_reference(
            "knowledge-uploader-backend:dev",
            git_sha=git_sha,
        )

    source = (Path(__file__).parents[2] / "scripts/run_infrastructure_e2e.py").read_text(
        encoding="utf-8"
    )
    assert 'project = f"ku-e2e-{git_sha[:12]}-{run_id.hex[:12]}"' in source


def test_compose_up_is_always_no_build() -> None:
    runner = _load_runner()

    class RecordingRunner:
        def __init__(self) -> None:
            self.command: list[str] = []

        def run(
            self,
            command: list[str],
            *,
            step: str,
            timeout_seconds: float,
            check: bool,
        ) -> object:
            del step, timeout_seconds, check
            self.command = command
            return runner.CommandResult(returncode=0, stdout="", stderr="")

    recording = RecordingRunner()
    runner._compose_up(
        recording,
        "ku-e2e-test",
        ["backend-api"],
        step="test",
        wait_timeout_seconds=30,
        command_timeout_seconds=45,
    )

    assert "up" in recording.command
    assert "--no-build" in recording.command
    assert "build" not in recording.command


@pytest.mark.parametrize(
    ("dependency", "service", "expected_outage"),
    [
        ("minio", "minio", "ready_503"),
        ("ragflow", "mock-ragflow", "tls_endpoint_unreachable"),
    ],
)
def test_fault_worker_start_is_no_deps_and_reconfirms_dependency_outage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    dependency: str,
    service: str,
    expected_outage: str,
) -> None:
    runner = _load_runner()
    commands: list[list[str]] = []
    endpoint_checks: list[str] = []

    class RecordingRunner:
        def run(
            self,
            command: list[str],
            *,
            step: str,
            timeout_seconds: float,
            check: bool,
        ) -> object:
            del step, timeout_seconds, check
            commands.append(command)
            return runner.CommandResult(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(
        runner,
        "_expect_ready_503",
        lambda *_args, **_kwargs: endpoint_checks.append("ready_503"),
    )
    monkeypatch.setattr(
        runner,
        "_expect_tls_endpoint_unavailable",
        lambda *_args, **_kwargs: endpoint_checks.append("tls_endpoint_unreachable"),
    )

    observed = runner._start_worker_during_dependency_outage(
        RecordingRunner(),
        "ku-e2e-test",
        dependency=dependency,
        service=service,
        api_ready_url="https://127.0.0.1/api/system/ready",
        ragflow_health_url="https://127.0.0.1/ragflow/health",
        ca_cert_file=tmp_path / "ca.crt",
    )

    assert commands[0][-5:] == [
        "up",
        "--detach",
        "--no-build",
        "--no-deps",
        "worker-ragflow",
    ]
    assert commands[1][-5:] == [
        "ps",
        "--status",
        "running",
        "--quiet",
        service,
    ]
    assert endpoint_checks == [expected_outage]
    assert observed == expected_outage


def test_real_dlq_drill_holds_and_releases_only_the_target_sync_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner()
    calls: list[tuple[list[str], str]] = []

    def fake_compose(
        _runner: object,
        _project: str,
        arguments: list[str],
        *,
        step: str,
        timeout_seconds: float = 180.0,
        check: bool = True,
    ) -> object:
        del timeout_seconds, check
        calls.append((arguments, step))
        return runner.CommandResult(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(runner, "_compose", fake_compose)
    file_id = uuid.uuid4()
    runner._set_ragflow_sync_lock(object(), "ku-e2e-test", file_id=file_id, hold=True)
    runner._set_ragflow_sync_lock(object(), "ku-e2e-test", file_id=file_id, hold=False)

    hold_program = calls[0][0][-1]
    release_program = calls[1][0][-1]
    assert calls[0][1] == "ragflow_sync_lock_hold"
    assert calls[1][1] == "ragflow_sync_lock_release"
    assert str(file_id) in hold_program
    assert "nx=True, ex=600" in hold_program
    assert "client.delete(key)" in release_program
    assert "CACHE_REDIS_URL" not in hold_program

    source = (Path(__file__).parents[2] / "scripts/run_infrastructure_e2e.py").read_text(
        encoding="utf-8"
    )
    assert '"observe-exhaustion"' in source
    assert '"exhaust"' not in source


def test_resolved_compose_contract_requires_exact_images_tls_and_loopback_ports() -> None:
    runner = _load_runner()
    resolved = _resolved_compose(runner)

    runner._validate_resolved_compose(
        resolved,
        backend_image="backend:test",
        frontend_image="frontend:test",
    )

    services = resolved["services"]
    assert isinstance(services, dict)
    nginx = services["nginx"]
    assert isinstance(nginx, dict)
    nginx["ports"] = [{"host_ip": "0.0.0.0"}]

    with pytest.raises(runner.InfrastructureE2EError, match="resolved_compose_contract"):
        runner._validate_resolved_compose(
            resolved,
            backend_image="backend:test",
            frontend_image="frontend:test",
        )


def test_resolved_compose_contract_rejects_tls_bypass() -> None:
    runner = _load_runner()
    resolved = _resolved_compose(runner)
    services = resolved["services"]
    assert isinstance(services, dict)
    minio = services["minio"]
    assert isinstance(minio, dict)
    minio["healthcheck"] = {"test": ["curl", "--insecure"]}

    with pytest.raises(runner.InfrastructureE2EError, match="resolved_compose_contract"):
        runner._validate_resolved_compose(
            resolved,
            backend_image="backend:test",
            frontend_image="frontend:test",
        )


def test_resolved_compose_contract_rejects_unprotected_prometheus_config() -> None:
    runner = _load_runner()
    resolved = _resolved_compose(runner)
    services = resolved["services"]
    assert isinstance(services, dict)
    prometheus = services["prometheus"]
    assert isinstance(prometheus, dict)
    prometheus["volumes"] = [
        {
            "source": "prometheus.yml",
            "target": "/etc/prometheus/prometheus.yml",
        }
    ]

    with pytest.raises(runner.InfrastructureE2EError, match="resolved_compose_contract"):
        runner._validate_resolved_compose(
            resolved,
            backend_image="backend:test",
            frontend_image="frontend:test",
        )


def test_release_evidence_requires_every_long_running_compose_service() -> None:
    runner = _load_runner()

    assert set(runner.REQUIRED_SERVICES) == {
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
    }


def test_rabbitmq_exercise_runs_as_module_from_backend_workdir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner()
    captured: list[str] = []

    def fake_compose(
        _runner: object,
        _project: str,
        arguments: list[str],
        *,
        step: str,
    ) -> object:
        assert step == "rabbitmq_observer"
        captured.extend(arguments)
        return runner.CommandResult(returncode=0, stdout='{"status":"ok"}', stderr="")

    monkeypatch.setattr(runner, "_compose", fake_compose)

    result = runner._exercise_rabbitmq(
        object(),
        "ku-e2e-test",
        ["--mode", "observe-retry"],
        step="rabbitmq_observer",
    )

    assert result == {"status": "ok"}
    assert captured[:8] == [
        "run",
        "--rm",
        "--no-deps",
        "backend-api",
        "python",
        "-m",
        "scripts.exercise_rabbitmq_dlq",
        "--mode",
    ]


def test_minio_fault_attempts_worker_before_restore_and_retries_persisted_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _load_runner()
    events: list[str] = []
    failed_task_id = uuid.uuid4()
    target_id = uuid.uuid4()

    class Probe:
        def create_fault_target(self, _state: object, *, dependency: str) -> object:
            events.append(f"target:{dependency}")
            return SimpleNamespace(file_id=target_id, file_name="fault.txt")

        def ragflow_upload_count(self) -> int:
            return 4

        def wait_for_failed_sync_task(self, _state: object, _target: object) -> uuid.UUID:
            events.append("postgres_failure")
            return failed_task_id

        def require_remote_unchanged(
            self,
            _target: object,
            *,
            baseline_upload_count: int,
        ) -> None:
            assert baseline_upload_count == 4
            events.append("remote_unchanged")

        def retry_failed_sync_task(self, _state: object, *, task_id: uuid.UUID) -> str:
            assert task_id == failed_task_id
            events.append("retry_failed")
            return "queued"

        def verify_fault_restored(
            self,
            _state: object,
            _target: object,
            *,
            baseline_upload_count: int,
        ) -> dict[str, object]:
            assert baseline_upload_count == 4
            return {
                "target_file_id": str(target_id),
                "remote_upload_delta": 1,
                "remote_document_count": 1,
                "terminal_state": "parsed",
                "event_loss_detected": False,
                "duplicate_remote_document": False,
            }

    def fake_compose(
        _runner: object,
        _project: str,
        _arguments: list[str],
        *,
        step: str,
        timeout_seconds: float = 180.0,
        check: bool = True,
    ) -> object:
        del timeout_seconds, check
        events.append(step)
        return runner.CommandResult(returncode=0, stdout="", stderr="")

    def fake_up(
        _runner: object,
        _project: str,
        _services: list[str],
        *,
        step: str,
        wait_timeout_seconds: int,
        command_timeout_seconds: float,
    ) -> object:
        del wait_timeout_seconds, command_timeout_seconds
        events.append(step)
        return runner.CommandResult(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(runner, "_compose", fake_compose)
    monkeypatch.setattr(runner, "_compose_up", fake_up)
    monkeypatch.setattr(
        runner,
        "_wait_for_queue",
        lambda *_args, **_kwargs: {"ragflow_queue": (0, 1)},
    )
    monkeypatch.setattr(
        runner,
        "_wait_for_queue_ready_below",
        lambda *_args, **_kwargs: {"ragflow_queue": (1, 0)},
    )
    monkeypatch.setattr(runner, "_expect_ready_503", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "_wait_ready", lambda *_args, **_kwargs: None)

    evidence = runner._exercise_dependency_fault(
        object(),
        "ku-e2e-test",
        probe=Probe(),
        business_state=object(),
        dependency="minio",
        run_id=uuid.uuid4(),
        api_ready_url="https://127.0.0.1/api/system/ready",
        ragflow_health_url="https://127.0.0.1/health",
        ca_cert_file=tmp_path / "ca.crt",
    )

    assert events.index("fault_minio_start_worker_during_outage") < events.index(
        "fault_minio_restore_dependency"
    )
    assert events.index("postgres_failure") < events.index("fault_minio_restore_dependency")
    assert events.index("remote_unchanged") < events.index("retry_failed")
    assert evidence["failure_observation"] == (
        "postgres_failed_sync_task_before_remote_upload"
    )
    assert evidence["failed_task_id"] == str(failed_task_id)
    assert evidence["retry_status_before"] == "failed"
    assert evidence["retry_status_after"] == "queued"
    assert evidence["remote_upload_delta"] == 1


def test_redis_fault_observes_real_retry_message_before_dependency_restore(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _load_runner()
    events: list[str] = []
    compose_commands: dict[str, list[str]] = {}
    queue_waits: list[tuple[str, int | None, int | None]] = []
    target_id = uuid.uuid4()
    retry_task_id = uuid.uuid4()

    class Probe:
        def create_fault_target(self, _state: object, *, dependency: str) -> object:
            events.append(f"target:{dependency}")
            return SimpleNamespace(file_id=target_id, file_name="fault.txt")

        def ragflow_upload_count(self) -> int:
            return 4

        def require_remote_unchanged(
            self,
            _target: object,
            *,
            baseline_upload_count: int,
        ) -> None:
            assert baseline_upload_count == 4
            events.append("remote_unchanged")

        def verify_fault_restored(
            self,
            _state: object,
            _target: object,
            *,
            baseline_upload_count: int,
        ) -> dict[str, object]:
            assert baseline_upload_count == 4
            return {
                "target_file_id": str(target_id),
                "remote_upload_delta": 1,
                "remote_document_count": 1,
                "terminal_state": "parsed",
                "event_loss_detected": False,
                "duplicate_remote_document": False,
            }

    def fake_compose(
        _runner: object,
        _project: str,
        _arguments: list[str],
        *,
        step: str,
        timeout_seconds: float = 180.0,
        check: bool = True,
    ) -> object:
        del timeout_seconds, check
        events.append(step)
        compose_commands[step] = _arguments
        return runner.CommandResult(returncode=0, stdout="", stderr="")

    def fake_up(
        _runner: object,
        _project: str,
        _services: list[str],
        *,
        step: str,
        wait_timeout_seconds: int,
        command_timeout_seconds: float,
    ) -> object:
        del wait_timeout_seconds, command_timeout_seconds
        events.append(step)
        return runner.CommandResult(returncode=0, stdout="", stderr="")

    def fake_retry_observation(
        _runner: object,
        _project: str,
        arguments: list[str],
        *,
        step: str,
    ) -> dict[str, object]:
        events.append(step)
        assert "observe-retry" in arguments
        assert str(target_id) in arguments
        return {
            "retry_message": {
                "task_id": str(retry_task_id),
                "target_id": str(target_id),
                "task_name": "ragflow.create_upload_task",
                "queue_name": "ragflow_queue",
                "retry_count": 1,
                "persistent_message": True,
                "result": "retry_requeued",
            }
        }

    monkeypatch.setattr(runner, "_compose", fake_compose)
    monkeypatch.setattr(runner, "_compose_up", fake_up)
    monkeypatch.setattr(runner, "_exercise_rabbitmq", fake_retry_observation)
    def fake_wait_for_queue(
        *_args: object,
        queue: str,
        consumers: int | None = None,
        messages_ready: int | None = None,
        **_kwargs: object,
    ) -> dict[str, tuple[int, int]]:
        queue_waits.append((queue, consumers, messages_ready))
        return {"ragflow_queue": (consumers or 0, messages_ready or 0)}

    monkeypatch.setattr(runner, "_wait_for_queue", fake_wait_for_queue)
    monkeypatch.setattr(
        runner,
        "_wait_for_queue_delivery",
        lambda *_args, **kwargs: (
            {"ragflow_queue.dlq": (0, 0, 0)}
            if kwargs["queue"] == "ragflow_queue.dlq"
            else {"ragflow_queue": (1, 1, 0)}
        ),
    )
    monkeypatch.setattr(
        runner,
        "_require_ragflow_worker_ping",
        lambda *_args, step, **_kwargs: events.append(step),
    )
    monkeypatch.setattr(
        runner,
        "_require_service_state",
        lambda *_args, step, **_kwargs: events.append(step),
    )
    monkeypatch.setattr(
        runner,
        "_reconfirm_dependency_outage",
        lambda *_args, **_kwargs: events.append("redis_outage_confirmed") or "ready_503",
    )
    monkeypatch.setattr(
        runner,
        "_wait_for_started_redis_retry_attempt",
        lambda *_args, **_kwargs: events.append("fault_redis_inspect_retry_activity"),
    )
    monkeypatch.setattr(runner, "_wait_ready", lambda *_args, **_kwargs: None)

    evidence = runner._exercise_dependency_fault(
        object(),
        "ku-e2e-test",
        probe=Probe(),
        business_state=object(),
        dependency="redis",
        run_id=uuid.uuid4(),
        api_ready_url="https://127.0.0.1/api/system/ready",
        ragflow_health_url="https://127.0.0.1/health",
        ca_cert_file=tmp_path / "ca.crt",
    )

    assert events.index("fault_redis_observe_retry") < events.index(
        "fault_redis_restore_dependency"
    )
    assert events.index("fault_redis_worker_ping_before_pause") < events.index(
        "fault_redis_pause_running_worker"
    )
    assert events.index("fault_redis_pause_running_worker") < events.index("target:redis")
    assert events.index("target:redis") < events.index("fault_redis_stop_dependency")
    assert events.index("fault_redis_stop_dependency") < events.index(
        "fault_redis_unpause_running_worker"
    )
    assert events.index("fault_redis_unpause_running_worker") < events.index(
        "fault_redis_worker_ping_after_unpause"
    )
    assert events.index("fault_redis_inspect_retry_activity") < events.index(
        "fault_redis_kill_worker_for_retry_inspection"
    )
    assert events.index("fault_redis_kill_worker_for_retry_inspection") < events.index(
        "fault_redis_observe_retry"
    )
    assert queue_waits.count(("ragflow_queue", 0, 1)) == 3
    assert "fault_redis_start_worker_during_outage" not in events
    assert compose_commands["fault_redis_restart_worker"][-5:] == [
        "up",
        "--detach",
        "--no-build",
        "--no-deps",
        "worker-ragflow",
    ]
    assert evidence["failure_observation"] == (
        "celery_retry_requeued_while_cache_unavailable"
    )
    assert evidence["retry_task_id"] == str(retry_task_id)
    assert evidence["retry_task_name"] == "ragflow.create_upload_task"
    assert evidence["retry_queue"] == "ragflow_queue"
    assert evidence["retry_count_observed"] == 1
    assert evidence["retry_status_before_restore"] == "requeued"
    assert evidence["remote_upload_delta"] == 1
