# CLAUDE.md — Knowledge Uploader

> 项目级 AI 工程师规则文件。每次工作开始前必读。

## 1. 项目一句话

公司员工通过 Web 上传文档 → 校验/去重/可选 AI 分析 → 管理员审核 → 同步到 RAGFlow → 喂给钉钉客服机器人。


## 3. 目录结构

```
E:\知识库系统搭建\RAGFlow\          ← 项目根（.claude/ 所在目录）
├── backend/
│   └── app/
│       ├── main.py                 ← FastAPI 入口
│       ├── core/                   ← 基础设施（config / database / events / security / permissions / ...）
│       ├── db/                     ← ORM base / session / migrations（Alembic）
│       ├── adapters/               ← 外部服务适配器
│       ├── modules/                ← 业务模块（见 §7）
│       ├── workers/                ← Celery app + outbox-dispatcher
│       ├── utils/                  ← 通用工具
│       └── tests/                  ← pytest 测试
├── frontend/
│   └── src/
│       ├── api/client.ts           ← API 客户端 + 全部数据模型
│       ├── components/             ← 可复用组件（StatusTag / KpiCard / Sparkline / QueryBoundary）
│       ├── hooks/                  ← 自定义 Hooks
│       ├── layouts/                ← AppShell / Sidebar / TopHeader / PageContainer
│       ├── pages/                  ← 路由页面（20+ 页面目录）
│       ├── router/                 ← 路由定义 + 守卫（RBAC）
│       ├── store/                  ← Zustand（auth.store / ui.store）
│       ├── theme/                  ← tokens.ts + antd-theme.ts
│       └── utils/                  ← format / download / uploadConfig
├── nginx/                          ← Nginx 配置
├── deploy/                         ← 部署脚本
├── scripts/                        ← 辅助脚本（check_arm64_wheels / check_module_boundaries）
├── 需求文档/                        ← 产品/架构/API spec
├── docs/                           ← 补充文档
├── .claude/                        ← Claude Code 配置（skills / scripts / settings）
├── docker-compose.yml              ← 开发环境编排
├── pyproject.toml                  ← ruff / mypy / pytest 配置
└── tasks.py                        ← invoke 任务定义
```

## 4. 技术栈

### 后端

| 层       | 技术                                             |
| -------- | ------------------------------------------------ |
| 框架     | FastAPI + Uvicorn                                |
| ORM      | SQLAlchemy 2.0（AsyncSession）                   |
| 数据库   | PostgreSQL 16                                    |
| 迁移     | Alembic（autogenerate）                          |
| 对象存储 | MinIO                                            |
| 消息队列 | RabbitMQ（Celery broker）                        |
| 任务队列 | Celery（4 个专用 worker + 1 个 beat scheduler）  |
| 缓存/锁  | Redis 7.2                                        |
| 密码     | Argon2id（argon2-cffi）                          |
| 加密     | Fernet（cryptography）                           |
| 日志     | structlog                                        |
| Python   | 3.11                                             |

### 前端

| 层         | 技术                                    |
| ---------- | --------------------------------------- |
| 框架       | React 18 + TypeScript（strict）        |
| 构建       | Vite 8                                  |
| UI         | Ant Design 5 + Pro Components           |
| 状态管理   | Zustand（持久化到 localStorage）        |
| 服务端状态 | TanStack Query 5                        |
| HTTP       | Axios（Bearer Token 自动注入）          |
| 图表       | ECharts 5                               |
| 路由       | React Router 6                          |
| 测试       | Vitest + React Testing Library + Playwright |
| Node       | ≥ 20.19（ESM）                          |

## 5. 服务架构（docker-compose）

