# Knowledge Uploader 实现状态审计报告

日期：2026-06-09

## 一、TL;DR（执行摘要）

Knowledge Uploader 项目整体处于「核心业务闭环可用、生产部署不可投产」的功能性 MVP 阶段，已完成 spec 全景的约 60-65%。已稳定落地的硬骨架：14 服务 Docker 编排（含 ARM64 overlay）、8 个 Alembic 迁移线性链、`auth/document/review/ragflow` 四大核心模块达 functional ~ complete 水平、文件状态机 17 状态严格走 `DocumentStateMachine`、Redis 分布式锁 + Celery 幂等 + Outbox 事件总线在 RAGFlow 同步链路上经 22+ 用例锤炼、API Key Fernet 加密 + 日志脱敏 + 审计落库全链路通畅，前端 7 个核心页面已接真实 API。

最大风险（投产阻断）：
1. `backend/Dockerfile` runtime stage 错用 `BUILDPLATFORM` → ARM64 多架构构建被破坏（CLAUDE.md §5 红线）；
2. `outbox-dispatcher` 绕过 `@event_handler` 装饰器，事件分发硬编码 if/elif（CLAUDE.md §7 红线）；
3. `audit/config/notification` 三模块仍是骨架，`ConfigChanged` 事件、审计查询 API、邮件/站内信通道完全缺失；
4. 前端 `Register/ForgotPassword/ResetPassword/Users/Settings/Dashboard` 六个页面要么 mock 要么未联调，关键交互测试覆盖严重不足；
5. AI 模块只调用启发式占位，未真正打通 LLM + Outbox 事件 + `ai_usage_logs`。

可投产判断：**内网试运行 OK，DGX Spark 正式上线前必须完成上述四项硬性修复 + AI 模块实质化**。

## 二、基础设施现状

### 2.1 服务清单（`docker-compose.yml` 共 14 个服务）

| 服务 | 镜像 / 构建 | 用途 |
| --- | --- | --- |
| `nginx` | `nginx:1.25-alpine` | 反向代理 + 静态资源入口，`/api` 转 backend-api、其余转 frontend |
| `frontend` | build `frontend/Dockerfile` (`node:20-alpine` + `nginx:1.25-alpine`) | React 构建产物，由内部 nginx 托管 |
| `backend-api` | build `knowledge-uploader-backend:dev` (`python:3.11-slim-bookworm`) | FastAPI 主服务，uvicorn 启动 `app.main:app`，对外暴露 8000 |
| `outbox-dispatcher` | `knowledge-uploader-backend:dev` | 运行 `app.workers.outbox_dispatcher`，把 `event_outbox` 投递 RabbitMQ |
| `worker-document` | `knowledge-uploader-backend:dev` | Celery worker，消费 `document_queue` |
| `worker-ai` | `knowledge-uploader-backend:dev` | Celery worker，消费 `ai_queue` |
| `worker-ragflow` | `knowledge-uploader-backend:dev` | Celery worker，消费 `ragflow_queue` |
| `worker-statistics` | `knowledge-uploader-backend:dev` | Celery worker，消费 `statistics_queue` |
| `worker-notification` | `knowledge-uploader-backend:dev` | Celery worker，消费 `notification_queue` |
| `scheduler` | `knowledge-uploader-backend:dev` | Celery beat 定时调度 |
| `postgres` | `postgres:16-alpine` | 主数据库（UTF8 初始化，`postgres-data` 命名卷） |
| `rabbitmq` | `rabbitmq:3.13-management-alpine` | Celery broker + 域事件总线，含管理 UI |
| `redis` | `redis:7.2-alpine` | Celery result backend、缓存、分布式锁、限流（AOF 持久化） |
| `minio` | `minio/minio:RELEASE.2024-04-18T19-09-19Z` | 对象存储，console 端口 9001 |
| ARM64 Overlay | `docker-compose.arm64.yml` | 给上面全部 14 服务追加 `platform: linux/arm64` |

> 备注：CLAUDE.md / `infra.md` 规则说 12 个服务，实际为 14 个（多出 `nginx` 与 `outbox-dispatcher`）。

### 2.2 Alembic 迁移链（`backend/app/db/migrations/versions/`，共 8 份，线性）

| 迁移文件 | 摘要 |
| --- | --- |
| `e4a13dd4f395_add_auth_user_tables.py` | 初始迁移（`down_revision=None`），创建 `users` 表（UUID、email、email_domain、password_hash、department 等） |
| `47c18588d876_add_audit_logs.py` | 新增 `audit_logs` 表（含 actor_id/action/target_type/target_id 等，覆盖管理员操作审计） |
| `9c1f4d2a6b7e_add_event_outbox.py` | 新增 `event_outbox` 表（BigInt id、event_type、aggregate_type/id、payload jsonb） |
| `b8d4c2e1f903_add_user_session_version.py` | `users` 增 `session_version` 列（默认 0，CHECK 非负），支持 JWT 强制下线 |
| `6d8f2a4c1e90_add_files_table.py` | 新增 `files` 表（original_name、stored_name、extension、mime_type、size 等） |
| `3f9a1c7d2b84_add_review_categories_dataset_mappings.py` | 新增 `categories`（含 parent_id 树形 + `require_review`）与 `dataset_mappings` |
| `a91c4e5d7b20_add_sync_tasks.py` | 新增 `sync_tasks` 表（file_id、task_type、status、retry_count/max_retry_count、error_message） |
| `c7f1a2b9d6e4_add_ai_analysis_tables.py` | 新增 `ai_providers`（含 Fernet 加密的 `api_key_encrypted`）、`ai_feature_configs`、`prompt_templates`、`sensitive_rules`、`document_analysis`、`ai_usage_logs` |

### 2.3 CI（`.github/workflows/knowledge-uploader.yml`）

- 触发：push `main` / PR / `workflow_dispatch`
- 主 job `lint-test-arm64`（`ubuntu-24.04`，附带 `postgres:16-alpine` + `redis:7.2-alpine` service）：
  1. checkout + setup Python 3.11 + setup Node 20（npm 缓存）
  2. `docker compose config` 校验 compose 语法
  3. `python scripts/check_arm64_wheels.py backend/requirements.txt backend/requirements-dev.txt`
  4. 后端：`pip install` → `compileall` → `ruff check backend/app scripts tasks.py` → `python scripts/check_module_boundaries.py` → `cd backend && mypy app` → `pytest backend/app/tests`
  5. 前端：`npm ci` → `npm run lint` → `npm test -- --run` → `npm run build`
  6. Buildx 构建 backend + frontend 的 `linux/amd64` 镜像（`--load`）
- `local-act` job：仅 `nektos/act actor`，通过 `docker.m.daocloud.io` 镜像源拉 base 镜像，便于本地 act 复现

> 落差：CI 不构建 `arm64` 镜像，与 `infra.md §10` 「main 分支 buildx amd64+arm64 push」描述存在落差。

### 2.4 工具链

