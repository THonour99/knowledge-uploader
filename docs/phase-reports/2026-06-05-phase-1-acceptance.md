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
  - `01c5ef0 ci(test): 修复阶段一测试服务依赖`
  - review 修复提交待推送：补齐 outbox dispatcher、登录限流、权限收紧、session 版本、日志脱敏、模块边界检查和管理员读审计

## 实现内容

- 新增 `users`、`email_verification_tokens`、`password_reset_tokens`、`audit_logs`、`event_outbox` 表及 Alembic 迁移。
- 注册接口支持公司邮箱域名白名单，邮箱统一 lowercase，非公司域名拒绝注册。
- 密码使用 Argon2id；JWT 使用 HS256，包含 `jti` 和密码哈希指纹。
- verification/reset token 表只以 SHA256 hex hash 入库；邮件事件 outbox payload 存 Fernet 密文 token，API 不返回原始 token。
- 注册已存在邮箱返回通用 accepted，不暴露账号是否存在。
- 登录接口加 Redis email/IP 限流；账号状态只在密码正确后暴露，避免无密码状态枚举。
- 登录失败计数支持锁定；锁定/禁用会递增 `session_version`，旧 JWT 即使账号重新可用也会被拒绝。
- `/api/auth/me`、`/logout`、`/change-password` 走 Bearer JWT 鉴权。
- `/logout` 将当前 JWT `jti` 写入 Redis blacklist；重置/修改密码后旧 JWT 自动失效。
- `/register`、`/forgot-password`、`/resend-verification` 加 Redis 限流。
- validation error 返回固定文案，不回显 password/token 输入。
- outbox dispatcher 会读取 `event_outbox`，发布到 RabbitMQ topic exchange `knowledge.events`，并写回 `published_at` / `publish_attempts` / `last_error`。
- 用户管理接口支持 list/get/disable/enable；仅 `system_admin` 可用。
- 禁止管理员自我禁用、禁用同级/更高权限管理员。
- disable/enable 与 `audit_logs` 同事务提交；审计字段包含 actor、action、target、ip、user_agent、timestamp。
- 后端测试改为独立 PostgreSQL 测试库 `knowledge_uploader_test` 和 Redis DB 15，避免污染开发库。
- GitHub Actions 为 pytest 提供 PostgreSQL/Redis service containers；本地 Docker 网络默认路径保持不变。
- ruff 启用 `TID`，并新增 `scripts/check_module_boundaries.py`，CI 与 `invoke lint` 都会检查跨模块 `models/repository/service` 违规导入。

## 验收结果

| 验收项 | 证据 | 状态 |
|---|---|---|
| 公司邮箱可以注册 | `test_register_accepts_allowed_domain_and_rejects_other_domain` 中 `Alice@company.com` 返回 201 | 通过 |
| 非公司邮箱不能注册 | 同一测试中 `bob@outside.com` 返回 `EMAIL_DOMAIN_NOT_ALLOWED` | 通过 |
| 可以登录 | `test_login_issues_jwt_and_me_returns_current_user` 登录返回 access token | 通过 |
| 登录限流 | `test_login_is_rate_limited_for_unknown_email` 覆盖未知邮箱连续请求返回 `RATE_LIMITED` | 通过 |
| JWT 鉴权可用 | `/api/auth/me` 使用 Bearer token 返回当前用户 | 通过 |
| 可以邮箱验证 | `test_verify_email_activates_pending_user` 激活 pending 用户 | 通过 |
| 邮箱验证可投递 | `test_register_writes_verification_outbox_with_encrypted_token` 覆盖注册写 `auth.user.registered` outbox | 通过 |
| 可以重置密码 | `test_reset_password_allows_login_with_new_password` 旧密码失败、新密码成功 | 通过 |
| 忘记密码可投递 | `test_forgot_password_writes_reset_outbox_with_encrypted_token` 覆盖重置密码写 `auth.password.reset-requested` outbox | 通过 |
| disabled 用户不能登录 | `test_disabled_user_cannot_login` 返回 `USER_DISABLED` | 通过 |
| locked 用户旧 JWT 被拒绝 | `test_locked_user_existing_jwt_is_rejected` 返回 `USER_LOCKED` | 通过 |
| locked 过期不恢复旧 JWT | `test_expired_lock_does_not_reactivate_old_jwt` 覆盖 session_version 拒绝旧 token | 通过 |
| 登录枚举侧信道缓解 | `test_unknown_email_login_runs_dummy_password_verification` 覆盖未知邮箱 dummy Argon2 校验 | 通过 |
| outbox dispatcher 投递 | `test_dispatch_once_publishes_and_marks_event_published` / `test_dispatch_once_marks_failed_event_attempt` 覆盖成功和失败标记 | 通过 |
| outbox 失败不持久化敏感异常文本 | `test_dispatch_once_does_not_persist_sensitive_exception_text` 覆盖失败原因只保存异常类型 | 通过 |
| RBAC 基础权限 | `test_knowledge_admin_cannot_disable_users` 中 knowledge_admin 禁用用户返回 403 | 通过 |
| 管理员 list/get 写审计 | `test_system_admin_list_and_get_users_write_audit_logs` 覆盖 `user.list` / `user.view` | 通过 |
| 管理员启停用户 | `test_system_admin_can_disable_and_enable_user_with_audit_log` 覆盖 disable/enable | 通过 |
| 管理员禁用保护 | `test_system_admin_cannot_disable_self_or_peer_admin` 覆盖自禁/同级管理员禁用返回 403 | 通过 |
| 管理员操作写审计 | 同一测试断言 `user.disable` / `user.enable` 两条 audit log | 通过 |
| token 不回显 | `test_validation_error_does_not_echo_password_or_token` 覆盖敏感输入不出现在错误响应 | 通过 |
| JWT 撤销 | `test_logout_revokes_current_jwt` 覆盖 logout 后旧 token 返回 401 | 通过 |
| 密码变更后旧 JWT 失效 | `test_reset_password_invalidates_existing_jwt` 覆盖 reset 后旧 token 返回 401 | 通过 |
| 过期锁定后输错密码继续计数 | `test_expired_lock_wrong_password_counts_failed_login` 覆盖 expired lock 后失败计数递增 | 通过 |

