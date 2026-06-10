# R2 修复计划：系统配置中枢（config 模块实体化）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将完全空壳的 config 模块实体化（总览缺陷 #3）：PRD §6.14 的 24 项系统配置 + §7.2.7 RAGFlow 配置全部落库、可经管理界面修改、变更可审计；各业务模块通过 core 层读取器消费配置，不破坏模块边界。

**Architecture:** 单表 `system_configs` 存全部配置（ADR-5）；config 模块负责**写路径**（校验 / 加密 / 审计 / 发 `config.updated` outbox 事件），新建 `core/runtime_config.py` 负责**读路径**（直查表 + 进程内 TTL 缓存 30–60s）。优先级：数据库值 > 环境变量（环境变量作种子默认与回退）。敏感值（RAGFlow API Key、SMTP 密码）Fernet 加密存储、响应只回脱敏掩码。RAGFlow"测试连接"端点放 ragflow 模块自己的 api.py（复用其 client），前端配置页分两路请求，避免跨模块 import。

**Tech Stack:** FastAPI, SQLAlchemy async ORM, Alembic, PostgreSQL 16 (JSONB), cryptography (Fernet), React + Ant Design Tabs, pytest, Vitest.

**前置依赖:** 无（可与 R1 并行；迁移基于当时 head，当前为 `c7f1a2b9d6e4`）。

---

### Task 1: 后端配置 API 测试

**Files:**
- Create: `backend/app/tests/unit/test_config_api.py`
- Read: `backend/app/tests/unit/test_review_api.py`（测试样板）

- [ ] **Step 1: 写失败测试（RED）**

覆盖：
- system_admin 可按组读取配置（upload / processing / security / basic / ragflow 五组）；
- knowledge_admin 只读、employee 403；
- system_admin 批量更新一组配置 → 值生效且写入一条 `audit_logs`（action: `config.update`）；
- 敏感项（`ragflow.api_key`）写入后读取只返回掩码（`sk-****xxxx` 风格），数据库中为 Fernet 密文；
- 非法值（如负数的最大上传大小）返回 VALIDATION_ERROR；
- 更新后 `event_outbox` 出现 `config.updated` 事件。

```powershell
docker compose run --rm backend-api pytest app/tests/unit/test_config_api.py
```

预期：失败（模块为空壳）。

### Task 2: SystemConfig 模型与迁移

**Files:**
- Modify: `backend/app/modules/config/models.py`
- Create: `backend/app/db/migrations/versions/<revision>_add_system_configs.py`
- Read: `backend/app/db/migrations/versions/c7f1a2b9d6e4_add_ai_analysis_tables.py`（迁移写法样板）

- [ ] **Step 1: 定义模型**

`SystemConfig` 字段：`id`、`key`（唯一索引）、`group`（upload/processing/security/basic/ragflow，CheckConstraint）、`value`（JSONB）、`value_type`（string/int/bool/list/secret）、`is_secret`、`description`、`updated_by`、`created_at`、`updated_at`。

- [ ] **Step 2: 种子数据迁移**

同一迁移内 `op.bulk_insert` 写入全部配置项默认值（默认值取自现有环境变量 / `core/config.py` 的当前取值语义）：

- **upload 组（§6.14.1，6 项）**：`upload.allowed_extensions`、`upload.max_file_size_mb`、`upload.user_quota_mb`（R4 配额预埋）、`upload.allow_multi_file`、`upload.allow_user_delete`、`upload.enable_duplicate_check`
- **processing 组（§6.14.2，5+2 项）**：`processing.auto_parse_on_upload`、`processing.auto_sync_after_parse`、`processing.sync_after_ai_analysis`、`processing.task_max_retries`、`processing.task_timeout_seconds`、`processing.parse_max_pages`、`processing.parse_max_chars`（R1 截断上限落库）
- **security 组（§6.14.3，7 项）**：`security.allowed_email_domains`、`security.password_policy`、`security.login_max_failed_attempts`、`security.login_lock_minutes`、`security.require_email_verification`、`security.require_review_before_sync`、`security.block_critical_sensitive_sync`
- **basic 组（§6.14.4，6 项）**：`basic.system_name`、`basic.system_logo_url`、`basic.default_language`、`basic.default_timezone`、`basic.notification_channels`、`basic.admin_contact_email`
- **ragflow 组（§6.8.2，7 项）**：`ragflow.base_url`、`ragflow.api_key`（secret）、`ragflow.default_dataset_id`、`ragflow.auto_sync_enabled`、`ragflow.sync_max_retries`、`ragflow.sync_timeout_seconds`、`ragflow.allow_high_risk_sync`，外加 §6.8.8 的 `ragflow.delete_remote_on_file_delete`、`ragflow.keep_remote_on_archive`（R4 消费）