`pyproject.toml`：
- `ruff` line-length=100、target py311，`select=[E,F,I,B,UP,ASYNC,TID,PTH,RUF]`
- `flake8-tidy-imports.banned-api` 禁止跨模块 import 10 个模块的 `service` 与 `repository`（auth/user/document/review/ragflow/ai/statistics/notification/config/audit），CI 阻塞
- `isort known-first-party=["app"]`，`format quote-style="double"`、`line-ending="lf"`
- `pytest asyncio_mode="auto"`、`testpaths=["backend/app/tests"]`、`pythonpath=["backend"]`
- `mypy strict=true`、`mypy_path=["backend"]`、`plugins=["pydantic.mypy"]`
- `coverage branch=true`、`source=["backend/app"]`

`tasks.py` invoke 命令：`up / down / logs / migrate / test / lint / fmt / check-arm64 / build-arm64`。

### 2.5 关键观察

- `backend/alembic/versions/` 不存在，实际迁移目录是 `backend/app/db/migrations/versions/`。
- `ALEMBIC_DATABASE_URL` 用 `psycopg` v3（`postgresql+psycopg`），运行时 `DATABASE_URL` 用 `asyncpg`，符合 backend 技术栈约束；禁用清单中的 `psycopg2` 未出现。
- 各 worker 健康检查通过 `celery inspect ping` 校验 hostname，`scheduler` 仅 `import` 校验。
- ruff `TID251` 在 `audit/service.py`、`auth/api.py`、`auth/service.py`、`user/api.py`、`user/service.py` 五个文件被 `per-file-ignores` 放行，属历史例外。

相关绝对路径：
- `E:\知识库系统搭建\RAGFlow\docker-compose.yml`
- `E:\知识库系统搭建\RAGFlow\docker-compose.arm64.yml`
- `E:\知识库系统搭建\RAGFlow\.github\workflows\knowledge-uploader.yml`
- `E:\知识库系统搭建\RAGFlow\pyproject.toml`
- `E:\知识库系统搭建\RAGFlow\tasks.py`
- `E:\知识库系统搭建\RAGFlow\backend\app\db\migrations\versions\*.py`

## 三、9 阶段验收清单对照

### 阶段 0 — 项目初始化（complete）

| 状态 | 项目 |
| --- | --- |
| ✅ | monorepo 目录结构齐备（`backend/` + `frontend/` + `docker-compose.yml` + `nginx/` + `deploy/`） |
| ✅ | backend FastAPI 项目落地（`backend/app/main.py`） |
| ✅ | frontend React 项目落地（`frontend/src` 含 14 页面 + AppShell） |
| ✅ | docker-compose 定义 14 个服务 |
| ✅ | PostgreSQL 16 / RabbitMQ 3.13 / Redis 7.2 / MinIO 全部接入 |
| ✅ | Alembic 配置完成（8 个迁移线性链） |
| ✅ | 基础日志 structlog + 统一异常处理 |
| ✅ | `/api/system/health` 路由可用 |
| ✅ | 前端登录页可访问（`frontend/src/pages/Login/index.tsx`） |
| ⚠️ | CI 仅构建 amd64，arm64 镜像未自动 push（与 `infra.md §10` 有落差） |

证据：`docker-compose.yml`、`docker-compose.arm64.yml`、`backend/app/db/migrations/versions/e4a13dd4f395_add_auth_user_tables.py`、`backend/app/main.py`、`frontend/src/pages/Login/index.tsx`、`.github/workflows/knowledge-uploader.yml`、`tasks.py`、`pyproject.toml`

### 阶段 1 — 认证与用户（partial）

| 状态 | 项目 |
| --- | --- |
| ✅ | `users` / `email_verification_tokens` / `password_reset_tokens` 表 |
| ✅ | 9 个 auth API 全部实现（register/login/logout/me/verify-email/resend-verification/forgot-password/reset-password/change-password） |
| ✅ | 公司邮箱域名白名单（`auth/service.py` `allowed_email_domains` + `normalize_email`） |
| ✅ | JWT HS256 + jti 黑名单 + 失败 5 次锁定 + `session_version` 自增 |
| ✅ | Argon2id 密码哈希 + SHA256 token 哈希 |
| ✅ | RBAC 基础（`Roles.EMPLOYEE/KNOWLEDGE_ADMIN/SYSTEM_ADMIN` + `RoleGuard`） |
| ✅ | 用户启用/禁用 API（`user/api.py` `POST /api/users/{id}/disable\|enable`） |
| ✅ | 后端单测（`test_auth_api.py` + `test_user_admin_api.py`） |
| ❌ | 前端 `Register` 页未接 API（`frontend/src/pages/Register/index.tsx`） |
| ❌ | 前端 `ForgotPassword` 仅 UI 无 mutation |
| ❌ | 前端 `ResetPassword` 仅 UI 无 mutation |
| ❌ | `apiClient` 未暴露 register / forgot / reset / verify-email 函数 |
| ⚠️ | user 模块缺 `GET /api/users/me` + `PATCH /api/users/me` + 自助 change-password |
| ⚠️ | `UpdateUserRequest` schema 已定义但无对应 API |
| ⚠️ | `user/list_users` 无分页/搜索参数 |
| ⚠️ | `user/events.py` + `handlers.py` 空骨架，`UserRegistered/UserVerified` 事件未定义 |

证据：`backend/app/modules/auth/api.py`、`backend/app/modules/auth/service.py`、`backend/app/modules/user/api.py`、`backend/app/modules/user/identity.py`、`frontend/src/pages/Register/index.tsx`、`frontend/src/pages/ForgotPassword/index.tsx`、`frontend/src/api/client.ts`

### 阶段 2 — 文件上传与 MinIO（functional）

| 状态 | 项目 |
| --- | --- |
| ✅ | MinIO Client 集成（adapters 层 + `MinioDocumentStorage`） |
| ✅ | `POST /api/files/upload` 完整（扩展名/MIME/大小/hash/去重/限流/Windows 保留字清洗） |
| ✅ | `files` 表（`6d8f2a4c1e90_add_files_table.py`） |
| ✅ | `object_key` + `bucket` 入库，响应隐藏敏感字段 |
| ✅ | 按 `(hash, uploader)` 去重 |
| ✅ | `GET /api/files`（我的文件）+ `GET /api/files/{id}`（详情） |
| ✅ | 前端 Upload / MyFiles / FileDetail 接 API |
| ✅ | `document.file.uploaded` 事件写 `event_outbox` |
| ✅ | 审计日志 `file.upload` |
| ⚠️ | document 模块未引入 `DocumentStateMachine` 抽象（service 直接 `status='uploaded'/'pending'`） |
| ⚠️ | 缺文件下载 / 删除 / 可见性更新 API |
| ⚠️ | 缺管理员视角的文件列表（仅 `list_my_files`） |
| ⚠️ | `document/handlers.py` + `tasks.py` 空骨架 |
| ⚠️ | 未发布 `TextExtracted` / `FileSubmittedForReview` 等后续事件 |

