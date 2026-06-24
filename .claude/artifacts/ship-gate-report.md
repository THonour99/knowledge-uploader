# Ship Gate Report — feat/adversarial-completion-gate

范围: 提交 `f64122b` (fix) + `9c42fd9` (red-team 守护)
日期: 2026-06-24
主题: 修复 `backend/scripts/seed_admin.py` 在全新环境初始化首个管理员时缺少部门模型导入

## 门禁决议: ✅ 放行

四方独立审查全绿: 事实层 PASS · quality-reviewer 0 BLOCK · security-auditor 0 CRITICAL · red-team 0 跑红未修。

## 改动

- `backend/scripts/seed_admin.py` (+7): 加 `from importlib import import_module` 并 `import_module("app.db.models")`,
  复用项目统一 model 聚合点 (alembic env.py / 测试 `_reset_database` 同款), 一次性注册全部 ORM model,
  让 `users.department_id -> departments` 跨模块外键在 flush 时可解析。
- `backend/app/tests/unit/test_seed_admin_script.py` (+81): 2 个回归测试 (进程内功能校验 + 子进程隔离回归守卫)。
- `backend/app/tests/red_team/test_seed_admin_model_registration.py` (+275): 4 个常驻红队守护。

## 根因复现证据 (跑红为证)

`git stash` 退掉修复后, 子进程回归测试 `test_seed_admin_script_runs_standalone_with_department_foreign_key`
跑红, 报与用户一致的:
`sqlalchemy.exc.NoReferencedTableError: Foreign key associated with column 'users.department_id' could not find table 'departments'`。
恢复修复后转绿。证明 bug 真实存在且修复生效, 非幻觉。

## 事实层

- ruff check: 0 (2 测试文件 + 脚本)
- ruff format: clean
- mypy --strict: 0 (2 测试文件; 脚本不在 `files=["app"]` 范围, 改动为 stdlib import 调用, 类型平凡)
- 模块边界 (`check_module_boundaries.py`): PASS
- check-arm64: PASS (37 依赖全 allowlisted; 本改动**未新增依赖**)
- pytest (blast radius): 9/9 (unit 5 + red_team 4)
- alembic: N/A (本改动无 schema 变更)

> 注: `invoke lint` / `invoke test` 全量套件经 `docker compose run` 挂载当前工作区, 而工作区含团队其他
> **未提交的在途改动** (runtime_config / document·ragflow·review service / celery_app / docker-compose 等)。
> 为隔离本提交, 事实层按改动 blast radius 取证, 上述均针对本提交两文件 + 脚本, 全绿。

## 🤖 quality-reviewer

🔴 0  🟡 2 (HIGH)  🟢 2 (LOW) — 无 BLOCK, 过门。两条 HIGH 已落实:
- 进程内功能测试改名 `test_seed_admin_creates_user_with_unassigned_department_id` 并加 docstring,
  澄清它**不是**进程隔离回归守卫 (registry 污染会让它即便脚本漏 import 也绿), 真正的守卫是子进程测试。
- 子进程测试 env 固定 `PASSWORD_MIN_LENGTH=8`, 不受继承环境 / `.env` 漂移影响 (hermetic)。

## 🔒 security-auditor

🔴 0  🟠 0  🟡 0 — 纯防御性改动, 过门。
- 密码经 **env** (非 argv) 传给子进程, 不进进程列表; 断言不打印密码/hash/密钥。
- `import_module("app.db.models")` 连锁 import 的各 `models.py` 在 import 期**无** `get_settings`/建连/`Fernet`/`print`/密钥加载。
- subprocess 用 list 形式 (`shell=False`), 无命令注入; 全程 ORM 参数化, 无 SQL 注入; 路径用 `pathlib`。
- audit 写入与"只能恢复既有 system_admin"权限边界未被削弱。

## 💣 red-team

攻击测试: 4 写 / 0 跑红未修 / 0 确认活漏洞。全绿守护已留存:
- `test_db_models_registers_every_module_that_defines_a_table` — 聚合点完整性 (行为断言, 变异测试证明"有牙")
- `test_standalone_seed_actually_commits_rows_to_db` — 直查 DB, 防子进程伪绿
- `test_standalone_force_recovery_resolves_all_foreign_keys` — `--force` 恢复路径在全新进程无残留未解析外键
- `test_importing_db_models_does_not_open_connection` — 仅 import 不建连/不触发 `after_create` DDL

## 非阻塞跟进项 (HIGH/MEDIUM/LOW, 不阻断放行)

- `backend/app/modules/statistics/models.py` 未挂入聚合点 `app/db/models.py`。当前 statistics 是空壳 (0 表),
  对本提交**无任何活漏洞**; 但若将来往该模块 (或任何漏挂模块) 加带外键的真表, 会复现同类 bug。
  已由上面的"聚合点完整性"红队测试常驻守护: 一旦漏挂模块贡献了表, 立即转红。建议 (非必须) 后续把
  `import_module("app.modules.statistics.models")` 补进聚合点以兑现修复注释承诺。

## 决议: 四方全绿 → 可向用户宣称完成 ✓
