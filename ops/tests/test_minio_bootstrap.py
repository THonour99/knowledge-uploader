from __future__ import annotations

import _thread
import importlib
import importlib.util
import os
import signal
import stat
import sys
import threading
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).parents[2]
BACKEND = ROOT / "backend"


@pytest.fixture(scope="module")
def modules() -> tuple[ModuleType, ModuleType]:
    sys.path.insert(0, str(BACKEND))
    try:
        strict_json = importlib.import_module("app.core.strict_json")
        path = BACKEND / "scripts/minio_bootstrap.py"
        spec = importlib.util.spec_from_file_location("minio_bootstrap_contract", path)
        if spec is None or spec.loader is None:
            raise RuntimeError("could not load MinIO bootstrap")
        bootstrap = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = bootstrap
        spec.loader.exec_module(bootstrap)
        return strict_json, bootstrap
    finally:
        sys.path.remove(str(BACKEND))


@pytest.mark.parametrize(
    "payload",
    (
        b'{"outer":{"value":1,"value":2}}',
        b'{"value":NaN}',
        b'{"value":Infinity}',
        b'{"value":1e999}',
        b'["not-an-object"]',
        b'"not-an-object"',
        b'{"value":\xff}',
    ),
)
def test_shared_strict_json_rejects_ambiguous_or_non_object_payloads(
    modules: tuple[ModuleType, ModuleType],
    payload: bytes,
) -> None:
    strict_json, _bootstrap = modules

    with pytest.raises(strict_json.StrictJsonError) as captured:
        strict_json.strict_json_object(payload)

    assert str(captured.value) == ""
    assert payload.decode("utf-8", errors="ignore") not in str(captured.value)


def test_shared_strict_json_accepts_a_finite_unique_object(
    modules: tuple[ModuleType, ModuleType],
) -> None:
    strict_json, _bootstrap = modules

    assert strict_json.strict_json_object(b'{"nested":{"value":1.5}}') == {"nested": {"value": 1.5}}


@pytest.mark.parametrize(
    "payload",
    (
        b'{"status":"success","value":1,"value":2}\n',
        b'{"status":"success","value":NaN}\n',
        b'{"status":"success","value":1e999}\n',
        b'["not-an-object"]\n',
        b'{"status":"error"}\n',
        b"",
    ),
)
def test_bootstrap_json_command_rejects_non_strict_or_unsuccessful_output(
    modules: tuple[ModuleType, ModuleType],
    monkeypatch: pytest.MonkeyPatch,
    payload: bytes,
) -> None:
    _strict_json, bootstrap = modules
    monkeypatch.setattr(
        bootstrap,
        "_run_mc",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=payload, stderr=b"", returncode=0),
    )

    with pytest.raises(bootstrap.BootstrapError) as captured:
        bootstrap._run_json(["admin", "group", "list", "bootstrap"], environment={})

    assert str(captured.value) == ""


def test_list_wrapper_accepts_exit_zero_empty_only_when_explicit(
    modules: tuple[ModuleType, ModuleType],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _strict_json, bootstrap = modules
    monkeypatch.setattr(
        bootstrap,
        "_run_mc",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=b"", stderr=b"", returncode=0),
    )

    assert (
        bootstrap._run_json(
            ["admin", "user", "list", "bootstrap"],
            environment={},
            allow_empty=True,
        )
        == []
    )
    with pytest.raises(bootstrap.BootstrapError):
        bootstrap._run_json(
            ["admin", "user", "info", "bootstrap", "data-user"],
            environment={},
        )


