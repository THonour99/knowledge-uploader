"""Fail-closed Git provenance helpers for local acceptance evidence."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final

GIT_SHA_PATTERN: Final = re.compile(r"^[0-9a-f]{40}$")
GIT_VERSION_PATTERN: Final = re.compile(r"^git version (\d+)\.(\d+)(?:\.(\d+))?")
MINIMUM_GIT_VERSION: Final = (2, 36, 0)
EXECUTABLE_SUFFIXES: Final = frozenset(
    {".py", ".pyw", ".pyc", ".pyo", ".pyd", ".so", ".dll", ".dylib"}
)
PYTEST_INPUT_NAMES: Final = frozenset(
    {
        "conftest.py",
        "sitecustomize.py",
        "usercustomize.py",
        "pytest.ini",
        ".pytest.ini",
        "pyproject.toml",
        "tox.ini",
        "setup.cfg",
    }
)
UNTRACKED_SCAN_PATHS: Final = (
    "scripts",
    "ops",
    "backend/app",
    "__pycache__",
    *tuple(sorted(PYTEST_INPUT_NAMES)),
    *tuple(f":(top,glob)*{suffix}" for suffix in sorted(EXECUTABLE_SUFFIXES)),
)


class AcceptanceGitError(RuntimeError):
    """Raised when a candidate cannot be bound to an unmodified Git commit."""


@dataclass(frozen=True)
class GitSnapshot:
    head: str
    tree: str
    status: str
    source_sha256: dict[str, str]

    @property
    def clean(self) -> bool:
        return not self.status


MINIMAL_GIT_HOST_ENVIRONMENT_KEYS: Final = frozenset(
    {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "SYSTEMDRIVE"}
)


def sanitized_git_environment(source: dict[str, str]) -> dict[str, str]:
    """Build a minimal Git environment without propagating host credentials."""
    normalized: dict[str, tuple[str, str]] = {}
    for key, value in source.items():
        upper = key.upper()
        if upper not in MINIMAL_GIT_HOST_ENVIRONMENT_KEYS:
            continue
        if upper in normalized:
            raise AcceptanceGitError("ambiguous host environment key")
        normalized[upper] = (key, value)
    if "PATH" not in normalized:
        raise AcceptanceGitError("host PATH is required for Git verification")
    environment = {original: value for original, value in normalized.values()}
    environment.update(
        {
            "LANG": "C",
            "LC_ALL": "C",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return environment


def git_command(*arguments: str) -> tuple[str, ...]:
    """Build a Git command that ignores replacement objects and repository hooks."""
    return (
        "git",
        "--no-replace-objects",
        "-c",
        f"core.hooksPath={os.devnull}",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.excludesFile=/dev/null",
        *arguments,
    )


def _supported_git_version(environment: dict[str, str]) -> str:
    try:
        completed = subprocess.run(
            ("git", "--version"),
            env=environment,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise AcceptanceGitError("git version check failed") from error
    if completed.returncode != 0:
        raise AcceptanceGitError("git version check failed")
    try:
        output = bytes(completed.stdout).decode("utf-8", errors="strict").strip()
    except UnicodeError as error:
        raise AcceptanceGitError("git version output is not valid UTF-8") from error
    match = GIT_VERSION_PATTERN.match(output)
    if match is None:
        raise AcceptanceGitError("git version output is invalid")
    version = tuple(int(part or 0) for part in match.groups())
    if version < MINIMUM_GIT_VERSION:
        raise AcceptanceGitError("Git 2.36 or newer is required")
    return output.removeprefix("git version ")


def _run_git(
    repo_root: Path,
    *arguments: str,
    environment: dict[str, str],
    timeout_seconds: int = 30,
) -> bytes:
    try:
        completed = subprocess.run(
            git_command(*arguments),
            cwd=repo_root,
            env=environment,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise AcceptanceGitError("git command failed") from error
    if completed.returncode != 0:
        raise AcceptanceGitError("git command failed")
    return bytes(completed.stdout)


def _git_text(
    repo_root: Path,
    *arguments: str,
    environment: dict[str, str],
) -> str:
    try:
        return (
            _run_git(repo_root, *arguments, environment=environment)
            .decode("utf-8", errors="strict")
            .strip()
        )
    except UnicodeError as error:
        raise AcceptanceGitError("git output is not valid UTF-8") from error


def _common_git_dir(repo_root: Path, environment: dict[str, str]) -> Path:
    raw = _git_text(repo_root, "rev-parse", "--git-common-dir", environment=environment)
    path = Path(raw)
    if not path.is_absolute():
        path = repo_root / path
    return path.resolve()


def _assert_no_replace_or_grafts(repo_root: Path, environment: dict[str, str]) -> None:
    replace_refs = _git_text(
        repo_root,
        "for-each-ref",
        "--format=%(refname)",
        "refs/replace",
        environment=environment,
    )
    if replace_refs:
        raise AcceptanceGitError("Git replacement refs are forbidden")
    grafts_path = _common_git_dir(repo_root, environment) / "info" / "grafts"
    try:
        if grafts_path.is_symlink():
            raise AcceptanceGitError("Git grafts path must not be a symlink")
        if grafts_path.is_file() and grafts_path.read_bytes().strip():
            raise AcceptanceGitError("Git grafts are forbidden")
    except OSError as error:
        raise AcceptanceGitError("Git grafts cannot be inspected") from error


def _assert_info_exclude_inactive(repo_root: Path, environment: dict[str, str]) -> None:
    exclude_path = _common_git_dir(repo_root, environment) / "info" / "exclude"
    try:
        if exclude_path.is_symlink():
            raise AcceptanceGitError("Git info/exclude must not be a symlink")
        if not exclude_path.exists():
            return
        if not exclude_path.is_file():
            raise AcceptanceGitError("Git info/exclude must be a regular file")
        lines = exclude_path.read_text(encoding="utf-8", errors="strict").splitlines()
    except (OSError, UnicodeError) as error:
        raise AcceptanceGitError("Git info/exclude cannot be inspected") from error
    if any(line.strip() and not line.lstrip().startswith("#") for line in lines):
        raise AcceptanceGitError("active Git info/exclude rules are forbidden")


def _is_execution_input(relative_path: str) -> bool:
    normalized = relative_path.replace("\\", "/")
    lowered = normalized.lower()
    parts = lowered.split("/")
    if not normalized or normalized.startswith("/") or ".." in parts:
        raise AcceptanceGitError("untracked path is not repository-relative")
    in_primary_scope = (
        lowered.startswith("scripts/")
        or lowered.startswith("ops/tests/")
        or lowered.startswith("backend/app/")
    )
    in_pytest_scope = "/" not in lowered or lowered.startswith("ops/")
    in_root_pycache = lowered.startswith("__pycache__/")
    return in_root_pycache or ((in_primary_scope or in_pytest_scope) and (
        "__pycache__" in parts
        or Path(lowered).suffix in EXECUTABLE_SUFFIXES
        or parts[-1] in PYTEST_INPUT_NAMES
    ))


def _assert_no_untracked_execution_inputs(
    repo_root: Path,
    environment: dict[str, str],
) -> None:
    payload = _run_git(
        repo_root,
        "ls-files",
        "--others",
        "-z",
        "--",
        *UNTRACKED_SCAN_PATHS,
        environment=environment,
    )
    try:
        paths = [
            record.decode("utf-8", errors="strict") for record in payload.split(b"\0") if record
        ]
    except UnicodeError as error:
        raise AcceptanceGitError("untracked path is not valid UTF-8") from error
    if any(_is_execution_input(path) for path in paths):
        raise AcceptanceGitError("untracked or ignored Python execution inputs are forbidden")


def _assert_no_hidden_index_flags(repo_root: Path, environment: dict[str, str]) -> None:
    payload = _run_git(repo_root, "ls-files", "-v", "-z", environment=environment)
    for record in payload.split(b"\0"):
        if not record:
            continue
        tag = record[:1]
        if tag == b"S" or (b"a" <= tag <= b"z"):
            raise AcceptanceGitError("Git index hiding flags are forbidden")


def _commit_source_sha256(
    repo_root: Path,
    head: str,
    relative_paths: tuple[Path, ...],
    environment: dict[str, str],
) -> dict[str, str]:
    result: dict[str, str] = {}
    for relative_path in relative_paths:
        normalized = relative_path.as_posix()
        if relative_path.is_absolute() or normalized.startswith("../"):
            raise AcceptanceGitError("source path must be repository-relative")
        payload = _run_git(
            repo_root,
            "show",
            f"{head}:{normalized}",
            environment=environment,
        )
        result[normalized] = hashlib.sha256(payload).hexdigest()
    return result


def verify_git_snapshot(
    repo_root: Path,
    *,
    expected_sha: str,
    relative_paths: tuple[Path, ...],
    source_sha256: dict[str, str],
) -> GitSnapshot:
    """Bind clean working sources to exact commit blobs under hardened Git semantics."""
    normalized = expected_sha.strip().lower()
    if GIT_SHA_PATTERN.fullmatch(normalized) is None:
        raise AcceptanceGitError("expected Git SHA must be exactly 40 hexadecimal characters")
    environment = sanitized_git_environment(dict(os.environ))
    _supported_git_version(environment)
    _assert_no_replace_or_grafts(repo_root, environment)
    _assert_info_exclude_inactive(repo_root, environment)
    _assert_no_hidden_index_flags(repo_root, environment)
    _assert_no_untracked_execution_inputs(repo_root, environment)
    head = _git_text(repo_root, "rev-parse", "HEAD", environment=environment).lower()
    tree = _git_text(repo_root, "rev-parse", "HEAD^{tree}", environment=environment).lower()
    status = _git_text(
        repo_root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        environment=environment,
    )
    if head != normalized:
        raise AcceptanceGitError("expected Git SHA does not match HEAD")
    if GIT_SHA_PATTERN.fullmatch(tree) is None:
        raise AcceptanceGitError("candidate Git tree identity is invalid")
    if status:
        raise AcceptanceGitError("tracked and non-ignored untracked worktree must be clean")
    committed = _commit_source_sha256(
        repo_root,
        head,
        relative_paths,
        environment,
    )
    if committed != source_sha256:
        raise AcceptanceGitError("executed sources do not match candidate commit blobs")
    return GitSnapshot(head=head, tree=tree, status=status, source_sha256=committed)
