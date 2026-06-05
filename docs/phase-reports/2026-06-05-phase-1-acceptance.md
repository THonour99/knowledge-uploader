# Phase 1 验收报告

## 阶段范围

Phase 1 目标是完成认证与用户基础能力，包含用户表、认证 token 表、注册、公司邮箱域名限制、邮箱验证、登录、JWT 鉴权、忘记密码、重置密码、用户启用/禁用、RBAC 基础权限，以及管理员操作审计。

## 当前分支

- 分支：`codex/phase-1-core-backend`
- 基线：Phase 0 PR 已合并后的 `main`
- 阶段提交：
  - `bdc496a feat(auth): 添加认证用户表迁移`
  - `831154c feat(auth): 添加认证接口与JWT鉴权`
  - `0551dd6 feat(user): 添加用户启停与审计`
  - `87aa4ad fix(audit): 补全管理员审计字段`

## 实现内容

- 新增 `users`、`email_verification_tokens`、`password_reset_tokens`、`audit_logs` 表及 Alembic 迁移。
- 注册接口支持公司邮箱域名白名单，邮箱统一 lowercase，非公司域名拒绝注册。
- 密码使用 Argon2id；JWT 使用 HS256，包含 `jti` 和密码哈希指纹。
- verification/reset token 只以 SHA256 hex hash 入库，API 不返回原始 token。
- 登录失败计数支持锁定；disabled 用户不能登录。
- `/api/auth/me`、`/logout`、`/change-password` 走 Bearer JWT 鉴权。
- `/logout` 将当前 JWT `jti` 写入 Redis blacklist；重置/修改密码后旧 JWT 自动失效。
- `/register`、`/forgot-password`、`/resend-verification` 加 Redis 限流。
- validation error 返回固定文案，不回显 password/token 输入。
- 用户管理接口支持 list/get/disable/enable；仅 `knowledge_admin` / `system_admin` 可用。
- disable/enable 与 `audit_logs` 同事务提交；审计字段包含 actor、action、target、ip、user_agent、timestamp。
- 后端测试改为独立 PostgreSQL 测试库 `knowledge_uploader_test` 和 Redis DB 15，避免污染开发库。

## 验收结果

| 验收项 | 证据 | 状态 |
|---|---|---|
| 公司邮箱可以注册 | `test_register_accepts_allowed_domain_and_rejects_other_domain` 中 `Alice@company.com` 返回 201 | 通过 |
| 非公司邮箱不能注册 | 同一测试中 `bob@outside.com` 返回 `EMAIL_DOMAIN_NOT_ALLOWED` | 通过 |
| 可以登录 | `test_login_issues_jwt_and_me_returns_current_user` 登录返回 access token | 通过 |
| JWT 鉴权可用 | `/api/auth/me` 使用 Bearer token 返回当前用户 | 通过 |
| 可以邮箱验证 | `test_verify_email_activates_pending_user` 激活 pending 用户 | 通过 |
| 可以重置密码 | `test_reset_password_allows_login_with_new_password` 旧密码失败、新密码成功 | 通过 |
| disabled 用户不能登录 | `test_disabled_user_cannot_login` 返回 `USER_DISABLED` | 通过 |
| RBAC 基础权限 | `test_employee_cannot_disable_users` 中 employee 禁用用户返回 403 | 通过 |
| 管理员启停用户 | `test_admin_can_disable_and_enable_user_with_audit_log` 覆盖 disable/enable | 通过 |
| 管理员操作写审计 | 同一测试断言 `user.disable` / `user.enable` 两条 audit log | 通过 |
| token 不回显 | `test_validation_error_does_not_echo_password_or_token` 覆盖敏感输入不出现在错误响应 | 通过 |
| JWT 撤销 | `test_logout_revokes_current_jwt` 覆盖 logout 后旧 token 返回 401 | 通过 |
| 密码变更后旧 JWT 失效 | `test_reset_password_invalidates_existing_jwt` 覆盖 reset 后旧 token 返回 401 | 通过 |

## 验证命令

```text
python -m invoke lint
python -m invoke test
python -m invoke check-arm64
```

结果：

- `python -m invoke lint` 通过：后端 ruff/mypy 0 errors，前端 ESLint 0 errors。
- `python -m invoke test` 通过：后端 20 tests passed，前端 2 tests passed。
- `python -m invoke check-arm64` 通过：31 个直接依赖 allowlisted。

## 迁移验证

临时数据库 `knowledge_migration_test` 上完成：

```text
alembic upgrade head
alembic downgrade -1
alembic upgrade head
```

结果：两个迁移均可前进；`47c18588d876_add_audit_logs.py` 可回退并再次升级。

运行中开发库状态：

```text
alembic_version = 47c18588d876
audit_logs.actor_id / target_id / ip_address / user_agent 均为 NOT NULL
```

## 运行态验收

本机已有无关容器 `xiaosheng-esp32-server` 占用宿主机 `8000`，因此本阶段运行态验收使用：

```powershell
$env:BACKEND_API_PORT='18000'
python -m invoke up
```

结果：

- `docker compose ps` 显示 14 个 Knowledge Uploader 容器全部 `healthy`。
- `http://127.0.0.1:18000/api/system/health` 返回 `{"status":"ok"}`。
- 运行中 `backend-api` 执行 `alembic downgrade -1` / `alembic upgrade head` 后健康检查仍通过。

## Review 修复记录

- 安全审查指出 validation error 回显 password/token 输入，已改为固定错误文案并加测试。
- 安全审查指出注册、忘记密码、重发验证缺限流，已接入 Redis 限流并加测试。
- 安全审查指出 logout no-op、密码变更后旧 JWT 仍有效，已加入 `jti` blacklist 和密码指纹校验并加测试。
- 安全审查指出 `audit_logs` 缺 `user_agent` 且关键审计字段 nullable，已补齐字段并设为 NOT NULL。
- 质量审查指出 metadata 注册测试未覆盖生产导入路径，已改为导入 `app.db.models`。

## 阶段边界状态

Phase 1 代码与本地验收已通过。当前分支已完成阶段 1 所有任务项，下一步应推送分支、创建 PR 并等待 review；在 review gate 通过或明确批准继续前，不进入 Phase 2。
