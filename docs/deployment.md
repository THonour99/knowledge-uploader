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
| `redis` | 缓存、锁、限流 |
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
| Redis | `REDIS_PASSWORD`, `CACHE_REDIS_URL` |
| MinIO | `MINIO_ENDPOINT`, `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_BUCKET`, `MINIO_SECURE`, `MINIO_CA_CERT_FILE` |
| 上传 | `UPLOAD_MAX_FILE_SIZE_BYTES`, `UPLOAD_RATE_LIMIT_PER_MINUTE`, `UPLOAD_ALLOWED_EXTENSIONS`, `UPLOAD_ALLOWED_MIME_TYPES` |
| 认证 | `ALLOWED_EMAIL_DOMAINS`, `LOGIN_MAX_FAILED_ATTEMPTS`, `LOGIN_LOCK_MINUTES`, `AUTH_*_RATE_LIMIT_PER_HOUR` |
| RAGFlow | `RAGFLOW_BASE_URL`, `RAGFLOW_ALLOWED_BASE_URLS`, `RAGFLOW_TLS_SPKI_PINS`, `RAGFLOW_API_KEY`, `RAGFLOW_ALLOWED_DATASET_IDS` |
| AI | `AI_ANALYSIS_ENABLED`, `ALLOW_EXTERNAL_LLM`, `LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_ALLOWED_BASE_URLS`, `LLM_TLS_SPKI_PINS`, `LLM_API_KEY`, `LLM_MODEL` |
| SMTP | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_TLS`, `SMTP_CA_CERT_FILE`, `SMTP_TIMEOUT_SECONDS` |
| 指标 | `OUTBOX_METRICS_PORT`, `OPERATIONAL_METRICS_PORT`, `OPERATIONAL_METRICS_INTERVAL_SECONDS`, `PROMETHEUS_CONFIG_FILE`, `MINIO_TLS_DIR` |

`MINIO_SERVER_IMAGE` and `MINIO_MC_IMAGE` are pinned to repository-approved `tag@sha256:<64hex>` multi-arch manifests. Protected and E2E overrides must equal those approved digests; tag-only or alternate digests fail the resolved-Compose gate. Every CI backend build explicitly forwards `MINIO_MC_IMAGE`, and backend/ops obtain mc only from a `TARGETPLATFORM` stage.

`MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` 是控制面凭据，只能注入 MinIO、`minio-bootstrap` 与 `minio-metrics-token-init`；`MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` 是桶级数据面凭据，只能由幂等 `minio-bootstrap` 创建并授予指定桶的最小对象权限。两组凭据必须不同，业务服务不得获得 root，`operational-metrics` 只接收无数据权限哨兵值与 bearer 文件路径。

`APP_ENV=production`、`prod` 或 `staging` 时，启动校验会拒绝占位 `JWT_SECRET`、默认 `ENCRYPTION_KEY`、默认数据库 / RabbitMQ / Redis / MinIO 凭据、`MINIO_SECURE=false` 和空的 `MINIO_CA_CERT_FILE`。受保护环境必须配置 SMTP 且必须启用 TLS：必须同时具备 `SMTP_HOST` 与 `SMTP_FROM`（或 `SMTP_USER`）；用户名与密码必须成对出现，认证 relay 的 `SMTP_PASSWORD` 还必须是非占位密钥；仅配置 host + from 的匿名 TLS relay 不要求密码。端口范围为 `1..65535`，超时范围为 `(0, 300]` 秒。

如果 `APP_ENV` 漏配但 `APP_BASE_URL` 已经是非本机地址（例如 `https://knowledge.company.com` 或内网生产 IP），同样按受保护环境执行密钥校验，防止生产或 staging 误用 development 默认值。

`COST-002` 未定版期间，`staging`/`production` 的启动硬门禁要求
`ALLOW_EXTERNAL_LLM=false`，发现 `ALLOW_EXTERNAL_LLM=true` 必须在进程启动时拒绝，不能由系统
管理员确认、数据库开关或环境豁免放宽。已批准的内部非计费 Provider 同样使用
`ALLOW_EXTERNAL_LLM=false`；`development` 只允许为受控开发联调临时开启，不能作为 protected
环境配置或发布证据。

### TLS 信任链

- protected overlay 的 `MINIO_TLS_DIR` 必须恰好提供 `public.crt`、`private.key`、`ca.crt`；`public.crt` 的 SAN 必须包含 Compose 服务名 `DNS:minio`，健康检查固定通过 CA 访问 `https://minio:9000/minio/health/cluster`，不得使用 `-k` / `--insecure`。`private.key` 只挂入 MinIO。
- `MINIO_CA_CERT_FILE` 指向后端容器内只读挂载的 PEM CA bundle。安全 MinIO 客户端会保留 SDK 的 5 分钟连接/读取超时、连接池大小 10，以及对 `500/502/503/504` 的 5 次退避重试；CA 文件缺失或无效时在客户端构造或 readiness 阶段安全失败，响应不回显宿主路径。
- `SMTP_CA_CERT_FILE` 可指定企业 SMTP CA bundle。587 端口使用 STARTTLS，465 端口使用隐式 TLS；CA 文件缺失或无效会被归类为不可重试的配置错误，错误信息不回显路径。配置 CA 时禁止关闭 `SMTP_TLS`。
- 企业 RAGFlow/LLM HTTPS CA 应加入后端容器信任链（例如只读挂载并设置 `SSL_CERT_FILE`）。protected 环境还必须为完整 endpoint 配置对应的 SPKI SHA-256 pin；pin 校验不能替代 CA、证书主机名或 SNI 校验。禁止使用 `--insecure`、`CERT_NONE` 或关闭证书校验来通过联调。
- 更新 CA 或服务器证书后必须重启 `backend-api`、全部 worker、dispatcher、scheduler 和指标采集容器，确保所有长驻进程加载同一信任链。

