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


@task
def test(c: Context, k: str = "") -> None:
    """运行后端和前端测试。"""
    pytest_cmd = "pytest"
    if k:
        pytest_cmd += f' -k "{k}"'
    _compose(c, f"run --rm backend-api {pytest_cmd}")
    c.run("npm --prefix frontend test -- --run", pty=False)


@task
def lint(c: Context) -> None:
    """运行 ruff、mypy 和前端 lint。"""
    _compose(c, "run --rm backend-api ruff check app")
    c.run("python scripts/check_module_boundaries.py", pty=False)
    _compose(c, "run --rm backend-api mypy app")
    c.run("npm --prefix frontend run lint", pty=False)


@task
def fmt(c: Context) -> None:
    """格式化后端和前端代码。"""
    _compose(c, "run --rm backend-api ruff format app")
    c.run("npm --prefix frontend run format", pty=False)


@task(name="check-arm64")
def check_arm64(c: Context) -> None:
    """检查 Python 依赖是否符合 ARM64 约束。"""
    c.run(
        "python scripts/check_arm64_wheels.py "
        "backend/requirements.txt backend/requirements-dev.txt",
        pty=False,
    )


@task(name="build-arm64")
def build_arm64(c: Context, version: str = "dev") -> None:
    """构建 ARM64 后端镜像。"""
    c.run(
        "docker buildx build --platform linux/arm64 "
        f"-t knowledge-backend:{version}-arm64 -f backend/Dockerfile backend/ --load",
        pty=False,
    )


@task(pre=[lint, test])
def review(c: Context) -> None:
    """只读评审预检: lint + test。完整四方评审走 /review-code skill。"""


@task(pre=[lint, test, check_arm64])
def ship(c: Context) -> None:
    """完成门事实层: lint + test + check-arm64。含 agent 的四方门走 /ship-gate skill。"""