证据：`backend/app/modules/document/api.py`、`backend/app/modules/document/service.py`、`backend/app/modules/document/schemas.py`、`backend/app/tests/unit/test_document_api.py`、`frontend/src/pages/Upload/index.tsx`、`frontend/src/pages/MyFiles/index.tsx`、`frontend/src/pages/FileDetail/index.tsx`

### 阶段 3 — 审核与 Dataset 映射（functional）

| 状态 | 项目 |
| --- | --- |
| ✅ | `categories` 表（含 parent_id 树形 + `require_review`） |
| ✅ | `dataset_mappings` 表（`3f9a1c7d2b84` 迁移） |
| ✅ | `GET /api/review/files`（管理员） |
| ✅ | `POST /api/files/{id}/submit-review / approve / reject` 全部实现 |
| ✅ | `PATCH /api/files/{id}` 修改分类与 Dataset |
| ✅ | Datasets CRUD（`GET/POST/PATCH/DELETE /api/datasets`） |
| ✅ | 分类级 AI 开关 + 审核开关 |
| ✅ | 状态变更走 `DocumentStateMachine.transition` |
| ✅ | `critical` 敏感等级阻止同步 RAGFlow（`_ensure_ragflow_sync_allowed`） |
| ✅ | AI 失败放行策略（`Settings.ai_allow_sync_when_analysis_failed`） |
| ✅ | `ragflow_allowed_dataset_ids` 白名单校验 |
| ✅ | 事件 `review.file.submitted/approved/rejected` 写 outbox |
| ✅ | 审计日志覆盖 7 类管理员动作 |
| ✅ | 前端 `FileManagement` + `DatasetConfig` 全接 API |
| ⚠️ | `list_review_files` / `list_categories` / `list_dataset_mappings` 无分页 |
| ⚠️ | `review/handlers.py` + `tasks.py` 空骨架 |
| ⚠️ | service 层无单测（仅 api 层测试） |
| ⚠️ | `FileManagement` 批量审核/批量同步/导出按钮 disabled 或无 onClick |

证据：`backend/app/modules/review/api.py`、`backend/app/modules/review/service.py`、`backend/app/db/migrations/versions/3f9a1c7d2b84_add_review_categories_dataset_mappings.py`、`backend/app/tests/unit/test_review_api.py`、`frontend/src/pages/FileManagement/index.tsx`、`frontend/src/pages/DatasetConfig/index.tsx`

### 阶段 4 — 任务队列（functional）

| 状态 | 项目 |
| --- | --- |
| ✅ | Celery 配置完整（RabbitMQ broker + Redis result backend） |
| ✅ | `sync_tasks` + `sync_task_logs` 表 |
| ✅ | `GET /api/tasks` + `GET /api/tasks/{id}`（含 logs） |
| ✅ | `POST /api/tasks/{id}/retry` 手动重试（带分布式锁） |
| ✅ | `POST /api/tasks/{id}/cancel` |
| ✅ | Redis 分布式锁 `lock:sync:{file_id}`（`sync_locks.py`） |
| ✅ | partial unique index 防同一文件并发 `ragflow_upload` |
| ✅ | 5 个 worker 容器（document/ragflow/ai/statistics/notification） |
| ✅ | Celery 幂等：`claim_running` `FOR UPDATE` + 状态判断 |
| ✅ | 重复消息测试覆盖（`test_ragflow_task_api.py` 22 用例） |
| ⚠️ | AI Celery 任务 `ai.analyze_file` 未套 Redis 分布式锁 |
| ⚠️ | AI 任务每次 `engine.dispose()` 破坏连接池 |
| ⚠️ | `notification` worker 无任何 Celery 任务定义 |
| ⚠️ | `statistics` worker 无聚合任务（`statistics/tasks.py` 空） |
| ⚠️ | `scheduler`（Celery beat）无定时任务配置 |

证据：`backend/app/modules/ragflow/tasks.py`、`backend/app/modules/ragflow/sync_locks.py`、`backend/app/db/migrations/versions/a91c4e5d7b20_add_sync_tasks.py`、`backend/app/tests/unit/test_ragflow_task_api.py`、`backend/app/modules/ai/tasks.py`

### 阶段 5 — RAGFlow 集成（complete）

| 状态 | 项目 |
| --- | --- |
| ✅ | `RagflowClient` 实现（HTTP adapter） |
| ✅ | 上传文档 / 触发解析 / 查询解析状态 / 删除文档 全部实现 |
| ✅ | `document_id` 入库（`ragflow_document_id` 字段） |
| ✅ | 复用已有 document（重试时不重复上传） |
| ✅ | UNSTART/RUNNING/DONE/FAIL 多分支处理 |
| ✅ | metadata 生成（`_build_metadata`） |
| ✅ | 同步日志（`SyncTaskLog` 写入） |
| ✅ | 错误中 API Key 脱敏（`test_http_ragflow_client_redacts_api_key_from_errors`） |
| ✅ | 失败可重试，`RagflowParsePendingError` 不置文件为 failed |
| ✅ | 状态机 queued/syncing/uploaded_to_ragflow/parsing/parsed/failed 全部经 `DocumentStateMachine` |
| ✅ | E2E 测试（`test_full_pipeline.py`） |
| ⚠️ | `_build_metadata.summary` 永远 None，未注入 AI 摘要 |
| ⚠️ | `handlers.py` 空骨架，事件分派集中在 `outbox_dispatcher`（风格偏离 spec） |
| ⚠️ | `permissions.py` 空骨架，`ADMIN_ROLES` 内联在 service |

证据：`backend/app/modules/ragflow/service.py`、`backend/app/modules/ragflow/tasks.py`、`backend/app/modules/ragflow/sync_locks.py`、`backend/app/tests/unit/test_ragflow_client.py`、`backend/app/tests/unit/test_ragflow_task_api.py`、`backend/app/tests/e2e/test_full_pipeline.py`、`backend/app/workers/outbox_dispatcher.py`

### 阶段 6 — AI 配置与分析（partial）

