"""Generate, validate, and atomically publish the MinIO metrics bearer token."""

from __future__ import annotations

import os
import re
import shutil
import signal
import ssl
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import FrameType
from typing import BinaryIO
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)

from app.core.jwt_validation import is_semantic_time_bound_jwt
from app.core.minio_endpoint import strict_minio_base_url

TOKEN_DIRECTORY = Path("/run/secrets/minio-metrics")
TOKEN_PATH = TOKEN_DIRECTORY / "token"
TOKEN_MAX_BYTES = 16 * 1024
MAX_COMMAND_BYTES = 4 * 1024 * 1024
MAX_CA_CERTIFICATE_BYTES = 1024 * 1024
COMMAND_TIMEOUT_SECONDS = 30.0
PROCESS_TERMINATION_GRACE_SECONDS = 2.0
PROCESS_SIGNAL_POLL_SECONDS = 0.1
TERMINATION_SIGNALS = tuple(
    signal.Signals(raw_signal)
    for name in ("SIGHUP", "SIGINT", "SIGTERM")
    if (raw_signal := getattr(signal, name, None)) is not None
)
BEARER_LINE_PATTERN = re.compile(
    r"^\s*bearer_token:\s*[\\\"']?([A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)[\\\"']?\s*$",
    flags=re.MULTILINE,
)


class TokenInitializationError(RuntimeError):
    """A deliberately message-free fail-closed initialization error."""


class TokenInitializationInterrupted(BaseException):
    """A message-free signal path that is propagated after child cleanup."""


def _validate_token(token: str) -> None:
    try:
        is_ascii = len(token.encode("ascii", errors="strict")) == len(token)
    except UnicodeError:
        is_ascii = False
    if not is_ascii or len(token) + 1 > TOKEN_MAX_BYTES or not is_semantic_time_bound_jwt(token):
        raise TokenInitializationError


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value or value != value.strip():
        raise TokenInitializationError
    return value


def _secure_mode() -> bool:
    value = _required_environment("MINIO_SECURE").lower()
    if value in {"true", "1"}:
        return True
    if value in {"false", "0"}:
        return False
    raise TokenInitializationError


def _validated_ca_file() -> Path:
    path = Path(_required_environment("MINIO_CA_CERT_FILE"))
    try:
        metadata = path.lstat()
    except OSError as error:
        raise TokenInitializationError from error
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_size <= 0
        or metadata.st_size > MAX_CA_CERTIFICATE_BYTES
    ):
        raise TokenInitializationError
    return path


def _client_environment(
    *,
    working_directory: Path,
    secure: bool,
) -> dict[str, str]:
    config_directory = working_directory / "mc"
    try:
        config_directory.mkdir(mode=0o700)
        metadata = config_directory.lstat()
    except OSError as error:
        raise TokenInitializationError from error
    if (
        config_directory.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or (os.name == "posix" and stat.S_IMODE(metadata.st_mode) != 0o700)
    ):
        raise TokenInitializationError
    environment = {
        "HOME": str(working_directory),
        "MC_CONFIG_DIR": str(config_directory),
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
    }
    if secure:
        environment["SSL_CERT_FILE"] = str(_validated_ca_file())
    return environment


def _mc_command(arguments: list[str]) -> list[str]:
    return ["mc", *arguments]


def _signal_process(process: subprocess.Popen[bytes], *, force: bool) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            selected_signal = signal.SIGKILL if force else signal.SIGTERM
            os.killpg(process.pid, selected_signal)
        elif force:
            process.kill()
        else:
            process.terminate()
    except (OSError, ProcessLookupError):
        return


def _deliver_cleanup_signal(
    process: subprocess.Popen[bytes],
    *,
    force: bool,
) -> bool:
    interrupted = False
    deadline = time.monotonic() + PROCESS_TERMINATION_GRACE_SECONDS
    while process.poll() is None:
        try:
            _signal_process(process, force=force)
            return interrupted
        except TokenInitializationInterrupted:
            interrupted = True
            if time.monotonic() >= deadline:
                return interrupted
    return interrupted


