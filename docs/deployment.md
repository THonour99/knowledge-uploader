# 部署说明

本文面向本地联调、测试环境和生产环境部署。架构红线：PostgreSQL 16、MinIO、RabbitMQ、Redis、Celery Worker 必须保留；不使用 SQLite、本地文件系统或 FastAPI BackgroundTasks 承担核心长任务。

## 服务清单

`docker-compose.yml` 当前编排服务：

| 服务 | 职责 |
|---|---|
| `nginx` | 前端入口、API 反向代理、上传大小限制 |
| `frontend` | React 静态资源 |
| `backend-api` | FastAPI API 服务 |
| `outbox-dispatcher` | 从 `event_outbox` 投递领域事件到 RabbitMQ |
| `worker-document` | 文档预处理队列 |
| `worker-ai` | AI 分析队列 |
| `worker-ragflow` | RAGFlow 上传、解析、轮询队列 |
| `worker-statistics` | 统计快照和聚合队列 |
| `worker-notification` | 邮件通知队列 |
| `scheduler` | Celery Beat 定时任务 |
| `postgres` | 业务数据库 |
| `rabbitmq` | Celery Broker |
| `redis` | 缓存、锁、限流、Celery result backend |
| `minio` | 文件对象存储 |

## 本地端口

基础 compose 默认只暴露：

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `NGINX_HTTP_PORT` | `80` | 前端和 API 统一入口 |
| `BACKEND_API_HOST` | `127.0.0.1` | 后端宿主机监听地址 |
| `BACKEND_API_PORT` | `18000` | 后端宿主机端口，避免占用 8000 |

容器内后端仍监听 `8000`，仅 Compose 网络内部使用。不要把宿主机后端端口改回 `8000`，以免和现有 Docker 服务冲突。

需要直接访问依赖服务时，可启用 `docker-compose.override.yml.example`：

```powershell
Copy-Item docker-compose.override.yml.example docker-compose.override.yml
```

覆盖文件默认使用：

- Nginx: `8080`
- Frontend: `5173`
- Backend API: `127.0.0.1:18000`
- PostgreSQL: `5432`
- RabbitMQ: `5672`, `15672`
- Redis: `6379`
- MinIO: `9000`, `9001`

## 环境变量

生产和共享环境必须配置：

| 分类 | 变量 |
|---|---|
| 应用 | `APP_ENV`, `APP_BASE_URL`, `JWT_SECRET`, `ENCRYPTION_KEY` |
| 数据库 | `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `DATABASE_URL`, `ALEMBIC_DATABASE_URL` |
| RabbitMQ | `RABBITMQ_USER`, `RABBITMQ_PASSWORD`, `CELERY_BROKER_URL` |
| Redis | `REDIS_PASSWORD`, `CELERY_RESULT_BACKEND`, `CACHE_REDIS_URL` |
| MinIO | `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_BUCKET`, `MINIO_SECURE` |
| 上传 | `UPLOAD_MAX_FILE_SIZE_BYTES`, `UPLOAD_RATE_LIMIT_PER_MINUTE`, `UPLOAD_ALLOWED_EXTENSIONS`, `UPLOAD_ALLOWED_MIME_TYPES` |
| 认证 | `ALLOWED_EMAIL_DOMAINS`, `LOGIN_MAX_FAILED_ATTEMPTS`, `LOGIN_LOCK_MINUTES`, `AUTH_*_RATE_LIMIT_PER_HOUR` |
| RAGFlow | `RAGFLOW_BASE_URL`, `RAGFLOW_API_KEY`, `RAGFLOW_ALLOWED_DATASET_IDS` |
| AI | `AI_ANALYSIS_ENABLED`, `ALLOW_EXTERNAL_LLM`, `LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` |
| SMTP | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_TLS` |

`APP_ENV=production`、`prod` 或 `staging` 时，启动校验会拒绝占位 `JWT_SECRET`、默认 `ENCRYPTION_KEY` 和 `MINIO_SECURE=false`。

## RAGFlow

联调目标：

