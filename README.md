# Knowledge Uploader

公司内部知识库文件贡献与 RAGFlow 同步平台。

员工通过 Web 上传文档，平台完成文件校验、去重、可选 AI 分析、管理员审核和 RAGFlow Dataset 同步，最终提供给下游问答服务使用。前端不直接访问 RAGFlow 或 AI Provider，所有外部密钥只在后端和 Worker 环境中使用。

## 当前阶段

本仓库已进入阶段 9 的联调与验收整改，但**阶段 9 尚未完成**。本轮目标中的产品主链、角色工作台，以及真 LLM、版本/到期、保存视图、容量/成本可视化等增强治理已完成代码级补齐；这不等于发布验收通过，仍需按 [验收矩阵](docs/product/ACCEPTANCE_MATRIX.md) 取得真实基础设施、ARM64、灾备、外部服务和独立审查证据后，才能声明可上线。

已有基础能力包括：

- FastAPI 后端、React + TypeScript 前端、Nginx 反向代理。
- PostgreSQL 16、RabbitMQ、Redis、MinIO、Celery Worker、Outbox Dispatcher、Scheduler 的 Docker Compose 编排。
- 注册登录、部门归属与验证门禁、密码重置、登录锁定、JWT 注销。
- 文件草稿/自动提交、白名单与 MIME 校验、去重、MinIO 存储、鉴权原件预览/下载。
- 分类与 Dataset 映射、审核领取/SLA、明确的 `sync|approve_only` 决策、RAGFlow 同步/重试/取消。
- 员工、部门管理员和系统管理员工作台，通知闭环、服务端分页搜索与移动端布局。
- 规则与真实 LLM 分析、成本四态、文档版本/替代、到期负责人、保存视图及容量/成本可视化。
- 用户管理、权限控制、审计日志、日志脱敏、DLQ 与可观测性基础能力。

仍阻断阶段 9 的项目包括：真实全栈基础设施与故障恢复 E2E、受保护 MinIO 指标鉴权的最终证据、DGX Spark ARM64 实机、PostgreSQL + MinIO 配对备份恢复、真实 SMTP（`EXT-SMTP-001`）、Webhook（`EXT-WEBHOOK-001`）、LLM（`EXT-LLM-001`）和 RAGFlow（`EXT-RAGFLOW-001`）外部联调，以及尚未定版的可计费 LLM 月度预算契约（`COST-002`）。验收证据将在完成后归档到 `docs/phase-reports/<version>/`；目录、报告或单元测试存在本身都不代表通过。

## 快速启动

### 1. 准备环境

- Docker Desktop 或 Docker Engine + Compose v2。
- Python 3.11，用于 `invoke` 和本地脚本。
- Node.js 20，用于前端测试、lint 和本地开发。
- PowerShell 7 或 Windows PowerShell。

安装本地命令依赖：

```powershell
python -m pip install invoke
```

### 2. 创建本地配置

```powershell
Copy-Item .env.example .env
```

本地默认端口：

| 服务 | 地址 | 说明 |
|---|---|---|
| Nginx 前端入口 | `http://localhost` | 生产型访问入口 |
| 后端健康检查 | `http://localhost:18000/api/system/health` | 宿主机端口为 18000，避免占用 8000 |
| 容器内后端 | `backend-api:8000` | 仅供 Compose 网络内部访问 |

生产或共享环境必须替换 `.env` 中的 `JWT_SECRET`、`ENCRYPTION_KEY`、数据库密码、MinIO 密钥、RAGFlow API Key 和 AI Provider Key。生成 Fernet key 可使用：

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 3. 启动服务

```powershell
docker compose up -d --build
docker compose exec backend-api alembic upgrade head
```

也可以使用项目封装命令：

```powershell
invoke up
invoke migrate
```

确认健康状态：

```powershell
docker compose ps
curl http://localhost:18000/api/system/health
```

返回 `{"status":"ok"}` 即后端可用。

### 4. 初始化首个管理员

迁移完成后创建首个系统管理员：

```powershell
$env:SEED_ADMIN_PASSWORD="<至少 8 位的初始密码>"
docker compose exec -e SEED_ADMIN_PASSWORD backend-api python scripts/seed_admin.py --email admin@company.com --name "System Admin"
Remove-Item Env:\SEED_ADMIN_PASSWORD
```

脚本默认只允许首次 bootstrap。系统内已存在 `system_admin` 时会拒绝执行；仅在明确恢复既有 `system_admin` 账号时追加 `--force-existing-system-admin`，脚本会重置目标账号并写入一条 `user.seed_system_admin` 审计日志。

### 5. 访问前端

打开 `http://localhost`。当前产品路由如下；页面已实现仍须以 [前端契约](需求文档/04_FRONTEND_SPEC_前端开发规范.md) 和验收矩阵的角色、响应式与全链路证据为验收口径：