- [ ] **Step 3: 迁移往返验证**

```powershell
python -m invoke migrate --msg="add system configs"
python -m invoke migrate
docker compose exec -T backend-api alembic downgrade -1
docker compose exec -T backend-api alembic upgrade head
```

预期：upgrade / downgrade / upgrade 全通过。

### Task 3: config 模块写路径（repository / service / schemas / api）

**Files:**
- Modify: `backend/app/modules/config/repository.py`
- Modify: `backend/app/modules/config/service.py`
- Modify: `backend/app/modules/config/schemas.py`
- Modify: `backend/app/modules/config/api.py`
- Modify: `backend/app/modules/config/events.py`
- Modify: `backend/app/modules/config/permissions.py`
- Modify: `backend/app/main.py`（注册 config 路由）

- [ ] **Step 1: 实现读写服务**

- `GET /api/admin/configs?group=`：按组返回，secret 项只回 `has_value + masked` 不回明文；
- `PUT /api/admin/configs/{group}`：批量更新，逐 key 按 `value_type` 校验，secret 项 Fernet 加密（复用 `core/security.py` 既有 `encrypt_api_key`），同事务写 `record_admin_audit_log`（变更内容记 key 列表与非敏感旧/新值，**敏感值不入日志**）+ `OutboxRepository.append(event_type="config.updated", payload={group, keys})`；
- 角色：写仅 system_admin，读放开到 knowledge_admin（部分支持，对应 PRD 角色权限表"系统设置：管理员部分支持"）。

- [ ] **Step 2: 验证 GREEN**

```powershell
docker compose run --rm backend-api pytest app/tests/unit/test_config_api.py
docker compose run --rm backend-api ruff check app
docker compose run --rm backend-api mypy app
```

预期：全部通过（含模块边界检查 test_module_boundaries.py 不回归）。

### Task 4: core 运行时配置读取器

**Files:**
- Create: `backend/app/core/runtime_config.py`
- Create: `backend/app/tests/unit/test_runtime_config.py`

- [ ] **Step 1: 写失败测试（RED）**

- `get(key)` 命中 DB 值；DB 无值回退环境变量默认；
- TTL 内重复读取不再查库（mock session 断言调用次数）；
- `invalidate()` 后强制回源；
- secret 项 `get` 返回解密明文（仅供后端内部使用，永不出 API）。

- [ ] **Step 2: 实现（GREEN）**

模块级缓存 `dict[key, (value, expires_at)]`，TTL 默认 60s、security 组 30s（总览风险 #3）；提供 `get` / `get_group` / `invalidate`；时间用 `time.monotonic`。运行测试与 lint 通过。

### Task 5: RAGFlow 配置消费与测试连接

**Files:**
- Modify: `backend/app/modules/ragflow/api.py`（新增 `POST /api/admin/ragflow/test-connection`）
- Modify: `backend/app/modules/ragflow/service.py`、`backend/app/adapters/ragflow/http.py`（client 构造改读 runtime_config，环境变量回退）
- Create/Modify: `backend/app/tests/unit/test_ragflow_config.py`

- [ ] **Step 1: 测试连接端点**

仅 system_admin；调 client `ping()`，返回 `{ok, latency_ms, error}` 两态；API Key 一律不出现在响应与日志（沿用 `core/logging.py` 脱敏）。

- [ ] **Step 2: 同步任务读配置**

`run_upload_task` 等处的 base_url / api_key / 重试次数 / 超时改经 runtime_config 读取；保持失败重试语义不变。补单测验证"改库值后新任务用新值"。

### Task 6: 业务模块读取点切换（盘点清单）

