# Knowledge Uploader

公司内部知识库文件贡献与 RAGFlow 同步平台。

员工通过 Web 上传文档，平台完成文件校验、去重、可选 AI 分析、管理员审核和 RAGFlow Dataset 同步，最终提供给下游问答服务使用。前端不直接访问 RAGFlow 或 AI Provider，所有外部密钥只在后端和 Worker 环境中使用。

## 当前阶段

本仓库已进入阶段 9 的联调与验收整改，但**阶段 9 尚未完成**。当前代码已具备主要模块骨架与多数基础流程，仍需按 [验收矩阵](docs/product/ACCEPTANCE_MATRIX.md) 完成 P0 主链、P1 上线门禁与独立审查后，才能声明可上线。

已有基础能力包括：

- FastAPI 后端、React + TypeScript 前端、Nginx 反向代理。
- PostgreSQL 16、RabbitMQ、Redis、MinIO、Celery Worker、Outbox Dispatcher、Scheduler 的 Docker Compose 编排。
- 注册登录、账号密码登录、密码重置、登录锁定、JWT 注销。
- 文件上传、白名单校验、MIME 二次校验、去重、MinIO 存储、个人文件列表。
- 分类与 Dataset 映射、文件审核、RAGFlow 上传任务、重试与取消。
- AI 配置、默认 Prompt/敏感规则和规则分析状态机；真正 LLM 分析仍属于 P2 增强治理。
- 统计分析、用户管理、权限控制、审计日志和日志脱敏。

正在整改的关键项包括：部门归属与验证门禁、草稿/自动提交、鉴权原件访问、明确的 RAGFlow 决策、角色工作台、领取/SLA、通知与分页、死配置、真实基础设施 E2E、DLQ/告警、ARM64 实机和备份恢复。验收证据将在完成后归档到 `docs/phase-reports/<version>/`，目录或报告存在本身不代表通过。

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

打开 `http://localhost`。目标产品路由如下；角色工作台和部分认证路由仍在阶段 9 整改中，以 [前端契约](需求文档/04_FRONTEND_SPEC_前端开发规范.md) 为验收口径：

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