```env
RAGFLOW_BASE_URL=http://192.168.4.46:8092
RAGFLOW_API_KEY=<只放在后端环境>
RAGFLOW_ALLOWED_DATASET_IDS=<新建测试 Dataset id>
```

规则：

- 不在前端保存或展示 RAGFlow API Key。
- `RAGFLOW_API_KEY` 非空时必须配置 `RAGFLOW_ALLOWED_DATASET_IDS`。
- `/datasets` 只能创建 allowlist 内的新测试或明确目标 Dataset 映射。
- 不删除、不覆盖、不迁移 RAGFlow 服务器上的既有知识库。

`DEFAULT_DATASET_ID` 目前只保留在 `.env.example` 和 compose 环境中用于后续扩展，当前同步目标以 `/datasets` 创建的 Dataset 映射为准。

## 首个系统管理员

迁移完成后创建首个 `system_admin`：

```powershell
$env:SEED_ADMIN_PASSWORD="<至少 8 位的初始密码>"
docker compose exec -e SEED_ADMIN_PASSWORD backend-api python scripts/seed_admin.py --email admin@company.com --name "System Admin"
Remove-Item Env:\SEED_ADMIN_PASSWORD
```

脚本默认只允许首次 bootstrap；系统内已存在 `system_admin` 时会拒绝执行。仅在明确恢复既有 `system_admin` 账号时追加 `--force-existing-system-admin`，脚本会重置目标账号并写入 `user.seed_system_admin` 审计日志。共享环境执行后应立即由管理员登录并修改密码。

## 前端 API 地址

前端默认使用同域 `/api`，由 Nginx 转发到 `backend-api:8000`。`VITE_API_BASE_URL` 是 Vite 构建期变量，Compose 会通过 frontend build arg 传入；静态 Nginx 镜像启动后的 runtime env 不会改变已构建 JS。

## AI Provider

本地默认不调用外部模型：

```env
ALLOW_EXTERNAL_LLM=false
LLM_PROVIDER=disabled
```

如需接入企业内部模型，推荐：

```env
LLM_PROVIDER=local_openai_compatible
LLM_BASE_URL=http://<internal-llm-host>/v1
LLM_MODEL=<model>
ALLOW_EXTERNAL_LLM=false
```

如确需外部 OpenAI-compatible 服务，必须在系统管理员确认后开启：

```env
ALLOW_EXTERNAL_LLM=true
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://<provider>/v1
LLM_API_KEY=<secret>
LLM_MODEL=<model>
```

AI Provider Key 会加密入库，日志和前端响应只允许出现脱敏值。

## 数据库迁移

启动后执行：

```powershell
docker compose exec backend-api alembic upgrade head
```

创建新迁移时使用：

```powershell
invoke migrate --msg="中文迁移说明"
```

迁移文件必须人工 review downgrade，不能只依赖 autogenerate。

## 健康检查

```powershell
docker compose ps
curl http://localhost:18000/api/system/health
```

所有服务应为 `running` 或 `healthy`，后端健康检查返回：

```json
{"status":"ok"}
```

## ARM64 生产

DGX Spark ARM64 部署前必须执行：

```powershell
invoke check-arm64
docker compose -f docker-compose.yml -f docker-compose.arm64.yml build
```

约束：

- Dockerfile base image 必须是官方多架构镜像。
- 新增 Python 依赖前必须通过 ARM64 wheel 检查。
- 禁用 `psycopg2*`、`python-magic*`、`mysqlclient`、`pycrypto`、`m2crypto`。
- 路径处理使用 `pathlib.Path`，文件读写显式 `encoding="utf-8"`。

## 扩容

| 压力点 | 扩容服务 |
|---|---|
| API 请求多 | `backend-api` |
| 文档预处理慢 | `worker-document` |
| AI 分析慢 | `worker-ai` |
| RAGFlow 同步慢 | `worker-ragflow` |
| 统计慢 | `worker-statistics` |
| 邮件积压 | `worker-notification` |

扩容 Worker 不改变业务模块边界，模块通信仍通过 outbox、RabbitMQ 和 Celery task。