| 状态 | 项目 |
| --- | --- |
| ✅ | `ai_providers` / `ai_feature_configs` / `prompt_templates` / `sensitive_rules` 表 |
| ✅ | `document_analysis` + `ai_usage_logs` 已建模 |
| ✅ | OpenAI-compatible Client（`OpenAICompatibleProvider`） |
| ✅ | AI 总开关（`AI_ANALYSIS_ENABLED` + `allow_external_llm`） |
| ✅ | 模型供应商配置页（前端 `AiConfig` + `GET/PATCH /api/admin/ai/config`） |
| ✅ | `POST /api/admin/ai/providers/{id}/test` 连通性测试 |
| ✅ | API Key Fernet 加密 + `sk-****abcd` 脱敏 |
| ✅ | 文本抽取（`extract_text` for txt/md/csv） |
| ✅ | 摘要/分类/标签/敏感检测 启发式实现 |
| ✅ | 状态机 `extracting_text/analyzing/analyzed/analysis_failed/sensitive_review_required` 全部经 `DocumentStateMachine` |
| ✅ | AI 关闭时跳过 AI 状态（符合 §4 红线） |
| ✅ | Celery `ai.analyze_file` 任务 + 6 个测试场景 |
| ❌ | `generate_summary` / `suggest_category` / `generate_tags` 仅启发式，未真正调用 LLM |
| ❌ | `ai_usage_logs` 表已建但 service 无 INSERT |
| ❌ | AI 事件 `ai.text.extracted/ai.file.analyzed/ai.sensitive.detected` 从未写 `event_outbox`（违反 §7） |
| ❌ | `handlers.py` 空，未订阅 `FileUploaded` |
| ❌ | PDF/DOCX/OCR 文本抽取未实现 |
| ❌ | AI Provider/Prompt/SensitiveRule 增删改 API 仅部分实现（spec 18 个 admin 端点，仅 5 个完整） |
| ⚠️ | AI Celery 任务无 Redis 锁，每次 dispose engine |

证据：`backend/app/modules/ai/api.py`、`backend/app/modules/ai/service.py`、`backend/app/modules/ai/events.py`、`backend/app/modules/ai/handlers.py`、`backend/app/modules/ai/tasks.py`、`backend/app/db/migrations/versions/c7f1a2b9d6e4_add_ai_analysis_tables.py`、`backend/app/tests/unit/test_ai_api.py`、`backend/app/tests/unit/test_ai_tasks.py`、`frontend/src/pages/AiConfig/index.tsx`

### 阶段 7 — 统计分析（functional）

| 状态 | 项目 |
| --- | --- |
| ✅ | `GET /api/admin/statistics/overview` 概览 |
| ✅ | `GET /api/admin/statistics/users` 用户排行榜 + 分页 |
| ✅ | `GET /api/admin/statistics/users/{user_id}` 单用户详情 |
| ✅ | `GET /api/admin/statistics/departments` 部门统计 |
| ✅ | `GET /api/admin/statistics/categories` 分类统计 |
| ✅ | `GET /api/admin/statistics/trends` 趋势（day/week/month） |
| ✅ | `GET /api/admin/statistics/failures` 失败原因聚合 |
| ✅ | `GET /api/admin/statistics/export` CSV 导出（含公式注入防护） |
| ✅ | 时间/部门/用户/分类/状态/排序/分页过滤完整 |
| ✅ | 所有端点写审计日志 |
| ✅ | 前端 `Statistics` 全接 API + ECharts 可视化 |
| ✅ | employee 403 + invalid sync_status 400 + ragflow 边界等场景测试 |
| ⚠️ | `statistics_snapshots` / `user_upload_statistics` 可选表未实现 |
| ⚠️ | `statistics/handlers.py` + `tasks.py` 空骨架 |
| ⚠️ | 测试归在 `unit/` 但实际为集成测试 |
| ⚠️ | 大数据量下无缓存，每次全量查 files+joins |

证据：`backend/app/modules/statistics/api.py`、`backend/app/modules/statistics/service.py`、`backend/app/modules/statistics/repository.py`、`backend/app/tests/unit/test_statistics_api.py`、`frontend/src/pages/Statistics/index.tsx`

### 阶段 8 — 安全与审计（partial）

| 状态 | 项目 |
| --- | --- |
| ✅ | `audit_logs` 表（`47c18588d876` 迁移，含 4 个索引） |
| ✅ | 登录/上传/审核日志写入（`auth.login.success/failed`、`file.upload`、`file.submit_review/approve/reject/update_classification`） |
| ✅ | Dataset/Category/AI 配置修改日志全部写入 |
| ✅ | 统计查询/导出日志写入 |
| ✅ | API Key Fernet 加密入库（`ai_providers.api_key_encrypted`） |
| ✅ | 日志脱敏 `sk-****abcd` |
| ✅ | 上传频率限制（`_enforce_upload_rate_limit`） |
| ✅ | 登录失败 5 次锁定 15 分钟 |
| ✅ | JWT `jti` 黑名单 + `session_version` 强制下线 |
| ✅ | 普通用户无法访问管理员接口（`RoleGuard` + `SystemAdminDep`） |
| ✅ | `FileResponse` 显式排除 `bucket/object_key/hash/api_key_masked` |
| ❌ | `audit` 模块 API 完全缺失：仅声明 `APIRouter(prefix=/api/admin/audit)` 但无任何 `@router.*` 路由 |
| ❌ | `AuditService` 形同虚设：其他 7 模块直接走 `app.core.audit.record_admin_audit_log` |
| ❌ | `audit` 模块无单测 |
| ❌ | `audit` 模块无保留期清理任务 |
| ❌ | `config` 模块完全是骨架：11 文件中 8 个仅 `from __future__ import annotations` |
| ❌ | `ConfigChanged` 事件未实现（CLAUDE.md §7 点名） |
| ⚠️ | `notification` 模块完全骨架，无邮件/站内信功能 |

证据：`backend/app/db/migrations/versions/47c18588d876_add_audit_logs.py`、`backend/app/core/audit.py`、`backend/app/modules/audit/api.py`、`backend/app/modules/audit/service.py`、`backend/app/modules/config/*`、`backend/app/modules/notification/*`、`backend/app/modules/ai/service.py`

### 阶段 9 — 联调与文档（partial）

| 状态 | 项目 |
| --- | --- |
| ✅ | Docker Compose 完整（14 服务 + arm64 overlay） |
| ✅ | E2E 测试（`backend/app/tests/e2e/test_full_pipeline.py` 全链路） |
| ✅ | CI 流水线完整（lint + module boundaries + mypy + pytest + frontend lint/test/build） |
| ✅ | ARM64 wheel 静态检查（`scripts/check_arm64_wheels.py`） |
| ✅ | 跨模块边界自动检查（`scripts/check_module_boundaries.py`） |
| ✅ | invoke 命令齐全 |
| ✅ | 主要后端流程有测试覆盖（9+ 测试文件） |
| ❌ | 前端 `Register/ForgotPassword/ResetPassword` 未联调（阶段 1 验收链路断裂） |
| ❌ | 前端 `Users` 页 hardcoded mock，无 `/admin/users` API 对接 |
| ❌ | 前端 `Settings` 页 4 Tab 全静态，保存按钮无 `onClick` |
| ❌ | 前端 `Dashboard` 全 mock，未对接 statistics API |
| ❌ | 前端测试覆盖严重不足（仅 StatusTag + AiConfig + Statistics 三个测试） |
| ⚠️ | 未见 README / `.env.example` / 部署说明 / 常见问题文档落库 |
| ⚠️ | `api/client.ts` 单文件 617 行，未按领域拆分 |
| ⚠️ | 缺少全局错误边界 + i18n copy 常量 |

