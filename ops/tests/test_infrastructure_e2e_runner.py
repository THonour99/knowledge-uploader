from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from types import ModuleType

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
        name: {"image": backend_image} for name in runner.BACKEND_IMAGE_SERVICES
    }
    services["frontend"] = {"image": "frontend:test"}
    services["backend-api"] = {
        "image": backend_image,
        "environment": {
            "MINIO_SECURE": "true",
            "REQUIRE_EMAIL_VERIFICATION": "true",
            "SMTP_HOST": "mock-smtp",
            "SMTP_PORT": "1025",
            "SMTP_FROM": "noreply@e2e.invalid",
        },
        "volumes": [{"target": "/e2e-certs/ca.crt"}],
        "ports": [{"host_ip": "127.0.0.1"}],
    }
    services["mock-ragflow"] = {
        "image": backend_image,
        "ports": [{"host_ip": "127.0.0.1"}],
    }
    services["mock-smtp"] = {
        "image": backend_image,
        "ports": [{"host_ip": "127.0.0.1"}],
    }
    services["nginx"] = {"ports": [{"host_ip": "127.0.0.1"}]}
    services["minio"] = {
        "volumes": [
            {"target": "/root/.minio/certs/public.crt"},
            {"target": "/root/.minio/certs/private.key"},
        ]
    }
    return {"services": services}


def test_release_status_only_passes_clean_arm64_run() -> None:
    runner = _load_runner()

    assert (
        runner._release_status(
            source_clean=True,
            host_architecture="aarch64",
            docker_architecture="arm64",
        )
        == "passed"
    )
    assert (
        runner._release_status(
            source_clean=False,
            host_architecture="aarch64",
            docker_architecture="arm64",
        )
        == "development_passed"
    )
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
    }
