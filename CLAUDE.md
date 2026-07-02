# CLAUDE.md

本文件指导 Claude Code 在本仓库中工作。

## 回复要求

必须回复Kai

## ⚠️ 不可违反的规则（最高优先级）

- **YOU MUST**：文档状态变更只能通过 `DocumentStateMachine`（`backend/app/core/document_state.py`），禁止直接改写 `status` 字段。状态转移白名单只在该文件维护。
- **YOU MUST**：RAGFlow API Key 与 AI Provider API Key 绝不返回前端、绝不写入日志或错误信息。前端不直接访问 RAGFlow 或 AI Provider，所有外部密钥只在后端和 Worker 环境使用。
- **YOU MUST**：跨模块禁止直接 import 其他模块的 `service.py` / `repository.py`（ruff TID251 会报错）。模块间通信只能通过：领域事件、Celery task、共享 schemas。
- **YOU MUST**：管理员操作（角色变更、审核、配置修改、部门管理等）必须写 `audit_logs`——在 service 层调用 `record_admin_audit_log` / `record_audit_log`（`backend/app/core/audit.py`）。
- **YOU MUST**：后端测试只在 Docker 容器内运行（`invoke test-backend`），不要在宿主机直接跑 pytest。
- **IMPORTANT**：技术选型固定——数据库只用 PostgreSQL 16（不用 SQLite）、文件存储只用 MinIO（不用本地文件系统）、长任务只用 Celery + RabbitMQ（不用 FastAPI BackgroundTasks）。
- **IMPORTANT**：新增 Python 依赖前必须通过 ARM64 wheel 检查（`invoke check-arm64`，或用 check-arm64 skill）。

## 项目概述

Knowledge Uploader — 公司内部知识库文件贡献与 RAGFlow 同步平台。员工通过 Web 上传文档，平台完成文件校验、去重、AI 分析、管理员审核、RAGFlow Dataset 同步，最终供钉钉客服机器人检索。

## 常用命令

只列最高频命令；完整命令清单用 cmd skill 查询。

```powershell
# 本地开发（Docker 只跑基础设施，FastAPI + Vite 在宿主机热更新）
scripts\dev.bat                    # 启动基础设施 + 后端 + 前端
scripts\dev.bat worker             # 额外启动 Outbox Dispatcher + Celery Worker + Beat

invoke migrate                     # alembic upgrade head（新建迁移用 new-migration skill）
invoke test-backend                # 后端 pytest（容器内），-k "login" 可过滤
invoke test-frontend               # 前端 vitest --run
invoke lint                        # 后端 ruff + 模块边界 + mypy --strict；前端 eslint
invoke fmt                         # ruff format + prettier
invoke check                       # 提交前门禁：lint + test
```

## 架构

- **后端**：FastAPI + SQLAlchemy (async) + Alembic + Celery + Kombu
- **前端**：React 18 + TypeScript + Ant Design 5 + React Query + Zustand + Vite
- **基础设施**：PostgreSQL 16 + RabbitMQ + Redis + MinIO + Nginx

### 后端分层（模块化单体）

`backend/app/` 四层：

- `core/` — 跨模块基础设施：`config`（pydantic-settings）、`database`、`permissions`（Role enum：employee / dept_admin / system_admin，`require_role` 依赖）、`document_state`（状态机）、`events`（事件总线）、`outbox`、`security`（JWT）、`audit`、`deps`
- `modules/` — 11 个业务模块：auth、user、document、review、ragflow、ai、config、department、statistics、audit、notification
- `adapters/` — 外部服务适配器：email、llm、minio、ragflow、storage
- `workers/`（Celery app + Outbox Dispatcher）、`db/`（Base model + migrations）、`tests/`（unit / integration / e2e / red_team）

每个模块遵循标准 9 文件结构（api → schemas → service → repository → models → events → handlers → tasks → permissions/exceptions）。**新建模块必须用 new-module skill 脚手架**，不要手工创建。

### 事件驱动

Service 层写 `event_outbox` 表 → `outbox_dispatcher` 轮询发布 → 各模块 `handlers.py` 消费 → 可能发 Celery task。Celery 按模块分队列：`document_queue`、`ai_queue`、`ragflow_queue`、`notification_queue`。

### 前端结构

`frontend/src/`：`api/client.ts`（Axios 封装，统一 `ApiEnvelope<T>`，401 自动登出）、`store/`（Zustand，localStorage 持久化）、`router/`（React Router v6，lazy loading，RequireAuth / RoleGuard 守卫）、`pages/`（按功能分目录，每目录 `index.tsx` + `styles.css`）、`layouts/`、`components/`、`theme/`（AntD token + `--ku-*` CSS 变量）、`utils/`。

### API 约定

所有业务响应统一 envelope：`{ success, data, message, request_id, error_code? }`。认证使用 Bearer JWT。三种角色：`employee`、`dept_admin`（知识库管理员）、`system_admin`。

## 代码风格与测试

- 后端行宽 100（ruff + mypy --strict）；前端 Prettier 行宽 100（ESLint strict TS）——均由 `invoke lint` 强制
- pytest 使用 `asyncio_mode = "auto"`，测试数据库为 `knowledge_uploader_test`
- 提交前用 review-code skill 做四方评审；宣称"完成"前跑 ship-gate skill

## 分支命名

格式 `tsk/YYYYMMDD/功能描述`：tsk 固定前缀 + 创建日期 + 短横线英文描述。
示例：`tsk/20260701/nav-restructure`、`tsk/20260701/fix-upload-403`。

## 开发端口

| 服务 | 本地开发端口 | Docker 端口 |
| --- | --- | --- |
| 前端 Vite | 5173 | 80 (Nginx) |
| 后端 API | 18000 | 18000→8000 |
| PostgreSQL | 15432 | 5432 |
| Redis | 16379 | 6379 |
| RabbitMQ AMQP | 15673 | 5672 |
| MinIO API | 19000 | 9000 |

## 结尾复述（最致命的三条）

1. 文档状态只能走 `DocumentStateMachine`。
2. RAGFlow / AI Provider 的 API Key 绝不出现在前端响应和日志里。
3. 跨模块只能走领域事件 / Celery task / 共享 schemas，禁止直接 import 别的模块的 service 和 repository。
