from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_runner() -> ModuleType:
    path = ROOT / "scripts" / "run_governance_acceptance.py"
    spec = importlib.util.spec_from_file_location("run_governance_acceptance_contract", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load governance acceptance runner")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def runner() -> ModuleType:
    return _load_runner()


def test_plan_maps_every_acceptance_id_to_exact_nodes_and_semantics(runner: ModuleType) -> None:
    plans = {plan.acceptance_id: plan for plan in runner.ACCEPTANCE_PLAN}
    assert set(plans) == {"AI-001", "VER-001", "EXP-001"}
    assert all(plan.scope for plan in plans.values())

    identities: list[str] = []
    for plan in plans.values():
        for target in plan.targets:
            assert target.executor in {"backend_pytest", "frontend_vitest"}
            assert target.node.count("::") == 1
            assert target.node.split("::", 1)[1].startswith("test_") or (
                target.executor == "frontend_vitest"
            )
            assert target.assertion
            identities.append(f"{target.executor}:{target.node}")
    assert len(identities) == len(set(identities))

    ai_nodes = {target.node for target in plans["AI-001"].targets}
    assert any("test_http_statuses_map_to_sanitized_retry_policy" in node for node in ai_nodes)
    assert any(
        "test_timeout_is_retryable_without_transport_message_leak" in node for node in ai_nodes
    )
    assert any("test_malformed_llm_output_is_repaired_once" in node for node in ai_nodes)
    assert any("test_ai_001_persists_prompt_version" in node for node in ai_nodes)

    version_nodes = {target.node for target in plans["VER-001"].targets}
    assert any("recovers_old_remote_failure" in node for node in version_nodes)
    assert any("unknown_predecessor_delete_outcome" in node for node in version_nodes)
    assert any("candidate_remote_activation_is_idempotent" in node for node in version_nodes)

    expiry_nodes = {target.node for target in plans["EXP-001"].targets}
    assert any("expiry_prefers_active_owner_falls_back" in node for node in expiry_nodes)
    assert any("expiry_event_snapshot_skips" in node for node in expiry_nodes)
    assert any("TopHeader.test.tsx" in node for node in expiry_nodes)
    runner._validate_test_targets(ROOT)


def test_backend_nodes_are_mapped_to_exact_container_paths(runner: ModuleType) -> None:
    nodes = runner._backend_nodes()
    assert nodes
    assert all(node.startswith("app/tests/") for node in nodes)
    assert all(node.count("::") == 1 for node in nodes)
    with pytest.raises(runner.GovernanceAcceptanceError, match="outside"):
        runner._container_backend_node("frontend/test_wrong.py::test_wrong")


def test_frontend_files_are_package_relative_and_scoped(runner: ModuleType) -> None:
    assert runner._frontend_files() == ("src/layouts/TopHeader.test.tsx",)
    with pytest.raises(runner.GovernanceAcceptanceError, match="outside"):
        runner._frontend_test_file(
            "backend/app/tests/test_wrong.tsx::test_wrong",
        )


def test_compose_prefix_is_bound_to_detached_root_and_one_explicit_file(
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    candidate_root = tmp_path / "candidate"
    prefix = runner._compose_prefix("docker", "isolated", candidate_root)

    assert prefix == (
        "docker",
        "compose",
        "--project-name",
        "isolated",
        "--project-directory",
        str(candidate_root),
        "--file",
        str(candidate_root / "docker-compose.yml"),
    )


def test_ambient_compose_file_cannot_replace_config_or_cleanup_source(
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    candidate_root = tmp_path / "candidate"
    rogue_compose = tmp_path / "rogue.yml"
    environment, removed = runner._sanitized_environment(
        {
            "PATH": "kept",
            "COMPOSE_FILE": str(rogue_compose),
            "COMPOSE_PATH_SEPARATOR": ";",
        }
    )
    prefix = runner._compose_prefix("docker", "isolated", candidate_root)
    config_command = runner._compose_config_command(prefix)
    cleanup_command = (*prefix, "down", "--volumes", "--remove-orphans")

    assert environment == {"PATH": "kept"}
    assert removed == ["COMPOSE_FILE", "COMPOSE_PATH_SEPARATOR"]
    assert str(rogue_compose) not in config_command
    assert str(rogue_compose) not in cleanup_command
    assert config_command[: len(prefix)] == prefix
    assert cleanup_command[: len(prefix)] == prefix
    assert config_command[config_command.index("--file") + 1] == str(
        candidate_root / "docker-compose.yml"
    )
    assert cleanup_command[cleanup_command.index("--file") + 1] == str(
        candidate_root / "docker-compose.yml"
    )


def test_compose_source_environment_is_removed_case_insensitively(runner: ModuleType) -> None:
    sanitized, removed = runner._sanitized_environment(
        {
            "PATH": "kept",
            "COMPOSER_HOME": "kept-too",
            "GITHUB_ACTIONS": "kept-three",
            "CoMpOsE_fIlE": "rogue.yml",
            "compose_path_separator": ";",
            "COMPOSE_PROFILES": "rogue",
            "Compose_Env_Files": "rogue.env",
            "cOmPoSe_CoNvErT_WiNdOwS_pAtHs": "1",
            "compose_bake": "true",
            "Compose_Ignore_Orphans": "true",
            "cOmPoSe_ReMoVe_OrPhAnS": "true",
            "gIt_DiR": "rogue-git-dir",
            "Git_Config_Count": "1",
        }
    )

    assert sanitized == {
        "PATH": "kept",
        "COMPOSER_HOME": "kept-too",
        "GITHUB_ACTIONS": "kept-three",
    }
    assert removed == [
        "COMPOSE_BAKE",
        "COMPOSE_CONVERT_WINDOWS_PATHS",
        "COMPOSE_ENV_FILES",
        "COMPOSE_FILE",
        "COMPOSE_IGNORE_ORPHANS",
        "COMPOSE_PATH_SEPARATOR",
        "COMPOSE_PROFILES",
        "COMPOSE_REMOVE_ORPHANS",
        "GIT_CONFIG_COUNT",
        "GIT_DIR",
    ]
    assert "rogue.yml" not in json.dumps(removed)


def test_compose_interpolation_host_secrets_are_removed_before_controlled_injection(
    runner: ModuleType,
) -> None:
    compose_source = (ROOT / "docker-compose.yml").read_bytes()
    interpolation_keys = runner._compose_interpolation_keys(compose_source)
    assert {
        "APP_ENV",
        "BACKEND_BUILD_TARGET",
        "BACKEND_IMAGE",
        "ENCRYPTION_KEY",
        "JWT_SECRET",
        "RAGFLOW_API_KEY",
        "SMTP_PASSWORD",
        "VCS_REF",
    }.issubset(interpolation_keys)

    host_sentinel = "host-secret-sentinel-must-not-reach-candidate"
    sanitized, removed = runner._sanitized_environment(
        {
            "PATH": "kept",
            "jWt_SeCrEt": host_sentinel,
            "ENCRYPTION_KEY": host_sentinel,
            "ragflow_api_key": host_sentinel,
            "SmTp_PaSsWoRd": host_sentinel,
            "APP_ENV": "production",
            "BACKEND_BUILD_TARGET": "runtime",
            "BACKEND_IMAGE": "production-image",
            "VCS_REF": "untrusted-revision",
        },
        compose_interpolation_keys=interpolation_keys,
    )
    assert sanitized == {"PATH": "kept"}
    assert removed == [
        "APP_ENV",
        "BACKEND_BUILD_TARGET",
        "BACKEND_IMAGE",
        "ENCRYPTION_KEY",
        "JWT_SECRET",
        "RAGFLOW_API_KEY",
        "SMTP_PASSWORD",
        "VCS_REF",
    ]

    controlled = runner._controlled_runtime_environment(
        image_tag="governance:test",
        expected_sha="a" * 40,
    )
    sanitized.update(controlled)
    assert set(controlled) == runner.CONTROLLED_RUNTIME_ENVIRONMENT_KEYS
    assert host_sentinel not in sanitized.values()
    assert sanitized["APP_ENV"] == "test"
    assert sanitized["BACKEND_IMAGE"] == "governance:test"
    assert sanitized["VCS_REF"] == "a" * 40
    assert runner._runtime_environment_is_controlled(
        sanitized,
        compose_interpolation_keys=interpolation_keys,
        controlled_environment=controlled,
    )

    contaminated = dict(sanitized)
    contaminated["smtp_password"] = host_sentinel
    assert not runner._runtime_environment_is_controlled(
        contaminated,
        compose_interpolation_keys=interpolation_keys,
        controlled_environment=controlled,
    )


def test_compose_binding_rejects_source_or_normalized_config_swap(runner: ModuleType) -> None:
    def result(name: str, stdout: bytes, returncode: int = 0) -> object:
        return runner.ProcessResult(
            name=name,
            command=("docker", "compose", "config", "--no-interpolate"),
            returncode=returncode,
            stdout=stdout,
            stderr=b"",
            duration_ms=1,
        )

    expected_source = "a" * 64
    before = result("before", b"services:\n  backend-api: {}\n")
    after = result("after", b"services:\n  backend-api: {}\n")
    assert runner._compose_binding_passed(
        expected_source_sha256=expected_source,
        source_sha256_before=expected_source,
        source_sha256_after=expected_source,
        config_before=before,
        config_after=after,
    )

    assert not runner._compose_binding_passed(
        expected_source_sha256=expected_source,
        source_sha256_before=expected_source,
        source_sha256_after="b" * 64,
        config_before=before,
        config_after=after,
    )
    assert not runner._compose_binding_passed(
        expected_source_sha256=expected_source,
        source_sha256_before=expected_source,
        source_sha256_after=expected_source,
        config_before=before,
        config_after=result("after", b"services:\n  attacker: {}\n"),
    )
    assert not runner._compose_binding_passed(
        expected_source_sha256=expected_source,
        source_sha256_before=expected_source,
        source_sha256_after=expected_source,
        config_before=result("before", b"", returncode=1),
        config_after=after,
    )


def test_interpolated_compose_config_secret_is_never_hashed(runner: ModuleType) -> None:
    secret_config = b"JWT_SECRET=low-entropy-secret\nRAGFLOW_API_KEY=sk-sensitive\n"
    unsafe = runner.ProcessResult(
        name="compose_config_before",
        command=("docker", "compose", "config"),
        returncode=0,
        stdout=secret_config,
        stderr=b"secret-in-error",
        duration_ms=1,
    )

    assert runner._config_digest(unsafe) is None
    assert not runner._compose_binding_passed(
        expected_source_sha256="a" * 64,
        source_sha256_before="a" * 64,
        source_sha256_after="a" * 64,
        config_before=unsafe,
        config_after=unsafe,
    )
    unsafe_evidence = runner._phase_evidence(unsafe)
    serialized = json.dumps(unsafe_evidence)
    assert unsafe_evidence["compose_config_no_interpolate"] is False
    assert unsafe_evidence["content_digests_suppressed"] is True
    assert unsafe_evidence["stdout_sha256"] is None
    assert unsafe_evidence["stderr_sha256"] is None
    assert hashlib.sha256(secret_config).hexdigest() not in serialized

    safe = runner.ProcessResult(
        name="compose_config_before",
        command=("docker", "compose", "config", "--no-interpolate"),
        returncode=0,
        stdout=b"JWT_SECRET: ${JWT_SECRET:-synthetic-default}\n",
        stderr=b"",
        duration_ms=1,
    )
    safe_evidence = runner._phase_evidence(safe)
    assert runner._config_digest(safe) == hashlib.sha256(safe.stdout).hexdigest()
    assert safe_evidence["compose_config_no_interpolate"] is True
    assert safe_evidence["content_digests_suppressed"] is False


def test_runtime_names_are_unique_and_revision_scoped(
    runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tokens = iter(("00112233", "44556677"))
    monkeypatch.setattr(runner.secrets, "token_hex", lambda _byte_count: next(tokens))
    first = runner._runtime_names("a" * 40)
    second = runner._runtime_names("a" * 40)
    assert first == (
        "ku-gov-aaaaaaaaaaaa-00112233",
        "knowledge-uploader-governance:aaaaaaaaaaaa-00112233",
    )
    assert second != first
    assert second[1].endswith("-44556677")


def test_runtime_initialization_failure_removes_exact_temporary_tree(
    runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "governance-runtime"

    def fake_mkdtemp(*, prefix: str) -> str:
        assert prefix == "isolated-"
        runtime_root.mkdir()
        (runtime_root / "sentinel.txt").write_text("created", encoding="utf-8")
        return str(runtime_root)

    def fail_after_reports_created(*_arguments: object) -> tuple[str, ...]:
        raise OSError("synthetic initialization failure")

    monkeypatch.setattr(runner.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(runner, "_compose_prefix", fail_after_reports_created)

    with pytest.raises(runner.GovernanceAcceptanceError, match="initialization failed"):
        runner._initialize_runtime_layout(
            project_name="isolated",
            docker_executable="docker",
        )
    assert not runtime_root.exists()


@pytest.mark.parametrize(
    "value",
    ["a" * 39, "a" * 41, "A" * 40, "g" * 40, "not-a-sha"],
)
def test_expected_sha_rejects_non_exact_identity(runner: ModuleType, value: str) -> None:
    with pytest.raises(runner.GovernanceAcceptanceError):
        runner._validate_expected_sha(value)


def test_candidate_identity_uses_full_untracked_status(
    runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_git(
        _root: Path,
        *arguments: str,
        environment: dict[str, str] | None = None,
    ) -> bytes:
        assert environment == {"PATH": "safe"}
        calls.append(arguments)
        return (b"a" * 40 + b"\n") if arguments == ("rev-parse", "HEAD") else b""

    monkeypatch.setattr(runner, "_git_bytes", fake_git)
    identity = runner.candidate_identity(ROOT, environment={"PATH": "safe"})
    assert identity.git_sha == "a" * 40
    assert identity.clean is True
    assert calls == [
        ("rev-parse", "HEAD"),
        ("status", "--porcelain=v1", "--untracked-files=all"),
    ]


def test_dirty_or_mismatched_candidate_is_refused(runner: ModuleType) -> None:
    with pytest.raises(runner.GovernanceAcceptanceError, match="fully clean"):
        runner._assert_candidate(
            runner.CandidateIdentity("a" * 40, "?? untracked.txt"),
            "a" * 40,
        )
    with pytest.raises(runner.GovernanceAcceptanceError, match="does not match"):
        runner._assert_candidate(
            runner.CandidateIdentity("a" * 40, ""),
            "b" * 40,
        )


def test_missing_exact_node_is_refused(
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    test_file = tmp_path / "backend/app/tests/unit/test_missing.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("def test_present():\n    pass\n", encoding="utf-8")
    plan = runner.AcceptancePlan(
        acceptance_id="AI-001",
        scope="negative contract",
        targets=(
            runner.TestTarget(
                "backend_pytest",
                "backend/app/tests/unit/test_missing.py::test_removed",
                "missing node must fail closed",
            ),
        ),
    )
    with pytest.raises(runner.GovernanceAcceptanceError, match="pytest node is missing"):
        runner._validate_test_targets(tmp_path, (plan,))


@pytest.mark.parametrize("modifier", ["skip", "todo", "only"])
def test_disabled_or_exclusive_vitest_target_is_refused(
    runner: ModuleType,
    tmp_path: Path,
    modifier: str,
) -> None:
    test_file = tmp_path / "frontend/src/components/layout/TopHeader.test.tsx"
    test_file.parent.mkdir(parents=True)
    test_name = "shows expiry responsibility"
    test_file.write_text(
        f'it.{modifier}("{test_name}", () => {{}});\n',
        encoding="utf-8",
    )
    plan = runner.AcceptancePlan(
        acceptance_id="EXP-001",
        scope="negative contract",
        targets=(
            runner.TestTarget(
                "frontend_vitest",
                f"frontend/src/components/layout/TopHeader.test.tsx::{test_name}",
                "disabled or exclusive target must fail closed",
            ),
        ),
    )
    with pytest.raises(runner.GovernanceAcceptanceError, match="disabled or exclusive"):
        runner._validate_test_targets(tmp_path, (plan,))


@pytest.mark.parametrize("modifier", ["skip", "only"])
def test_disabled_or_exclusive_vitest_suite_is_refused(
    runner: ModuleType,
    tmp_path: Path,
    modifier: str,
) -> None:
    test_file = tmp_path / "frontend/src/components/layout/TopHeader.test.tsx"
    test_file.parent.mkdir(parents=True)
    test_name = "shows expiry responsibility"
    test_file.write_text(
        f'describe.{modifier}("suite", () => {{ it("{test_name}", () => {{}}); }});\n',
        encoding="utf-8",
    )
    plan = runner.AcceptancePlan(
        acceptance_id="EXP-001",
        scope="negative suite contract",
        targets=(
            runner.TestTarget(
                "frontend_vitest",
                f"frontend/src/components/layout/TopHeader.test.tsx::{test_name}",
                "suite modifier must fail closed",
            ),
        ),
    )
    with pytest.raises(runner.GovernanceAcceptanceError, match="suite is disabled"):
        runner._validate_test_targets(tmp_path, (plan,))


def test_output_must_be_external_absolute_and_create_only(
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    with pytest.raises(runner.GovernanceAcceptanceError, match="absolute"):
        runner._validate_output_dir(ROOT, Path("relative-evidence"))
    with pytest.raises(runner.GovernanceAcceptanceError, match="outside"):
        runner._validate_output_dir(ROOT, ROOT / "artifacts" / "governance")

    existing = tmp_path / "existing-evidence"
    existing.mkdir()
    sentinel = existing / "sentinel.txt"
    sentinel.write_text("do not overwrite", encoding="utf-8")
    with pytest.raises(runner.GovernanceAcceptanceError, match="must not already exist"):
        runner._validate_output_dir(ROOT, existing)
    assert sentinel.read_text(encoding="utf-8") == "do not overwrite"


def test_atomic_seal_writes_manifest_without_temporary_residue(
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    output = tmp_path / "sealed-evidence"
    evidence = {"schema_version": runner.EVIDENCE_SCHEMA_VERSION, "status": "failed"}
    runner._seal_evidence(output, evidence)

    payload = (output / "evidence.json").read_bytes()
    assert json.loads(payload) == evidence
    assert (output / "manifest.sha256").read_text(encoding="utf-8") == (
        f"{hashlib.sha256(payload).hexdigest()}  evidence.json\n"
    )
    assert not list(tmp_path.glob(".sealed-evidence.tmp-*"))


def test_test_or_cleanup_failure_can_never_be_candidate_passed(runner: ModuleType) -> None:
    baseline = {
        "compose_bound": True,
        "build_passed": True,
        "image_bound": True,
        "backend_passed": True,
        "frontend_passed": True,
        "cleanup_passed": True,
        "candidate_unchanged": True,
    }
    assert runner._final_status(**baseline) == "candidate_passed"
    for failed_gate in baseline:
        failed = dict(baseline)
        failed[failed_gate] = False
        assert runner._final_status(**failed) == "failed"


def test_raw_logs_are_hashed_but_never_archived(runner: ModuleType) -> None:
    raw_stdout = b"private original and prompt"
    raw_stderr = b"sk-private-key"
    result = runner.ProcessResult(
        name="negative",
        command=("pytest",),
        returncode=1,
        stdout=raw_stdout,
        stderr=raw_stderr,
        duration_ms=1,
    )
    record = runner._phase_evidence(result)
    serialized = json.dumps(record)
    assert record["stdout_sha256"] == hashlib.sha256(raw_stdout).hexdigest()
    assert record["stderr_sha256"] == hashlib.sha256(raw_stderr).hexdigest()
    assert record["raw_logs_archived"] is False
    assert "private original" not in serialized
    assert "sk-private-key" not in serialized


def test_windows_command_wrapper_is_resolved_explicitly(
    runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner.shutil, "which", lambda _name: r"C:\Program Files\nodejs\npm.CMD")
    assert runner._resolve_executable("npm").endswith("npm.CMD")
    monkeypatch.setattr(runner.shutil, "which", lambda _name: None)
    with pytest.raises(runner.GovernanceAcceptanceError, match="unavailable"):
        runner._resolve_executable("npm")


def test_pytest_junit_requires_every_exact_target_to_pass(
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    report = tmp_path / "backend.junit.xml"
    expected = ("app/tests/unit/test_sample.py::test_target",)
    report.write_text(
        '<testsuites><testsuite tests="2" failures="0" errors="0" skipped="0">'
        '<testcase classname="app.tests.unit.test_sample" name="test_target[first]" />'
        '<testcase classname="app.tests.unit.test_sample" name="test_target[second]" />'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    passed = runner._pytest_report_closure(report, expected)
    assert passed.passed is True
    assert passed.executed_targets == 1
    assert passed.total_cases == 2
    assert passed.nonpassed_cases == 0

    report.write_text(
        '<testsuites><testsuite tests="1" failures="0" errors="0" skipped="1">'
        '<testcase classname="app.tests.unit.test_sample" name="test_target">'
        '<skipped type="pytest.skip" /></testcase></testsuite></testsuites>',
        encoding="utf-8",
    )
    skipped = runner._pytest_report_closure(report, expected)
    assert skipped.passed is False
    assert skipped.nonpassed_cases == 1

    missing = runner._pytest_report_closure(
        report,
        ("app/tests/unit/test_sample.py::test_removed",),
    )
    assert missing.passed is False
    assert missing.reason == "target_identity_mismatch"


def test_vitest_json_requires_selected_target_identity_and_pass_status(
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    frontend_root = tmp_path / "frontend"
    test_file = frontend_root / "src/layouts/TopHeader.test.tsx"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("test source", encoding="utf-8")
    report = tmp_path / "frontend.vitest.json"
    expected = ("src/layouts/TopHeader.test.tsx::selected target",)
    payload = {
        "success": True,
        "numTotalTests": 1,
        "numPassedTests": 1,
        "numFailedTests": 0,
        "numPendingTests": 0,
        "numTodoTests": 0,
        "testResults": [
            {
                "name": str(test_file),
                "assertionResults": [{"title": "selected target", "status": "passed"}],
            }
        ],
    }
    report.write_text(json.dumps(payload), encoding="utf-8")
    passed = runner._vitest_report_closure(report, expected, frontend_root)
    assert passed.passed is True
    assert passed.executed_targets == 1

    payload["numPassedTests"] = 0
    payload["numPendingTests"] = 1
    payload["testResults"][0]["assertionResults"][0]["status"] = "pending"
    report.write_text(json.dumps(payload), encoding="utf-8")
    pending = runner._vitest_report_closure(report, expected, frontend_root)
    assert pending.passed is False
    assert pending.nonpassed_cases == 1

    payload["numTotalTests"] = 2
    payload["numPassedTests"] = 2
    payload["numPendingTests"] = 0
    payload["testResults"][0]["assertionResults"] = [
        {"title": "selected target", "status": "passed"},
        {"title": "unexpected passing target", "status": "passed"},
    ]
    report.write_text(json.dumps(payload), encoding="utf-8")
    unexpected = runner._vitest_report_closure(report, expected, frontend_root)
    assert unexpected.passed is False


def test_runner_contract_is_isolated_and_never_claims_external_llm(runner: ModuleType) -> None:
    source = (ROOT / "scripts/run_governance_acceptance.py").read_text(encoding="utf-8")
    assert runner.TEST_DATABASE_NAME == "knowledge_uploader_governance_acceptance_test"
    assert runner.TEST_DATABASE_NAME.endswith("_test")
    assert runner.TEST_REDIS_DB == "15"
    assert '"--untracked-files=all"' in source
    assert '"--volumes", "--remove-orphans"' in source
    assert "compose_volume_cleanup_check" in source
    assert '"external_llm_verified": False' in source
    assert '"cost002_not_evaluated": True' in source
    assert '"protocol_substitute_only": True' in source
    assert "TEST_CACHE_REDIS_URL=redis://redis:6379/15" in source
    assert "temporary.replace(output_dir)" in source
    assert "candidate_worktree_add" in source
    assert '"--detach"' in source
    assert "backend.junit.xml" in source
    assert "frontend.vitest.json" in source
    assert "xfail_strict=true" in source
    assert '_resolve_executable("npm")' in source
    assert "candidate_worktree_remove" in source
    assert "runtime_report_tree_remove" in source
    assert "_validate_test_targets(candidate_root)" in source
    assert '"--project-directory"' in source
    assert '"--file"' in source
    assert '"--no-interpolate"' in source
    assert "compose_config_before" in source
    assert "compose_config_after" in source
    assert "compose_source_sha256_before" in source
    assert "compose_binding_passed" in source
    assert '"compose_config_no_interpolate"' in source
    assert "_compose_interpolation_keys(expected_compose_source)" in source
    assert '"compose_environment_values_archived": False' in source
    assert 'TOOL_ENVIRONMENT_PREFIXES = ("COMPOSE_", "GIT_")' in source
    assert "compose_environment_keys_removed" in source
