from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import uuid
from http import HTTPStatus
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def runner() -> ModuleType:
    return load_module(
        "run_ai_mainchain_e2e_contract",
        ROOT / "scripts" / "run_ai_mainchain_e2e.py",
    )


@pytest.fixture(scope="module")
def mock_llm() -> ModuleType:
    return load_module(
        "mock_llm_contract",
        ROOT / "ops" / "e2e" / "mock_llm.py",
    )


def stub_execute_runtime(
    runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    *,
    cleanup_failure_step: str | None = None,
) -> list[tuple[str, list[str]]]:
    calls: list[tuple[str, list[str]]] = []

    def fake_environment(
        *,
        base_environment: dict[str, str],
        run_id: uuid.UUID,
        revision: str,
    ) -> dict[str, str]:
        del base_environment, run_id, revision
        return {
            "BACKEND_IMAGE": "candidate:test",
            "AI_PROBE_ADMIN_EMAIL": "admin@probe.example.com",
        }

    def fake_run(
        args: list[str],
        *,
        environment: dict[str, str],
        step: str,
        timeout_seconds: int,
    ) -> object:
        del environment, timeout_seconds
        calls.append((step, args))
        if step == "candidate_worktree_add":
            candidate_root = Path(args[-2])
            candidate_root.mkdir(parents=True)
            (candidate_root / runner.BASE_COMPOSE.name).write_text(
                'services:\n  backend-api:\n    image: "${BACKEND_IMAGE:?required}"\n',
                encoding="utf-8",
            )
            (candidate_root / runner.AI_COMPOSE.name).write_text(
                'services:\n  mock-llm:\n    environment:\n'
                '      AI_PROBE_LLM_API_KEY: "${AI_PROBE_LLM_API_KEY:?required}"\n',
                encoding="utf-8",
            )
        if step == "candidate_worktree_remove":
            shutil.rmtree(Path(args[-1]))
        if step == cleanup_failure_step:
            raise runner.AiMainchainE2EError(step)
        return runner.RunResult(stdout="", stderr="")

    def fake_inspect(
        *,
        image_reference: str,
        revision: str,
        environment: dict[str, str],
    ) -> dict[str, str]:
        del environment
        return {
            "image_reference": image_reference,
            "image_id": "sha256:" + "1" * 64,
            "oci_revision": revision,
        }

    monkeypatch.setattr(runner, "ephemeral_environment", fake_environment)
    monkeypatch.setattr(runner, "run_command", fake_run)
    monkeypatch.setattr(runner, "inspect_candidate_image", fake_inspect)
    monkeypatch.setattr(runner, "resolve_tree_revision", lambda *_args, **_kwargs: "f" * 40)
    monkeypatch.setattr(runner, "parse_probe_evidence", lambda _stdout: {"status": "passed"})
    monkeypatch.setattr(runner, "verify_runtime", lambda **_kwargs: {})
    return calls


def test_runner_uses_isolated_test_database_and_internal_protocol_mock(
    runner: ModuleType,
) -> None:
    environment = runner.ephemeral_environment(
        base_environment={},
        run_id=uuid.UUID("12345678-1234-5678-9234-567812345678"),
        revision="a" * 40,
    )

    assert environment["POSTGRES_DB"] == "knowledge_uploader_ai_probe_test"
    assert environment["POSTGRES_DB"].endswith("_test")
    assert environment["DATABASE_URL"].endswith("/knowledge_uploader_ai_probe_test")
    assert environment["ALEMBIC_DATABASE_URL"].endswith("/knowledge_uploader_ai_probe_test")
    assert environment["APP_ENV"] == "test"
    assert environment["AI_ANALYSIS_ENABLED"] == "true"
    assert environment["LLM_PROVIDER"] == "local_openai_compatible"
    assert environment["LLM_BASE_URL"] == "http://mock-llm:8081/v1"
    assert environment["LLM_ALLOWED_BASE_URLS"] == environment["LLM_BASE_URL"]
    assert environment["ALLOW_EXTERNAL_LLM"] == "false"
    assert environment["LLM_API_KEY"] == environment["AI_PROBE_LLM_API_KEY"]
    assert environment["AI_PROBE_ADMIN_PASSWORD"] == environment["SEED_ADMIN_PASSWORD"]