## 验证命令

```text
python -m invoke lint
python -m invoke test
python -m invoke check-arm64
python scripts/check_module_boundaries.py
```

结果：

- `python -m invoke lint` 通过：后端 ruff/mypy 0 errors，模块边界检查通过，前端 ESLint 0 errors。
- `python -m invoke test` 通过：后端 35 tests passed、1 skipped（根目录边界脚本不在 backend Docker build context），前端 2 tests passed。
- `python -m invoke check-arm64` 通过：31 个直接依赖 allowlisted。
- `python scripts/check_module_boundaries.py` 通过：未发现跨模块 `models/repository/service` 导入违规。

## 迁移验证

运行中开发库上完成：

```text
docker compose exec backend-api alembic current
docker compose exec backend-api alembic downgrade 9c1f4d2a6b7e
docker compose exec backend-api alembic upgrade head
docker compose exec backend-api alembic current
```

结果：迁移可前进；新增 `b8d4c2e1f903_add_user_session_version.py` 可回退并再次升级，最终版本为 `b8d4c2e1f903 (head)`。前一轮已验证 `9c1f4d2a6b7e_add_event_outbox.py` 可前进/回退。

运行中开发库状态：

```text
alembic_version = b8d4c2e1f903
audit_logs.actor_id / target_id / ip_address / user_agent 均为 NOT NULL
event_outbox.published_at / publish_attempts / last_error 可由 dispatcher 更新
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
- 插入临时 `event_outbox` 事件后，`outbox-dispatcher` 在 3 秒内将其标记为 `published=True`，`publish_attempts=0`，`last_error=None`；验收后已删除该临时事件。

## Review 修复记录

- 安全审查指出 validation error 回显 password/token 输入，已改为固定错误文案并加测试。
- 安全审查指出注册、忘记密码、重发验证缺限流，已接入 Redis 限流并加测试。
- 安全审查指出 logout no-op、密码变更后旧 JWT 仍有效，已加入 `jti` blacklist 和密码指纹校验并加测试。
- 安全审查指出 `audit_logs` 缺 `user_agent` 且关键审计字段 nullable，已补齐字段并设为 NOT NULL。
- 质量审查指出 metadata 注册测试未覆盖生产导入路径，已改为导入 `app.db.models`。
- 质量审查指出注册/重置密码 token 无可投递路径，已新增 `event_outbox` 表、dispatcher，并在认证事务中写入加密 token 邮件事件。
- 质量/安全审查指出用户管理权限过宽，已收紧为仅 `system_admin`，并禁止自禁和同级管理员禁用。
- 质量/安全审查指出 locked 用户旧 JWT 可继续访问，已在 `get_current_user` 中拒绝 `locked` 状态。
- 安全审查指出注册/登录存在枚举风险，已改为注册通用 accepted，登录状态错误延后到密码正确后返回。
- 安全审查指出登录缺 Redis 限流，已按 email/IP 增加限流。
- 安全审查指出日志脱敏函数未接入，已加入 structlog processor。
- 安全复审指出 locked 过期后旧 JWT 可能恢复，已通过 `session_version` 使锁定/禁用前 token 永久失效。
- 安全复审指出未知邮箱登录存在时间侧信道，已加入 dummy Argon2 校验。
- 安全复审指出 forgot/resend 限流异常未转换，已统一返回 429。
- 质量复审指出 ruff banned-api 未生效、认证层直接读用户 ORM，已启用 `TID`，将 auth/core 用户读取改为 `UserIdentityStore` + shared schema，并新增模块边界脚本接入 CI。
- 质量复审指出管理员 `GET /api/users` / `GET /api/users/{id}` 未写审计，已新增 `user.list` / `user.view` 审计并加测试。
- 安全复审指出过期锁定账号输错密码不会重新累计失败次数，已改为过期锁定状态继续计数并加测试。
- 安全复审指出 outbox 发布失败可能持久化/记录敏感异常文本，已改为只保存和记录异常类型。
- 质量复审指出模块边界脚本漏检包级导入和 core 导入 module models，已扩展导入解析和 core 禁止层，并加边界脚本测试。
- 质量复审指出 `core.identity` 直接查询/更新 `users`，已改为 core 只保留 `UserIdentityStore` 协议，具体 ORM 实现在 user 模块的 `SqlUserIdentityStore`。

## 阶段边界状态

Phase 1 代码与本地验收已通过，PR #2 已创建且前一轮 CI 通过。当前 review 修复待提交并推送；推送后必须等待 PR CI 与 review gate 通过，在 review gate 通过或明确批准继续前，不进入 Phase 2。
