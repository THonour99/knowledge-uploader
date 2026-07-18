from __future__ import annotations

import base64
import importlib.util
import json
import os
import signal
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).parents[2]
BACKEND = ROOT / "backend"
MINIO_MC_IMAGE = (
    "minio/mc:RELEASE.2024-04-18T16-45-29Z"
    "@sha256:5a84109d6b29bab96c3122e4a7ba888fbf48d4cdc83bc8bf88e3a7ac67b970b8"
)


def _load_path(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def modules() -> tuple[ModuleType, ModuleType, ModuleType, ModuleType]:
    sys.path.insert(0, str(BACKEND))
    try:
        shared = _load_path(
            "app.core.jwt_validation",
            BACKEND / "app/core/jwt_validation.py",
        )
        endpoint = _load_path(
            "app.core.minio_endpoint",
            BACKEND / "app/core/minio_endpoint.py",
        )
        initializer = _load_path(
            "minio_metrics_token_init",
            BACKEND / "scripts/minio_metrics_token_init.py",
        )
        telemetry = _load_path(
            "minio_capacity_telemetry_contract",
            BACKEND / "app/core/minio_capacity_telemetry.py",
        )
        return shared, endpoint, initializer, telemetry
    finally:
        sys.path.remove(str(BACKEND))


def _segment(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _token(header: bytes, claims: bytes, signature: bytes = b"signature") -> str:
    return ".".join((_segment(header), _segment(claims), _segment(signature)))


def _json(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), allow_nan=False).encode()


def _vectors(now: float) -> tuple[tuple[str, bool], ...]:
    valid_header = _json({"alg": "HS256", "typ": "JWT"})
    valid_claims = _json({"sub": "minio", "iat": now - 5, "exp": now + 3600})
    return (
        (_token(valid_header, valid_claims), True),
        (_token(valid_header, _json({"aud": ["minio"], "exp": now + 3600})), True),
        (_token(valid_header, _json({"exp": now + 3600})), False),
        (_token(valid_header, _json({"sub": "", "exp": now + 3600})), False),
        (_token(valid_header, _json({"iss": "   ", "exp": now + 3600})), False),
        (_token(valid_header, _json({"accessKey": 42, "exp": now + 3600})), False),
        (_token(valid_header, _json({"aud": [], "exp": now + 3600})), False),
        (_token(valid_header, _json({"aud": [""], "exp": now + 3600})), False),
        (_token(b'{"alg":"none","alg":"HS256"}', valid_claims), False),
        (_token(valid_header, b'{"sub":"minio","exp":NaN}'), False),
        (_token(valid_header, b'{"sub":"minio","exp":1e999}'), False),
        (_token(b'{"alg":"HS256",}', valid_claims), False),
        (_token(b'["HS256"]', valid_claims), False),
        (_token(valid_header, b'["minio"]'), False),
        (_token(valid_header, _json({"sub": "minio", "exp": now - 1})), False),
        (
            _token(
                valid_header,
                _json({"sub": "minio", "nbf": now + 3600, "exp": now + 7200}),
            ),
            False,
        ),
        (
            _token(
                valid_header,
                _json({"sub": "minio", "iat": now + 3600, "exp": now + 7200}),
            ),
            False,
        ),
        (_token(_json({"alg": "none"}), valid_claims), False),
        (_token(valid_header, valid_claims, b""), False),
    )


def test_initializer_and_runtime_share_strict_jwt_vectors(
    modules: tuple[ModuleType, ModuleType, ModuleType, ModuleType],
) -> None:
    shared, _endpoint, initializer, telemetry = modules
    now = time.time()

    for token, expected in _vectors(now):
        assert shared.is_semantic_time_bound_jwt(token, now_seconds=now) is expected
        assert telemetry._is_semantic_jwt(token) is expected
        if expected:
            initializer._validate_token(token)
        else:
            with pytest.raises(initializer.TokenInitializationError) as captured:
                initializer._validate_token(token)
            assert str(captured.value) == ""
            assert token not in repr(captured.value)


def test_initializer_semantic_probe_reuses_scheme_hostname_ca_and_bearer(
    modules: tuple[ModuleType, ModuleType, ModuleType, ModuleType],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _shared, _endpoint, initializer, _telemetry = modules
    observed: dict[str, object] = {}
    context = object()

    class Response:
        status = 200

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def geturl(self) -> str:
            return "https://minio:9000/minio/v2/metrics/cluster"

        def read(self, size: int) -> bytes:
            observed["read_size"] = size
            return b"x"

    class Opener:
        def open(self, request: object, *, timeout: int) -> Response:
            observed["url"] = request.full_url
            observed["authorization"] = request.headers["Authorization"]
            observed["timeout"] = timeout
            return Response()

    def fake_build_opener(*handlers: object) -> Opener:
        observed["handler_names"] = [type(handler).__name__ for handler in handlers]
        return Opener()

    monkeypatch.setattr(initializer, "_ssl_context", lambda *, secure: context)
    monkeypatch.setattr(initializer, "build_opener", fake_build_opener)

    initializer._verify_metrics_access(
        metrics_url="https://minio:9000/minio/v2/metrics/cluster",
        secure=True,
        token="internal-test-token",
    )

    assert observed == {
        "url": "https://minio:9000/minio/v2/metrics/cluster",
        "authorization": "Bearer internal-test-token",
        "timeout": 10,
        "handler_names": ["ProxyHandler", "_NoRedirect", "HTTPSHandler"],
        "read_size": 1,
    }
    assert (
        initializer._NoRedirect().redirect_request(
            object(),
            object(),
            302,
            "redirect",
            object(),
            "https://outside.example",
        )
        is None
    )


@pytest.mark.parametrize(
    "endpoint",
    (
        "https://minio:9000",
        "user@minio:9000",
        "minio:9000/path",
        "minio:9000?query=value",
        "minio:9000#fragment",
        "outside.example:9000",
        "minio:9001",
        " minio:9000",
        "minio:9000 ",
    ),
)
def test_privileged_initializer_rejects_unsafe_or_external_endpoint(
    modules: tuple[ModuleType, ModuleType, ModuleType, ModuleType],
    endpoint: str,
) -> None:
    _shared, endpoint_module, _initializer, _telemetry = modules

    with pytest.raises(ValueError, match="MinIO endpoint is invalid") as captured:
        endpoint_module.strict_minio_base_url(
            endpoint,
            secure=True,
            allowed_hosts={"minio"},
            allowed_ports={9000},
        )
    assert endpoint not in str(captured.value)


def test_privileged_initializer_accepts_only_internal_minio_authority(
    modules: tuple[ModuleType, ModuleType, ModuleType, ModuleType],
) -> None:
    _shared, endpoint_module, _initializer, _telemetry = modules

    assert (
        endpoint_module.strict_minio_base_url(
            "minio:9000",
            secure=True,
            allowed_hosts={"minio"},
            allowed_ports={9000},
        )
        == "https://minio:9000"
    )


def test_initializer_uses_a_private_mc_config_and_exact_ca_path(
    modules: tuple[ModuleType, ModuleType, ModuleType, ModuleType],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _shared, _endpoint, initializer, _telemetry = modules
    ca_file = tmp_path / "ca.crt"
    ca_file.write_bytes(b"private-ca")
    working_directory = tmp_path / "private"
    working_directory.mkdir(mode=0o700)
    monkeypatch.setenv("MINIO_CA_CERT_FILE", str(ca_file))

    environment = initializer._client_environment(
        working_directory=working_directory,
        secure=True,
    )

    assert environment == {
        "HOME": str(working_directory),
        "MC_CONFIG_DIR": str(working_directory / "mc"),
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "SSL_CERT_FILE": str(ca_file),
    }
    assert (working_directory / "mc").is_dir()


def test_initializer_bounded_output_preserves_content_and_overflow_reaps(
    modules: tuple[ModuleType, ModuleType, ModuleType, ModuleType],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _shared, _endpoint, initializer, _telemetry = modules

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

    monkeypatch.setattr(initializer.subprocess, "Popen", completed_popen)
    completed = initializer._run_mc(["version"], environment={})
    assert completed.stdout == b"stdout"
    assert completed.stderr == b"stderr"

    signals: list[bool] = []

    class OverflowProcess:
        returncode: int | None = None

        def poll(self) -> int | None:
            return self.returncode

        def communicate(self, *, timeout: float) -> tuple[bytes, bytes]:
            raise initializer.subprocess.TimeoutExpired("mc", timeout)

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

    monkeypatch.setattr(initializer.subprocess, "Popen", overflow_popen)
    monkeypatch.setattr(initializer, "_signal_process", signal_process)
    monkeypatch.setattr(initializer, "MAX_COMMAND_BYTES", 32)
    monkeypatch.setattr(initializer, "PROCESS_TERMINATION_GRACE_SECONDS", 0.002)
    monkeypatch.setattr(initializer, "PROCESS_SIGNAL_POLL_SECONDS", 0.001)

    with pytest.raises(initializer.TokenInitializationError) as captured:
        initializer._run_mc(["generate"], environment={})

    assert str(captured.value) == ""
    assert signals == [False, True]
    assert overflow_process.poll() == -9


def test_token_publish_metadata_is_fsynced_before_atomic_replace() -> None:
    source = (BACKEND / "scripts/minio_metrics_token_init.py").read_text(encoding="utf-8")
    start = source.index("def _write_atomic(")
    end = source.index("\n\ndef main(", start)
    block = source[start:end]

    assert block.count("os.fsync(stream.fileno())") == 2
    first_fsync = block.index("os.fsync(stream.fileno())")
    fchown = block.index("os.fchown(stream.fileno(), 65534, 65534)")
    fchmod = block.index("os.fchmod(stream.fileno(), 0o440)")
    final_fsync = block.index("os.fsync(stream.fileno())", first_fsync + 1)
    replace = block.index("temporary_path.replace(TOKEN_PATH)")
    directory_fsync = block.index("os.fsync(directory_descriptor)")
    assert first_fsync < fchown < fchmod < final_fsync < replace < directory_fsync


def test_initializer_cleanup_interrupt_is_rethrown_after_child_is_reaped(
    modules: tuple[ModuleType, ModuleType, ModuleType, ModuleType],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _shared, _endpoint, initializer, _telemetry = modules

    class Process:
        returncode: int | None = None
        calls = 0

        def poll(self) -> int | None:
            return self.returncode

        def communicate(self, *, timeout: float) -> tuple[bytes, bytes]:
            assert timeout > 0
            self.calls += 1
            if self.calls == 1:
                raise initializer.TokenInitializationInterrupted
            self.returncode = -int(signal.SIGTERM)
            return b"", b""

    process = Process()
    monkeypatch.setattr(
        initializer,
        "_signal_process",
        lambda _process, *, force: None,
    )

    with pytest.raises(initializer.TokenInitializationInterrupted):
        initializer._stop_process(process)

    assert process.calls == 2
    assert process.poll() is not None


def test_initializer_original_interrupt_survives_kill_and_reap_failure(
    modules: tuple[ModuleType, ModuleType, ModuleType, ModuleType],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _shared, _endpoint, initializer, _telemetry = modules

    class NeverReapedProcess:
        returncode = None

        def poll(self) -> None:
            return None

        def communicate(self, *, timeout: float) -> tuple[bytes, bytes]:
            raise initializer.subprocess.TimeoutExpired("mc", timeout)

    process = NeverReapedProcess()
    monkeypatch.setattr(
        initializer.subprocess,
        "Popen",
        lambda *_args, **_kwargs: process,
    )
    monkeypatch.setattr(
        initializer,
        "_communicate_with_signal_poll",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(initializer.TokenInitializationInterrupted),
    )
    monkeypatch.setattr(
        initializer,
        "_signal_process",
        lambda _process, *, force: None,
    )
    monkeypatch.setattr(initializer, "PROCESS_TERMINATION_GRACE_SECONDS", 0.002)
    monkeypatch.setattr(initializer, "PROCESS_SIGNAL_POLL_SECONDS", 0.001)

    with pytest.raises(initializer.TokenInitializationInterrupted) as captured:
        initializer._run_mc(["alias", "set", "metrics"], environment={})

    assert isinstance(
        captured.value.__cause__,
        initializer.TokenInitializationError,
    )


def test_initializer_signal_covers_generation_and_cleans_private_config(
    modules: tuple[ModuleType, ModuleType, ModuleType, ModuleType],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _shared, _endpoint, initializer, _telemetry = modules
    working_directory = tmp_path / "initializer-private"
    handlers: dict[signal.Signals, object] = {}

    def make_private_directory(*_args: object, **_kwargs: object) -> str:
        working_directory.mkdir(mode=0o700)
        return str(working_directory)

    def install_handler(current: signal.Signals, handler: object) -> object:
        previous = handlers.get(current, signal.SIG_DFL)
        handlers[current] = handler
        return previous

    def interrupt_generation(**_kwargs: object) -> str:
        handler = handlers[signal.SIGTERM]
        assert callable(handler)
        handler(signal.SIGTERM, None)
        pytest.fail("signal handler must interrupt token generation")

    monkeypatch.setenv("MINIO_ENDPOINT", "minio:9000")
    monkeypatch.setenv("MINIO_ROOT_USER", "root-user")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "root-secret")
    monkeypatch.setenv("MINIO_SECURE", "false")
    monkeypatch.setattr(initializer.tempfile, "mkdtemp", make_private_directory)
    monkeypatch.setattr(initializer.signal, "signal", install_handler)
    monkeypatch.setattr(initializer, "_generate_token", interrupt_generation)

    with pytest.raises(initializer.TokenInitializationInterrupted):
        initializer.main()

    assert not working_directory.exists()


def test_backend_mc_stage_is_target_platform_pinned_and_not_build_platform() -> None:
    dockerfile = (BACKEND / "Dockerfile").read_text(encoding="utf-8")

    assert f"ARG MINIO_MC_IMAGE={MINIO_MC_IMAGE}" in dockerfile
    assert "FROM --platform=$TARGETPLATFORM ${MINIO_MC_IMAGE} AS minio-client" in dockerfile
    assert "COPY --from=minio-client /usr/bin/mc /usr/local/bin/mc" in dockerfile
    assert "FROM --platform=$BUILDPLATFORM ${MINIO_MC_IMAGE}" not in dockerfile

    ops_dockerfile = (ROOT / "ops/Dockerfile").read_text(encoding="utf-8")
    assert f"ARG MC_IMAGE={MINIO_MC_IMAGE}" in ops_dockerfile
    assert "FROM --platform=$TARGETPLATFORM ${MC_IMAGE} AS target-mc" in ops_dockerfile
    assert "COPY --from=target-mc /usr/bin/mc /usr/local/bin/mc" in ops_dockerfile
    assert "FROM --platform=$BUILDPLATFORM ${MC_IMAGE}" not in ops_dockerfile