**Files:**
- Modify: `backend/app/modules/document/service.py`（`upload_allowed_extensions`、`max_file_size` 校验，约 223–233 行）
- Modify: `backend/app/modules/auth/service.py`（`allowed_email_domains` 约 111 行、`login_max_failed_attempts` / `login_lock_minutes` 约 226–270 行、`require_email_verification` 约 121–132 行）
- Modify: `backend/app/modules/ai/parsers.py`（截断上限改读 `processing.parse_max_pages` / `parse_max_chars`）
- Modify: `backend/app/modules/review/service.py`（`require_review_before_sync` / `block_critical_sensitive_sync` 门禁读取）

- [ ] **Step 1: 逐点切换并记录清单**

每个读取点从 `get_settings()` 改为 `runtime_config.get(...)`（环境变量自动作为回退默认）；在本批次验收报告附"读取点盘点清单"表格（文件 / 行 / 旧来源 / 新 key），确保无半切换状态（总览风险 #3）。

- [ ] **Step 2: 回归**

```powershell
docker compose run --rm backend-api pytest
```

预期：既有 auth / document / review 测试全绿（fixtures 中按需 seed system_configs）。

### Task 7: 前端系统设置页与 RAGFlow 配置页

**Files:**
- Modify: `frontend/src/pages/Settings/index.tsx`（静态展示 → 真实接线）
- Modify: `frontend/src/api/client.ts`（`getConfigs(group)` / `updateConfigs(group, payload)` / `testRagflowConnection()`）
- Create: `frontend/src/pages/Settings/index.test.tsx`

- [ ] **Step 1: 写失败测试（RED）**

- 渲染后按组加载配置并回填表单；
- 修改"单文件最大大小"保存 → `updateConfigs` 以正确 payload 调用 → 成功提示；
- RAGFlow Tab：API Key 输入框只写不读（占位显示掩码），"测试连接"按钮成功/失败两态展示。

- [ ] **Step 2: 实现（GREEN）**

Tabs 分组：基础 / 上传 / 处理 / 安全 / RAGFlow（对应后端五组）；危险项（安全组）保存前二次确认 Modal；保存成功后 invalidate 对应 query。运行：

```powershell
npm --prefix frontend run test:run -- Settings
npm --prefix frontend run lint
npm --prefix frontend run build
```

预期：全部通过。

### Task 8: R2 批次验收

**Files:**
- Create: `docs/phase-reports/2026-06-10-r2-acceptance.md`

- [ ] **Step 1: 全量验证**

```powershell
python -m invoke lint
python -m invoke test
python -m invoke up
docker compose exec -T backend-api alembic current
```

- [ ] **Step 2: 端到端运行时验收**

- Settings 页把"单文件最大大小"改小 → 60s 内（TTL 窗口）上传超限文件被拒，恢复后成功（验证缓存生效与失效）；
- RAGFlow 配置页填错误地址 → 测试连接失败态；填正确地址 → 成功态含延迟；
- 修改安全组邮箱后缀 → 新后缀邮箱可注册、旧后缀外邮箱被拒；
- `audit_logs` 中存在 config.update 记录且不含 API Key 明文；`event_outbox` 有 config.updated 事件。

- [ ] **Step 3: 原子提交**

- `feat(config): 添加 system_configs 表与种子迁移`
- `feat(config): 实现配置读写 API 与审计`
- `feat(config): 添加 core 运行时配置读取器`
- `refactor(config): 业务模块配置读取点切换`
- `feat(ragflow): 添加配置化连接与测试连接端点`
- `feat(frontend): 系统设置页与 RAGFlow 配置接线`
- `docs(report): 添加 R2 批次验收报告`

---

## Self-Review

- Spec coverage: 覆盖 PRD §6.14 全部 24 项配置、§6.8.2 RAGFlow 配置 7 项 + §6.8.8 删除策略 2 项预埋、§7.2.7 / §7.2.13 配置页、验收标准 §11.2"配置 RAGFlow"。
- Placeholder scan: 无 TBD/TODO 占位。
- Type consistency: 配置 key 命名 `group.snake_key` 全栈一致；secret 掩码风格与 ai_providers 既有 `api_key_masked` 一致。
