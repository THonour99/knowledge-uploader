"""红队: 攻击 seed_admin 模型注册修复 (fix(auth): seed_admin 缺少部门模型导入)。

被攻击修复: backend/scripts/seed_admin.py 新增 ``import_module("app.db.models")``,
让 SQLAlchemy 能解析跨模块外键 (users.department_id -> departments)。

铁律: 跑红 = 漏洞真实存在; 跑绿 = 假设被证伪 (防御有效, 保留为防回归)。

攻击面 (对应任务):
1. 聚合点完整性回归 (test_db_models_imports_every_module_models_file)
   —— app/db/models.py 是否漏挂了某个 modules/*/models.py? 漏挂的模块一旦
      将来加带外键的 model, 又会复现 NoReferencedTableError。这是常驻守护。
2. 子进程伪绿 (test_standalone_seed_actually_commits_rows_to_db)
   —— 现有回归只断言 returncode==0 + stdout 子串; 脚本可能 exit 0 却没真正
      把 User + AuditLog 落库 (或写到了别的 DB)。本测试直查 DB 证明确实 commit。
3. 全新进程 force 恢复路径 (test_standalone_force_recovery_resolves_all_foreign_keys)
   —— 现有 standalone 只覆盖 create 路径; --force-existing-system-admin 恢复路径
      在全新进程里是否也无残留未解析外键 (full flush of User + AuditLog)。
4. import 副作用 (test_importing_db_models_does_not_open_connection)
   —— department model 的 after_create 监听器在"仅 import 不建表"语境下是否安全,
      会不会误触发 DDL / 数据库连接。
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
from collections.abc import AsyncGenerator
from importlib import import_module
from pathlib import Path

import pytest

from app.tests.safety import require_safe_test_database_reset

# asyncio_mode=auto 自动收集 async 测试; 不在模块级加 asyncio mark, 以免给本文件里
# 唯一的同步测试 (test_db_models_registers_*) 产生 PytestWarning。
BACKEND_ROOT = Path(__file__).resolve().parents[3]
SEED_ADMIN_SCRIPT = BACKEND_ROOT / "scripts" / "seed_admin.py"
DB_MODELS_FILE = BACKEND_ROOT / "app" / "db" / "models.py"


async def _reset_database() -> None:
    require_safe_test_database_reset()
    import_module("app.db.models")

    from app.core.database import engine
    from app.db.base import Base

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()


@pytest.fixture
async def clean_database() -> AsyncGenerator[None, None]:
    await _reset_database()
    yield
    from app.core.database import engine
    from app.db.base import Base

    require_safe_test_database_reset()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()


def _run_seed_admin_script(*args: str, password: str) -> subprocess.CompletedProcess[str]:
    """在独立解释器里运行 seed_admin, 复现真实部署的首个管理员初始化命令。

    子进程继承父进程 (全局 conftest 设置的) DATABASE_URL=测试库, 因此写入同一测试库,
    测试得以直查 DB 验证 commit。同步阻塞, 由测试用 ``asyncio.to_thread`` 调度,
    避免在 async 函数里直接 subprocess.run。
    """
    env = {
        **os.environ,
        "SEED_ADMIN_PASSWORD": password,
        "ALLOWED_EMAIL_DOMAINS": "company.com",
        # 固定最小依赖集, 不受继承环境 / .env 中 PASSWORD_MIN_LENGTH 漂移影响。
        "PASSWORD_MIN_LENGTH": "8",
    }
    return subprocess.run(
        [sys.executable, str(SEED_ADMIN_SCRIPT), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _run_db_models_import_probe() -> subprocess.CompletedProcess[str]:
    """全新进程里仅 import app.db.models, 用不可达端口的 DB 探测 import 期是否建连。

    若 import 阶段尝试建连 / 跑 DDL, 进程会因连接失败而非零退出。同步阻塞, 由测试用
    asyncio.to_thread 调度, 避免在 async 函数里直接 subprocess.run (违反 ASYNC101)。
    """
    probe = "import importlib;importlib.import_module('app.db.models');print('IMPORT_OK')"
    env = {
        **os.environ,
        # 不可达端口: 任何隐式建连都会立刻失败。
        "DATABASE_URL": "postgresql+asyncpg://nouser:nopass@127.0.0.1:1/nodb",
        "ALEMBIC_DATABASE_URL": "postgresql+psycopg://nouser:nopass@127.0.0.1:1/nodb",
        "ALLOWED_EMAIL_DOMAINS": "company.com",
    }
    return subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(BACKEND_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


# ---------------------------------------------------------------------------
# 攻击 1 [聚合点完整性] —— 常驻回归守护 (行为断言, 现绿; 漏挂带表的模块即转红)
# ---------------------------------------------------------------------------
def test_db_models_registers_every_module_that_defines_a_table() -> None:
    """凡是定义了 ORM 表的模块, 其 models 路径必须被 app/db/models.py import。

    本次修复的根因是聚合点漏注册 -> 跨模块外键无法解析
    (users.department_id -> departments)。修复注释承诺"any new table/foreign key
    added there is picked up here without touching this script", 但该承诺仅当聚合点
    真正覆盖每个"贡献了表"的模块时才成立。

    守护方式 (行为断言, 非文本扫描): 先 import 聚合点, 把 SQLAlchemy registry 里每个
    mapper 类按 ``__module__`` 归类, 得到"实际贡献了表的模块集合"; 再断言其全部出现在
    app/db/models.py 的 import 列表里。空壳 models.py (如当前 statistics, 0 张表) 不会
    被误报; 一旦有人往未挂进聚合点的模块加一张真表, 立即转红 —— 精准复刻本次 bug 类,
    且杜绝对占位空文件的假阳性。常驻防回归。
    """
    import_module("app.db.models")

    from app.db.base import Base

    source = DB_MODELS_FILE.read_text(encoding="utf-8")
    imported = set(re.findall(r'import_module\(\s*["\']([\w\.]+)["\']\s*\)', source))

    table_modules = {
        mapper.class_.__module__
        for mapper in Base.registry.mappers
        if mapper.class_.__module__.startswith("app.")
    }
    assert table_modules, "registry 为空, 测试前提错误"

    missing = sorted(module for module in table_modules if module not in imported)
    assert not missing, (
        "以下模块定义了 ORM 表却未被 app/db/models.py 注册, 聚合点不完整, "
        "其跨模块外键会重现 NoReferencedTableError: " + ", ".join(missing)
    )


# ---------------------------------------------------------------------------
# 攻击 2 [子进程伪绿] —— 预期[跑绿]: 直查 DB 证明脚本确实 commit, 回归不可被伪绿欺骗
# ---------------------------------------------------------------------------
async def test_standalone_seed_actually_commits_rows_to_db(clean_database: None) -> None:
    """全新进程跑脚本后, User + AuditLog 必须真实落库 (而非仅 exit 0 + 打印)。

    现有回归 test_seed_admin_script_runs_standalone_with_department_foreign_key 只断言
    returncode==0 与 stdout 子串。攻击假设: 脚本可能因别的原因 exit 0 却没真正提交,
    或写到与测试不同的库, 使"回归守护"被伪绿欺骗。这里直接查测试库证伪该假设。
    """
    from sqlalchemy import select

    from app.core.database import AsyncSessionFactory
    from app.modules.audit.models import AuditLog
    from app.modules.department.models import UNASSIGNED_DEPARTMENT_ID
    from app.modules.user.models import User

    email = "redteam-commit@company.com"
    result = await asyncio.to_thread(
        _run_seed_admin_script,
        "--email",
        email,
        password="RedTeam@Commit123",
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"

    async with AsyncSessionFactory() as session:
        user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
        assert user is not None, "脚本 exit 0 但 User 未落库 —— 回归守护可被伪绿欺骗"
        # users.department_id 外键 server_default 必须解析到"未分配"部门。
        assert user.department_id == UNASSIGNED_DEPARTMENT_ID
        assert user.role == "system_admin"

        audit = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "user.seed_system_admin")
            )
        ).scalar_one_or_none()
        assert audit is not None, "脚本 exit 0 但 AuditLog 未落库 —— 审计写入路径未真正 commit"
        assert audit.target_id == user.id


# ---------------------------------------------------------------------------
# 攻击 3 [force 恢复路径全新进程] —— 预期[跑绿]: 恢复路径在全新进程无残留未解析外键
# ---------------------------------------------------------------------------
async def test_standalone_force_recovery_resolves_all_foreign_keys(
    clean_database: None,
) -> None:
    """--force-existing-system-admin 恢复路径在全新进程里也必须无未解析外键。

    现有 standalone 仅覆盖 create 路径。攻击假设: 恢复路径 (update 既有 admin + 写
    AuditLog) 在缺省导入的全新进程里可能踩到某条少见的未解析外键。这里先建一个
    system_admin, 再用全新进程跑 --force 恢复, 直查 DB 证明恢复后状态正确且 commit。
    """
    from sqlalchemy import select

    from app.core.database import AsyncSessionFactory
    from app.core.security import hash_password
    from app.modules.audit.models import AuditLog
    from app.modules.user.models import User

    email = "redteam-recover@company.com"
    async with AsyncSessionFactory() as session:
        session.add(
            User(
                name="Stale Admin",
                email=email,
                email_domain="company.com",
                password_hash=hash_password("password123"),
                role="system_admin",
                status="disabled",
                email_verified=False,
            )
        )
        await session.commit()

    result = await asyncio.to_thread(
        _run_seed_admin_script,
        "--email",
        email,
        "--force-existing-system-admin",
        password="RedTeam@Recover123",
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "system_admin recovered" in result.stdout

    async with AsyncSessionFactory() as session:
        user = (await session.execute(select(User).where(User.email == email))).scalar_one()
        # 恢复必须把禁用账号重新激活 (证明 update 路径真正 commit)。
        assert user.status == "active"
        assert user.email_verified is True

        audit = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "user.seed_system_admin")
            )
        ).scalar_one()
        assert audit.metadata_json["force_existing_system_admin"] is True
        assert audit.metadata_json["previous_status"] == "disabled"


# ---------------------------------------------------------------------------
# 攻击 4 [import 副作用] —— 预期[跑绿]: import app.db.models 不触发连接/DDL
# ---------------------------------------------------------------------------
async def test_importing_db_models_does_not_open_connection() -> None:
    """仅 import app.db.models 不得连接数据库或执行 DDL。

    department model 注册了 after_create 监听器 (seed 未分配部门)。攻击假设: 在脚本
    语境 (只 import, 不 create_all) 下 import 会误触发监听器 / 建连。用一个指向不可达
    端口的 DATABASE_URL 跑全新进程, 只做 import_module("app.db.models")。若 import
    阶段尝试建连或跑 DDL, 进程会因连接失败而非零退出 -> 跑红。绿 = 防御有效。
    """
    result = await asyncio.to_thread(_run_db_models_import_probe)
    assert result.returncode == 0, (
        "import app.db.models 触发了连接/DDL (非零退出) —— after_create 监听器在"
        f" import 语境下不安全。stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "IMPORT_OK" in result.stdout