证据：`backend/app/tests/e2e/test_full_pipeline.py`、`.github/workflows/knowledge-uploader.yml`、`tasks.py`、`scripts/check_arm64_wheels.py`、`scripts/check_module_boundaries.py`、`frontend/src/pages/Users/index.tsx`、`frontend/src/pages/Settings/index.tsx`、`frontend/src/pages/Dashboard/index.tsx`、`frontend/src/api/client.ts`

### 整体进度结论

项目整体处于「阶段 5 完成、阶段 6-9 部分完成」的状态，已是核心闭环可用的功能性 MVP，但距 spec 完整完成约还差 35-40%。核心硬骨架（14 服务 Docker 编排、8 迁移线性链、auth/document/review/ragflow 四大模块达 functional ~ complete、文件状态机走 `DocumentStateMachine`、Redis 锁 + Celery 幂等 + outbox 在 ragflow 链路经 22+ 用例验证、Fernet 加密 + 日志脱敏 + 审计落库全通）皆已就位，前端 7 核心页面已接真实 API。距 spec 完成的主要差距：

1. AI 模块未真正调用 LLM（启发式占位）、事件未发布到 outbox、`ai_usage_logs` 未落库、缺 13 个 admin 端点；
2. `config` 与 `notification` 模块基本是骨架，`ConfigChanged` 事件与邮件/站内信通道完全缺失；
3. `audit` 模块无查询 API、`AuditService` 被绕过；
4. 前端 `Register/Forgot/Reset/Users/Settings/Dashboard` 五个页面要么 mock 要么未联调，关键交互测试覆盖严重不足；
5. `user` 模块缺自助接口与分页。

建议下一阶段优先打通 AI 事件总线 + config/notification 实质实现 + 前端认证副流程联调三条线。

## 四、后端模块矩阵

| 模块 | 完成度 | API 数 | 表数 | 事件 | Celery | 测试覆盖 | 红线问题 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `auth` | complete | 9 | 2 (`email_verification_tokens` / `password_reset_tokens`) | 4 (`registered` / `verification-resent` / `verified` / `password.reset-requested`) | 0 | comprehensive (`test_auth_api.py` + `test_auth_models.py`) | 无 — schemas 跨模块复用 `user.identity` 处于 §6 边界擦边 |
| `user` | partial | 4 | 1 (`users`) | 0 | 0 | partial (`test_user_admin_api.py`) | 无；`UserNotFoundError/UserPermissionError` 未走 `AppException` 是规范偏离 |
| `document` | partial | 3 | 1 (`files`) | 1 (`document.file.uploaded`) | 0 | good (`test_document_api.py` 13 场景) | §8 隐患：状态字段未经 `DocumentStateMachine` 抽象（目前只设置初始状态，后续推进若不引入会违规） |
| `review` | functional | 12 | 2 (`categories` / `dataset_mappings`) | 3 (`review.file.submitted/approved/rejected`) | 0 | partial (仅 api 层测试) | 无；用裸 Table 读跨模块表是合理变通 |
| `ai` | functional / partial | 5 (spec 期望 18) | 6 (`ai_providers` / `ai_feature_configs` / `prompt_templates` / `sensitive_rules` / `document_analysis` / `ai_usage_logs`) | 0 写入（仅常量声明） | 1 (`ai.analyze_file`) | good (`test_ai_api.py` + `test_ai_tasks.py`) | **§7 违规：事件未写 `event_outbox`**；`handlers.py` 空 |
| `ragflow` | complete | 4 | 2 (`sync_tasks` / `sync_task_logs`) | 1 (`ragflow.sync_task.queued`) | 2 (`ragflow.create_upload_task` / `ragflow.upload`) | comprehensive (`test_ragflow_client.py` + `test_ragflow_task_api.py` 22 用例 + e2e) | 风格偏离 §7：`handlers.py` 空，桥接放在 `outbox_dispatcher` |
| `audit` | partial | 0 | 1 (`audit_logs`) | 0 | 0 | none | **§6 偏离：其他 7 模块绕过 `AuditService` 直接走 `core/audit.py`**；模块对外查询能力缺失 |
| `config` | scaffold_only | 0 | 0 | 0 | 0 | partial (`test_config.py` 测的是 `core.config.Settings`) | **§7 红线缺口：`ConfigChanged` 事件未实现**；Fernet/审计/super_admin 校验全未落地 |
| `notification` | scaffold_only | 0 | 0 | 0 | 0 | none | 未实现 §7 订阅机制；无邮件/站内信通道 |
| `statistics` | functional | 8 | 0 (只读其他模块表) | 0 | 0 | good (`test_statistics_api.py` 端到端) | 无；CSV 公式注入防护到位、审计齐全；events/handlers/tasks/permissions 4 空文件 |

详细模块清单（API/Service/Events/红线核查）见审计原始数据，关键风险点：

- **`audit` 模块 API 完全缺失**：`backend/app/modules/audit/api.py` 仅声明 `APIRouter(prefix="/api/admin/audit")`，无 `@router.*` 路由；`AuditService.record_admin_action` 未被任何模块调用，所有审计写入走 `app/core/audit.py::record_admin_audit_log`，模块化单体的「模块内聚」语义被旁路。
- **`config` 模块全骨架**：11 文件中 8 个仅 `from __future__ import annotations`；`models.py` 无 `system_configs/ai_configs/ragflow_configs` 表；`api.py` 空 router；`ConfigChanged` 事件常量 + 发布全缺，违反 CLAUDE.md §7 核心事件清单。
- **`notification` 模块全骨架**：无表、无路由、无 handler、无 Celery 任务、无测试；spec 期望订阅 `UserRegistered/UserVerified/FileApproved/FileRejected/SensitiveDetected/RAGFlowParseFailed` 触发邮件/站内信，目前完全未实现。
- **`ai` 模块事件链断裂**：`events.py` 仅声明 routing key 常量，`AiAnalysisService` 从未向 `event_outbox` 写入；`handlers.py` 空，未订阅 `FileUploaded`，依赖 `outbox_dispatcher` 集中分派，与 §7 装饰器订阅约定不符。

## 五、前端实现

### 5.1 页面表（`frontend/src/pages/`）

