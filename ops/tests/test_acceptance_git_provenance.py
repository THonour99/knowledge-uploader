from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest
from scripts.acceptance_git import (
    AcceptanceGitError,
    git_command,
    sanitized_git_environment,
    verify_git_snapshot,
)


def _git(repo: Path, *arguments: str) -> str:
    completed = subprocess.run(
        git_command(*arguments),
        cwd=repo,
        env=sanitized_git_environment(dict(os.environ)),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def _commit(repo: Path, content: str, message: str) -> str:
    (repo / "tracked.txt").write_bytes(content.encode("utf-8"))
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD").lower()


def _repository(tmp_path: Path) -> tuple[Path, str, dict[str, str]]:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Acceptance Test")
    _git(repo, "config", "user.email", "acceptance@example.invalid")
    head = _commit(repo, "candidate\n", "initial")
    source = (repo / "tracked.txt").read_bytes()
    return repo, head, {"tracked.txt": hashlib.sha256(source).hexdigest()}


def test_git_command_and_environment_disable_ambient_controls() -> None:
    command = git_command("status", "--porcelain=v1")
    assert command[:2] == ("git", "--no-replace-objects")
    assert any(argument.startswith("core.hooksPath=") for argument in command)
    assert "core.fsmonitor=false" in command
    assert sanitized_git_environment(
        {
            "PATH": "kept",
            "GITHUB_ACTIONS": "kept-too",
            "GIT_DIR": "rogue",
            "git_work_tree": "rogue-too",
        }
    ) == {"PATH": "kept", "GITHUB_ACTIONS": "kept-too"}


def test_snapshot_ignores_ambient_git_dir_and_binds_commit_blobs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, head, source_sha256 = _repository(tmp_path)
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "rogue.git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path / "rogue-worktree"))

    snapshot = verify_git_snapshot(
        repo,
        expected_sha=head,
        relative_paths=(Path("tracked.txt"),),
        source_sha256=source_sha256,
    )

    assert snapshot.head == head
    assert snapshot.clean
    assert snapshot.source_sha256 == source_sha256


def test_snapshot_rejects_replace_refs_and_nonempty_grafts(tmp_path: Path) -> None:
    replace_repo, _first, _source = _repository(tmp_path / "replace")
    replacement = _commit(replace_repo, "replacement\n", "replacement")
    original = _git(replace_repo, "rev-parse", "HEAD^")
    _git(replace_repo, "replace", replacement, original)
    replacement_source = (replace_repo / "tracked.txt").read_bytes()
    with pytest.raises(AcceptanceGitError, match="replacement refs"):
        verify_git_snapshot(
            replace_repo,
            expected_sha=replacement,
            relative_paths=(Path("tracked.txt"),),
            source_sha256={"tracked.txt": hashlib.sha256(replacement_source).hexdigest()},
        )

    graft_repo, graft_head, graft_source = _repository(tmp_path / "grafts")
    grafts = graft_repo / ".git" / "info" / "grafts"
    grafts.write_text(f"{graft_head}\n", encoding="utf-8")
    with pytest.raises(AcceptanceGitError, match="grafts"):
        verify_git_snapshot(
            graft_repo,
            expected_sha=graft_head,
            relative_paths=(Path("tracked.txt"),),
            source_sha256=graft_source,
        )


@pytest.mark.parametrize("flag", ("--assume-unchanged", "--skip-worktree"))
def test_snapshot_rejects_hidden_index_flags(tmp_path: Path, flag: str) -> None:
    repo, head, source_sha256 = _repository(tmp_path)
    _git(repo, "update-index", flag, "tracked.txt")

    with pytest.raises(AcceptanceGitError, match="index hiding flags"):
        verify_git_snapshot(
            repo,
            expected_sha=head,
            relative_paths=(Path("tracked.txt"),),
            source_sha256=source_sha256,
        )


def test_snapshot_rejects_source_digest_not_backed_by_commit(tmp_path: Path) -> None:
    repo, head, _source_sha256 = _repository(tmp_path)

    with pytest.raises(AcceptanceGitError, match="commit blobs"):
        verify_git_snapshot(
            repo,
            expected_sha=head,
            relative_paths=(Path("tracked.txt"),),
            source_sha256={"tracked.txt": "0" * 64},
        )
