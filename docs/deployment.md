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
staging/production 的 `BACKEND_API_HOST` 必须保持 loopback；Nginx 对 `/metrics` 返回 404
只能保护统一入口，不能替代宿主端口绑定和防火墙。protected release gate 会拒绝
`0.0.0.0` 或任意非 loopback 的后端直连地址。

主 compose 默认构建 `runtime` target，不包含 pytest、mypy、ruff 等开发工具。仅本地需要在
容器内执行开发门禁时才显式设置 `BACKEND_BUILD_TARGET=development`；staging/production
禁止覆盖该值，protected release 必须使用解析后 target 为 `runtime` 的 compose 配置。

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
| RAGFlow | `RAGFLOW_BASE_URL`, `RAGFLOW_ALLOWED_BASE_URLS`, `RAGFLOW_API_KEY`, `RAGFLOW_ALLOWED_DATASET_IDS` |
| AI | `AI_ANALYSIS_ENABLED`, `ALLOW_EXTERNAL_LLM`, `LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` |
| SMTP | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_TLS` |
| 指标 | `OUTBOX_METRICS_PORT`, `OPERATIONAL_METRICS_PORT`, `OPERATIONAL_METRICS_INTERVAL_SECONDS` |

`APP_ENV=production`、`prod` 或 `staging` 时，启动校验会拒绝占位 `JWT_SECRET`、默认 `ENCRYPTION_KEY`、默认数据库 / RabbitMQ / Redis / MinIO 凭据和 `MINIO_SECURE=false`。

如果 `APP_ENV` 漏配但 `APP_BASE_URL` 已经是非本机地址（例如 `https://knowledge.company.com` 或内网生产 IP），同样按受保护环境执行密钥校验，防止生产或 staging 误用 development 默认值。

### 指标采集参数

| 变量 | 默认值 | 有效范围 | 约束 |
|---|---:|---:|---|
| `OUTBOX_METRICS_PORT` | `9101` | `1..65535` | outbox-dispatcher 容器私网监听端口 |
| `OPERATIONAL_METRICS_PORT` | `9102` | `1..65535` | operational-metrics 容器私网监听端口 |
| `OPERATIONAL_METRICS_INTERVAL_SECONDS` | `30` | `5..3600` | 数据库、对象存储和邮件投递聚合采样周期 |

`docker-compose.observability.yml` 的 Prometheus target 默认固定为
`outbox-dispatcher:9101` 和 `operational-metrics:9102`。修改任一监听端口时必须在同一次
发布中同步修改 target 并运行 Prometheus 规则测试；否则采集目标会 Down。两个端口只能在
Compose 私网内访问，不得发布到公网或绕过 Nginx 暴露。

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

`DEFAULT_DATASET_ID` 已删除。同步目标必须由审核决定并明确选择 `/datasets` 中启用且在
`RAGFLOW_ALLOWED_DATASET_IDS` allowlist 内的 Dataset 映射；系统不会回退到默认 Dataset。

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

## Proxy Headers

`backend-api` 通过 Uvicorn `--proxy-headers` 读取可信反向代理传入的 `X-Forwarded-For` 和 `X-Forwarded-Proto`，用于生成正确的客户端 IP 与 HTTPS scheme。

Compose 默认：

```env
UVICORN_FORWARDED_ALLOW_IPS=127.0.0.1
```

默认值选择安全失效模式：后端不会信任任意来源的 `X-Forwarded-For`。Compose 入口 Nginx 会用 `$remote_addr` 覆盖客户端传入的 `X-Forwarded-For`，避免登录限流和审计 IP 被客户端伪造。共享环境和生产环境如能固定反代来源 IP，应把 `UVICORN_FORWARDED_ALLOW_IPS` 配置为精确 IP 列表，例如：

```env
UVICORN_FORWARDED_ALLOW_IPS=127.0.0.1,172.18.0.5
```

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
curl http://localhost:18000/api/system/ready
```

`/api/system/health` 是轻量存活检查，成功返回：

```json
{"status":"ok"}
```

`/api/system/ready` 是深度就绪检查，覆盖 PostgreSQL、Redis、RabbitMQ 和 MinIO。全部依赖可用时返回 200：

```json
{
  "status": "ok",
  "dependencies": {
    "database": {"status": "ok"},
    "redis": {"status": "ok"},
    "rabbitmq": {"status": "ok"},
    "minio": {"status": "ok"}
  }
}
```

任一依赖不可用时返回 503，`detail` 只暴露异常类型，不回显连接串或密钥。`backend-api` 容器健康检查使用 `/api/system/ready`。

## ARM64 生产

DGX Spark ARM64 部署前必须执行：

```powershell
invoke check-arm64
docker compose -f docker-compose.yml -f docker-compose.arm64.yml build
```

约束：

- Dockerfile base image 必须是官方多架构镜像。
- 后端 Dockerfile runtime stage 使用 `TARGETPLATFORM`，CI 使用 buildx 验证 `linux/arm64` 后端镜像可构建。
- 新增 Python 依赖前必须通过 ARM64 wheel 检查。
- 禁用 `psycopg2*`、`python-magic*`、`mysqlclient`、`pycrypto`、`m2crypto`。
- 路径处理使用 `pathlib.Path`，文件读写显式 `encoding="utf-8"`。

原生构建通过不等于实机上线通过。候选版本必须依次执行 DGX 实机、独立外部运维证据收集和
保护发布 workflow；镜像隔离、证据文件归属、GitHub Environment 审批及本地
`invoke ship` 用法见
[保护发布证据门禁](../ops/runbooks/protected-release.md)。没有最终 validated artifact
时，不得把阶段 9 或 ARM64/灾备验收项改为完成。

候选镜像采用 build-once OCI artifact：只允许主 CI 在默认分支测试成功后构建；DGX 与部署
下载同一 artifact id/digest，并复验 OCI index、平台 manifest/config、SBOM、provenance、
base image 和 lockfile checksum。DGX/部署端禁止 `docker build`，相同 Git SHA、Docker image
ID 或本地 tag 不能替代制品 digest。protected gate 生成的 30 分钟 deployment authorization
只是一份 fail-closed 交接契约；仓库尚无获授权的生产部署 workflow 或真实 registry 证据，
因此发布、DGX 与部署状态仍按验收矩阵保留待执行。

CI 的 Python、Node、Nginx base 与 Prometheus/Alertmanager 校验器均使用官方多架构
manifest-list digest；tag 只保留作可读版本说明。外部证据同时携带并由最终 gate 比对校验器
完整 digest、实际 image ID、Linux OS 和 runner Docker 架构，任何 tag 回退、平台子 digest、
架构错配或校验前后镜像身份变化都会阻断发布。

## 扩容

| 压力点 | 扩容服务 |
|---|---|
| API 请求多 | `backend-api` |
| 文档预处理慢 | `worker-document` |
| AI 分析慢 | `worker-ai` |
| RAGFlow 同步慢 | `worker-ragflow` |
| 邮件积压 | `worker-notification` |

扩容 Worker 不改变业务模块边界，模块通信仍通过 outbox、RabbitMQ 和 Celery task。
