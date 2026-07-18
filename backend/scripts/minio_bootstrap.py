"""Reconcile the least-privileged MinIO data-plane identity without leaking secrets."""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterable
from contextvars import ContextVar
from pathlib import Path
from types import FrameType
from typing import BinaryIO, TypeVar

from app.core.minio_endpoint import strict_minio_base_url
from app.core.strict_json import StrictJsonError, strict_json_object

POLICY_NAME = "knowledge-uploader-data-plane"
MAX_COMMAND_BYTES = 4 * 1024 * 1024
MAX_CA_CERTIFICATE_BYTES = 1024 * 1024
COMMAND_TIMEOUT_SECONDS = 30
CONVERGENCE_ATTEMPTS = 8
CONVERGENCE_BACKOFF_SECONDS = 0.5
CONVERGENCE_TIMEOUT_SECONDS = 15.0
PROCESS_TERMINATION_GRACE_SECONDS = 2.0
PROCESS_SIGNAL_POLL_SECONDS = 0.1
TERMINATION_SIGNALS = tuple(
    signal.Signals(raw_signal)
    for name in ("SIGHUP", "SIGINT", "SIGTERM")
    if (raw_signal := getattr(signal, name, None)) is not None
)
ACCESS_KEY_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{2,19}")
SECRET_KEY_PATTERN = re.compile(r"[\x21-\x7e]{8,40}")
DEFAULT_ROOT_USERS = frozenset({"knowledge-root", "minioadmin"})
DEFAULT_ROOT_PASSWORDS = frozenset({"knowledge_root_password", "minioadmin"})
DEFAULT_DATA_USERS = frozenset({"knowledge", "minioadmin"})
DEFAULT_DATA_SECRETS = frozenset({"knowledge_password", "minioadmin"})
BUCKET_PATTERN = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]")
ENTITY_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@+-]{0,127}")
BROAD_POLICIES = frozenset({"consoleAdmin", "diagnostics", "readwrite", "readonly", "writeonly"})
BUCKET_ACTIONS = (
    "s3:GetBucketLocation",
    "s3:ListBucket",
    "s3:ListBucketMultipartUploads",
)
OBJECT_ACTIONS = (
    "s3:GetObject",
    "s3:PutObject",
    "s3:DeleteObject",
    "s3:AbortMultipartUpload",
    "s3:ListMultipartUploadParts",
)


class BootstrapError(RuntimeError):
    """A deliberately message-free fail-closed bootstrap error."""


class CommandRejected(BootstrapError):
    """The bounded mc command completed and returned a non-zero exit code."""


class BootstrapInterrupted(BaseException):
    """A message-free signal path that convergence must never retry."""


_T = TypeVar("_T")
_CONVERGENCE_DEADLINE: ContextVar[float | None] = ContextVar(
    "minio_bootstrap_convergence_deadline",
    default=None,
)


def _remaining_command_timeout() -> float:
    deadline = _CONVERGENCE_DEADLINE.get()
    if deadline is None:
        return float(COMMAND_TIMEOUT_SECONDS)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise BootstrapError
    return min(float(COMMAND_TIMEOUT_SECONDS), remaining)


def _converge(operation: Callable[[], _T]) -> _T:
    deadline = time.monotonic() + CONVERGENCE_TIMEOUT_SECONDS
    token = _CONVERGENCE_DEADLINE.set(deadline)
    try:
        for attempt in range(CONVERGENCE_ATTEMPTS):
            if deadline - time.monotonic() <= 0:
                raise BootstrapError
            try:
                return operation()
            except BootstrapError:
                if attempt + 1 >= CONVERGENCE_ATTEMPTS:
                    raise BootstrapError from None
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise BootstrapError from None
                time.sleep(min(CONVERGENCE_BACKOFF_SECONDS, remaining))
        raise BootstrapError
    finally:
        _CONVERGENCE_DEADLINE.reset(token)


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value or value != value.strip():
        raise BootstrapError
    return value


def _secure_mode() -> bool:
    value = _required_environment("MINIO_SECURE").lower()
    if value in {"true", "1"}:
        return True
    if value in {"false", "0"}:
        return False
    raise BootstrapError