def test_exit_nonzero_empty_output_still_fails_before_json_contract(
    modules: tuple[ModuleType, ModuleType],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _strict_json, bootstrap = modules

    class FailedProcess:
        returncode = 1

        def communicate(self, *, timeout: float) -> tuple[bytes, bytes]:
            assert timeout > 0
            return b"", b""

        def poll(self) -> int:
            return self.returncode

    monkeypatch.setattr(
        bootstrap.subprocess,
        "Popen",
        lambda *_args, **_kwargs: FailedProcess(),
    )

    with pytest.raises(bootstrap.BootstrapError) as captured:
        bootstrap._run_mc(["admin", "user", "list", "bootstrap"], environment={})

    assert str(captured.value) == ""


def test_empty_user_list_after_removal_continues_to_exact_rebuild(
    modules: tuple[ModuleType, ModuleType],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _strict_json, bootstrap = modules
    access_key = "data-user"
    calls: list[list[str]] = []
    user_sets = iter(({access_key}, set()))
    expected = bootstrap._expected_policy("fixture-bucket")
    monkeypatch.setattr(
        bootstrap,
        "_run_mc",
        lambda arguments, **_kwargs: (
            calls.append(arguments) or SimpleNamespace(stdout=b"", stderr=b"", returncode=0)
        ),
    )
    monkeypatch.setattr(bootstrap, "_remove_group_memberships", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bootstrap, "_user_names", lambda **_kwargs: next(user_sets))
    monkeypatch.setattr(bootstrap, "_policy_names", lambda **_kwargs: set())
    monkeypatch.setattr(bootstrap, "_verify_user", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        bootstrap,
        "_user_policies",
        lambda *_args, **_kwargs: {bootstrap.POLICY_NAME},
    )
    monkeypatch.setattr(
        bootstrap,
        "_policy_entities",
        lambda *_args, **_kwargs: ({access_key}, set()),
    )
    monkeypatch.setattr(bootstrap, "_read_policy", lambda _path: expected)

    bootstrap._reconcile(
        base_url="https://minio:9000",
        root_user="root-user",
        root_password="Root-secret-123!",
        access_key=access_key,
        secret_key="Data-secret-123!",
        bucket="fixture-bucket",
        environment={},
        working_directory=tmp_path,
    )

    remove_index = calls.index(["admin", "user", "remove", "bootstrap", access_key])
    add_index = calls.index(["admin", "user", "add", "bootstrap", access_key, "Data-secret-123!"])
    assert remove_index < add_index
    assert any(arguments[:3] == ["admin", "policy", "create"] for arguments in calls)


def test_bootstrap_removes_target_from_every_group_before_rechecking(
    modules: tuple[ModuleType, ModuleType],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _strict_json, bootstrap = modules
    target = "data-user"
    members = {
        "alpha": {target},
        "beta": {target, "other-user"},
        "gamma": {"other-user"},
    }
    removed: list[str] = []
    monkeypatch.setattr(bootstrap, "_group_names", lambda **_kwargs: set(members))
    monkeypatch.setattr(
        bootstrap,
        "_group_members",
        lambda group, **_kwargs: set(members[group]),
    )

    def fake_run(arguments: list[str], **_kwargs: object) -> SimpleNamespace:
        assert arguments[:4] == ["admin", "group", "remove", "bootstrap"]
        group = arguments[4]
        assert arguments[5] == target
        removed.append(group)
        members[group].remove(target)
        return SimpleNamespace(stdout=b"", stderr=b"", returncode=0)

    monkeypatch.setattr(bootstrap, "_run_mc", fake_run)

    bootstrap._remove_group_memberships(target, environment={})

    assert removed == ["alpha", "beta"]
    assert all(target not in group_members for group_members in members.values())


def test_existing_policy_bound_to_another_entity_fails_closed_before_removal(
    modules: tuple[ModuleType, ModuleType],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _strict_json, bootstrap = modules
    calls: list[list[str]] = []
    monkeypatch.setattr(
        bootstrap,
        "_run_mc",
        lambda arguments, **_kwargs: (
            calls.append(arguments) or SimpleNamespace(stdout=b"", stderr=b"", returncode=0)
        ),
    )
    monkeypatch.setattr(bootstrap, "_remove_group_memberships", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bootstrap, "_user_names", lambda **_kwargs: set())
    monkeypatch.setattr(
        bootstrap,
        "_policy_names",
        lambda **_kwargs: {bootstrap.POLICY_NAME},
    )
    monkeypatch.setattr(
        bootstrap,
        "_policy_entities",
        lambda *_args, **_kwargs: ({"unrelated-user"}, set()),
    )

    with pytest.raises(bootstrap.BootstrapError) as captured:
        bootstrap._reconcile(
            base_url="https://minio:9000",
            root_user="root-user",
            root_password="root-secret",
            access_key="data-user",
            secret_key="data-secret",
            bucket="knowledge-files",
            environment={},
            working_directory=tmp_path,
        )

    assert str(captured.value) == ""
    assert not any(arguments[:3] == ["admin", "policy", "remove"] for arguments in calls)
    assert not any(arguments[:3] == ["admin", "policy", "create"] for arguments in calls)


def test_policy_file_is_exact_regular_mode_0600_and_private(
    modules: tuple[ModuleType, ModuleType],
    tmp_path: Path,
) -> None:
    strict_json, bootstrap = modules
    expected = bootstrap._expected_policy("knowledge-files")

    path = bootstrap._write_policy(tmp_path, expected)
    try:
        metadata = path.lstat()
        assert stat.S_ISREG(metadata.st_mode)
        if os.name == "posix":
            assert stat.S_IMODE(metadata.st_mode) == 0o600
        assert strict_json.strict_json_object(path.read_bytes()) == expected
    finally:
        path.unlink(missing_ok=True)


@pytest.mark.parametrize(
    ("name", "value"),
    (
        ("MINIO_ROOT_USER", "ab"),
        ("MINIO_ROOT_USER", "root-user-is-too-long1"),
        ("MINIO_ROOT_USER", "root$user"),
        ("MINIO_ACCESS_KEY", "ab"),
        ("MINIO_ACCESS_KEY", "data-user-is-too-long1"),
        ("MINIO_ACCESS_KEY", "data$user"),
        ("MINIO_ROOT_PASSWORD", "short7"),
        ("MINIO_ROOT_PASSWORD", "x" * 41),
        ("MINIO_SECRET_KEY", "short7"),
        ("MINIO_SECRET_KEY", "x" * 41),
        ("MINIO_ROOT_USER", "knowledge-root"),
        ("MINIO_ROOT_PASSWORD", "knowledge_root_password"),
        ("MINIO_ACCESS_KEY", "knowledge"),
        ("MINIO_SECRET_KEY", "knowledge_password"),
    ),
)
def test_protected_bootstrap_rejects_invalid_or_default_credentials_before_mc(
    modules: tuple[ModuleType, ModuleType],
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    _strict_json, bootstrap = modules
    environment = {
        "MINIO_ENDPOINT": "minio:9000",
        "MINIO_ROOT_USER": "root-user-123",
        "MINIO_ROOT_PASSWORD": "Root-secret-123!",
        "MINIO_ACCESS_KEY": "data-user-123",
        "MINIO_SECRET_KEY": "Data-secret-123!",
        "MINIO_BUCKET": "knowledge-files",
        "MINIO_SECURE": "true",
    }
    environment[name] = value
    for key, configured in environment.items():
        monkeypatch.setenv(key, configured)

    with pytest.raises(bootstrap.BootstrapError) as captured:
        bootstrap._validate_environment()

    assert str(captured.value) == ""
    assert value not in str(captured.value)


def test_development_defaults_remain_explicitly_non_tls_only(
    modules: tuple[ModuleType, ModuleType],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _strict_json, bootstrap = modules
    environment = {
        "MINIO_ENDPOINT": "minio:9000",
        "MINIO_ROOT_USER": "knowledge-root",
        "MINIO_ROOT_PASSWORD": "knowledge_root_password",
        "MINIO_ACCESS_KEY": "knowledge",
        "MINIO_SECRET_KEY": "knowledge_password",
        "MINIO_BUCKET": "knowledge-files",
        "MINIO_SECURE": "false",
    }
    for key, configured in environment.items():
        monkeypatch.setenv(key, configured)

    values = bootstrap._validate_environment()

    assert values[0] == "http://minio:9000"
    assert values[-1] is False


def test_secure_client_environment_installs_ca_in_private_mc_trust_directory(
    modules: tuple[ModuleType, ModuleType],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _strict_json, bootstrap = modules
    source = tmp_path / "source-ca.crt"
    source_payload = b"fixture-ca-certificate"
    source.write_bytes(source_payload)
    working_directory = tmp_path / "working"
    working_directory.mkdir(mode=0o700)
    monkeypatch.setenv("MINIO_CA_CERT_FILE", str(source))

    environment = bootstrap._client_environment(
        working_directory=working_directory,
        secure=True,
    )

    installed = working_directory / "mc" / "certs" / "CAs" / "minio-ca.crt"
    assert environment["MC_CONFIG_DIR"] == str(working_directory / "mc")
    assert environment["SSL_CERT_FILE"] == str(installed)
    assert installed.read_bytes() == source_payload
    assert source.read_bytes() == source_payload
    metadata = installed.lstat()
    assert stat.S_ISREG(metadata.st_mode)
    if os.name == "posix":
        assert stat.S_IMODE(metadata.st_mode) == 0o600
        for directory in (installed.parent, installed.parent.parent, installed.parents[2]):
            assert stat.S_IMODE(directory.lstat().st_mode) == 0o700


def test_secure_client_environment_rejects_symlink_and_oversized_ca(
    modules: tuple[ModuleType, ModuleType],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _strict_json, bootstrap = modules
    source = tmp_path / "source-ca.crt"
    source.write_bytes(b"fixture-ca-certificate")
    symlink = tmp_path / "linked-ca.crt"
    try:
        symlink.symlink_to(source)
    except OSError:
        symlink = source
    if symlink != source:
        monkeypatch.setenv("MINIO_CA_CERT_FILE", str(symlink))
        symlink_working = tmp_path / "symlink-working"
        symlink_working.mkdir()
        with pytest.raises(bootstrap.BootstrapError):
            bootstrap._client_environment(
                working_directory=symlink_working,
                secure=True,
            )

    oversized = tmp_path / "oversized-ca.crt"
    oversized.write_bytes(b"x" * (bootstrap.MAX_CA_CERTIFICATE_BYTES + 1))
    monkeypatch.setenv("MINIO_CA_CERT_FILE", str(oversized))
    oversized_working = tmp_path / "oversized-working"
    oversized_working.mkdir()
    with pytest.raises(bootstrap.BootstrapError):
        bootstrap._client_environment(
            working_directory=oversized_working,
            secure=True,
        )


def test_non_tls_client_environment_does_not_install_ca(
    modules: tuple[ModuleType, ModuleType],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _strict_json, bootstrap = modules
    monkeypatch.setenv("MINIO_CA_CERT_FILE", str(tmp_path / "missing-ca.crt"))
    working_directory = tmp_path / "working"
    working_directory.mkdir()

    environment = bootstrap._client_environment(
        working_directory=working_directory,
        secure=False,
    )

    assert "SSL_CERT_FILE" not in environment
    assert not (working_directory / "mc").exists()


def test_pinned_mc_json_shapes_map_to_exact_identity_sets(
    modules: tuple[ModuleType, ModuleType],
) -> None:
    _strict_json, bootstrap = modules
    group_list = [{"status": "success", "groups": ["drift-group"]}]
    group_info = [
        {
            "status": "success",
            "groupName": "drift-group",
            "groupStatus": "enabled",
            "groupPolicy": "readwrite",
            "members": ["data-user"],
        }
    ]
    user_list = [
        {
            "status": "success",
            "accessKey": "data-user",
            "userStatus": "enabled",
            "policyName": "readwrite",
        }
    ]
    policy_list = [
        {
            "status": "success",
            "policy": "knowledge-uploader-data-plane",
            "isGroup": False,
            "policyInfo": {"createDate": "redacted", "updateDate": "redacted"},
        }
    ]
    entity_result = [
        {
            "status": "success",
            "result": {
                "userMappings": [
                    {
                        "user": "data-user",
                        "policies": ["knowledge-uploader-data-plane"],
                    }
                ],
                "groupMappings": [],
            },
        }
    ]

    assert bootstrap._records_entities(group_list, keys=bootstrap.GROUP_KEYS) == {"drift-group"}
    assert bootstrap._records_entities(group_info, keys=bootstrap.MEMBER_KEYS) == {"data-user"}
    assert bootstrap._records_entities(user_list, keys=frozenset({"accessKey"})) == {"data-user"}
    assert bootstrap._records_entities(policy_list, keys=frozenset({"policy"})) == {
        "knowledge-uploader-data-plane"
    }
    assert bootstrap._records_entities(entity_result, keys=bootstrap.USER_KEYS) == {"data-user"}
    assert bootstrap._records_entities(entity_result, keys=bootstrap.POLICY_KEYS) == {
        "knowledge-uploader-data-plane"
    }
    assert bootstrap._records_entities(entity_result, keys=bootstrap.GROUP_KEYS) == set()


def test_exact_bucket_policy_accepts_only_mc_order_normalization(
    modules: tuple[ModuleType, ModuleType],
) -> None:
    _strict_json, bootstrap = modules
    policy = bootstrap._expected_policy("fixture-bucket")
    statements = policy["Statement"]
    assert isinstance(statements, list)
    normalized = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Resource": list(reversed(statement["Resource"])),
                "Action": list(reversed(statement["Action"])),
                "Effect": statement["Effect"],
            }
            for statement in reversed(statements)
        ],
    }

    bootstrap._verify_exact_bucket_policy(normalized, bucket="fixture-bucket")


@pytest.mark.parametrize(
    "case",
    (
        "version",
        "top_level_extra",
        "missing_statement",
        "extra_statement",
        "deny",
        "duplicate_action",
        "extra_action",
        "non_string_action",
        "action_not_list",
        "duplicate_resource",
        "extra_resource",
        "non_string_resource",
        "resource_not_list",
        "Sid",
        "Condition",
        "Principal",
        "NotAction",
        "NotResource",
    ),
)
def test_exact_bucket_policy_rejects_any_semantic_expansion_or_ambiguity(
    modules: tuple[ModuleType, ModuleType],
    case: str,
) -> None:
    import copy

    _strict_json, bootstrap = modules
    policy = copy.deepcopy(bootstrap._expected_policy("fixture-bucket"))
    statements = policy["Statement"]
    assert isinstance(statements, list)
    first = statements[0]
    assert isinstance(first, dict)
    if case == "version":
        policy["Version"] = "invalid"
    elif case == "top_level_extra":
        policy["Extra"] = True
    elif case == "missing_statement":
        statements.pop()
    elif case == "extra_statement":
        statements.append(copy.deepcopy(first))
    elif case == "deny":
        first["Effect"] = "Deny"
    elif case == "duplicate_action":
        first["Action"].append(first["Action"][0])
    elif case == "extra_action":
        first["Action"].append("s3:*")
    elif case == "non_string_action":
        first["Action"].append(1)
    elif case == "action_not_list":
        first["Action"] = first["Action"][0]
    elif case == "duplicate_resource":
        first["Resource"].append(first["Resource"][0])
    elif case == "extra_resource":
        first["Resource"].append("arn:aws:s3:::another-bucket")
    elif case == "non_string_resource":
        first["Resource"].append(1)
    elif case == "resource_not_list":
        first["Resource"] = first["Resource"][0]
    else:
        first[case] = "forbidden"

    with pytest.raises(bootstrap.BootstrapError) as captured:
        bootstrap._verify_exact_bucket_policy(policy, bucket="fixture-bucket")

    assert str(captured.value) == ""


def test_startup_convergence_retries_a_transient_allowlisted_command(
    modules: tuple[ModuleType, ModuleType],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _strict_json, bootstrap = modules
    calls: list[list[str]] = []

    def fake_run(arguments: list[str], **_kwargs: object) -> SimpleNamespace:
        calls.append(arguments)
        if len(calls) < 3:
            raise bootstrap.BootstrapError
        return SimpleNamespace(stdout=b"", stderr=b"", returncode=0)

    monkeypatch.setattr(bootstrap, "_run_mc", fake_run)
    monkeypatch.setattr(bootstrap.time, "sleep", lambda _seconds: None)
    arguments = [
        "alias",
        "set",
        "bootstrap",
        "https://minio:9000",
        "root-user",
        "root-secret",
    ]

    result = bootstrap._run_startup_command(arguments, environment={})

    assert result.returncode == 0
    assert calls == [arguments, arguments, arguments]


def test_startup_convergence_fails_closed_after_fixed_attempts(
    modules: tuple[ModuleType, ModuleType],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _strict_json, bootstrap = modules
    calls = 0

    def fail(*_args: object, **_kwargs: object) -> None:
        nonlocal calls
        calls += 1
        raise bootstrap.BootstrapError

    monkeypatch.setattr(bootstrap, "_run_mc", fail)
    monkeypatch.setattr(bootstrap.time, "sleep", lambda _seconds: None)

    with pytest.raises(bootstrap.BootstrapError) as captured:
        bootstrap._run_startup_command(
            ["mb", "--ignore-existing", "bootstrap/knowledge-files"],
            environment={},
        )

    assert str(captured.value) == ""
    assert calls == bootstrap.CONVERGENCE_ATTEMPTS


def test_startup_convergence_stops_when_wall_clock_budget_is_exhausted(
    modules: tuple[ModuleType, ModuleType],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _strict_json, bootstrap = modules
    clock = iter((0.0, 0.0, bootstrap.CONVERGENCE_TIMEOUT_SECONDS + 1.0))
    calls = 0

    def fail(*_args: object, **_kwargs: object) -> None:
        nonlocal calls
        calls += 1
        raise bootstrap.BootstrapError

    monkeypatch.setattr(bootstrap, "_run_mc", fail)
    monkeypatch.setattr(bootstrap.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(
        bootstrap.time,
        "sleep",
        lambda _seconds: pytest.fail("budget exhaustion must not sleep"),
    )

    with pytest.raises(bootstrap.BootstrapError):
        bootstrap._run_startup_command(
            ["mb", "--ignore-existing", "bootstrap/knowledge-files"],
            environment={},
        )

    assert calls == 1


def test_startup_convergence_rejects_non_allowlisted_mutation_without_calling_mc(
    modules: tuple[ModuleType, ModuleType],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _strict_json, bootstrap = modules
    calls = 0

    def observe(*_args: object, **_kwargs: object) -> None:
        nonlocal calls
        calls += 1

    monkeypatch.setattr(bootstrap, "_run_mc", observe)

    with pytest.raises(bootstrap.BootstrapError):
        bootstrap._run_startup_command(
            ["admin", "user", "add", "bootstrap", "data-user", "data-secret"],
            environment={},
        )

    assert calls == 0


def test_signal_interrupt_during_backoff_is_never_retried(
    modules: tuple[ModuleType, ModuleType],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _strict_json, bootstrap = modules
    attempts = 0

    def operation() -> None:
        nonlocal attempts
        attempts += 1
        raise bootstrap.BootstrapError

    def interrupt(_seconds: float) -> None:
        raise bootstrap.BootstrapInterrupted

    monkeypatch.setattr(bootstrap.time, "sleep", interrupt)

    with pytest.raises(bootstrap.BootstrapInterrupted):
        bootstrap._converge(operation)

    assert attempts == 1


def test_real_popen_interrupt_terminates_child_before_it_can_continue(
    modules: tuple[ModuleType, ModuleType],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _strict_json, bootstrap = modules
    started = tmp_path / "started"
    completed = tmp_path / "completed"
    program = (
        "from pathlib import Path; import time; "
        f"Path({str(started)!r}).write_text('started', encoding='utf-8'); "
        "time.sleep(30); "
        f"Path({str(completed)!r}).write_text('completed', encoding='utf-8')"
    )
    trigger_failures: list[str] = []

    monkeypatch.setattr(
        bootstrap,
        "_mc_command",
        lambda _arguments: [sys.executable, "-c", program],
    )

    def interrupt_parent() -> None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not started.exists():
            time.sleep(0.01)
        if not started.exists():
            trigger_failures.append("child_not_started")
        _thread.interrupt_main()

    trigger = threading.Thread(target=interrupt_parent, daemon=True)
    began_at = time.monotonic()
    trigger.start()
    with pytest.raises(KeyboardInterrupt):
        bootstrap._run_mc(
            ["alias", "set", "bootstrap", "unused", "unused", "unused"],
            environment=dict(os.environ),
        )
    trigger.join(timeout=5)
    elapsed = time.monotonic() - began_at

    assert elapsed < 5
    assert not trigger.is_alive()
    assert trigger_failures == []
    assert started.is_file()
    time.sleep(0.2)
    assert not completed.exists()


def test_signal_handler_interrupt_cleans_private_directory_without_entering_reconcile(
    modules: tuple[ModuleType, ModuleType],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _strict_json, bootstrap = modules
    working_directory = tmp_path / "bootstrap-private"
    handlers: dict[signal.Signals, object] = {}
    reconcile_calls = 0

    def make_private_directory(*_args: object, **_kwargs: object) -> str:
        working_directory.mkdir(mode=0o700)
        (working_directory / "private.tmp").write_bytes(b"private")
        return str(working_directory)

    def install_handler(current: signal.Signals, handler: object) -> object:
        previous = handlers.get(current, signal.SIG_DFL)
        handlers[current] = handler
        return previous

    def interrupt_reconcile(**_kwargs: object) -> None:
        nonlocal reconcile_calls
        reconcile_calls += 1
        handler = handlers[signal.SIGTERM]
        assert callable(handler)
        handler(signal.SIGTERM, None)
        pytest.fail("signal handler must interrupt reconcile immediately")

    monkeypatch.setattr(
        bootstrap,
        "_validate_environment",
        lambda: (
            "https://minio:9000",
            "root-user",
            "root-secret",
            "data-user",
            "data-secret",
            "knowledge-files",
            True,
        ),
    )
    monkeypatch.setattr(bootstrap, "_client_environment", lambda **_kwargs: {})
    monkeypatch.setattr(bootstrap.tempfile, "mkdtemp", make_private_directory)
    monkeypatch.setattr(bootstrap.signal, "signal", install_handler)
    monkeypatch.setattr(bootstrap, "_reconcile", interrupt_reconcile)

    with pytest.raises(bootstrap.BootstrapInterrupted):
        bootstrap.main()

    assert reconcile_calls == 1
    assert not working_directory.exists()


def test_bootstrap_bounded_output_preserves_content_and_overflow_reaps(
    modules: tuple[ModuleType, ModuleType],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _strict_json, bootstrap = modules

    class CompletedProcess:
        returncode = 0

        def poll(self) -> int:
            return self.returncode

    def completed_popen(
        _command: list[str],
        **kwargs: object,
    ) -> CompletedProcess:
        stdout = kwargs["stdout"]
        stderr = kwargs["stderr"]
        stdout.write(b"stdout")
        stderr.write(b"stderr")
        stdout.flush()
        stderr.flush()
        return CompletedProcess()

    monkeypatch.setattr(bootstrap.subprocess, "Popen", completed_popen)
    completed = bootstrap._run_mc(["version"], environment={})
    assert completed.stdout == b"stdout"
    assert completed.stderr == b"stderr"

    signals: list[bool] = []

    class OverflowProcess:
        returncode: int | None = None

        def poll(self) -> int | None:
            return self.returncode

        def communicate(self, *, timeout: float) -> tuple[bytes, bytes]:
            raise bootstrap.subprocess.TimeoutExpired("mc", timeout)

    overflow_process = OverflowProcess()

    def overflow_popen(_command: list[str], **kwargs: object) -> OverflowProcess:
        stdout = kwargs["stdout"]
        stdout.write(b"x" * 32)
        stdout.flush()
        return overflow_process

    def signal_process(_process: object, *, force: bool) -> None:
        signals.append(force)
        if force:
            overflow_process.returncode = -9

    monkeypatch.setattr(bootstrap.subprocess, "Popen", overflow_popen)
    monkeypatch.setattr(bootstrap, "_signal_process", signal_process)
    monkeypatch.setattr(bootstrap, "MAX_COMMAND_BYTES", 32)
    monkeypatch.setattr(bootstrap, "PROCESS_TERMINATION_GRACE_SECONDS", 0.002)
    monkeypatch.setattr(bootstrap, "PROCESS_SIGNAL_POLL_SECONDS", 0.001)

    with pytest.raises(bootstrap.BootstrapError) as captured:
        bootstrap._run_mc(["generate"], environment={})

    assert str(captured.value) == ""
    assert signals == [False, True]
    assert overflow_process.poll() == -9


def test_cleanup_interrupt_is_rethrown_after_child_is_reaped(
    modules: tuple[ModuleType, ModuleType],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _strict_json, bootstrap = modules

    class Process:
        returncode: int | None = None
        calls = 0

        def poll(self) -> int | None:
            return self.returncode

        def communicate(self, *, timeout: float) -> tuple[bytes, bytes]:
            assert timeout > 0
            self.calls += 1
            if self.calls == 1:
                raise bootstrap.BootstrapInterrupted
            self.returncode = -int(signal.SIGTERM)
            return b"", b""

    process = Process()
    monkeypatch.setattr(
        bootstrap,
        "_signal_process",
        lambda _process, *, force: None,
    )

    with pytest.raises(bootstrap.BootstrapInterrupted):
        bootstrap._stop_process(process)

    assert process.calls == 2
    assert process.poll() is not None


def test_original_interrupt_survives_kill_and_reap_failure(
    modules: tuple[ModuleType, ModuleType],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _strict_json, bootstrap = modules

    class NeverReapedProcess:
        returncode = None

        def poll(self) -> None:
            return None

        def communicate(self, *, timeout: float) -> tuple[bytes, bytes]:
            raise bootstrap.subprocess.TimeoutExpired("mc", timeout)

    process = NeverReapedProcess()
    monkeypatch.setattr(bootstrap.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        bootstrap,
        "_communicate_with_signal_poll",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(bootstrap.BootstrapInterrupted),
    )
    monkeypatch.setattr(
        bootstrap,
        "_signal_process",
        lambda _process, *, force: None,
    )
    monkeypatch.setattr(bootstrap, "PROCESS_TERMINATION_GRACE_SECONDS", 0.002)
    monkeypatch.setattr(bootstrap, "PROCESS_SIGNAL_POLL_SECONDS", 0.001)

    with pytest.raises(bootstrap.BootstrapInterrupted) as captured:
        bootstrap._run_mc(["alias", "set", "bootstrap"], environment={})

    assert isinstance(captured.value.__cause__, bootstrap.BootstrapError)


def test_bootstrap_source_is_silent_and_cleans_private_state() -> None:
    source = (BACKEND / "scripts/minio_bootstrap.py").read_text(encoding="utf-8")

    for required in (
        "tempfile.TemporaryFile()",
        "stdout=stdout_stream",
        "stderr=stderr_stream",
        "stdin=subprocess.DEVNULL",
        'start_new_session=os.name == "posix"',
        "_stop_process(process)",
        "_cleanup_communicate(",
        "except BootstrapInterrupted:",
        "raise BootstrapInterrupted from cleanup_error",
        "_communicate_with_signal_poll(",
        "policy_path.unlink(missing_ok=True)",
        "verified_path.unlink(missing_ok=True)",
        "shutil.rmtree(working_directory",
        "except BaseException:",
        "sys.exit(1)",
    ):
        assert required in source
    for forbidden in ("print(", "sys.stderr", "logging.", "logger."):
        assert forbidden not in source
