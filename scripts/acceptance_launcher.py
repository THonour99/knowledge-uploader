#!/usr/bin/env python3
# ruff: noqa: E402, PTH102, PTH108, PTH112, PTH114, PTH118, PTH120
"""Launch acceptance targets with an atomically fresh external Python cache."""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence

CLAIM_TOKEN_ENV = "KNOWLEDGE_UPLOADER_ACCEPTANCE_TOKEN"
CLAIM_MARKER_ENV = "KNOWLEDGE_UPLOADER_ACCEPTANCE_MARKER"
CLAIM_RUNTIME_ENV = "KNOWLEDGE_UPLOADER_ACCEPTANCE_RUNTIME"
CLAIM_FILENAME = ".knowledge-uploader-acceptance-launch"
CHILD_PYCACHE_NAME = "pycache"
TARGETS = {
    "baseline": "check_baseline_contract.py",
    "observability": "run_observability_acceptance.py",
}


def _launcher_isolation_error() -> str | None:
    if sys.flags.isolated != 1:
        return "Python isolated mode (-I) is required"
    if sys.flags.no_site != 1:
        return "Python no-site mode (-S) is required"
    if sys.flags.utf8_mode != 1:
        return "Python UTF-8 mode (-X utf8) is required"
    if sys.pycache_prefix is not None:
        return "the launcher must not use a caller-provided pycache_prefix"
    return None


if __name__ == "__main__":
    _bootstrap_error = _launcher_isolation_error()
    if _bootstrap_error is not None:
        raise SystemExit(f"acceptance launcher refused: {_bootstrap_error}")


import secrets
import shutil
import subprocess
import tempfile
from typing import Final

CLAIM_ENVIRONMENT_KEYS: Final = frozenset(
    {CLAIM_TOKEN_ENV, CLAIM_MARKER_ENV, CLAIM_RUNTIME_ENV}
)
MINIMAL_HOST_ENVIRONMENT_KEYS: Final = frozenset(
    {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "SYSTEMDRIVE"}
)


def _is_within(parent: str, child: str) -> bool:
    try:
        return os.path.commonpath((parent, child)) == parent
    except ValueError:
        return False


def _child_environment(
    source: dict[str, str],
    *,
    token: str,
    marker: str,
    runtime: str,
) -> dict[str, str]:
    normalized: dict[str, tuple[str, str]] = {}
    for key, value in source.items():
        upper = key.upper()
        if upper not in MINIMAL_HOST_ENVIRONMENT_KEYS:
            continue
        if upper in normalized:
            raise RuntimeError("ambiguous launcher host environment key")
        normalized[upper] = (key, value)
    if "PATH" not in normalized:
        raise RuntimeError("launcher host PATH is required")
    environment = {original: value for original, value in normalized.values()}
    runtime_paths = {
        "HOME": os.path.join(runtime, "home"),
        "USERPROFILE": os.path.join(runtime, "home"),
        "TEMP": os.path.join(runtime, "tmp"),
        "TMP": os.path.join(runtime, "tmp"),
        "TMPDIR": os.path.join(runtime, "tmp"),
        "XDG_CONFIG_HOME": os.path.join(runtime, "xdg"),
        "DOCKER_CONFIG": os.path.join(runtime, "docker"),
    }
    for directory in set(runtime_paths.values()):
        os.mkdir(directory)
    environment.update(runtime_paths)
    environment.update(
        {
            "COMPOSE_DISABLE_ENV_FILE": "1",
            CLAIM_TOKEN_ENV: token,
            CLAIM_MARKER_ENV: marker,
            CLAIM_RUNTIME_ENV: runtime,
        }
    )
    return environment


def launch(target_name: str, arguments: Sequence[str]) -> int:
    """Run one allowlisted acceptance target and remove its whole runtime."""
    target_filename = TARGETS.get(target_name)
    if target_filename is None:
        raise ValueError("acceptance target is not allowlisted")

    script_dir = os.path.realpath(os.path.dirname(__file__))
    repository = os.path.realpath(os.path.join(script_dir, os.pardir))
    target = os.path.join(script_dir, target_filename)
    runtime = tempfile.mkdtemp(prefix="knowledge-uploader-acceptance-")
    marker = os.path.join(runtime, CLAIM_FILENAME)
    pycache_prefix = os.path.join(runtime, CHILD_PYCACHE_NAME)
    token = secrets.token_hex(32)
    returncode = 1
    cleanup_failed = False

    try:
        resolved_runtime = os.path.realpath(runtime)
        if _is_within(repository, resolved_runtime):
            raise RuntimeError("launcher runtime must be outside the repository")
        if os.path.islink(runtime) or not os.path.isdir(runtime):
            raise RuntimeError("launcher runtime must be a new directory")
        if os.path.lexists(marker) or os.path.lexists(pycache_prefix):
            raise RuntimeError("launcher runtime was not empty")
        marker_descriptor = os.open(
            marker,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
        try:
            os.write(marker_descriptor, token.encode("ascii"))
            os.fsync(marker_descriptor)
        finally:
            os.close(marker_descriptor)

        command = (
            sys.executable,
            "-I",
            "-S",
            "-X",
            "utf8",
            "-X",
            f"pycache_prefix={pycache_prefix}",
            target,
            *arguments,
        )
        environment = _child_environment(
            dict(os.environ),
            token=token,
            marker=marker,
            runtime=runtime,
        )
        try:
            completed = subprocess.run(command, env=environment, check=False)
        except OSError:
            sys.stderr.write("acceptance launcher could not start the child process\n")
        else:
            returncode = completed.returncode
    except (OSError, RuntimeError) as error:
        sys.stderr.write(f"acceptance launcher refused: {error}\n")
    finally:
        token = ""
        try:
            if os.path.lexists(marker):
                os.unlink(marker)
        except OSError:
            cleanup_failed = True
        try:
            shutil.rmtree(runtime)
        except OSError:
            cleanup_failed = True

    if cleanup_failed:
        sys.stderr.write("acceptance launcher runtime cleanup failed\n")
        return 1
    return returncode


def main(arguments: Sequence[str] | None = None) -> int:
    """Parse the fixed target selector without accepting arbitrary paths."""
    argv = tuple(sys.argv[1:] if arguments is None else arguments)
    if not argv or argv[0] not in TARGETS:
        targets = "|".join(sorted(TARGETS))
        sys.stderr.write(
            f"usage: acceptance_launcher.py <{targets}> [target arguments...]\n"
        )
        return 2
    return launch(argv[0], argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
