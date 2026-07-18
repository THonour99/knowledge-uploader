from __future__ import annotations

from typing import Any

import tasks


class RecordingContext:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def run(self, command: str, **kwargs: Any) -> None:
        self.calls.append((command, kwargs))


def test_backend_task_uses_development_target_and_read_only_contract_mounts() -> None:
    context = RecordingContext()

    tasks.test_backend.body(context, k="proxy_config")

    assert len(context.calls) == 1
    command, options = context.calls[0]
    assert command.startswith("docker compose run --rm --build ")
    assert "--volume ./docker-compose.yml:/docker-compose.yml:ro" in command
    assert "--volume ./nginx/default.conf:/nginx/default.conf:ro" in command
    assert "--volume ./frontend/nginx.conf:/frontend/nginx.conf:ro" in command
    assert command.endswith('backend-api pytest -k "proxy_config"')
    assert options == {
        "pty": False,
        "env": {"BACKEND_BUILD_TARGET": "development"},
    }


def test_backend_lint_and_format_tasks_build_the_development_target() -> None:
    lint_context = RecordingContext()
    format_context = RecordingContext()

    tasks.lint_backend.body(lint_context)
    tasks.fmt_backend.body(format_context)

    assert lint_context.calls[0][0].startswith("docker compose run --rm --build backend-api ruff")
    assert lint_context.calls[-1][0].endswith("backend-api mypy app")
    assert format_context.calls[0][0].startswith(
        "docker compose run --rm --build backend-api ruff format app"
    )


def test_frontend_task_forces_utf8_output_decoding() -> None:
    context = RecordingContext()

    tasks.test_frontend.body(context)

    assert context.calls == [
        ("npm --prefix frontend run test:run", {"pty": False, "encoding": "utf-8"})
    ]