def _validate_environment() -> tuple[str, str, str, str, str, str, bool]:
    endpoint = _required_environment("MINIO_ENDPOINT")
    root_user = _required_environment("MINIO_ROOT_USER")
    root_password = _required_environment("MINIO_ROOT_PASSWORD")
    access_key = _required_environment("MINIO_ACCESS_KEY")
    secret_key = _required_environment("MINIO_SECRET_KEY")
    bucket = _required_environment("MINIO_BUCKET")
    secure = _secure_mode()
    if (
        ACCESS_KEY_PATTERN.fullmatch(root_user) is None
        or ACCESS_KEY_PATTERN.fullmatch(access_key) is None
        or SECRET_KEY_PATTERN.fullmatch(root_password) is None
        or SECRET_KEY_PATTERN.fullmatch(secret_key) is None
        or BUCKET_PATTERN.fullmatch(bucket) is None
        or ".." in bucket
        or re.fullmatch(r"\d+\.\d+\.\d+\.\d+", bucket) is not None
        or root_user == access_key
        or root_password == secret_key
        or (
            secure
            and (
                root_user in DEFAULT_ROOT_USERS
                or root_password in DEFAULT_ROOT_PASSWORDS
                or access_key in DEFAULT_DATA_USERS
                or secret_key in DEFAULT_DATA_SECRETS
            )
        )
    ):
        raise BootstrapError
    base_url = strict_minio_base_url(
        endpoint,
        secure=secure,
        allowed_hosts={"minio"},
        allowed_ports={9000},
    )
    return base_url, root_user, root_password, access_key, secret_key, bucket, secure


def _read_ca_certificate(path: Path) -> bytes:
    descriptor = -1
    try:
        metadata = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            raise BootstrapError
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
        opened_metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened_metadata.st_mode)
            or opened_metadata.st_size <= 0
            or opened_metadata.st_size > MAX_CA_CERTIFICATE_BYTES
        ):
            raise BootstrapError
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = -1
            payload = stream.read(MAX_CA_CERTIFICATE_BYTES + 1)
        if len(payload) != opened_metadata.st_size:
            raise BootstrapError
        return payload
    except OSError as error:
        raise BootstrapError from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _install_mc_ca(*, working_directory: Path, source: Path) -> Path:
    payload = _read_ca_certificate(source)
    config_directory = working_directory / "mc"
    certificates_directory = config_directory / "certs"
    authorities_directory = certificates_directory / "CAs"
    try:
        for directory in (
            config_directory,
            certificates_directory,
            authorities_directory,
        ):
            directory.mkdir(mode=0o700)
            metadata = directory.lstat()
            if (
                directory.is_symlink()
                or not stat.S_ISDIR(metadata.st_mode)
                or (os.name == "posix" and stat.S_IMODE(metadata.st_mode) != 0o700)
            ):
                raise BootstrapError
        destination = authorities_directory / "minio-ca.crt"
        descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                descriptor = -1
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        installed = destination.lstat()
        if (
            destination.is_symlink()
            or not stat.S_ISREG(installed.st_mode)
            or installed.st_size != len(payload)
            or (os.name == "posix" and stat.S_IMODE(installed.st_mode) != 0o600)
            or destination.read_bytes() != payload
        ):
            raise BootstrapError
        return destination
    except OSError as error:
        raise BootstrapError from error


def _client_environment(*, working_directory: Path, secure: bool) -> dict[str, str]:
    environment = {
        "HOME": str(working_directory),
        "MC_CONFIG_DIR": str(working_directory / "mc"),
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
    }
    if secure:
        ca_file = Path(_required_environment("MINIO_CA_CERT_FILE"))
        installed_ca = _install_mc_ca(
            working_directory=working_directory,
            source=ca_file,
        )
        environment["SSL_CERT_FILE"] = str(installed_ca)
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
        except BootstrapInterrupted:
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
        except BootstrapInterrupted:
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
            raise BootstrapInterrupted from cleanup_error
        raise BootstrapError from cleanup_error
    if interrupted:
        raise BootstrapInterrupted
    if cleanup_error is not None:
        raise BootstrapError from cleanup_error


def _bounded_output_sizes(
    stdout_stream: BinaryIO,
    stderr_stream: BinaryIO,
) -> tuple[int, int]:
    try:
        stdout_size = os.fstat(stdout_stream.fileno()).st_size
        stderr_size = os.fstat(stderr_stream.fileno()).st_size
    except OSError as error:
        raise BootstrapError from error
    if stdout_size < 0 or stderr_size < 0 or stdout_size + stderr_size >= MAX_COMMAND_BYTES:
        raise BootstrapError
    return stdout_size, stderr_size