| 路由 | 角色 | 页面 |
|---|---|---|
| `/login` | 公开 | 登录 |
| `/register` | 公开 | 注册 |
| `/forgot-password` | 公开 | 找回密码 |
| `/upload` | 登录用户 | 文件上传 |
| `/my-files` | 登录用户 | 我的文件 |
| `/dashboard` | 登录用户 | 按角色展示员工、部门管理员或系统管理员工作台 |
| `/files` | 部门管理员、系统管理员 | 文件管理与审核 |
| `/datasets` | 系统管理员 | Dataset 配置 |
| `/ai-config` | 系统管理员 | AI 配置 |
| `/statistics` | 系统管理员 | 统计分析 |
| `/users` | 系统管理员 | 用户管理 |
| `/departments` | 系统管理员 | 部门管理 |
| `/settings` | 系统管理员 | 系统设置占位页 |

前端视觉以 [产品视觉与工作台设计](docs/design/design.md) 为权威参考，并复用 [设计系统](docs/design-system.md) 与 [交互原型](docs/design-system/prototype-app.html)。配套 image concept 当前为非阻塞待补项，不作为实现真源。

## 本地开发模式

日常改前端或后端时，优先使用本地开发脚本：Docker 只运行 PostgreSQL、Redis、RabbitMQ、MinIO，FastAPI 和 Vite 在宿主机运行，支持热更新。

```powershell
scripts\dev.bat
```

首次运行前如本机没有后端虚拟环境或前端依赖，先执行：

```powershell
scripts\dev-setup.bat
```

默认启动：

- Docker 基础设施：PostgreSQL、Redis、RabbitMQ、MinIO。
- 本地后端：`http://127.0.0.1:18000`，使用 `uvicorn --reload`。
- 本地前端：`http://127.0.0.1:5173`，使用 Vite dev server，`/api` 自动代理到后端。
- 默认开发期基础设施端口：PostgreSQL `15432`、Redis `16379`、RabbitMQ AMQP `15673`、RabbitMQ 管理页 `15672`、MinIO API `19000`、MinIO 控制台 `19001`。

需要调试异步任务时：

```powershell
scripts\dev.bat worker
```

这会额外启动 Outbox Dispatcher、Celery Worker 和 Celery Beat。Windows 本地 Celery 使用 `--pool=solo`。

脚本自检：

```powershell
scripts\dev.bat check
```

停止开发基础设施：

```powershell
scripts\dev-stop.bat
```

部署或提交前仍使用全 Docker 验证：

```powershell
scripts\dev-check.bat
```

## RAGFlow 联调

只在测试 Dataset 或明确目标 Dataset 上联调，不操作既有知识库。

```env
RAGFLOW_BASE_URL=http://192.168.4.46:8092
RAGFLOW_API_KEY=<后端环境变量中配置>
RAGFLOW_ALLOWED_DATASET_IDS=<新建测试 Dataset id>
```

`RAGFLOW_API_KEY` 非空时，后端会强制要求 `RAGFLOW_ALLOWED_DATASET_IDS` 非空。管理员在 `/datasets` 中创建映射时也必须使用 allowlist 内的 Dataset id。

## 常用命令

```powershell
# 启停
invoke up
invoke down
invoke logs --service=backend-api

# 数据库
invoke migrate
invoke migrate --msg="add users"

# 日常聚焦检查
invoke lint-backend
invoke test-backend -k "test_login"
invoke lint-frontend
invoke test-frontend

# 提交前与发布前门禁
invoke check
invoke ship

# 格式化与 ARM64
invoke fmt
invoke check-arm64
invoke build-arm64 --version=0.1.0
```

## 文档索引

- [权威文档索引](需求文档/README.md)：PRD、唯一状态机/API、架构、前后端、AI/RAGFlow、部署和真实阶段。
- [角色工作台 IA](docs/product/IA_ROLE_WORKBENCH.md)：员工、部门管理员、系统管理员与移动端信息架构。
- [运行时配置契约](docs/product/CONFIG_CONTRACT.md)：逐项消费者、默认值、敏感性、生效方式与死配置处置。
- [保护发布证据门禁](ops/runbooks/protected-release.md)：DGX 实机、外部运维证据、手动 workflow 与 `invoke ship` 的闭环。
- [产品与上线验收矩阵](docs/product/ACCEPTANCE_MATRIX.md)：P0/P1/P2、真实基础设施、ARM64 与备份恢复门禁。
- [视觉设计](docs/design/design.md)：Emerald/Stone token 迁移、两类工作台和响应式规范。
- `docs/api.md` / `docs/deployment.md`：当前实现快照；与权威目标冲突时以 `需求文档/` 为准，并在验收矩阵跟踪差距。
- [常见问题](docs/faq.md)：端口、迁移、MinIO、RAGFlow、AI 和前端构建。
- [AGENTS.md](AGENTS.md)：项目级工程红线。

## 关键约束

- 数据库统一 PostgreSQL 16，不使用 SQLite。
- 正式文件存储统一 MinIO，不使用本地文件系统。
- 核心长任务统一 Celery + RabbitMQ。
- 文件状态变更只能通过 service 层状态机。
- 管理员操作必须写 `audit_logs`。
- RAGFlow API Key 与 AI Provider API Key 不返回前端、不打日志。
- 新增 Python 依赖前必须通过 ARM64 wheel 检查。
