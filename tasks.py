"""跨平台开发任务。用法: invoke <task>"""

from __future__ import annotations

from invoke import Context, task


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
    _compose(c, "run --rm frontend npm test -- --run")


@task
def lint(c: Context) -> None:
    """运行 ruff、mypy 和前端 lint。"""
    _compose(c, "run --rm backend-api ruff check app")
    _compose(c, "run --rm backend-api mypy app")
    _compose(c, "run --rm frontend npm run lint")


@task
def fmt(c: Context) -> None:
    """格式化后端和前端代码。"""
    _compose(c, "run --rm backend-api ruff format app")
    _compose(c, "run --rm frontend npm run format")


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