```
                        ┌──────────┐
                        │  nginx   │:80
                        └──┬───┬───┘
                  静态资源 │   │ /api/*
               ┌──────────┘   └──────────┐
               ▼                         ▼
        ┌───────────┐            ┌──────────────┐
        │ frontend  │            │ backend-api  │:8000
        │ (Vite→Nginx)│           └──────┬───────┘
        └───────────┘                    │ 发布事件到 outbox
                                         │
        ┌────────────────────┐   ┌───────▼────────┐
        │  outbox-dispatcher │──▶│   RabbitMQ     │
        │  (轮询 outbox 表)   │   └──┬──┬──┬──┬───┘
        └────────────────────┘      │  │  │  │  4 个队列
   ┌────────────────────────────────┘  │  │  └──────────────────┐
   ▼                    ▼              ▼                        ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌───────────────────┐
│worker-document│ │  worker-ai   │ │worker-ragflow│ │worker-notification│
│ document_queue│ │  ai_queue    │ │ragflow_queue │ │notification_queue │
└──────────────┘ └──────────────┘ └──────────────┘ └───────────────────┘
                                                    
        ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
        │ Postgres │  │  Redis   │  │  MinIO   │  │scheduler │
        │   :5432  │  │  :6379   │  │  :9000   │  │(celery   │
        └──────────┘  └──────────┘  └──────────┘  │  beat)   │
                                                   └──────────┘
```

## 6. 架构红线（不可逾越）

- 不使用 SQLite。数据库统一 **PostgreSQL 16**。
- 不使用本地文件系统作为正式存储。文件统一 **MinIO**。
- 不使用 FastAPI BackgroundTasks 承担核心长任务。长任务统一 **Celery + RabbitMQ**。
- 前端不直接访问 RAGFlow、AI 模型。
- RAGFlow API Key 与 AI Provider API Key **绝不返回前端**，**绝不打日志**。
- 文件状态机变更只能通过 service 层方法，不能直接 update ORM。
- 所有管理员操作必须写 `audit_logs`。
- AI 关闭时（`AI_ANALYSIS_ENABLED=false`），文件不能进入任何 AI 相关状态。

## 7. 模块边界（硬规则）

### 当前模块清单（11 个）

`ai` · `audit` · `auth` · `config` · `department` · `document` · `notification` · `ragflow` · `review` · `statistics` · `user`

### 标准文件结构

```
backend/app/modules/<module>/
├── __init__.py
├── api.py           ← FastAPI Router
├── schemas.py       ← Pydantic DTO（可被跨模块导入）
├── models.py        ← SQLAlchemy ORM
├── repository.py    ← 数据访问，无业务逻辑
├── service.py       ← 业务编排
├── events.py        ← 本模块发布的域事件
├── handlers.py      ← 本模块订阅的事件处理
├── permissions.py   ← 模块特定权限（可选）
├── tasks.py         ← Celery task（可选）
└── exceptions.py    ← 模块特定异常（可选）
```

### 跨模块规则

- ❌ 禁止 `from app.modules.X.service import ...`
- ❌ 禁止 `from app.modules.X.repository import ...`
- ✅ 允许 `from app.modules.X.schemas import ...`
- ✅ 模块间通信只走：(1) 事件总线 (2) Celery task (3) 共享 schemas
- ✅ `auth`/`user` 共享 `users` 表的特例：`auth` 只能依赖 `app.core.identity.UserIdentityStore` 协议；具体 ORM 实现只能放在 `user` 模块内
- `ruff` 配置 + `check_module_boundaries.py` 已 ban 跨模块 service/repository import，违规 CI 阻塞

## 8. 域事件规则

- 模块发布事件用 `event_outbox` 表（同事务），由 `outbox-dispatcher` 投递 RabbitMQ
- 模块订阅事件用 `@event_handler(EventClass)` 装饰器，定义在 `handlers.py`
- 事件命名：`<module>.<aggregate>.<action>`（routing key）
- 核心事件清单：`UserRegistered` · `UserVerified` · `FileUploaded` · `TextExtracted` · `FileAnalyzed` · `SensitiveDetected` · `FileSubmittedForReview` · `FileApproved` · `FileRejected` · `RAGFlowDocumentUploaded` · `RAGFlowParseStarted` · `RAGFlowParseCompleted` · `RAGFlowParseFailed` · `ConfigChanged`

## 9. 文件状态机

完整状态见 `需求文档/05_DATABASE_API_SPEC_数据库与API规范.md §2`。硬规则：

- 状态变更只能通过 `DocumentStateMachine.transition(from, to)` 调用（`backend/app/core/document_state.py`）
- AI 关闭：跳过 `extracting_text` / `analysis_queued` / `analyzing` / `analysis_failed` / `analyzed`
- 敏感等级 `critical`：默认阻止同步 RAGFlow
- 同一文件不能同时存在多个 `ragflow_upload` 任务（Redis 分布式锁，key 模式 `lock:sync:{file_id}`）