def _read_exact_output(stream: BinaryIO, *, size: int) -> bytes:
    try:
        stream.seek(0)
        payload = stream.read(size + 1)
    except OSError as error:
        raise BootstrapError from error
    if not isinstance(payload, bytes) or len(payload) != size:
        raise BootstrapError
    return payload


def _communicate_with_signal_poll(
    process: subprocess.Popen[bytes],
    *,
    stdout_stream: BinaryIO,
    stderr_stream: BinaryIO,
    timeout: float,
) -> tuple[bytes, bytes]:
    deadline = time.monotonic() + timeout
    while process.poll() is None:
        _bounded_output_sizes(stdout_stream, stderr_stream)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise BootstrapError
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
                timeout=_remaining_command_timeout(),
            )
        except (OSError, subprocess.SubprocessError) as error:
            if process is not None:
                _stop_process(process)
            raise BootstrapError from error
        except BaseException as error:
            if process is not None:
                try:
                    _stop_process(process)
                except BootstrapInterrupted:
                    raise
                except BootstrapError as cleanup_error:
                    if isinstance(error, BootstrapInterrupted):
                        raise BootstrapInterrupted from cleanup_error
                    raise
            raise
        result = subprocess.CompletedProcess(
            command,
            process.returncode,
            stdout=stdout,
            stderr=stderr,
        )
        if result.returncode != 0:
            raise CommandRejected
        if len(result.stdout) + len(result.stderr) >= MAX_COMMAND_BYTES:
            raise BootstrapError
        return result


def _run_json(
    arguments: list[str],
    *,
    environment: dict[str, str],
    allow_empty: bool = False,
) -> list[dict[str, object]]:
    result = _run_mc([*arguments, "--json"], environment=environment)
    lines = result.stdout.splitlines()
    if not lines:
        if allow_empty:
            return []
        raise BootstrapError
    records: list[dict[str, object]] = []
    try:
        for line in lines:
            if not line:
                raise BootstrapError
            record = strict_json_object(line)
            status_value = record.get("status")
            if isinstance(status_value, str) and status_value.lower() not in {
                "success",
                "enabled",
            }:
                raise BootstrapError
            records.append(record)
    except (StrictJsonError, UnicodeError, ValueError) as error:
        raise BootstrapError from error
    return records


def _collect_strings(value: object, *, keys: frozenset[str]) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in keys:
                if isinstance(nested, str):
                    found.append(nested)
                elif isinstance(nested, list):
                    if not all(isinstance(item, str) for item in nested):
                        raise BootstrapError
                    found.extend(item for item in nested if isinstance(item, str))
                elif nested is not None:
                    found.extend(_collect_strings(nested, keys=keys))
            else:
                found.extend(_collect_strings(nested, keys=keys))
    elif isinstance(value, list):
        for nested in value:
            found.extend(_collect_strings(nested, keys=keys))
    return found


def _validated_entities(values: Iterable[str]) -> set[str]:
    result: set[str] = set()
    for value in values:
        if ENTITY_PATTERN.fullmatch(value) is None or value in result:
            raise BootstrapError
        result.add(value)
    return result


def _records_entities(
    records: Iterable[dict[str, object]],
    *,
    keys: frozenset[str],
) -> set[str]:
    values: list[str] = []
    for record in records:
        values.extend(_collect_strings(record, keys=keys))
    return _validated_entities(values)


GROUP_KEYS = frozenset({"group", "groupName", "groups"})
MEMBER_KEYS = frozenset({"member", "members"})
USER_KEYS = frozenset({"accessKey", "user", "users", "username", "usernames"})
POLICY_KEYS = frozenset({"policy", "policies", "policyName", "policyNames"})


def _group_names(*, environment: dict[str, str]) -> set[str]:
    records = _run_json(
        ["admin", "group", "list", "bootstrap"],
        environment=environment,
        allow_empty=True,
    )
    return _records_entities(records, keys=GROUP_KEYS)


def _group_members(group: str, *, environment: dict[str, str]) -> set[str]:
    records = _run_json(
        ["admin", "group", "info", "bootstrap", group],
        environment=environment,
    )
    return _records_entities(records, keys=MEMBER_KEYS)


def _remove_group_memberships(access_key: str, *, environment: dict[str, str]) -> None:
    for group in sorted(_group_names(environment=environment)):
        if access_key in _group_members(group, environment=environment):
            _run_mc(
                ["admin", "group", "remove", "bootstrap", group, access_key],
                environment=environment,
            )
    for group in _group_names(environment=environment):
        if access_key in _group_members(group, environment=environment):
            raise BootstrapError