def test_runner_requires_explicit_mock_provider_boundary(runner: ModuleType) -> None:
    valid: dict[str, Any] = {
        "status": "passed",
        "database_name": "knowledge_uploader_ai_probe_test",
        "provider_boundary": {"external_provider_verified": False},
    }
    parsed = runner.parse_probe_evidence(
        runner.PROBE_MARKER + json.dumps(valid, separators=(",", ":")) + "\n"
    )
    assert parsed == valid

    invalid_boundary = dict(valid)
    invalid_boundary["provider_boundary"] = {"external_provider_verified": True}
    with pytest.raises(runner.AiMainchainE2EError):
        runner.parse_probe_evidence(
            runner.PROBE_MARKER + json.dumps(invalid_boundary, separators=(",", ":")) + "\n"
        )

    invalid_database = dict(valid)
    invalid_database["database_name"] = "knowledge_uploader"
    with pytest.raises(runner.AiMainchainE2EError):
        runner.parse_probe_evidence(
            runner.PROBE_MARKER + json.dumps(invalid_database, separators=(",", ":")) + "\n"
        )


def test_runner_queue_parser_preserves_messages_and_consumers(runner: ModuleType) -> None:
    queues = runner.parse_queue_snapshot(
        "Timeout: 60.0 seconds ...\nai_queue\t0\t1\nai_queue.dlq\t0\t0\n"
    )
    assert queues == {"ai_queue": (0, 1), "ai_queue.dlq": (0, 0)}


def test_mock_llm_validates_openai_compatible_json_contract(mock_llm: ModuleType) -> None:
    body = json.dumps(
        {
            "model": "probe-model",
            "messages": [{"role": "user", "content": "analyze without storing this prompt"}],
            "response_format": {"type": "json_object"},
        }
    ).encode("utf-8")
    model = mock_llm.validate_completion_request(
        {"Authorization": "Bearer probe-secret"},
        body,
        api_key="probe-secret",
        expected_model="probe-model",
    )
    assert model == "probe-model"

    payload = mock_llm.completion_payload(model)
    assert payload["model"] == "probe-model"
    choices = payload["choices"]
    assert isinstance(choices, list)
    content = json.loads(choices[0]["message"]["content"])
    assert content["sensitive_risk_level"] == "none"
    assert content["category_id"] is None


def test_mock_llm_rejects_missing_auth_and_json_mode(mock_llm: ModuleType) -> None:
    valid_body = {
        "model": "probe-model",
        "messages": [{"role": "user", "content": "safe"}],
        "response_format": {"type": "json_object"},
    }
    with pytest.raises(mock_llm.MockProtocolError) as auth_error:
        mock_llm.validate_completion_request(
            {},
            json.dumps(valid_body).encode("utf-8"),
            api_key="probe-secret",
            expected_model="probe-model",
        )
    assert auth_error.value.status == HTTPStatus.UNAUTHORIZED

    valid_body.pop("response_format")
    with pytest.raises(mock_llm.MockProtocolError) as protocol_error:
        mock_llm.validate_completion_request(
            {"Authorization": "Bearer probe-secret"},
            json.dumps(valid_body).encode("utf-8"),
            api_key="probe-secret",
            expected_model="probe-model",
        )
    assert protocol_error.value.status == HTTPStatus.BAD_REQUEST


def test_probe_mutates_only_through_public_api_and_reads_ordering_evidence() -> None:
    source = (ROOT / "scripts" / "ai_mainchain_probe.py").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.ai-mainchain.yml").read_text(encoding="utf-8")

    assert "/api/files/upload" in source
    assert "/api/admin/departments" in source
    assert "app.modules.ai.tasks" not in source
    assert "AiAnalysisService" not in source
    assert "session.add(" not in source
    assert "select(EventOutbox)" in source
    assert "select(AuditLog)" in source
    assert "select(AiUsageLog)" in source
    assert "external_provider_verified" in source
    assert "does not satisfy EXT-LLM" in source

    assert "./ops/e2e/mock_llm.py:/ai-probe/mock_llm.py:ro" in compose
    assert "./scripts/ai_mainchain_probe.py:/ai-probe/ai_mainchain_probe.py:ro" in compose
    assert "mock-llm:" in compose
    assert compose.count("condition: service_healthy") >= 3


