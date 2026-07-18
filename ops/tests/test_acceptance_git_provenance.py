from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest
import scripts.acceptance_git as acceptance_git
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
    assert any(argument.startswith("core.excludesFile=") for argument in command)
    environment = sanitized_git_environment(
        {
            "PATH": "kept",
            "PATHEXT": ".EXE",
            "GITHUB_ACTIONS": "secret-sentinel",
            "GIT_DIR": "rogue",
            "git_work_tree": "rogue-too",
            "AWS_SECRET_ACCESS_KEY": "secret-sentinel",
            "NPM_TOKEN": "secret-sentinel",
        }
    )
    assert environment == {
        "PATH": "kept",
        "PATHEXT": ".EXE",
        "LANG": "C",
        "LC_ALL": "C",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
    }
    assert "secret-sentinel" not in environment.values()


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


def test_snapshot_rejects_ignored_python_bytecode(tmp_path: Path) -> None:
    repo, _head, _source_sha256 = _repository(tmp_path)
    (repo / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-q", "-m", "ignore generated bytecode")
    head = _git(repo, "rev-parse", "HEAD").lower()
    payload = repo / "scripts" / "__pycache__" / "payload.cpython-311.pyc"
    payload.parent.mkdir(parents=True)
    payload.write_bytes(b"untrusted bytecode")
    source = (repo / "tracked.txt").read_bytes()

    with pytest.raises(AcceptanceGitError, match="untracked or ignored Python"):
        verify_git_snapshot(
            repo,
            expected_sha=head,
            relative_paths=(Path("tracked.txt"),),
            source_sha256={"tracked.txt": hashlib.sha256(source).hexdigest()},
        )


def test_snapshot_rejects_info_exclude_conftest(tmp_path: Path) -> None:
    repo, head, source_sha256 = _repository(tmp_path)
    conftest = repo / "ops" / "tests" / "conftest.py"
    conftest.parent.mkdir(parents=True)
    conftest.write_text("pytest_plugins = ()\n", encoding="utf-8")
    (repo / ".git" / "info" / "exclude").write_text(
        "# local excludes\nops/tests/conftest.py\n",
        encoding="utf-8",
    )

    with pytest.raises(AcceptanceGitError, match="info/exclude"):
        verify_git_snapshot(
            repo,
            expected_sha=head,
            relative_paths=(Path("tracked.txt"),),
            source_sha256=source_sha256,
        )


def test_snapshot_disables_core_excludes_file(tmp_path: Path) -> None:
    repo, head, source_sha256 = _repository(tmp_path)
    excludes = tmp_path / "global-excludes"
    excludes.write_text("pytest.ini\n", encoding="utf-8")
    _git(repo, "config", "core.excludesFile", str(excludes))
    (repo / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")

    with pytest.raises(AcceptanceGitError, match="untracked or ignored Python"):
        verify_git_snapshot(
            repo,
            expected_sha=head,
            relative_paths=(Path("tracked.txt"),),
            source_sha256=source_sha256,
        )


def test_snapshot_rejects_git_older_than_236(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, head, source_sha256 = _repository(tmp_path)
    real_run = acceptance_git.subprocess.run

    def old_git_run(command: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if command == ("git", "--version"):
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout=b"git version 2.35.1\n",
                stderr=b"",
            )
        return real_run(command, **kwargs)

    monkeypatch.setattr(acceptance_git.subprocess, "run", old_git_run)

    with pytest.raises(AcceptanceGitError, match=r"2\.36 or newer"):
        verify_git_snapshot(
            repo,
            expected_sha=head,
            relative_paths=(Path("tracked.txt"),),
            source_sha256=source_sha256,
        )


@pytest.mark.parametrize(
    "suffix",
    (".py", ".pyw", ".pyc", ".pyo", ".pyd", ".so", ".dll", ".dylib"),
)
def test_execution_input_classifier_rejects_python_and_native_loaders(suffix: str) -> None:
    assert acceptance_git._is_execution_input(f"scripts/ignored_payload{suffix}")
    assert acceptance_git._is_execution_input(f"backend/app/ignored_payload{suffix}")
    assert acceptance_git._is_execution_input(f"ops/tests/ignored_payload{suffix}")
    assert acceptance_git._is_execution_input(f"__pycache__/ignored_payload{suffix}")