def _cleanup_communicate(
    process: subprocess.Popen[bytes],
    *,
    timeout: float,
) -> tuple[bool, bool, BaseException | None]:
    interrupted = False
    deadline = time.monotonic() + timeout
    while process.poll() is None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            process.communicate(timeout=min(PROCESS_SIGNAL_POLL_SECONDS, remaining))
        except subprocess.TimeoutExpired:
            continue
        except TokenInitializationInterrupted:
            interrupted = True
            continue
        except (OSError, subprocess.SubprocessError) as error:
            return process.poll() is not None, interrupted, error
        else:
            break
    return process.poll() is not None, interrupted, None


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return

    interrupted = _deliver_cleanup_signal(process, force=False)
    reaped, interrupted_while_waiting, cleanup_error = _cleanup_communicate(
        process,
        timeout=PROCESS_TERMINATION_GRACE_SECONDS,
    )
    interrupted = interrupted or interrupted_while_waiting

    if not reaped:
        interrupted = _deliver_cleanup_signal(process, force=True) or interrupted
        reaped, interrupted_while_waiting, force_error = _cleanup_communicate(
            process,
            timeout=PROCESS_TERMINATION_GRACE_SECONDS,
        )
        interrupted = interrupted or interrupted_while_waiting
        cleanup_error = cleanup_error or force_error

    if not reaped:
        if interrupted:
            raise TokenInitializationInterrupted from cleanup_error
        raise TokenInitializationError from cleanup_error
    if interrupted:
        raise TokenInitializationInterrupted
    if cleanup_error is not None:
        raise TokenInitializationError from cleanup_error


def _bounded_output_sizes(
    stdout_stream: BinaryIO,
    stderr_stream: BinaryIO,
) -> tuple[int, int]:
    try:
        stdout_size = os.fstat(stdout_stream.fileno()).st_size
        stderr_size = os.fstat(stderr_stream.fileno()).st_size
    except OSError as error:
        raise TokenInitializationError from error
    if stdout_size < 0 or stderr_size < 0 or stdout_size + stderr_size >= MAX_COMMAND_BYTES:
        raise TokenInitializationError
    return stdout_size, stderr_size


def _read_exact_output(stream: BinaryIO, *, size: int) -> bytes:
    try:
        stream.seek(0)
        payload = stream.read(size + 1)
    except OSError as error:
        raise TokenInitializationError from error
    if not isinstance(payload, bytes) or len(payload) != size:
        raise TokenInitializationError
    return payload


def _communicate_with_signal_poll(
    process: subprocess.Popen[bytes],
    *,
    stdout_stream: BinaryIO,
    stderr_stream: BinaryIO,
) -> tuple[bytes, bytes]:
    deadline = time.monotonic() + COMMAND_TIMEOUT_SECONDS
    while process.poll() is None:
        _bounded_output_sizes(stdout_stream, stderr_stream)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TokenInitializationError
        time.sleep(min(PROCESS_SIGNAL_POLL_SECONDS, remaining))
    stdout_size, stderr_size = _bounded_output_sizes(stdout_stream, stderr_stream)
    return (
        _read_exact_output(stdout_stream, size=stdout_size),
        _read_exact_output(stderr_stream, size=stderr_size),
    )


def _run_mc(
    arguments: list[str],
    *,
    environment: dict[str, str],
) -> subprocess.CompletedProcess[bytes]:
    command = _mc_command(arguments)
    with tempfile.TemporaryFile() as stdout_stream, tempfile.TemporaryFile() as stderr_stream:
        process: subprocess.Popen[bytes] | None = None
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=stdout_stream,
                stderr=stderr_stream,
                env=environment,
                start_new_session=os.name == "posix",
                creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0),
            )
            stdout, stderr = _communicate_with_signal_poll(
                process,
                stdout_stream=stdout_stream,
                stderr_stream=stderr_stream,
            )
        except (OSError, subprocess.SubprocessError) as error:
            if process is not None:
                _stop_process(process)
            raise TokenInitializationError from error
        except BaseException as error:
            if process is not None:
                try:
                    _stop_process(process)
                except TokenInitializationInterrupted:
                    raise
                except TokenInitializationError as cleanup_error:
                    if isinstance(error, TokenInitializationInterrupted):
                        raise TokenInitializationInterrupted from cleanup_error
                    raise
            raise

        result = subprocess.CompletedProcess(
            command,
            process.returncode,
            stdout=stdout,
            stderr=stderr,
        )
        if result.returncode != 0 or len(result.stdout) + len(result.stderr) >= MAX_COMMAND_BYTES:
            raise TokenInitializationError
        return result


def _generate_token(
    *,
    base_url: str,
    user: str,
    password: str,
    environment: dict[str, str],
) -> str:
    _run_mc(
        ["alias", "set", "metrics", base_url, user, password],
        environment=environment,
    )
    generated = _run_mc(
        ["admin", "prometheus", "generate", "metrics"],
        environment=environment,
    )
    try:
        output = generated.stdout.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise TokenInitializationError from error
    matches = BEARER_LINE_PATTERN.findall(output)
    if len(matches) != 1:
        raise TokenInitializationError
    token = matches[0]
    _validate_token(token)
    return token


def _ssl_context(*, secure: bool) -> ssl.SSLContext | None:
    if not secure:
        return None
    return ssl.create_default_context(cafile=str(_validated_ca_file()))


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self,
        _request: object,
        _file_pointer: object,
        _code: int,
        _message: str,
        _headers: object,
        _new_url: str,
    ) -> None:
        return None