def test_compose_command_binds_both_files_to_detached_project_root(
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    candidate_root = tmp_path / "detached"
    command = runner.compose_command(candidate_root, "isolated-project", "config", "--quiet")

    assert command[:6] == [
        "docker",
        "compose",
        "--project-name",
        "isolated-project",
        "--project-directory",
        str(candidate_root),
    ]
    assert command[6:10] == [
        "--file",
        str(candidate_root / "docker-compose.yml"),
        "--file",
        str(candidate_root / "docker-compose.ai-mainchain.yml"),
    ]


def test_compose_source_environment_is_removed_without_leaking_values(runner: ModuleType) -> None:
    source = {
        "PATH": "kept",
        "COMPOSE_FILE": "rogue.yml",
        "COMPOSE_PATH_SEPARATOR": ";",
        "COMPOSE_PROFILES": "rogue",
        "COMPOSE_ENV_FILES": "rogue.env",
        "compose_project_name": "rogue-project",
        "compose_bake": "true",
        "COMPOSE_REMOVE_ORPHANS": "true",
        "GIT_DIR": "rogue-git-dir",
        "git_work_tree": "rogue-worktree",
    }
    sanitized, removed = runner.sanitized_environment(source)

    assert sanitized == {"PATH": "kept"}
    assert removed == [
        "COMPOSE_BAKE",
        "COMPOSE_ENV_FILES",
        "COMPOSE_FILE",
        "COMPOSE_PATH_SEPARATOR",
        "COMPOSE_PROFILES",
        "COMPOSE_PROJECT_NAME",
        "COMPOSE_REMOVE_ORPHANS",
        "GIT_DIR",
        "GIT_WORK_TREE",
    ]
    assert "rogue.yml" not in json.dumps(removed)
    assert "rogue-git-dir" not in json.dumps(removed)


def test_all_detached_compose_interpolation_keys_are_removed_from_host_environment(
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    candidate_root = tmp_path / "candidate"
    candidate_root.mkdir()
    (candidate_root / "docker-compose.yml").write_text(
        'services:\n  api:\n    environment:\n'
        '      RAGFLOW_API_KEY: "${RAGFLOW_API_KEY:-}"\n'
        '      SMTP_PASSWORD: "${SMTP_PASSWORD:-}"\n',
        encoding="utf-8",
    )
    (candidate_root / "docker-compose.ai-mainchain.yml").write_text(
        'services:\n  api:\n    environment:\n'
        '      LLM_API_KEY: "${LLM_API_KEY:?required}"\n',
        encoding="utf-8",
    )
    keys = runner.compose_interpolation_keys(candidate_root)
    sanitized, removed = runner.sanitized_environment(
        {
            "PATH": "kept",
            "RAGFLOW_API_KEY": "ragflow-sentinel",
            "smtp_password": "smtp-sentinel",
            "LLM_API_KEY": "llm-sentinel",
        },
        additional_keys=keys,
    )

    assert keys == ("LLM_API_KEY", "RAGFLOW_API_KEY", "SMTP_PASSWORD")
    assert sanitized == {"PATH": "kept"}
    assert removed == ["LLM_API_KEY", "RAGFLOW_API_KEY", "SMTP_PASSWORD"]
    assert "sentinel" not in json.dumps(removed)

def test_candidate_revision_requires_exact_clean_head(
    runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revision = "b" * 40
    calls: list[tuple[str, list[str]]] = []

    def clean_run(
        args: list[str],
        *,
        environment: dict[str, str],
        step: str,
        timeout_seconds: int,
    ) -> object:
        del environment, timeout_seconds
        calls.append((step, args))
        if step == "git_identity":
            return runner.RunResult(stdout=revision + "\n", stderr="")
        return runner.RunResult(stdout="", stderr="")

    monkeypatch.setattr(runner, "run_command", clean_run)
    assert runner.resolve_candidate_revision({}, revision) == revision
    assert [step for step, _args in calls] == ["git_identity", "candidate_worktree"]
    assert calls[1][1] == [
        "git",
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    ]

    with pytest.raises(runner.AiMainchainE2EError) as mismatch:
        runner.resolve_candidate_revision({}, "c" * 40)
    assert mismatch.value.step == "git_identity"


def test_candidate_revision_rejects_untracked_file(
    runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revision = "b" * 40

    def dirty_run(
        _args: list[str],
        *,
        environment: dict[str, str],
        step: str,
        timeout_seconds: int,
    ) -> object:
        del environment, timeout_seconds
        if step == "git_identity":
            return runner.RunResult(stdout=revision + "\n", stderr="")
        if step == "candidate_worktree":
            return runner.RunResult(stdout="?? untracked.txt\n", stderr="")
        raise AssertionError(step)

    monkeypatch.setattr(runner, "run_command", dirty_run)
    with pytest.raises(runner.AiMainchainE2EError) as dirty:
        runner.resolve_candidate_revision({}, revision)
    assert dirty.value.step == "candidate_worktree"


def test_candidate_image_requires_matching_oci_revision(
    runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revision = "d" * 40
    image_id = "sha256:" + "e" * 64

    def inspect_run(
        _args: list[str],
        *,
        environment: dict[str, str],
        step: str,
        timeout_seconds: int,
    ) -> object:
        del environment, timeout_seconds
        if step == "candidate_image_labels":
            labels = {"org.opencontainers.image.revision": revision}
            return runner.RunResult(stdout=json.dumps(labels), stderr="")
        if step == "candidate_image_id":
            return runner.RunResult(stdout=image_id + "\n", stderr="")
        raise AssertionError(step)

    monkeypatch.setattr(runner, "run_command", inspect_run)
    identity = runner.inspect_candidate_image(
        image_reference="backend:candidate",
        revision=revision,
        environment={},
    )
    assert identity == {
        "image_reference": "backend:candidate",
        "image_id": image_id,
        "oci_revision": revision,
    }


def test_candidate_services_must_run_the_same_image(
    runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_id = "sha256:" + "f" * 64

    def inspect_run(
        args: list[str],
        *,
        environment: dict[str, str],
        step: str,
        timeout_seconds: int,
    ) -> object:
        del environment, timeout_seconds
        if step.endswith("_container_identity"):
            service = args[-1]
            return runner.RunResult(stdout=f"container-{service}\n", stderr="")
        if step.endswith("_image_identity"):
            return runner.RunResult(stdout=image_id + "\n", stderr="")
        raise AssertionError(step)

    monkeypatch.setattr(runner, "run_command", inspect_run)
    verified = runner.verify_candidate_containers(
        candidate_root=ROOT,
        project="isolated",
        environment={},
        expected_image_id=image_id,
    )
    assert verified == list(runner.BACKEND_CANDIDATE_SERVICES)


def test_execute_refuses_existing_output_without_overwrite(
    runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output = tmp_path / "evidence.json"
    sentinel = "do-not-overwrite\n"
    output.write_text(sentinel, encoding="utf-8")

    def unexpected_resolve(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("candidate resolution must not run")

    monkeypatch.setattr(runner, "resolve_candidate_revision", unexpected_resolve)
    with pytest.raises(runner.AiMainchainE2EError) as existing:
        runner.execute(
            output_path=output,
            requested_revision="a" * 40,
        )
    assert existing.value.step == "evidence_output_exists"
    assert output.read_text(encoding="utf-8") == sentinel


def test_atomic_evidence_write_is_create_only(
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    output = tmp_path / "atomic-evidence.json"
    original = {"status": "passed", "candidate": {"bound": True}}
    runner.atomic_write_json(output, original)
    first_text = output.read_text(encoding="utf-8")
    assert json.loads(first_text) == original

    with pytest.raises(runner.AiMainchainE2EError) as existing:
        runner.atomic_write_json(output, {"status": "replacement"})
    assert existing.value.step == "evidence_output_exists"
    assert output.read_text(encoding="utf-8") == first_text
    assert not list(tmp_path.glob("atomic-evidence.json.*.tmp"))


def test_execute_runs_every_compose_phase_from_one_detached_snapshot(
    runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    revision = "a" * 40
    output = tmp_path / "detached-evidence.json"
    monkeypatch.setenv("COMPOSE_FILE", "rogue.yml")
    monkeypatch.setenv("COMPOSE_PROFILES", "rogue")
    monkeypatch.setenv("GIT_DIR", "rogue-git-dir")
    monkeypatch.setattr(
        runner,
        "resolve_candidate_revision",
        lambda *_args, **_kwargs: revision,
    )
    fingerprint_roots: list[Path] = []

    def fingerprint(root: Path) -> str:
        fingerprint_roots.append(root)
        return "source-bound"

    monkeypatch.setattr(runner, "source_fingerprint", fingerprint)
    calls = stub_execute_runtime(runner, monkeypatch)

    assert runner.execute(output_path=output, requested_revision=revision) == output
    worktree_add = next(args for step, args in calls if step == "candidate_worktree_add")
    candidate_root = Path(worktree_add[-2])
    compose_calls = [args for _step, args in calls if args[:2] == ["docker", "compose"]]
    assert compose_calls
    assert all(
        args[args.index("--project-directory") + 1] == str(candidate_root) for args in compose_calls
    )
    assert all(
        args[args.index("--file") + 1] == str(candidate_root / "docker-compose.yml")
        for args in compose_calls
    )
    assert all(
        str(candidate_root / "docker-compose.ai-mainchain.yml") in args for args in compose_calls
    )
    assert fingerprint_roots == [candidate_root, candidate_root]
    assert not candidate_root.exists()
    assert not any(args[:3] == ["git", "worktree", "prune"] for _step, args in calls)

    evidence = json.loads(output.read_text(encoding="utf-8"))
    candidate = evidence["candidate"]
    assert candidate["git_sha"] == revision
    assert candidate["git_tree_sha"] == "f" * 40
    assert candidate["detached_worktree_bound"] is True
    assert candidate["detached_worktree_removed"] is True
    assert candidate["compose_files"] == [
        "docker-compose.yml",
        "docker-compose.ai-mainchain.yml",
    ]
    assert candidate["tool_environment_keys_removed"] == [
        "COMPOSE_FILE",
        "COMPOSE_PROFILES",
        "GIT_DIR",
    ]
    assert candidate["compose_environment_keys_removed"] == [
        "COMPOSE_FILE",
        "COMPOSE_PROFILES",
    ]
    assert candidate["compose_interpolation_keys_sanitized"] == [
        "AI_PROBE_LLM_API_KEY",
        "BACKEND_IMAGE",
    ]


def test_build_failure_still_runs_all_cleanup_checks(
    runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    revision = "a" * 40
    output = tmp_path / "build-failed.json"
    monkeypatch.setattr(
        runner,
        "resolve_candidate_revision",
        lambda *_args, **_kwargs: revision,
    )
    monkeypatch.setattr(runner, "source_fingerprint", lambda _root: "source-bound")
    calls = stub_execute_runtime(runner, monkeypatch, cleanup_failure_step="build_backend_image")

    with pytest.raises(runner.AiMainchainE2EError) as failed:
        runner.execute(output_path=output, requested_revision=revision)

    assert failed.value.step == "build_backend_image"
    steps = [step for step, _args in calls]
    assert "cleanup_compose" in steps
    assert "cleanup_candidate_image" in steps
    assert "cleanup_candidate_image_check" in steps
    assert "candidate_worktree_cleanup_check" in steps
    assert not output.exists()


@pytest.mark.parametrize(
    "failed_step",
    ["cleanup_compose", "cleanup_candidate_image_check", "candidate_worktree_cleanup_check"],
)
def test_cleanup_failure_cannot_write_passed_evidence(
    runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failed_step: str,
) -> None:
    revision = "a" * 40
    output = tmp_path / f"{failed_step}-failed.json"

    monkeypatch.setattr(
        runner,
        "resolve_candidate_revision",
        lambda *_args, **_kwargs: revision,
    )
    monkeypatch.setattr(runner, "source_fingerprint", lambda _root: "source-before")
    calls = stub_execute_runtime(runner, monkeypatch, cleanup_failure_step=failed_step)

    with pytest.raises(runner.AiMainchainE2EError) as cleanup:
        runner.execute(
            output_path=output,
            requested_revision=revision,
        )
    assert cleanup.value.step == failed_step
    assert failed_step in [step for step, _args in calls]
    assert not output.exists()


def test_candidate_change_after_cleanup_cannot_write_evidence(
    runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    revision = "a" * 40
    output = tmp_path / "candidate-changed.json"
    resolve_calls = 0

    def changing_resolve(*_args: object, **kwargs: object) -> str:
        nonlocal resolve_calls
        resolve_calls += 1
        if resolve_calls < 3:
            return revision
        raise runner.AiMainchainE2EError(str(kwargs["status_step"]))

    monkeypatch.setattr(runner, "resolve_candidate_revision", changing_resolve)
    monkeypatch.setattr(runner, "source_fingerprint", lambda _root: "stable-source")
    calls = stub_execute_runtime(runner, monkeypatch)

    with pytest.raises(runner.AiMainchainE2EError) as changed:
        runner.execute(
            output_path=output,
            requested_revision=revision,
        )
    assert changed.value.step == "detached_worktree_after"
    assert resolve_calls == 3
    cleanup_calls = {step: args for step, args in calls if step.startswith("cleanup_")}
    assert cleanup_calls["cleanup_compose"][-3:] == [
        "down",
        "--volumes",
        "--remove-orphans",
    ]
    assert cleanup_calls["cleanup_candidate_image"] == [
        "docker",
        "image",
        "rm",
        "--force",
        "candidate:test",
    ]
    assert cleanup_calls["cleanup_candidate_image_check"] == [
        "docker",
        "image",
        "ls",
        "--quiet",
        "candidate:test",
    ]
    assert not output.exists()