def _user_names(*, environment: dict[str, str]) -> set[str]:
    records = _run_json(
        ["admin", "user", "list", "bootstrap"],
        environment=environment,
        allow_empty=True,
    )
    return _records_entities(records, keys=frozenset({"accessKey"}))


def _policy_names(*, environment: dict[str, str]) -> set[str]:
    records = _run_json(
        ["admin", "policy", "list", "bootstrap"],
        environment=environment,
        allow_empty=True,
    )
    return _records_entities(records, keys=frozenset({"policy"}))


def _policy_entities(
    policy: str,
    *,
    environment: dict[str, str],
) -> tuple[set[str], set[str]]:
    records = _run_json(
        ["admin", "policy", "entities", "bootstrap", "--policy", policy],
        environment=environment,
    )
    users = _records_entities(records, keys=USER_KEYS)
    groups = _records_entities(records, keys=GROUP_KEYS)
    return users, groups


def _user_policies(access_key: str, *, environment: dict[str, str]) -> set[str]:
    records = _run_json(
        ["admin", "policy", "entities", "bootstrap", "--user", access_key],
        environment=environment,
    )
    return _records_entities(records, keys=POLICY_KEYS)


def _expected_policy(bucket: str) -> dict[str, object]:
    return {
        "Statement": [
            {
                "Action": [*BUCKET_ACTIONS],
                "Effect": "Allow",
                "Resource": [f"arn:aws:s3:::{bucket}"],
            },
            {
                "Action": [*OBJECT_ACTIONS],
                "Effect": "Allow",
                "Resource": [f"arn:aws:s3:::{bucket}/*"],
            },
        ],
        "Version": "2012-10-17",
    }


def _exact_policy_values(value: object) -> frozenset[str]:
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item for item in value)
        or len(value) != len(set(value))
    ):
        raise BootstrapError
    return frozenset(value)


def _verify_exact_bucket_policy(policy: dict[str, object], *, bucket: str) -> None:
    if set(policy) != {"Statement", "Version"} or policy.get("Version") != "2012-10-17":
        raise BootstrapError
    statements = policy.get("Statement")
    if not isinstance(statements, list) or len(statements) != 2:
        raise BootstrapError
    actual: set[tuple[str, frozenset[str], frozenset[str]]] = set()
    for statement in statements:
        if (
            not isinstance(statement, dict)
            or set(statement) != {"Action", "Effect", "Resource"}
            or statement.get("Effect") != "Allow"
        ):
            raise BootstrapError
        actions = _exact_policy_values(statement.get("Action"))
        resources = _exact_policy_values(statement.get("Resource"))
        signature = ("Allow", actions, resources)
        if signature in actual:
            raise BootstrapError
        actual.add(signature)
    expected = {
        (
            "Allow",
            frozenset(BUCKET_ACTIONS),
            frozenset({f"arn:aws:s3:::{bucket}"}),
        ),
        (
            "Allow",
            frozenset(OBJECT_ACTIONS),
            frozenset({f"arn:aws:s3:::{bucket}/*"}),
        ),
    }
    if actual != expected:
        raise BootstrapError


def _write_policy(directory: Path, policy: dict[str, object]) -> Path:
    payload = json.dumps(policy, separators=(",", ":"), sort_keys=True).encode("utf-8")
    descriptor = -1
    path: Path | None = None
    try:
        descriptor, raw_path = tempfile.mkstemp(prefix="policy.", suffix=".json", dir=directory)
        path = Path(raw_path)
        if os.name == "posix":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        metadata = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or (os.name == "posix" and stat.S_IMODE(metadata.st_mode) != 0o600)
            or path.read_bytes() != payload
        ):
            raise BootstrapError
        return path
    except OSError as error:
        if path is not None:
            path.unlink(missing_ok=True)
        raise BootstrapError from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_policy(path: Path) -> dict[str, object]:
    try:
        metadata = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size <= 0
            or metadata.st_size > MAX_COMMAND_BYTES
        ):
            raise BootstrapError
        path.chmod(0o600)
        return strict_json_object(path.read_bytes())
    except (OSError, StrictJsonError) as error:
        raise BootstrapError from error


def _verify_user(access_key: str, *, environment: dict[str, str]) -> None:
    if access_key not in _user_names(environment=environment):
        raise BootstrapError
    records = _run_json(
        ["admin", "user", "info", "bootstrap", access_key],
        environment=environment,
    )
    users = _records_entities(records, keys=frozenset({"accessKey"}))
    memberships = _records_entities(records, keys=MEMBER_KEYS | frozenset({"memberOf"}))
    statuses = _records_entities(
        records,
        keys=frozenset({"userStatus", "accountStatus"}),
    )
    if users != {access_key} or memberships or statuses != {"enabled"}:
        raise BootstrapError