| 页面 | 路径 | 用途 | 接 API | 完成度 |
| --- | --- | --- | --- | --- |
| Login | `frontend/src/pages/Login/index.tsx` | 登录入口，提交邮箱+密码换 JWT | ✅ | functional |
| Register | `frontend/src/pages/Register/index.tsx` | 注册表单（仅 UI） | ❌ | partial |
| ForgotPassword | `frontend/src/pages/ForgotPassword/index.tsx` | 找回密码邮箱表单 | ❌ | stub |
| ResetPassword | `frontend/src/pages/ResetPassword/index.tsx` | 根据 URL token 设新密码 | ❌ | stub |
| Dashboard | `frontend/src/pages/Dashboard/index.tsx` | 运营总览（全静态 mock） | ❌ | partial |
| Upload | `frontend/src/pages/Upload/index.tsx` | 文件上传，调 `uploadDocument` | ✅ | functional |
| MyFiles | `frontend/src/pages/MyFiles/index.tsx` | 我的文件列表 + 筛选 | ✅ | functional |
| FileManagement | `frontend/src/pages/FileManagement/index.tsx` | 管理员文件审核/驳回 | ✅ | functional |
| FileDetail | `frontend/src/pages/FileDetail/index.tsx` | 文件详情 + 同步信息 | ✅ | functional |
| DatasetConfig | `frontend/src/pages/DatasetConfig/index.tsx` | 分类与 Dataset 映射 CRUD | ✅ | complete |
| AiConfig | `frontend/src/pages/AiConfig/index.tsx` | AI 全局/Feature/Provider/Prompt/敏感规则 | ✅ | functional |
| Statistics | `frontend/src/pages/Statistics/index.tsx` | 概览/趋势/部门/分类/失败 + CSV 导出 | ✅ | complete |
| Users | `frontend/src/pages/Users/index.tsx` | 用户管理（完全 hardcoded mock） | ❌ | partial |
| Settings | `frontend/src/pages/Settings/index.tsx` | 系统设置 4 Tab（全静态） | ❌ | partial |

### 5.2 路由

使用 `react-router-dom` v6 `createBrowserRouter`（`router/index.tsx`）：

- `/` → `RootRedirect`（按 role 跳 `/dashboard` 或 `/my-files`）
- publicRoutes 包 `PublicRoute` 守卫：`/login`、`/register`、`/forgot-password`、`/reset-password/:token`
- 受保护区段包 `RequireAuth` + `AppShell`，子路由再包 `RoleGuard`（按 roles 数组校验）：
  - `/dashboard`、`/upload`、`/my-files`（员工可见）
  - `/files`、`/files/:id`（管理员）
  - `/datasets`、`/ai-config`、`/users`、`/settings`（system_admin）
  - `/statistics`（knowledge_admin + system_admin）
- `*` 兜底跳 `/`

角色定义在 `store/auth.store.ts`（`Roles.EMPLOYEE/KNOWLEDGE_ADMIN/SYSTEM_ADMIN`），导航菜单从 `appNavigationRoutes` 自动派生。

### 5.3 状态管理

符合 `.claude/rules/frontend.md §2` 分工：
- UI 状态走 Zustand（`store/` 目录；注意目录名为 `store` 而非规范的 `stores`）：`auth.store.ts`（accessToken + user，localStorage persist）、`ui.store.ts`（sidebarCollapsed，localStorage persist）
- 服务端状态走 TanStack Query（`QueryClientProvider` staleTime 30s, retry 1）；queryKey 形如 `['documents','mine']/['review-files']/['categories']/['dataset-mappings']/['ai-config']/['statistics',...]`
- 未发现服务端数据放进 Zustand 的违规

### 5.4 API Client

`frontend/src/api/client.ts` 完整实现（单文件 **617 行**，所有 API 类型 + 函数集中在此，未按 §3 推荐的按领域拆分）：
- ✅ axios 实例 baseURL 取自 `VITE_API_BASE_URL`（默认 `/api`），timeout 15s
- ✅ 请求拦截器从 `useAuthStore.getState().accessToken` 注入 `Authorization: Bearer <token>`
- ✅ 响应拦截器：401 自动 `clearSession` + `window.location.assign('/login')`
- ✅ `unwrapResponse` 解 `{success,data,message}` 信封
- ✅ 覆盖 auth login/logout、文件 upload/list/get、分类 CRUD、Dataset CRUD+disable、AI config、统计 6 查询 + export(blob)、审核 submit/approve/reject + 文件分类更新
- ⚠️ **缺**：注册 / 忘记密码 / 重置密码 / 邮箱验证 / 用户管理 / 设置保存

### 5.5 Theme Tokens

`frontend/src/theme/tokens.ts` 实现完整：colors（21 个语义/状态色）、`statusTagColors`（14 个 tone）、radius/spacing/typography/layout、`themeCssVariables`（25 个 CSS 变量挂到 `.app-root` `style`）。`theme/antd-theme.ts` 注入 antd `ConfigProvider`。`StatusTag.tsx` 严格走 token，提供 file/review/sync/risk/user/dataset 6 类映射 + dot 变体。Dashboard/Upload/MyFiles/FileManagement/AiConfig/Settings 等页面均通过 `StatusTag` 渲染状态，未发现硬编码 `<Tag color="green">`。**但 Dashboard ECharts color 数组仍硬编码 `['#1677ff','#16a34a',...]`**，未走 token（轻微违规）。

### 5.6 测试

Vitest 覆盖较薄，**仅 3 个测试文件**：
- `components/StatusTag.test.tsx`（3 断言）
- `pages/AiConfig/index.test.tsx`
- `pages/Statistics/index.test.tsx`

其余关键交互均无单测：登录 mutation、文件上传、审核/驳回流程、Dataset CRUD、路由守卫 `RequireAuth/RoleGuard`、`auth.store`、`apiClient` 拦截器（401 跳转、token 注入）。无 E2E。距 `tests.md §11` 「登录、上传、审核操作、AI 配置提交关键交互必有测试」差距明显。

### 5.7 整体评估

前端处于 **functional**（核心管理员/上传者闭环可用），尚未达 complete。核心业务链路（登录 → 上传 → 我的文件 → 文件管理审核 → Dataset 映射 → AI 配置 → 统计 + 导出 → 文件详情）已全部接真实 API。

按严重度排序的缺口：
1. **认证副流程未联调**：Register/ForgotPassword/ResetPassword 仅 UI，apiClient 无对应函数，直接影响阶段 1/9 验收
2. **Users 页面完全 hardcoded**：apiClient 无 `/admin/users` 接口
3. **Settings 页面 4 Tab 全静态**：「保存」按钮无 `onClick`
4. **Dashboard 全 mock**：已有 statistics API 完全可对接但未接
5. **测试覆盖严重不足**
6. 目录命名不一致（规范 `stores/` vs 实际 `store/`）
7. Dashboard ECharts 颜色硬编码
8. `api/client.ts` 617 行单文件，未拆分
9. FileManagement 批量按钮 disabled/无 onClick；FileDetail 缺敏感片段/AI 结果扩展
10. 缺全局错误边界 + i18n copy 常量

## 六、红线违规与质量风险

### 6.1 硬性红线违规

