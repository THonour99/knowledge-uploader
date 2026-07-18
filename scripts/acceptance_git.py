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


def sanitized_git_environment(source: dict[str, str]) -> dict[str, str]:
    """Remove every ambient Git control variable without exposing its value."""
    return {key: value for key, value in source.items() if not key.upper().startswith("GIT_")}


def git_command(*arguments: str) -> tuple[str, ...]:
    """Build a Git command that ignores replacement objects and repository hooks."""
    return (
        "git",
        "--no-replace-objects",
        "-c",
        f"core.hooksPath={os.devnull}",
        "-c",
        "core.fsmonitor=false",
        *arguments,
    )


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
    _assert_no_replace_or_grafts(repo_root, environment)
    _assert_no_hidden_index_flags(repo_root, environment)
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