隔离基础设施门禁会生成一次性 CA 和 MinIO、RAGFlow、SMTP、Gateway 四张服务器证书，并实际验证 HTTPS/STARTTLS、证书主机名、MinIO readiness、Prometheus 通过受信 CA 抓取 MinIO 的真实 target-up、上传审核同步链路，以及 RabbitMQ、Redis、MinIO、RAGFlow 的逐依赖故障恢复。Redis 场景必须在缓存仍停止时观察到带重试计数的持久化 Celery 消息；MinIO/RAGFlow 场景必须先持久化失败任务，再显式重试，并证明远端只创建一个文档。无论在开发机还是 DGX 上，本地 runner 的原始 `status` 与 `full_compose_e2e` 都固定为 `development_passed`；只有受保护的 DGX verifier 在同一实机复验 ARM64、干净工作树、镜像 ID、证据摘要与原始 OCI 来源后，才能生成独立的 `dgx-spark-evidence.json status=passed`，最终发布通过只由受保护聚合与授权流程产生。

### 指标采集参数

| 变量 | 默认值 | 有效范围 | 约束 |
|---|---:|---:|---|
| `OUTBOX_METRICS_PORT` | `9101` | `1..65535` | outbox-dispatcher 容器私网监听端口 |
| `OPERATIONAL_METRICS_PORT` | `9102` | `1..65535` | operational-metrics 容器私网监听端口 |
| `OPERATIONAL_METRICS_INTERVAL_SECONDS` | `30` | `5..3600` | 数据库、对象存储和邮件投递聚合采样周期 |
| `MINIO_METRICS_BEARER_TOKEN_FILE` | `/run/secrets/minio-metrics/token` | 容器内绝对路径 | 仅 `operational-metrics` 读取；由命名卷只读挂载 |

`docker-compose.observability.yml` 的 Prometheus target 默认固定为
`outbox-dispatcher:9101` 和 `operational-metrics:9102`。修改任一监听端口时必须在同一次
发布中同步修改 target 并运行 Prometheus 规则测试；否则采集目标会 Down。两个端口只能在
Compose 私网内访问，不得发布到公网或绕过 Nginx 暴露。

开发环境继续使用 `ops/observability/prometheus.yml` 的 HTTP 私网 target，staging/production 必须额外叠加 `docker-compose.observability.protected.yml`，显式把 `PROMETHEUS_CONFIG_FILE` 指向 `ops/observability/prometheus.protected.yml`，并通过 `MINIO_TLS_DIR` 把公开 CA bundle `ca.crt` 只读挂载到 Prometheus；同一目录还向 MinIO 提供 `public.crt` 和仅服务端可见的 `private.key`。两种配置都必须使用 `authorization.credentials_file=/run/secrets/minio-metrics/token`；MinIO 固定为 JWT，禁止公开 exporter。受保护配置固定使用 `https://minio:9000`、`server_name: minio` 且禁止跳过证书校验。一次性 init 必须成功、日志为空、token 文件为 `0440/65534:65534`，匿名访问返回 401/403，应用 collector 与 Prometheus 均使用同一只读卷。仅有 promtool 语法通过不构成上线证据，基础设施 E2E 还必须证明 collector last-success、每次由服务端重新签发 token 后两个消费者不重启便完成动态切换，以及 Prometheus target 为 `up`。常规刷新不会吊销旧 JWT，旧 JWT 在 `exp` 前仍可能有效；紧急吊销必须轮换 MinIO root 凭据并重启 MinIO，再重新签发 token；两个消费者必须保持原容器 ID 并原地自动恢复，否则阻塞发布。

## RAGFlow

仅限 `development` 本地联调的 HTTP 目标（不能作为 protected 发布证据）：

```env
RAGFLOW_BASE_URL=http://ragflow:9380
RAGFLOW_ALLOWED_BASE_URLS=
RAGFLOW_TLS_SPKI_PINS=
RAGFLOW_API_KEY=<只放在后端环境>
RAGFLOW_ALLOWED_DATASET_IDS=<新建测试 Dataset id>
```

Protected RAGFlow 配置示例：