- **`backend/Dockerfile` runtime stage 错用 `BUILDPLATFORM`**：在 amd64 host 上 buildx 构建 arm64 镜像时 runtime 层仍会拉 amd64 镜像，破坏 CLAUDE.md §5 跨平台规则与 `infra.md §1` 多架构要求
- **`outbox-dispatcher` 绕过 `@event_handler` 装饰器**：`core/events.py` 有 `@event_handler` 装饰器与 `EVENT_HANDLERS` 字典，但 `outbox_dispatcher` 实际通过 if/elif 硬编码三个事件类型（`DOCUMENT_FILE_UPLOADED / REVIEW_FILE_APPROVED / RAGFLOW_SYNC_TASK_QUEUED`）分发，违反 CLAUDE.md §7 「模块订阅事件用 `@event_handler` 装饰器」硬规则；各模块 `handlers.py` 形同虚设
- **`ai` 模块违反 §7 outbox 规则**：`events.py` 仅声明 routing key 常量，`AiAnalysisService` 从未向 `event_outbox` 写入 `ai.text.extracted / ai.file.analyzed / ai.sensitive.detected`，AI 对外完全沉默
- **核心事件清单不全**：CLAUDE.md §7 要求 14 个事件，`core/events.py` 只定义 1 个 `FileUploaded`，其余通过字符串 routing key 散落在各模块，未形成统一注册中心
- **`audit` 模块未按 §6 9 文件结构落地**：仅 `models.py` 有效，API/Service/handlers/permissions/tasks 全空，违反模块边界规则
- **`config` 模块未按 §6 9 文件结构落地**：8 文件仅 `from __future__ import annotations`，`ConfigChanged` 事件缺失
- **`nginx/default.conf proxy_read_timeout` 仅 300s**，`infra.md §7` 要求 600s 兼容 RAGFlow 长操作
- **`deploy/` 目录不存在**：CLAUDE.md §3 列出但仓库无此目录，无 K8s manifest / 无 DGX Spark 部署脚本 / 无 ARM64 host 挂载点示例
- **`outbox-dispatcher` publish 与 mark_published 分两步**：publish 成功后进程崩溃事务回滚，事件会重投；订阅方需幂等。`sync_locks` 兜底了 RAGFlow，但 `ai.analyze_file` 无 idempotency key

### 6.2 质量风险

- `DocumentStateMachine` 仅返回字符串、不实际更新 ORM，调用方需自行 update，存在绕过状态机的风险（项目内无静态检查）
- `Fernet ENCRYPTION_KEY` 在 `core/config.py` 硬编码 `DEFAULT_DEV_ENCRYPTION_KEY = 'RZ1Sw_27VrN9c5Cfsq01qiwViwT6y7jDCuXYn7tgGJY='`，且 `docker-compose.yml` 直接把该字符串作为默认值传入；非 PROTECTED_ENVS 环境不校验，存在误用到生产风险
- `jwt_secret` 默认值 `'change-me-change-me-change-me-change-me'` 长度 32 字符勉强通过检查，非 PROTECTED_ENVS 不校验
- `HttpRagflowClient._client_error` 通过 `_redact_secret` 替换 API Key 为 `****`，但若 RAGFlow 返回 message 中嵌入部分 key 前缀（如 `ragflow-xxx... invalid`），脱敏会被绕过；日志层 `mask_secrets_processor` 是兜底
- Celery `task_routes` 用 `document.* / ai.* / ragflow.*` 通配，但 `celery_app.conf.imports` 只导入 `ai.tasks` 和 `ragflow.tasks`；`document/statistics/notification` 的 `tasks.py` 未显式导入，worker 启动时 task 注册可能失败
- `outbox-dispatcher` healthcheck 仅 `python -c "import app"`，无法发现 `dispatch_loop` 卡死
- 上传限流仅靠 Redis incr，非滑动窗口；并发上传时 expire 首次设置后可能配额漂移
- `test_outbox_dispatcher` 等单测存在，但缺少 dispatcher 崩溃恢复 / 重复发布 / 死信队列的端到端测试
- AI 模块 `schemas.py` 中存在 `api_key` 字段引用，需进一步确认响应序列化层完全脱敏
- 缺 `deploy/` 目录意味着无 K8s manifest、无 TLS 配置、无 DGX Spark 部署脚本

### 6.3 缺口

- 测试归属：所有测试集中在 `backend/app/tests/unit/`，模块内 `tests/` 目录为空，违反 §6 模块化结构暗示；statistics 等集成测试归在 `unit/` 下分层不准
- 文档：未见 `README.md` / `.env.example` / 部署说明 / 常见问题文档落库
- nginx 默认无 TLS（仅监听 80），生产需外层补 TLS
- `docker-compose.arm64.yml` 仅 29 行 overlay，未详细审计 host 挂载点 / CPU/内存 limit

### 6.4 投产判断

总体处于「核心功能可投产、生产部署不可投产」状态。10 个模块的 service/repository 全部满足 ruff 跨模块禁令；状态机、Outbox、JWT、Argon2、Fernet、日志脱敏、限流、审计、RAGFlow HTTP client、AI mock+真实 Provider 均落地，CI 跑 lint+mypy+pytest+模块边界+ARM64 wheel 检查。

**4 个硬性阻断**：
1. `backend/Dockerfile` runtime stage 错用 `BUILDPLATFORM` → ARM64 镜像无法正确多架构构建
2. `outbox-dispatcher` 绕过 `@event_handler` → 违反 §7
3. `deploy/` 目录与生产 TLS、K8s manifest 完全缺失
4. `audit/notification` 模块未按 9 文件结构落地

**当前阶段评估为「内网试运行 OK，DGX Spark 正式上线前必须完成上述修复 + e2e 覆盖率验证」**。

## 七、剩余 gap 与下一步建议（P0/P1/P2）

### P0（投产阻断，必须修复）

1. **修复 `backend/Dockerfile` 多架构构建**：runtime stage 改用 `--platform=$TARGETPLATFORM`；在 CI 中真正跑 `docker buildx build --platform linux/amd64,linux/arm64 --push`，与 `infra.md §10` 对齐
2. **统一事件订阅到 `@event_handler` 装饰器**：删除 `outbox_dispatcher` 中的 if/elif 硬编码分发；让 `EVENT_HANDLERS` 字典在各模块 `handlers.py` 中通过 `@event_handler(EventType)` 自动注册，dispatcher 仅做透传
3. **`config` 模块实质化**：建表（`system_configs / ai_configs / ragflow_configs`）+ Alembic 迁移 + Fernet 加密 + `ConfigChanged` 事件 + super_admin 校验 + 审计写入 + 配置变更广播 Celery 任务
4. **`audit` 模块 API 落地**：实现 `GET /api/admin/audit/logs`（按 actor/target/时间范围/分页过滤）+ `GET /api/admin/audit/export`，把 `app/core/audit.py` 写入逻辑迁回 `AuditService`，补 `tasks.py` 保留期清理
5. **`notification` 模块实质化**：建 `notifications / notification_preferences / notification_templates` 表 + `handlers.py` 订阅 6+ 域事件 + `tasks.py` Celery 发送邮件 + API 未读列表/标记已读/偏好设置 + 单测/E2E
6. **`deploy/` 目录补齐**：K8s manifest（StatefulSet for postgres/rabbitmq/redis/minio、Deployment for backend-api + workers）+ DGX Spark 部署脚本 + nginx TLS 配置 + ARM64 host 挂载点示例
7. **生产密钥校验严格化**：把 `DEFAULT_DEV_ENCRYPTION_KEY` 与 `jwt_secret` 默认值在非显式 `dev`/`test` 环境一律拒绝启动
8. **前端 Register/ForgotPassword/ResetPassword 接 API**：apiClient 暴露 `register / forgotPassword / resetPassword / verifyEmail / resendVerification`，三页面接 useMutation + Toast 反馈，覆盖阶段 1 验收链路

