"""跨平台开发任务。用法: invoke <task>"""

from __future__ import annotations

import io
import os
import subprocess
import sys

from invoke.context import Context
from invoke.tasks import task


def _compose(c: Context, args: str, *, env: dict[str, str] | None = None) -> None:
    c.run(f"docker compose {args}", pty=False, env=env)


def _frontend_run(c: Context, script: str) -> None:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")
    c.run(f"npm --prefix frontend run {script}", pty=False, encoding="utf-8")


def _backend_dev_run(
    c: Context,
    command: str,
    *,
    build: bool = False,
    mount_repo_contracts: bool = False,
) -> None:
    """Run a backend development command with the image target that owns test tools."""
    build_flag = "--build " if build else ""
    contract_mounts = ""
    if mount_repo_contracts:
        contract_mounts = (
            "--volume ./docker-compose.yml:/docker-compose.yml:ro "
            "--volume ./nginx/default.conf:/nginx/default.conf:ro "
            "--volume ./frontend/nginx.conf:/frontend/nginx.conf:ro "
        )
    _compose(
        c,
        f"run --rm {build_flag}{contract_mounts}backend-api {command}",
        env={"BACKEND_BUILD_TARGET": "development"},
    )


@task
def up(c: Context) -> None:
    """启动所有容器。"""
    _compose(c, "up -d --build")


@task
def down(c: Context) -> None:
    """停止所有容器。"""
    _compose(c, "down")


@task
def logs(c: Context, service: str = "") -> None:
    """查看日志: invoke logs --service=backend-api。"""
    _compose(c, f"logs -f {service}".strip())


@task
def migrate(c: Context, msg: str = "") -> None:
    """创建或运行迁移: invoke migrate 或 invoke migrate --msg='add users'。"""
    if msg:
        _compose(c, f'exec backend-api alembic revision --autogenerate -m "{msg}"')
        return
    _compose(c, "exec backend-api alembic upgrade head")


@task(name="test-backend")
def test_backend(c: Context, k: str = "") -> None:
    """运行后端 pytest: invoke test-backend 或 invoke test-backend -k 'login'。"""
    pytest_cmd = "pytest"
    if k:
        pytest_cmd += f' -k "{k}"'
    _backend_dev_run(c, pytest_cmd, build=True, mount_repo_contracts=True)


@task(name="test-frontend")
def test_frontend(c: Context) -> None:
    """运行前端 Vitest 非 watch 测试。"""
    _frontend_run(c, "test:run")


@task
def test(c: Context, k: str = "") -> None:
    """运行后端和前端测试。"""
    test_backend(c, k=k)
    test_frontend(c)


@task(name="lint-backend")
def lint_backend(c: Context) -> None:
    """运行后端 ruff、模块边界检查和 mypy。"""
    _backend_dev_run(c, "ruff check app", build=True)
    c.run("python scripts/check_module_boundaries.py", pty=False)
    _backend_dev_run(c, "mypy app")


@task(name="lint-frontend")
def lint_frontend(c: Context) -> None:
    """运行前端 ESLint。"""
    _frontend_run(c, "lint")


@task
def lint(c: Context) -> None:
    """运行后端和前端 lint。"""
    lint_backend(c)
    lint_frontend(c)


@task(name="fmt-backend")
def fmt_backend(c: Context) -> None:
    """格式化后端代码。"""
    _backend_dev_run(c, "ruff format app", build=True)


@task(name="fmt-frontend")
def fmt_frontend(c: Context) -> None:
    """格式化前端代码。"""
    _frontend_run(c, "format")


@task
def fmt(c: Context) -> None:
    """格式化后端和前端代码。"""
    fmt_backend(c)
    fmt_frontend(c)


@task(name="check-arm64")
def check_arm64(c: Context) -> None:
    """检查 Python 依赖是否符合 ARM64 约束。"""
    c.run(
        "python scripts/check_arm64_wheels.py "
        "backend/requirements.txt backend/requirements-dev.txt",
        pty=False,
    )


@task(pre=[lint, test])
def check(c: Context) -> None:
    """提交前事实层门禁: lint + test。"""


@task(name="build-arm64")
def build_arm64(c: Context, version: str = "dev") -> None:
    """构建 ARM64 后端镜像。"""
    c.run(
        "docker buildx build --platform linux/arm64 "
        f"-t knowledge-backend:{version}-arm64 -f backend/Dockerfile backend/ --load",
        pty=False,
    )


@task(pre=[check])
def review(c: Context) -> None:
    """只读评审预检: check。完整四方评审走 /review-code skill。"""


@task(pre=[check, check_arm64])
def ship(
    c: Context,
    evidence_dir: str = "",
    alertmanager_config: str = "",
    git_sha: str = "",
    environment: str = "",
    backend_api_host: str = "",
) -> None:
    """发布门禁: 本地检查、ARM64 依赖与完整外部证据校验。"""
    del c
    resolved = {
        "evidence_dir": evidence_dir or os.getenv("PROTECTED_EVIDENCE_DIR", ""),
        "alertmanager_config": alertmanager_config
        or os.getenv("PROTECTED_ALERTMANAGER_CONFIG", ""),
        "git_sha": git_sha or os.getenv("RELEASE_GIT_SHA", os.getenv("GITHUB_SHA", "")),
        "environment": environment or os.getenv("RELEASE_ENVIRONMENT", ""),
        "backend_api_host": backend_api_host or os.getenv("BACKEND_API_HOST", "127.0.0.1"),
    }
    missing = [
        name
        for name in ("evidence_dir", "alertmanager_config", "git_sha", "environment")
        if not resolved[name]
    ]
    if missing:
        joined = ", ".join(missing)
        raise ValueError(
            "invoke ship requires protected release inputs: "
            f"{joined}; pass task options or PROTECTED_*/RELEASE_* environment variables"
        )
    if resolved["environment"] not in {"staging", "production"}:
        raise ValueError("environment must be staging or production")
    subprocess.run(
        [
            sys.executable,
            "scripts/check_protected_release.py",
            "--evidence-dir",
            resolved["evidence_dir"],
            "--alertmanager-config",
            resolved["alertmanager_config"],
            "--backend-api-host",
            resolved["backend_api_host"],
            "--git-sha",
            resolved["git_sha"],
            "--environment",
            resolved["environment"],
        ],
        check=True,
    )