```env
RAGFLOW_BASE_URL=https://ragflow.corp.example/api
RAGFLOW_ALLOWED_BASE_URLS=https://ragflow.corp.example/api
RAGFLOW_TLS_SPKI_PINS={"https://ragflow.corp.example/api":["sha256/<base64-spki-sha256>"]}
RAGFLOW_API_KEY=<只放在后端 secret manager>
RAGFLOW_ALLOWED_DATASET_IDS=<隔离的目标 Dataset id>
```

示例中的 pin 是占位符，必须替换为该 endpoint 证书公钥 SPKI 的真实 32 字节 SHA-256
base64 摘要。protected 环境只接受 HTTPS，并要求 `RAGFLOW_BASE_URL`、环境 allowlist 和
`RAGFLOW_TLS_SPKI_PINS` 的 JSON key 精确指向同一 scheme/hostname/port/path；缺 pin、错 pin
或只配置 CA 均 fail closed。同一 pin 禁止跨 hostname 复用。

规则：

- 不在前端保存或展示 RAGFlow API Key。
- Dataset 白名单优先在系统设置的 RAGFlow 配置中维护并审计；为空时禁止同步。
- `RAGFLOW_ALLOWED_DATASET_IDS` 仅用于首次部署或数据库运行时配置尚未保存时的回退。
- `/datasets` 只能创建 allowlist 内的新测试或明确目标 Dataset 映射。
- 不删除、不覆盖、不迁移 RAGFlow 服务器上的既有知识库。

`DEFAULT_DATASET_ID` 已删除。同步目标必须由审核决定并明确选择 `/datasets` 中启用且在
运行时 `ragflow.allowed_dataset_ids` 白名单内的 Dataset 映射；系统不会回退到默认 Dataset。

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

仅限 `development` 本地联调的 HTTP Provider：

```env
LLM_PROVIDER=local_openai_compatible
LLM_BASE_URL=http://llm:8000/v1
LLM_MODEL=<model>
ALLOW_EXTERNAL_LLM=false
LLM_ALLOWED_BASE_URLS=http://llm:8000/v1
LLM_TLS_SPKI_PINS=
```

Protected 内部非计费 LLM 配置示例：

```env
LLM_PROVIDER=local_openai_compatible
LLM_BASE_URL=https://llm.corp.example/v1
LLM_ALLOWED_BASE_URLS=https://llm.corp.example/v1
LLM_TLS_SPKI_PINS={"https://llm.corp.example/v1":["sha256/<base64-spki-sha256>"]}
LLM_MODEL=<已批准的精确模型 id>
ALLOW_EXTERNAL_LLM=false
```

示例中的 pin 是占位符，必须替换为目标 endpoint 的真实 SPKI SHA-256 base64 摘要。
protected 环境缺少精确 endpoint pin、使用 HTTP 或 pin 不匹配时必须 fail closed；同一 pin
禁止跨 hostname 复用。数据库 Provider 只能从环境 allowlist 与 pin 映射既有交集中选择，
不能通过保存新的 base URL 扩大网络信任边界。

`COST-002` 未定版期间不存在“管理员确认即可开启”的例外。`development` 可在受控开发联调中
临时使用 `ALLOW_EXTERNAL_LLM=true`，但该结果不能作为 staging、production 或发布验收证据；
`staging`/`production` 配置 `ALLOW_EXTERNAL_LLM=true` 必须启动失败。外部计费 Provider 只有在
`COST-002` 定版、运行时门禁与验收证据同步更新后才能开放，不能通过手工配置绕过。

`LLM_BASE_URL` 必须在逗号分隔的 `LLM_ALLOWED_BASE_URLS` 中精确匹配。系统会统一主机大小写、IDNA、默认端口和末尾斜杠，但不会放宽路径前缀；例如 `/v1` 不会授权 `/v1/admin`。外部地址必须同时开启环境硬门禁和数据库开关并使用 HTTPS；内部地址仅允许标记为内部 Provider 的 RFC1918/ULA 地址。每次调用都会重新解析全部 DNS 结果，拒绝回环、链路本地、组播、保留、云元数据及私网/公网混合答案，并把实际连接固定到已验证 IP，同时保留原主机名用于 TLS SNI。Provider 单次超时范围为 1–240 秒。
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

候选镜像采用 build-once OCI artifact：只允许主 CI 在默认分支测试成功后构建；DGX 按 exact
main artifact ID/digest 消费，protected gate 也按同一 ID 下载并完整复验两个 OCI archives，
再把这些原始字节连同证据和短期 authorization 封装进 final validated artifact。部署端的
`release_oci.py verify-deployment` 必须在线核验 exact protected run/attempt 与 final artifact
ID/digest，按 exact ID 下载，令原始 ZIP SHA-256 同时匹配 GitHub metadata 和独立部署锚点，
安全解压后再复验 OCI index、平台 manifest/config、SBOM、provenance、base image 和 lockfile
checksum。DGX/部署端禁止 `docker build`，相同 Git SHA、Docker image ID、本地 sidecar 或 tag
都不能替代服务端 artifact digest。仓库仍无获授权的生产部署 workflow 或真实 registry/部署
证据，因此发布、DGX 与部署状态继续按验收矩阵保留待执行。

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
