# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

Knowledge Uploader — 公司内部知识库文件贡献与 RAGFlow 同步平台。员工通过 Web 上传文档，平台完成文件校验、去重、AI 分析、管理员审核、RAGFlow Dataset 同步，最终供钉钉客服机器人检索。前端不直接访问 RAGFlow 或 AI Provider，所有外部密钥只在后端和 Worker 环境中使用。

## 常用命令

```powershell
# 本地开发（Docker 只跑基础设施，FastAPI + Vite 在宿主机热更新）
scripts\dev.bat                    # 启动基础设施 + 后端 + 前端
scripts\dev.bat worker             # 额外启动 Outbox Dispatcher + Celery Worker + Beat
scripts\dev-stop.bat               # 停止开发基础设施

# Docker 全量启停
invoke up                          # docker compose up -d --build
invoke down

# 数据库迁移
invoke migrate                     # alembic upgrade head
invoke migrate --msg="add users"   # alembic revision --autogenerate

# 测试
invoke test-backend                # pytest（容器内）
invoke test-backend -k "login"     # 按关键字过滤
invoke test-frontend               # vitest --run（本地 npm）
invoke test                        # 后端 + 前端

# Lint
invoke lint-backend                # ruff check + 模块边界检查 + mypy --strict
invoke lint-frontend               # eslint
invoke lint                        # 后端 + 前端

# 格式化
invoke fmt                         # ruff format + prettier

# 提交前门禁
invoke check                       # lint + test
invoke ship                        # check + check-arm64（发布前）
```

前端单独命令（在 `frontend/` 目录下或使用 `--prefix`）：

```powershell
npm --prefix frontend run dev      # Vite dev server (localhost:5173)
npm --prefix frontend run build    # tsc --noEmit + vite build
npm --prefix frontend run test     # vitest (watch 模式)
npm --prefix frontend run test:run # vitest --run
npm --prefix frontend run lint     # eslint
```

## 架构

### 技术栈

- **后端**: FastAPI + SQLAlchemy (async) + Alembic + Celery + Kombu
- **前端**: React 18 + TypeScript + Ant Design 5 + React Query + Zustand + Vite
- **基础设施**: PostgreSQL 16 + RabbitMQ + Redis + MinIO + Nginx

### 后端分层（模块化单体）

```
backend/app/
├── main.py              # FastAPI 入口，注册所有模块路由
├── core/                # 跨模块基础设施
│   ├── config.py        # pydantic-settings，全量环境变量
│   ├── database.py      # async engine + session factory
│   ├── permissions.py   # Role enum (employee/dept_admin/system_admin)，require_role 依赖
│   ├── document_state.py # 文档状态机（全量状态转移白名单）
│   ├── events.py        # 领域事件总线 (DomainEvent → @event_handler)
│   ├── outbox.py        # event_outbox 表 + OutboxRepository
│   ├── security.py      # JWT、密码哈希
│   └── deps.py          # FastAPI 依赖注入
├── modules/             # 业务模块（每个标准 9 文件）
│   ├── auth/            # 注册登录、邮箱验证、密码重置、JWT
│   ├── user/            # 用户管理、角色变更
│   ├── document/        # 文件上传、状态流转、分类
│   ├── review/          # 审核流程
│   ├── ragflow/         # RAGFlow API 集成、同步任务
│   ├── ai/              # AI 分析、Provider 配置
│   ├── config/          # 运行时系统配置
│   ├── department/      # 部门管理
│   ├── statistics/      # 统计聚合
│   ├── audit/           # 审计日志
│   └── notification/    # 邮件通知
├── adapters/            # 外部服务适配器 (email, llm, minio, ragflow, storage)
├── workers/             # Celery app + Outbox Dispatcher
├── db/                  # Base model、migrations、models 注册
└── tests/               # unit/ + integration/ + e2e/ + red_team/
```

**每个模块标准结构**: `api.py`（路由）→ `schemas.py`（Pydantic DTO）→ `service.py`（业务编排）→ `repository.py`（数据访问）→ `models.py`（ORM）→ `events.py`（域事件）→ `handlers.py`（事件处理）→ `tasks.py`（Celery 任务）→ `permissions.py`/`exceptions.py`（可选）。

### 模块边界规则

**跨模块禁止直接 import `service.py` 和 `repository.py`**（ruff TID251 强制）。模块间通信只能通过：
- 领域事件（event_outbox → outbox_dispatcher → handlers）
- Celery task
- 共享 schemas

### 事件驱动架构

Service 层写 `event_outbox` 表 → `outbox_dispatcher` 轮询发布 → `handlers.py` 消费 → 可能发 Celery task。Celery 路由按模块分队列：`document_queue`、`ai_queue`、`ragflow_queue`、`notification_queue`。

### 前端结构

```
frontend/src/
├── api/client.ts        # Axios 封装，统一 ApiEnvelope<T>，401 自动登出
├── store/               # Zustand (auth.store.ts, ui.store.ts)，localStorage 持久化
├── router/              # React Router v6，lazy loading，RequireAuth/RoleGuard 守卫
├── pages/               # 按功能分目录，每目录含 index.tsx + styles.css
├── layouts/             # AppShell + Sidebar + TopHeader + PageContainer
├── components/          # 共享组件 (QueryBoundary, StatusTag, KpiCard, Sparkline)
├── theme/               # Ant Design 主题 token + CSS 变量 (--ku-color-*, --ku-spacing-*)
└── utils/               # format, download, uploadConfig
```

### API 约定

所有业务响应统一 envelope: `{ success, data, message, request_id, error_code? }`。认证使用 Bearer JWT。三种角色：`employee`、`dept_admin`（知识库管理员）、`system_admin`。

## 关键约束

- 数据库统一 PostgreSQL 16，不使用 SQLite
- 文件存储统一 MinIO，不使用本地文件系统
- 核心长任务统一 Celery + RabbitMQ，不使用 FastAPI BackgroundTasks
- 文件状态变更只能通过 `DocumentStateMachine`（`core/document_state.py`）
- 管理员操作必须写 `audit_logs`
- RAGFlow API Key 与 AI Provider API Key 不返回前端、不打日志
- 新增 Python 依赖前必须通过 ARM64 wheel 检查（`invoke check-arm64`）
- 后端行宽 100，ruff + mypy --strict；前端 Prettier 行宽 100，ESLint strict TS
- pytest 使用 `asyncio_mode = "auto"`，测试数据库为 `knowledge_uploader_test`
- 后端测试在 Docker 容器内运行（`docker compose run --rm backend-api pytest`）

## 分支命名

格式：`tsk/YYYYMMDD/功能描述`

- **tsk** — 固定前缀
- **YYYYMMDD** — 创建分支的日期（如 `20260701`）
- **功能描述** — 短横线连接的英文描述（如 `nav-restructure`、`fix-upload-403`）

示例：
```
tsk/20260701/nav-restructure
tsk/20260701/fix-upload-403
tsk/20260702/add-department-module
```

## 开发端口

| 服务 | 本地开发端口 | Docker 端口 |
|---|---|---|
| 前端 Vite | 5173 | 80 (Nginx) |
| 后端 API | 18000 | 18000→8000 |
| PostgreSQL | 15432 | 5432 |
| Redis | 16379 | 6379 |
| RabbitMQ AMQP | 15673 | 5672 |
| MinIO API | 19000 | 9000 |
