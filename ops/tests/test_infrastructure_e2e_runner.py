from __future__ import annotations

import ast
import importlib.util
import signal
import sys
import uuid
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

TEST_RAGFLOW_TLS_SPKI_PINS = (
    '{"https://mock-ragflow:9380":["sha256/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="]}'
)


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


def _load_certificate_generator() -> ModuleType:
    generator_path = (
        Path(__file__).parents[2] / "backend" / "scripts" / "generate_e2e_certificates.py"
    )
    spec = importlib.util.spec_from_file_location("generate_e2e_certificates", generator_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load E2E certificate generator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _resolved_compose(runner: ModuleType) -> dict[str, object]:
    backend_image = "backend:test"
    services: dict[str, object] = {
        name: {
            "image": backend_image,
            "environment": {
                "SSL_CERT_FILE": "/e2e-certs/ca.crt",
                "MINIO_CA_CERT_FILE": "/e2e-certs/ca.crt",
                "RAGFLOW_TLS_SPKI_PINS": TEST_RAGFLOW_TLS_SPKI_PINS,
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
            "RAGFLOW_TLS_SPKI_PINS": TEST_RAGFLOW_TLS_SPKI_PINS,
        },
        "volumes": [{"target": "/e2e-certs/ca.crt"}],
        "ports": [{"host_ip": "127.0.0.1"}],
        "build": {"args": {"MINIO_MC_IMAGE": runner.MINIO_MC_IMAGE}},
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
        "image": runner.MINIO_SERVER_IMAGE,
        "environment": {
            "MINIO_ROOT_USER": "metrics-root",
            "MINIO_ROOT_PASSWORD": "metrics-root-secret",
            "MINIO_PROMETHEUS_AUTH_TYPE": "jwt",
        },
        "volumes": [
            {"target": "/root/.minio/certs/public.crt"},
            {"target": "/root/.minio/certs/private.key"},
            {"target": "/root/.minio/certs/CAs/e2e-ca.crt"},
        ],
        "healthcheck": {"test": ["--cacert", "https://minio:9000/minio/health/cluster"]},
    }
    services["minio-bootstrap"] = {
        "image": backend_image,
        "entrypoint": ["python", "-m", "scripts.minio_bootstrap"],
        "command": None,
        "restart": "no",
        "environment": {
            "MINIO_ENDPOINT": "minio:9000",
            "MINIO_ROOT_USER": "metrics-root",
            "MINIO_ROOT_PASSWORD": "metrics-root-secret",
            "MINIO_ACCESS_KEY": "data-access",
            "MINIO_SECRET_KEY": "data-secret",
            "MINIO_BUCKET": "knowledge-files",
            "MINIO_SECURE": "true",
            "MINIO_CA_CERT_FILE": "/e2e-certs/ca.crt",
            "SSL_CERT_FILE": "/e2e-certs/ca.crt",
        },
        "depends_on": {"minio": {"condition": "service_healthy"}},
        "volumes": [
            {
                "source": "ca.crt",
                "target": "/e2e-certs/ca.crt",
                "read_only": True,
            }
        ],
    }
    services["minio-metrics-token-init"] = {
        "image": backend_image,
        "entrypoint": ["python", "-m", "scripts.minio_metrics_token_init"],
        "command": None,
        "restart": "no",
        "environment": {
            "MINIO_ENDPOINT": "minio:9000",
            "MINIO_ROOT_USER": "metrics-root",
            "MINIO_ROOT_PASSWORD": "metrics-root-secret",
            "MINIO_SECURE": "true",
            "MINIO_CA_CERT_FILE": "/e2e-certs/ca.crt",
            "SSL_CERT_FILE": "/e2e-certs/ca.crt",
        },
        "depends_on": {
            "minio": {"condition": "service_healthy"},
            "minio-bootstrap": {"condition": "service_completed_successfully"},
        },
        "volumes": [
            {
                "source": "minio-metrics-auth",
                "target": "/run/secrets/minio-metrics",
            },
            {
                "source": "ca.crt",
                "target": "/e2e-certs/ca.crt",
                "read_only": True,
            },
        ],
    }
    operational = services["operational-metrics"]
    assert isinstance(operational, dict)
    operational["environment"] = {
        "SSL_CERT_FILE": "/e2e-certs/ca.crt",
        "MINIO_CA_CERT_FILE": "/e2e-certs/ca.crt",
        "RAGFLOW_TLS_SPKI_PINS": TEST_RAGFLOW_TLS_SPKI_PINS,
        "MINIO_ACCESS_KEY": "metrics-bearer-only-no-data-plane",
        "MINIO_SECRET_KEY": "metrics-bearer-only-no-data-plane",
        "MINIO_METRICS_BEARER_TOKEN_FILE": "/run/secrets/minio-metrics/token",
    }
    operational["volumes"] = [
        {"target": "/e2e-certs/ca.crt"},
        {
            "source": "minio-metrics-auth",
            "target": "/run/secrets/minio-metrics",
            "read_only": True,
        },
    ]
    operational["depends_on"] = {
        "minio-metrics-token-init": {"condition": "service_completed_successfully"}
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
            {
                "source": "minio-metrics-auth",
                "target": "/run/secrets/minio-metrics",
                "read_only": True,
            },
        ],
        "depends_on": {"minio-metrics-token-init": {"condition": "service_completed_successfully"}},
    }
    return {"services": services}


def test_ragflow_spki_pin_is_derived_from_ephemeral_certificate(tmp_path: Path) -> None:
    runner = _load_runner()
    generator = _load_certificate_generator()
    certificate_dir = tmp_path / "certificates"
    generator.generate_certificates(certificate_dir)

    certificate_path = certificate_dir / "ragflow.crt"
    certificate_bytes = certificate_path.read_bytes()
    certificate = runner.x509.load_pem_x509_certificate(certificate_bytes)
    subject_public_key = certificate.public_key().public_bytes(
        runner.serialization.Encoding.DER,
        runner.serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    expected_pin = "sha256/" + runner.base64.b64encode(
        runner.hashlib.sha256(subject_public_key).digest()
    ).decode("ascii")

    mapping = runner._ragflow_tls_spki_pin_mapping(certificate_path)
    assert runner.json.loads(mapping) == {"https://mock-ragflow:9380": [expected_pin]}
    encoded_mapping = mapping.encode("utf-8")
    assert certificate_bytes not in encoded_mapping
    assert (certificate_dir / "ragflow.key").read_bytes() not in encoded_mapping
    assert b"BEGIN " not in encoded_mapping


@pytest.mark.parametrize("payload", (b"", b"not-a-certificate"))
def test_ragflow_spki_pin_rejects_invalid_certificate(
    tmp_path: Path,
    payload: bytes,
) -> None:
    runner = _load_runner()
    certificate_path = tmp_path / "ragflow.crt"
    certificate_path.write_bytes(payload)

    with pytest.raises(runner.InfrastructureE2EError, match="ragflow_spki_pin"):
        runner._ragflow_tls_spki_pin_mapping(certificate_path)


def test_ragflow_spki_pin_rejects_missing_and_noncanonical_certificate(tmp_path: Path) -> None:
    runner = _load_runner()
    missing_path = tmp_path / "missing.crt"
    with pytest.raises(runner.InfrastructureE2EError, match="ragflow_spki_pin"):
        runner._ragflow_tls_spki_pin_mapping(missing_path)

    generator = _load_certificate_generator()
    certificate_dir = tmp_path / "certificates"
    generator.generate_certificates(certificate_dir)
    certificate_path = certificate_dir / "ragflow.crt"
    certificate_path.write_bytes(certificate_path.read_bytes() + b"\n")
    with pytest.raises(runner.InfrastructureE2EError, match="ragflow_spki_pin"):
        runner._ragflow_tls_spki_pin_mapping(certificate_path)


def test_resolved_compose_contract_rejects_missing_or_mismatched_ragflow_pin() -> None:
    runner = _load_runner()
    for replacement in ("", TEST_RAGFLOW_TLS_SPKI_PINS.replace("A", "B", 1)):
        resolved = _resolved_compose(runner)
        services = resolved["services"]
        assert isinstance(services, dict)
        worker = services["worker-ragflow"]
        assert isinstance(worker, dict)
        environment = worker["environment"]
        assert isinstance(environment, dict)
        environment["RAGFLOW_TLS_SPKI_PINS"] = replacement

        with pytest.raises(runner.InfrastructureE2EError, match="resolved_compose_contract"):
            runner._validate_resolved_compose(
                resolved,
                backend_image="backend:test",
                frontend_image="frontend:test",
                ragflow_tls_spki_pins=TEST_RAGFLOW_TLS_SPKI_PINS,
            )


def test_service_has_volume_treats_omitted_read_only_as_writable() -> None:
    runner = _load_runner()
    service = {"volumes": [{"target": "/run/secrets/minio-metrics"}]}

    assert runner._service_has_volume(
        service,
        target="/run/secrets/minio-metrics",
        read_only=False,
    )
    assert not runner._service_has_volume(
        service,
        target="/run/secrets/minio-metrics",
        read_only=True,
    )


def test_minio_identity_reconciliation_uses_bounded_semantic_verifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner()
    captured: dict[str, object] = {}

    def fake_compose(
        _runner: object,
        _project: str,
        arguments: list[str],
        *,
        step: str,
        timeout_seconds: float = 180.0,
        check: bool = True,
    ) -> object:
        del timeout_seconds
        captured["arguments"] = arguments
        captured["step"] = step
        captured["check"] = check
        return runner.CommandResult(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(runner, "_compose", fake_compose)

    evidence = runner._verify_minio_identity_reconciliation(object(), "ku-e2e-test")

    arguments = captured["arguments"]
    assert isinstance(arguments, list)
    assert arguments[arguments.index("--entrypoint") + 1] == "python"
    program = arguments[-1]
    assert isinstance(program, str)
    assert "from scripts import minio_bootstrap as bootstrap" in program
    assert "bootstrap._run_mc" in program
    assert "bootstrap._group_names" in program
    assert "bootstrap._group_members" in program
    assert "bootstrap._user_policies" in program
    assert "bootstrap._policy_entities" in program
    assert "bootstrap._verify_exact_bucket_policy" in program
    assert '"policy":"' not in program
    assert '"accessKey":"' not in program
    assert captured["step"] == "minio_identity_reconciliation"
    assert captured["check"] is True
    assert evidence["status"] == "passed"


@pytest.mark.parametrize(
    ("group_members", "policies", "denial_is_rejection", "should_pass"),
    (
        (set(), {"knowledge-uploader-data-plane"}, True, True),
        ({"data-user"}, {"knowledge-uploader-data-plane"}, True, False),
        (set(), {"knowledge-uploader-data-plane", "unexpected-policy"}, True, False),
        (set(), {"knowledge-uploader-data-plane"}, False, False),
    ),
)
def test_minio_identity_verifier_executes_root_contrasts_and_semantic_sets(
    monkeypatch: pytest.MonkeyPatch,
    group_members: set[str],
    policies: set[str],
    denial_is_rejection: bool,
    should_pass: bool,
) -> None:
    runner = _load_runner()
    captured: dict[str, object] = {}

    def fake_compose(
        _runner: object,
        _project: str,
        arguments: list[str],
        **_kwargs: object,
    ) -> object:
        captured["program"] = arguments[-1]
        return runner.CommandResult(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(runner, "_compose", fake_compose)
    runner._verify_minio_identity_reconciliation(object(), "ku-e2e-test")
    program = captured["program"]
    assert isinstance(program, str)

    bootstrap = ModuleType("scripts.minio_bootstrap")

    class BootstrapError(RuntimeError):
        pass

    class CommandRejected(BootstrapError):
        pass

    calls: list[list[str]] = []

    def run_mc(arguments: list[str], *, environment: dict[str, str]) -> object:
        del environment
        calls.append(arguments)
        target = arguments[-1]
        is_secondary_data = isinstance(target, str) and target.startswith(
            "data/knowledge-files-isolation"
        )
        is_data_admin = arguments[0] == "admin" and "data" in arguments
        if is_secondary_data or is_data_admin:
            if denial_is_rejection:
                raise CommandRejected
            raise BootstrapError
        if arguments[:1] == ["cat"]:
            if target == "data/knowledge-files/e2e-reconciliation-probe":
                return SimpleNamespace(stdout=b"target-data", stderr=b"")
            if target == "bootstrap/knowledge-files-isolation/drift-object":
                return SimpleNamespace(stdout=b"drift", stderr=b"")
            if target == "bootstrap/knowledge-files-isolation/root-probe":
                return SimpleNamespace(stdout=b"root-probe", stderr=b"")
        return SimpleNamespace(stdout=b"", stderr=b"")

    bootstrap.BootstrapError = BootstrapError
    bootstrap.CommandRejected = CommandRejected
    bootstrap.POLICY_NAME = "knowledge-uploader-data-plane"
    bootstrap.BROAD_POLICIES = frozenset(
        {"consoleAdmin", "diagnostics", "readwrite", "readonly", "writeonly"}
    )
    bootstrap._validate_environment = lambda: (
        "http://minio:9000",
        "root-user",
        "root-secret",
        "data-user",
        "data-secret",
        "knowledge-files",
        False,
    )
    bootstrap._client_environment = lambda **_kwargs: {}
    bootstrap._run_mc = run_mc
    bootstrap._user_names = lambda **_kwargs: {"data-user"}
    bootstrap._group_names = lambda **_kwargs: {"drift-group"}
    bootstrap._group_members = lambda *_args, **_kwargs: set(group_members)
    bootstrap._user_policies = lambda *_args, **_kwargs: set(policies)
    bootstrap._policy_entities = lambda *_args, **_kwargs: ({"data-user"}, set())
    bootstrap._read_policy = lambda _path: {}
    bootstrap._verify_exact_bucket_policy = lambda *_args, **_kwargs: None

    scripts = ModuleType("scripts")
    scripts.minio_bootstrap = bootstrap
    monkeypatch.setitem(sys.modules, "scripts", scripts)
    monkeypatch.setitem(sys.modules, "scripts.minio_bootstrap", bootstrap)

    if should_pass:
        exec(program, {})
        assert calls.index(["ls", "bootstrap/knowledge-files-isolation"]) < calls.index(
            ["ls", "data/knowledge-files-isolation"]
        )
        assert calls.index(["admin", "info", "bootstrap"]) < calls.index(["admin", "info", "data"])
    else:
        with pytest.raises(SystemExit) as captured_exit:
            exec(program, {})
        assert captured_exit.value.code == 1


@pytest.mark.parametrize(("stdout", "stderr"), (("unexpected", ""), ("", "unexpected")))
def test_minio_identity_reconciliation_rejects_container_output(
    monkeypatch: pytest.MonkeyPatch,
    stdout: str,
    stderr: str,
) -> None:
    runner = _load_runner()

    def fake_compose(*_args: object, **_kwargs: object) -> object:
        return runner.CommandResult(returncode=0, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(runner, "_compose", fake_compose)

    with pytest.raises(runner.InfrastructureE2EError, match="minio_identity_reconciliation"):
        runner._verify_minio_identity_reconciliation(object(), "ku-e2e-test")


def test_atomic_publish_probe_installs_and_restores_term_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner()
    initializer = ModuleType("scripts.minio_metrics_token_init")

    class TokenInitializationInterrupted(BaseException):
        pass

    def interrupt_write(_token: str) -> None:
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        handler(signal.SIGTERM, None)

    initializer.os = SimpleNamespace(fsync=lambda _descriptor: None)
    initializer.TERMINATION_SIGNALS = (signal.SIGTERM,)
    initializer.TokenInitializationInterrupted = TokenInitializationInterrupted
    initializer._write_atomic = interrupt_write
    scripts = ModuleType("scripts")
    scripts.minio_metrics_token_init = initializer
    monkeypatch.setitem(sys.modules, "scripts", scripts)
    monkeypatch.setitem(sys.modules, "scripts.minio_metrics_token_init", initializer)
    original_handler = signal.getsignal(signal.SIGTERM)

    with pytest.raises(SystemExit) as captured_exit:
        exec(runner._atomic_publish_probe_program(), {})

    assert captured_exit.value.code == 1
    assert signal.getsignal(signal.SIGTERM) == original_handler


def test_rotated_minio_root_credentials_match_bootstrap_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner()
    requested_bytes: list[int] = []

    def fake_token_hex(byte_count: int) -> str:
        requested_bytes.append(byte_count)
        return "a" * (byte_count * 2)

    monkeypatch.setattr(runner.secrets, "token_hex", fake_token_hex)

    credentials = runner._rotated_minio_root_credentials()

    assert requested_bytes == [4, 20]
    assert set(credentials) == {"MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD"}
    assert runner.re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{2,19}",
        credentials["MINIO_ROOT_USER"],
    )
    assert runner.re.fullmatch(r"[!-~]{8,40}", credentials["MINIO_ROOT_PASSWORD"])
    assert len(credentials["MINIO_ROOT_PASSWORD"]) == 40


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
        ragflow_tls_spki_pins=TEST_RAGFLOW_TLS_SPKI_PINS,
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
            ragflow_tls_spki_pins=TEST_RAGFLOW_TLS_SPKI_PINS,
        )


@pytest.mark.parametrize(
    "mutation",
    (
        "public_minio",
        "missing_initializer",
        "writable_prometheus_token",
        "inline_token",
        "mutable_minio_image",
        "mutable_mc_image",
        "bootstrap_command_override",
        "initializer_command_override",
    ),
)
def test_resolved_compose_contract_rejects_minio_metrics_auth_downgrade(
    mutation: str,
) -> None:
    runner = _load_runner()
    resolved = _resolved_compose(runner)
    services = resolved["services"]
    assert isinstance(services, dict)

    if mutation == "public_minio":
        minio = services["minio"]
        assert isinstance(minio, dict)
        minio["environment"] = {"MINIO_PROMETHEUS_AUTH_TYPE": "public"}
    elif mutation == "missing_initializer":
        services.pop("minio-metrics-token-init")
    elif mutation == "bootstrap_command_override":
        bootstrap = services["minio-bootstrap"]
        assert isinstance(bootstrap, dict)
        bootstrap["command"] = ["python", "-c", "raise SystemExit(0)"]
    elif mutation == "initializer_command_override":
        initializer = services["minio-metrics-token-init"]
        assert isinstance(initializer, dict)
        initializer["command"] = ["python", "-c", "raise SystemExit(0)"]
    elif mutation == "mutable_minio_image":
        minio = services["minio"]
        assert isinstance(minio, dict)
        minio["image"] = "minio/minio:RELEASE.2024-04-18T19-09-19Z"
    elif mutation == "mutable_mc_image":
        backend = services["backend-api"]
        assert isinstance(backend, dict)
        build = backend["build"]
        assert isinstance(build, dict)
        args = build["args"]
        assert isinstance(args, dict)
        args["MINIO_MC_IMAGE"] = "minio/mc:RELEASE.2024-04-18T16-45-29Z"
    elif mutation == "writable_prometheus_token":
        prometheus = services["prometheus"]
        assert isinstance(prometheus, dict)
        volumes = prometheus["volumes"]
        assert isinstance(volumes, list)
        token_volume = next(
            volume
            for volume in volumes
            if isinstance(volume, dict) and volume.get("target") == "/run/secrets/minio-metrics"
        )
        token_volume["read_only"] = False
    else:
        operational = services["operational-metrics"]
        assert isinstance(operational, dict)
        environment = operational["environment"]
        assert isinstance(environment, dict)
        environment["MINIO_METRICS_BEARER_TOKEN"] = "header.payload.signature"

    with pytest.raises(runner.InfrastructureE2EError, match="resolved_compose_contract"):
        runner._validate_resolved_compose(
            resolved,
            backend_image="backend:test",
            frontend_image="frontend:test",
            ragflow_tls_spki_pins=TEST_RAGFLOW_TLS_SPKI_PINS,
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
            ragflow_tls_spki_pins=TEST_RAGFLOW_TLS_SPKI_PINS,
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
            ragflow_tls_spki_pins=TEST_RAGFLOW_TLS_SPKI_PINS,
        )


def test_resolved_compose_secret_material_is_parse_only(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    runner = _load_runner()
    canary = "config-only-secret-canary"
    resolved = _resolved_compose(runner)
    services = resolved["services"]
    assert isinstance(services, dict)
    for service in services.values():
        if not isinstance(service, dict):
            continue
        environment = service.get("environment")
        if isinstance(environment, dict) and "MINIO_ROOT_PASSWORD" in environment:
            environment["MINIO_ROOT_PASSWORD"] = canary
    raw_config = runner.json.dumps(resolved, separators=(",", ":"))

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        del kwargs
        assert command[-3:] == ["config", "--format", "json"]
        return SimpleNamespace(returncode=0, stdout=raw_config, stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    command_runner = runner.CommandRunner(environment={})
    result = runner._compose(
        command_runner,
        "ku-e2e-test",
        ["config", "--format", "json"],
        step="resolved_compose_contract",
    )
    parsed = runner._json_object(result.stdout, step="resolved_compose_contract")
    runner._validate_resolved_compose(
        parsed,
        backend_image="backend:test",
        frontend_image="frontend:test",
        ragflow_tls_spki_pins=TEST_RAGFLOW_TLS_SPKI_PINS,
    )

    digest = runner.hashlib.sha256(result.stdout.encode("utf-8")).hexdigest()
    evidence_path = tmp_path / "infrastructure-e2e.json"
    evidence_bytes = runner._atomic_write_json(
        evidence_path,
        {"resolved_compose_sha256": digest},
    )
    assert canary.encode("utf-8") not in evidence_bytes
    assert canary.encode("utf-8") not in evidence_path.read_bytes()

    parsed_services = parsed["services"]
    assert isinstance(parsed_services, dict)
    parsed_services.pop("frontend")
    with pytest.raises(runner.InfrastructureE2EError) as captured_error:
        runner._validate_resolved_compose(
            parsed,
            backend_image="backend:test",
            frontend_image="frontend:test",
            ragflow_tls_spki_pins=TEST_RAGFLOW_TLS_SPKI_PINS,
        )
    assert canary not in str(captured_error.value)
    assert canary not in repr(captured_error.value)
    captured_output = capsys.readouterr()
    assert canary not in captured_output.out
    assert canary not in captured_output.err

    source = Path(runner.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    run_gate = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "run_gate"
    )
    loaded_names = [
        node.id
        for node in ast.walk(run_gate)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    ]
    assert loaded_names.count("resolved_result") == 2
    assert loaded_names.count("resolved_compose") == 1
    assert loaded_names.count("resolved_compose_sha256") == 1


@pytest.mark.parametrize(
    ("logs_stdout", "raises"),
    (
        ("", False),
        ("header.payload.signature", True),
        ("runtime-log-secret-canary", True),
    ),
)
def test_minio_metrics_initializer_requires_completed_silent_container(
    monkeypatch: pytest.MonkeyPatch,
    logs_stdout: str,
    raises: bool,
) -> None:
    runner = _load_runner()
    container_id = "a" * 64

    def fake_compose(
        _runner: object,
        _project: str,
        arguments: list[str],
        *,
        step: str,
        timeout_seconds: float = 180.0,
        check: bool = True,
    ) -> object:
        del step, timeout_seconds, check
        assert arguments == ["ps", "--all", "--quiet", "minio-metrics-token-init"]
        return runner.CommandResult(returncode=0, stdout=container_id, stderr="")

    class FakeRunner:
        def run(
            self,
            command: list[str],
            *,
            step: str,
            timeout_seconds: float = 180.0,
            check: bool = True,
        ) -> object:
            del step, timeout_seconds, check
            if command[:2] == ["docker", "inspect"]:
                return runner.CommandResult(returncode=0, stdout="exited 0", stderr="")
            assert command == ["docker", "logs", container_id]
            return runner.CommandResult(returncode=0, stdout=logs_stdout, stderr="")

    monkeypatch.setattr(runner, "_compose", fake_compose)
    if raises:
        with pytest.raises(runner.InfrastructureE2EError, match="token_init_logs"):
            runner._verify_minio_metrics_initializer(FakeRunner(), "ku-e2e-test")
    else:
        runner._verify_minio_metrics_initializer(FakeRunner(), "ku-e2e-test")


def test_semantic_jwt_scanner_rejects_credentials_without_flagging_dotted_noise() -> None:
    runner = _load_runner()

    def segment(value: dict[str, object]) -> str:
        payload = runner.json.dumps(value, separators=(",", ":")).encode("utf-8")
        return runner.base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")

    credential = f"{segment({'alg': 'HS256'})}.{segment({'sub': 'minio'})}.eA"
    audience = f"{segment({'alg': 'HS256'})}.{segment({'aud': ['minio']})}.eA"
    timing_only = f"{segment({'alg': 'HS256'})}.{segment({'exp': 9999999999})}.eA"
    empty_identity = f"{segment({'alg': 'HS256'})}.{segment({'sub': ''})}.eA"
    assert runner._contains_semantic_jwt(credential)
    assert runner._contains_semantic_jwt(audience)
    assert not runner._contains_semantic_jwt("foo.bar.baz")
    assert not runner._contains_semantic_jwt(timing_only)
    assert not runner._contains_semantic_jwt(empty_identity)


def test_invalid_minio_metrics_token_is_replaced_without_exposure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner()
    commands: list[list[str]] = []

    def fake_compose(
        _runner: object,
        _project: str,
        arguments: list[str],
        *,
        step: str,
        timeout_seconds: float = 180.0,
        check: bool = True,
    ) -> object:
        del step, timeout_seconds, check
        commands.append(arguments)
        return runner.CommandResult(returncode=0, stdout="", stderr="")

    recovered = {"mtime_ns": 2}
    monkeypatch.setattr(runner, "_compose", fake_compose)
    monkeypatch.setattr(
        runner,
        "_minio_metrics_token_metadata",
        lambda *_args, **_kwargs: recovered,
    )

    assert runner._replace_invalid_minio_metrics_token(object(), "ku-e2e-test") == recovered
    assert commands[0][:9] == [
        "run",
        "--rm",
        "--no-deps",
        "--no-TTY",
        "--entrypoint",
        "python",
        "minio-metrics-token-init",
        "-c",
        commands[0][8],
    ]
    assert "a.b.c" in commands[0][8]
    assert commands[1] == [
        "run",
        "--rm",
        "--no-deps",
        "--no-TTY",
        "minio-metrics-token-init",
    ]
    assert 'prefix"alg":"HS256"suffix' not in commands[2][8]
    assert "cHJlZml4ImFsZyI6IkhTMjU2InN1ZmZpeA" in commands[2][8]
    assert commands[3] == commands[1]


def _configure_refresh_mocks(
    runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    *,
    mtime_ns: int,
    changed: bool,
    statuses: tuple[int, int] = (200, 200),
) -> tuple[list[list[str]], list[str]]:
    commands: list[list[str]] = []
    waits: list[str] = []

    def fake_compose(
        _runner: object,
        _project: str,
        arguments: list[str],
        *,
        step: str,
        timeout_seconds: float = 180.0,
        check: bool = True,
    ) -> object:
        del step, timeout_seconds, check
        commands.append(arguments)
        return runner.CommandResult(returncode=0, stdout="", stderr="")

    status_values = iter(statuses)
    monkeypatch.setattr(runner, "_compose", fake_compose)
    monkeypatch.setattr(
        runner,
        "_minio_metrics_consumer_container_ids",
        lambda *_args, **_kwargs: {
            "operational-metrics": "a" * 64,
            "prometheus": "b" * 64,
        },
    )
    monkeypatch.setattr(
        runner,
        "_wait_for_prometheus_minio_target_up",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(runner, "_copy_minio_metrics_token", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        runner,
        "_minio_metrics_token_metadata",
        lambda *_args, **_kwargs: {"mtime_ns": mtime_ns},
    )
    monkeypatch.setattr(
        runner,
        "_minio_metrics_token_files_differ",
        lambda *_args, **_kwargs: changed,
    )
    monkeypatch.setattr(
        runner,
        "_minio_metrics_http_status",
        lambda *_args, **_kwargs: next(status_values),
    )
    monkeypatch.setattr(
        runner,
        "_wait_for_minio_capacity_collector",
        lambda *_args, **_kwargs: waits.append("collector") or 2.0,
    )
    return commands, waits


def test_minio_metrics_refresh_preserves_old_jwt_until_expiry_without_exposure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner()
    commands, waits = _configure_refresh_mocks(
        runner,
        monkeypatch,
        mtime_ns=2,
        changed=True,
    )

    refresh, collector_timestamp = runner._refresh_minio_metrics_token(
        object(),
        "ku-e2e-test",
        previous_metadata={"mtime_ns": 1},
        previous_collector_timestamp=1.0,
    )

    assert refresh == {
        "status": "passed",
        "semantics": "consumer_refresh_not_revocation",
        "credential_changed": True,
        "mtime_advanced": True,
        "previous_jwt_http_status": 200,
        "refreshed_jwt_http_status": 200,
        "consumer_processes_unchanged": True,
        "prometheus_health_before": "up",
        "prometheus_health_after": "up",
    }
    assert collector_timestamp == 2.0
    assert commands[0] == [
        "run",
        "--rm",
        "--no-deps",
        "--no-TTY",
        "minio-metrics-token-init",
    ]
    assert all("MINIO_METRICS_TOKEN_ROTATE" not in argument for argument in commands[0])
    assert len(commands) == 1
    assert waits == ["collector"]


@pytest.mark.parametrize(
    ("mtime_ns", "changed", "statuses"),
    (
        (1, True, (200, 200)),
        (2, False, (200, 200)),
        (2, True, (403, 200)),
    ),
)
def test_minio_metrics_refresh_rejects_false_rotation_claims(
    monkeypatch: pytest.MonkeyPatch,
    mtime_ns: int,
    changed: bool,
    statuses: tuple[int, int],
) -> None:
    runner = _load_runner()
    _configure_refresh_mocks(
        runner,
        monkeypatch,
        mtime_ns=mtime_ns,
        changed=changed,
        statuses=statuses,
    )

    with pytest.raises(runner.InfrastructureE2EError, match="token_refresh"):
        runner._refresh_minio_metrics_token(
            object(),
            "ku-e2e-test",
            previous_metadata={"mtime_ns": 1},
            previous_collector_timestamp=1.0,
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
    assert evidence["failure_observation"] == ("postgres_failed_sync_task_before_remote_upload")
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
    assert evidence["failure_observation"] == ("celery_retry_requeued_while_cache_unavailable")
    assert evidence["retry_task_id"] == str(retry_task_id)
    assert evidence["retry_task_name"] == "ragflow.create_upload_task"
    assert evidence["retry_queue"] == "ragflow_queue"
    assert evidence["retry_count_observed"] == 1
    assert evidence["retry_status_before_restore"] == "requeued"
    assert evidence["remote_upload_delta"] == 1