### P1（功能完整性）

1. **AI 模块事件总线 + 真 LLM**：
   - `events.py` 定义 `AiTextExtracted / AiFileAnalyzed / AiSensitiveDetected` Pydantic 事件
   - service 写 `event_outbox`（同事务）
   - `handlers.py` 订阅 `DOCUMENT_FILE_UPLOADED` 自动触发 `ai.analyze_file`
   - `generate_summary / suggest_category / generate_tags` 真正经 PromptTemplate + `OpenAICompatibleProvider` 调用，落 `ai_usage_logs`
   - Celery 任务套 `lock:sync:{file_id}` 锁，去掉每次 `engine.dispose()`
   - 补 13 个缺失的 admin 端点（Prompt CRUD、SensitiveRule CRUD、Provider 排序/默认设置等）
2. **document 模块状态机抽象 + 后续状态事件**：所有 status 写入走 `DocumentStateMachine.transition`；发布 `TextExtracted / FileSubmittedForReview` 等后续事件；补管理员视角列表 + 下载/删除 API
3. **user 模块自助接口**：补 `GET/PATCH /api/users/me`、`POST /api/users/me/change-password`、`PATCH /api/users/{id}` 改 role、list 分页/搜索
4. **前端 Users / Settings / Dashboard 接 API**：Users 页接 `/admin/users` 系列；Settings 4 Tab 接 `/admin/config`；Dashboard 接 `/admin/statistics/overview + trends`
5. **前端关键交互测试补齐**：登录 mutation、上传、审核/驳回、Dataset CRUD、路由守卫、`auth.store`、`apiClient` 拦截器
6. **CI 跑 arm64 镜像构建 + push**
7. **Celery 任务路由修复**：`celery_app.conf.imports` 加入 `document.tasks / statistics.tasks / notification.tasks`
8. **`outbox-dispatcher` liveness 探针**：把健康检查从 `import app` 改为 `dispatch_loop` 心跳

### P2（质量与体验）

1. 拆分 `frontend/src/api/client.ts` 为 `api/auth.ts / api/files.ts / api/admin-ai.ts / api/admin-users.ts / api/admin-statistics.ts`
2. 全局错误边界 + i18n copy 常量（`src/constants/copy.ts`）
3. 目录命名对齐（`store/` → `stores/`）
4. Dashboard ECharts 颜色改走 token
5. 模块内测试目录 `backend/app/modules/<module>/tests/` 拆分；把 statistics 集成测试搬到 `tests/integration/`
6. 文档落库：`README.md` + `.env.example` + `docs/deployment.md` + `docs/faq.md`
7. 统计模块预聚合：`statistics_snapshots / user_upload_statistics` 表 + 增量更新 Celery 任务（订阅 `FileApproved` 等）
8. RAGFlow `_build_metadata.summary` 注入 AI 摘要
9. `ragflow/permissions.py` 抽出 `ADMIN_ROLES` 与 `require_admin` helper
10. 上传限流改滑动窗口（lua 脚本或 Redis Streams）

## 八、附：审计方法论

### 8.1 使用的工具与 agent

- 直接 `Read / Glob / Grep / Bash` 工具检索仓库
- 未触发 codegraph 索引（项目 indexer 未确认就绪），所有结论以源码 + 迁移文件 + spec md 文件交叉验证为准
- 未触发其他 MCP 或 plugin skill

### 8.2 扫描覆盖范围

- 后端模块：`backend/app/modules/{auth,user,document,review,ragflow,ai,audit,config,notification,statistics}` 11 文件结构与跨模块 import 红线
- 数据库：`backend/app/db/migrations/versions/` 8 个 Alembic 迁移线性链
- 基础设施：`docker-compose.yml`、`docker-compose.arm64.yml`、`backend/Dockerfile`、`frontend/Dockerfile`、`nginx/`
- CI：`.github/workflows/knowledge-uploader.yml`、`scripts/check_arm64_wheels.py`、`scripts/check_module_boundaries.py`
- 工具：`pyproject.toml`、`tasks.py`
- 前端：`frontend/src/pages/`（14 页面）、`frontend/src/router/`、`frontend/src/store/`、`frontend/src/api/client.ts`、`frontend/src/theme/`、`frontend/src/components/StatusTag`
- 测试：`backend/app/tests/unit/`、`backend/app/tests/e2e/`、`frontend/src/**/*.test.{ts,tsx}`
- 安全/红线：JWT/Argon2/Fernet、`app/core/audit.py`、`app/core/events.py`、`app/workers/outbox_dispatcher.py`、`app/modules/ragflow/sync_locks.py`、`DocumentStateMachine`
- spec：CLAUDE.md §1-§16 + `需求文档/02-08`（按 spec 期望反查实现）

### 8.3 未覆盖部分（已知盲区）

- `frontend/src/components/AppShell/Sidebar/TopHeader/PageContainer` 三件套源码未逐行阅读，仅按页面 import 推断
- `backend/app/adapters/` 各 storage/http client 适配器实现细节未逐行核对
- `backend/app/tests/e2e/test_full_pipeline.py` 仅按文件名确认存在，未逐行验证覆盖率
- `nginx/default.conf` 仅看了 `proxy_read_timeout`，其他指令（CORS、上传 body 大小、gzip）未审计
- `docker-compose.arm64.yml` 仅 29 行 overlay，未与 `docker-compose.yml` diff 比对完整覆盖项
- RAGFlow / AI Provider 真实联调验证（需外网），仅按代码路径推断
- 性能压测（限流、Celery 吞吐、PostgreSQL 连接池）未进行
- 安全渗透测试（注入、CSRF、文件上传攻击向量）未执行，仅按代码静态评估
- Alembic `env.py` 并发/online 模式未检查
- `backend/alembic/` 路径（仓库根下）不存在，迁移实际在 `backend/app/db/migrations/`，需确认 `alembic.ini` 是否正确指向该目录

### 8.4 审计依据 spec 文档

- `CLAUDE.md` §1-§16（项目级规则）
- `需求文档/02_ARCHITECTURE_最终架构设计.md`
- `需求文档/03_BACKEND_SPEC_后端开发规范.md`
- `需求文档/05_DATABASE_API_SPEC_数据库与API规范.md`
- `需求文档/07_DEPLOYMENT_ENV_部署与环境配置.md`
- `需求文档/08_TASK_BREAKDOWN_开发任务拆解.md`
- `docs/spark/2026-06-04-p0-implementation-supplement.md`（按 §3 引用）
- `.claude/rules/backend.md` / `frontend.md` / `infra.md` / `tests.md`（按 §16 路径约定）