def _run_startup_command(
    arguments: list[str],
    *,
    environment: dict[str, str],
) -> subprocess.CompletedProcess[bytes]:
    alias_gate = len(arguments) == 6 and arguments[:3] == ["alias", "set", "bootstrap"]
    bucket_gate = (
        len(arguments) == 3
        and arguments[:2] == ["mb", "--ignore-existing"]
        and arguments[2].startswith("bootstrap/")
    )
    if not alias_gate and not bucket_gate:
        raise BootstrapError
    return _converge(lambda: _run_mc(arguments, environment=environment))


def _reconcile(
    *,
    base_url: str,
    root_user: str,
    root_password: str,
    access_key: str,
    secret_key: str,
    bucket: str,
    environment: dict[str, str],
    working_directory: Path,
) -> None:
    _run_startup_command(
        ["alias", "set", "bootstrap", base_url, root_user, root_password],
        environment=environment,
    )
    _run_startup_command(
        ["mb", "--ignore-existing", f"bootstrap/{bucket}"],
        environment=environment,
    )
    _remove_group_memberships(access_key, environment=environment)
    if access_key in _user_names(environment=environment):
        _run_mc(["admin", "user", "remove", "bootstrap", access_key], environment=environment)
    if access_key in _user_names(environment=environment):
        raise BootstrapError

    existing_policies = _policy_names(environment=environment)
    if POLICY_NAME in existing_policies:
        users, groups = _policy_entities(POLICY_NAME, environment=environment)
        if groups or users - {access_key}:
            raise BootstrapError
        _run_mc(
            ["admin", "policy", "remove", "bootstrap", POLICY_NAME],
            environment=environment,
        )
    if POLICY_NAME in _policy_names(environment=environment):
        raise BootstrapError

    expected = _expected_policy(bucket)
    policy_path = _write_policy(working_directory, expected)
    verified_path = working_directory / "verified-policy.json"
    try:
        _run_mc(
            ["admin", "policy", "create", "bootstrap", POLICY_NAME, str(policy_path)],
            environment=environment,
        )
        _run_mc(
            ["admin", "user", "add", "bootstrap", access_key, secret_key],
            environment=environment,
        )
        _run_mc(
            ["admin", "policy", "attach", "bootstrap", POLICY_NAME, "--user", access_key],
            environment=environment,
        )
        _remove_group_memberships(access_key, environment=environment)
        _verify_user(access_key, environment=environment)
        policies = _user_policies(access_key, environment=environment)
        if policies != {POLICY_NAME} or policies.intersection(BROAD_POLICIES):
            raise BootstrapError
        users, groups = _policy_entities(POLICY_NAME, environment=environment)
        if users != {access_key} or groups:
            raise BootstrapError
        _run_mc(
            [
                "admin",
                "policy",
                "info",
                "bootstrap",
                POLICY_NAME,
                "--policy-file",
                str(verified_path),
            ],
            environment=environment,
        )
        _verify_exact_bucket_policy(_read_policy(verified_path), bucket=bucket)
    finally:
        policy_path.unlink(missing_ok=True)
        verified_path.unlink(missing_ok=True)


def main() -> None:
    (
        base_url,
        root_user,
        root_password,
        access_key,
        secret_key,
        bucket,
        secure,
    ) = _validate_environment()
    working_directory = Path(tempfile.mkdtemp(prefix="knowledge-uploader-bootstrap.", dir="/tmp"))

    def terminate(_signum: int, _frame: FrameType | None) -> None:
        raise BootstrapInterrupted

    previous_handlers: dict[signal.Signals, signal.Handlers] = {}
    try:
        working_directory.chmod(0o700)
        environment = _client_environment(
            working_directory=working_directory,
            secure=secure,
        )
        for current in TERMINATION_SIGNALS:
            previous_handlers[current] = signal.signal(current, terminate)
        _reconcile(
            base_url=base_url,
            root_user=root_user,
            root_password=root_password,
            access_key=access_key,
            secret_key=secret_key,
            bucket=bucket,
            environment=environment,
            working_directory=working_directory,
        )
    finally:
        for current, previous in previous_handlers.items():
            signal.signal(current, previous)
        shutil.rmtree(working_directory)


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        sys.exit(1)