def _verify_metrics_access(*, metrics_url: str, secure: bool, token: str) -> None:
    request = Request(
        metrics_url,
        headers={"Authorization": f"Bearer {token}"},
    )
    handlers: list[object] = [ProxyHandler({}), _NoRedirect()]
    context = _ssl_context(secure=secure)
    if context is not None:
        handlers.append(HTTPSHandler(context=context))
    opener = build_opener(*handlers)
    with opener.open(request, timeout=10) as response:
        if response.status != 200 or response.geturl() != metrics_url:
            raise TokenInitializationError
        response.read(1)


def _open_token_directory() -> int:
    try:
        TOKEN_DIRECTORY.mkdir(mode=0o755, parents=True, exist_ok=True)
        if TOKEN_DIRECTORY.is_symlink():
            raise TokenInitializationError
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(TOKEN_DIRECTORY, flags)
        if os.name == "posix":
            os.fchmod(descriptor, 0o755)
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode) or (
            os.name == "posix"
            and (
                stat.S_IMODE(metadata.st_mode) != 0o755
                or metadata.st_uid != 0
                or metadata.st_gid != 0
            )
        ):
            os.close(descriptor)
            raise TokenInitializationError
        return descriptor
    except OSError as error:
        raise TokenInitializationError from error


def _write_atomic(token: str) -> None:
    directory_descriptor = _open_token_directory()
    descriptor = -1
    temporary_path: Path | None = None
    try:
        descriptor, raw_path = tempfile.mkstemp(
            prefix=".token.tmp.",
            dir=TOKEN_DIRECTORY,
        )
        temporary_path = Path(raw_path)
        payload = (token + "\n").encode("ascii")
        if len(payload) > TOKEN_MAX_BYTES or payload.count(b"\n") != 1:
            raise TokenInitializationError
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
            if os.name == "posix":
                os.fchown(stream.fileno(), 65534, 65534)
                os.fchmod(stream.fileno(), 0o440)
            else:
                temporary_path.chmod(0o440)
            os.fsync(stream.fileno())
        metadata = temporary_path.lstat()
        if (
            temporary_path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or (
                os.name == "posix"
                and (
                    stat.S_IMODE(metadata.st_mode) != 0o440
                    or metadata.st_uid != 65534
                    or metadata.st_gid != 65534
                )
            )
            or temporary_path.read_bytes() != payload
        ):
            raise TokenInitializationError
        temporary_path.replace(TOKEN_PATH)
        temporary_path = None
        published = TOKEN_PATH.lstat()
        if (
            TOKEN_PATH.is_symlink()
            or not stat.S_ISREG(published.st_mode)
            or TOKEN_PATH.read_bytes() != payload
        ):
            raise TokenInitializationError
        os.fsync(directory_descriptor)
    except OSError as error:
        raise TokenInitializationError from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        os.close(directory_descriptor)


def main() -> None:
    working_directory: Path | None = None

    def terminate(_signum: int, _frame: FrameType | None) -> None:
        raise TokenInitializationInterrupted

    previous_handlers: dict[signal.Signals, signal.Handlers] = {}
    try:
        for current in TERMINATION_SIGNALS:
            previous_handlers[current] = signal.signal(current, terminate)

        endpoint = _required_environment("MINIO_ENDPOINT")
        root_user = _required_environment("MINIO_ROOT_USER")
        root_password = _required_environment("MINIO_ROOT_PASSWORD")
        secure = _secure_mode()
        base_url = strict_minio_base_url(
            endpoint,
            secure=secure,
            allowed_hosts={"minio"},
            allowed_ports={9000},
        )

        working_directory = Path(
            tempfile.mkdtemp(
                prefix="knowledge-uploader-metrics-mc.",
                dir="/tmp",
            )
        )
        working_directory.chmod(0o700)
        metadata = working_directory.lstat()
        if (
            working_directory.is_symlink()
            or not stat.S_ISDIR(metadata.st_mode)
            or (os.name == "posix" and stat.S_IMODE(metadata.st_mode) != 0o700)
        ):
            raise TokenInitializationError
        environment = _client_environment(
            working_directory=working_directory,
            secure=secure,
        )
        token = _generate_token(
            base_url=base_url,
            user=root_user,
            password=root_password,
            environment=environment,
        )
        _verify_metrics_access(
            metrics_url=f"{base_url}/minio/v2/metrics/cluster",
            secure=secure,
            token=token,
        )
        _write_atomic(token)
    finally:
        try:
            if working_directory is not None:
                shutil.rmtree(working_directory)
        finally:
            for current, previous in previous_handlers.items():
                signal.signal(current, previous)


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        sys.exit(1)