## 10. 安全规则

- 密码：Argon2id（`argon2-cffi`）
- JWT：HS256，secret 至少 32 字节随机；过期 24h（可配 `JWT_EXPIRE_MINUTES`）
- API Key 字段级加密：Fernet（key 从环境变量 `ENCRYPTION_KEY` 加载）
- 邮箱验证 token / 重置密码 token：入库前 SHA256 hash，原文只在邮件中出现一次
- 文件上传：扩展名白名单 + `filetype` 二次校验 + 文件名清洗 + 大小限制（`UPLOAD_MAX_FILE_SIZE_BYTES`）
- 限流：登录失败 5 次锁 15 分钟；上传 10 次/分钟/用户
- 日志中所有 API Key 字段统一脱敏为 `sk-****abcd`

## 11. 跨平台规则（Windows 本机 → DGX Spark ARM64 生产）

- 路径用 `pathlib.Path`，禁止字符串拼接
- 文件读写明确 `encoding="utf-8"`
- 行尾全部 LF（`.gitattributes` 强制）
- 新加 Python 依赖前必须检查 ARM64 wheel：`invoke check-arm64`
- 禁用清单：`psycopg2*` · `python-magic*` · `mysqlclient` · `pycrypto` · `m2crypto`
- 文件名清洗必须过滤 Windows 保留名（CON / PRN / AUX / NUL / COM* / LPT*）
- Dockerfile 必须 `--platform=$BUILDPLATFORM`，base image 必须官方多架构

## 12. 代码规范

### Python

- `ruff` (lint + format) + `mypy --strict`
- 行宽 100，字符串引号统一 `"`
- 导入顺序：stdlib → 第三方 → app（ruff isort 规则集）
- 函数注解：所有 public 函数必须有完整类型注解
- 异步：API 路由、Service、Repository 全 `async def`，DB 用 `AsyncSession`
- 日志：用 `structlog.get_logger()`，不能 `print()`
- lint 规则集：E / F / I / B / UP / ASYNC / TID / PTH / RUF（见 `pyproject.toml`）

### TypeScript / React

- `tsconfig.json` strict 模式
- UI 状态用 Zustand（`store/`），服务端状态用 TanStack Query
- 所有 API 调用走 `api/client.ts`（含 JWT 注入和错误处理）
- 状态展示走 `<StatusTag>` 组件，不可硬编码颜色
- 颜色 / 圆角 / 间距走 `theme/tokens.ts`，不能内联魔数
- 角色守卫走 `router/guards.tsx`（RequireAuth / RoleGuard）

### 角色体系（三级）

| 角色 | 默认页 | 权限范围 |
|---|---|---|
| `employee` | `/my-files` | 上传、查看个人文件、修改资料 |
| `dept_admin` | `/files` | 部门文件管理、任务日志 |
| `system_admin` | `/dashboard` | 所有管理功能、系统配置 |

## 13. 测试要求

- 后端：pytest + pytest-asyncio + httpx + factory-boy
- 前端：Vitest + React Testing Library
- E2E：Playwright（`frontend/e2e/`）
- 覆盖：core / utils / repository 必有单测；每个 API 至少 1 happy + 1 failure
- E2E：上传 → 审核 → RAGFlow 同步全链路（mock RAGFlow + mock LLM）
- 测试不依赖外网（CI 无外网）

## 14. 提交规范

### 格式

```
type(scope): 中文描述
```

- **type**（英文，必填）：`feat` / `fix` / `refactor` / `docs` / `test` / `chore` / `perf` / `style` / `ci` / `build` / `revert`
- **scope**（英文，可选）：`auth` / `user` / `document` / `review` / `ragflow` / `ai` / `config` / `audit` / `department` / `statistics` / `notification` / `frontend` / `infra` / `claude`
- **冒号 + 中文描述**（必填）：简洁、动词开头、不带句号

### 示例

```
feat(auth): 添加邮箱验证流程
fix(document): 修复连续 5 次失败登录未锁定的问题
refactor(ragflow): 把 RagflowClient 拆分为接口与实现
test(ai): 补全 Prompt 模板渲染单测
```

### 规则

