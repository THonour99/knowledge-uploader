from __future__ import annotations

from collections.abc import Generator

import pytest
import tasks
from invoke.context import Context


@pytest.fixture(autouse=True)
def clear_release_environment(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    for name in (
        "PROTECTED_EVIDENCE_DIR",
        "PROTECTED_ALERTMANAGER_CONFIG",
        "RELEASE_GIT_SHA",
        "GITHUB_SHA",
        "RELEASE_ENVIRONMENT",
        "BACKEND_API_HOST",
    ):
        monkeypatch.delenv(name, raising=False)
    yield


def test_ship_fails_closed_when_external_evidence_inputs_are_missing() -> None:
    with pytest.raises(ValueError, match="requires protected release inputs"):
        tasks.ship.body(Context())


def test_ship_invokes_full_protected_checker(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], bool]] = []

    def fake_run(command: list[str], *, check: bool) -> None:
        calls.append((command, check))

    monkeypatch.setattr(tasks.subprocess, "run", fake_run)

    tasks.ship.body(
        Context(),
        evidence_dir="release-evidence",
        alertmanager_config="release-evidence/alertmanager.yml",
        git_sha="a" * 40,
        environment="staging",
        backend_api_host="127.0.0.1",
    )

    assert len(calls) == 1
    command, check = calls[0]
    assert check is True
    assert "--contract-only" not in command
    assert command[1:] == [
        "scripts/check_protected_release.py",
        "--evidence-dir",
        "release-evidence",
        "--alertmanager-config",
        "release-evidence/alertmanager.yml",
        "--backend-api-host",
        "127.0.0.1",
        "--git-sha",
        "a" * 40,
        "--environment",
        "staging",
    ]
