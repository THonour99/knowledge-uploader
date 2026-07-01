"""跨平台开发任务。用法: invoke <task>"""

from __future__ import annotations

from invoke.context import Context
from invoke.tasks import task


def _compose(c: Context, args: str) -> None:
    c.run(f"docker compose {args}", pty=False)


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
    _compose(c, f"run --rm backend-api {pytest_cmd}")


@task(name="test-frontend")
def test_frontend(c: Context) -> None:
    """运行前端 Vitest 非 watch 测试。"""
    c.run("npm --prefix frontend run test:run", pty=False)


@task
def test(c: Context, k: str = "") -> None:
    """运行后端和前端测试。"""
    test_backend(c, k=k)
    test_frontend(c)


@task(name="lint-backend")
def lint_backend(c: Context) -> None:
    """运行后端 ruff、模块边界检查和 mypy。"""
    _compose(c, "run --rm backend-api ruff check app")
    c.run("python scripts/check_module_boundaries.py", pty=False)
    _compose(c, "run --rm backend-api mypy app")


@task(name="lint-frontend")
def lint_frontend(c: Context) -> None:
    """运行前端 ESLint。"""
    c.run("npm --prefix frontend run lint", pty=False)


@task
def lint(c: Context) -> None:
    """运行后端和前端 lint。"""
    lint_backend(c)
    lint_frontend(c)


@task(name="fmt-backend")
def fmt_backend(c: Context) -> None:
    """格式化后端代码。"""
    _compose(c, "run --rm backend-api ruff format app")


@task(name="fmt-frontend")
def fmt_frontend(c: Context) -> None:
    """格式化前端代码。"""
    c.run("npm --prefix frontend run format", pty=False)


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
def ship(c: Context) -> None:
    """发布前事实层门禁: check + check-arm64。含 agent 的四方门走 /ship-gate skill。"""