- AI 完成代码必须主动提交（每个原子变更立刻 commit），不要堆积
- 一个提交一个原子变更（小到能独立 review 和回滚）
- DB schema 变更必须含 Alembic 迁移
- 新增 Python 依赖必须 `invoke check-arm64` 通过

### 禁止

- ❌ 英文描述（如 `feat(auth): add email verification`）
- ❌ 缺 type（如 `添加注册流程`）
- ❌ 模糊 type（如 `update` / `improve` / `change`）
- ❌ 句末有句号
- ❌ 多个原子变更塞一个 commit
- ❌ 任何 trailer（`Co-Authored-By:` / `Signed-off-by:` / `Reviewed-by:` 等都不要带）

## 15. 常用命令

```powershell
# === 启停 ===
invoke up                          # 启动所有容器
invoke down                        # 停止
invoke logs --service=backend-api  # 看日志

# === 数据库 ===
invoke migrate --msg="add files"   # 创建迁移
invoke migrate                     # 升级到最新

# === 测试 ===
invoke test-backend -k "test_login"  # 后端聚焦测试
invoke test-frontend                 # 前端测试
invoke test                          # 全部测试

# === 代码质量 ===
invoke lint                        # ruff + mypy + 模块边界 + ESLint
invoke fmt                         # ruff format + prettier

# === 门禁 ===
invoke check                       # 提交前：lint + test
invoke ship                        # 发布前：check + check-arm64

# === 跨架构 ===
invoke check-arm64                 # 依赖 ARM64 wheel 检查
invoke build-arm64 --version=0.1.0 # 构建 ARM64 镜像
```

## 16. Hooks 与自动化

项目通过 `.claude/scripts/` 下的 PowerShell 脚本实现自动化护栏：

| Hook | 类型 | 功能 |
|---|---|---|
| `protect-secrets.ps1` | PreToolUse | 阻止写 `.env` / 硬编码密钥到代码 |
| `check-cross-module-imports.ps1` | PostToolUse | 警告跨模块 service/repository import |
| `mark-pending-gate.ps1` | PostToolUse | 改了 `backend/app/` 或 `frontend/src/` → 标记未验收 |
| `adversarial-gate.ps1` | Stop | 有未验收改动时阻止结束，要求先跑 `/ship-gate` |

### 完成门流程

改了源码 → `mark-pending-gate` 自动标记 → 结束时 `adversarial-gate` 拦截 → 跑 `/ship-gate`（事实层 + quality-reviewer + security-auditor + red-team 四方审查） → 全绿才放行。

### Skills 清单

| Skill | 用途 |
|---|---|
| `/cmd` | 项目常用命令速查 |
| `/fix-issue` | 按问题定位代码、最小修复、加回归测试 |
| `/new-module` | 脚手架新后端模块（标准 9 文件结构） |
| `/new-migration` | 创建 Alembic 迁移 + review + 双向验证 |
| `/check-arm64` | 检查 Python 依赖的 ARM64 兼容性 |
| `/review-code` | 四方评审（事实层 + 审计 + 安全 + 红队），只出报告 |
| `/ship-gate` | 完成门验收（复用 review-code + 放行决策 + 清标记） |

## 17. 文档索引

| 你要找 | 去看 |
|---|---|
| 文件状态机 | `需求文档/05_DATABASE_API_SPEC §2` + `需求文档/03_BACKEND_SPEC §5` |
| API 设计 | `需求文档/05_DATABASE_API_SPEC §3` |
| AI Provider 集成 | `需求文档/06_AI_RAGFLOW_SPEC §2` |
| 部署/环境变量 | `需求文档/07_DEPLOYMENT_ENV` |
| 前端设计稿 | `docs/design/design.md` |
| 测试 fixture | `backend/app/tests/conftest.py` |
| 跨平台注意事项 | 补充 spec §2 |
| 域事件 | 补充 spec §3 |
| ruff 规则配置 | `pyproject.toml` |
| invoke 任务定义 | `tasks.py` |

## 18. 维护原则

- 每完成一个阶段后 review 一次本文件
- 发现 AI 反复犯同样错误 → 加入对应章节的规则
- 不写"建议"类规则，只写"必须 / 禁止"
- 详细的路径特定规则在 `.claude/skills/` 中定义，不要塞到此文件
