---
description: 基础设施（Docker / Nginx / 部署）规则
paths:
  - docker-compose*.yml
  - docker-compose*.yaml
  - deploy/**
  - nginx/**
  - "**/Dockerfile"
  - "**/Dockerfile.*"
---

# 基础设施规则

## 1. 多架构（amd64 + arm64）

- 所有 Dockerfile 必须使用 `--platform=$BUILDPLATFORM` 模式
- Base image 必须官方维护多架构（详见补充 spec §6.4）
- 本机开发用 amd64 native；CI 用 buildx + QEMU 构建 arm64
- 禁用 `latest` tag，所有镜像锁具体版本

## 2. 服务清单（13 个）

```text
nginx                ← 反向代理 + 静态资源
frontend             ← React 静态托管（构建产物）
backend-api          ← FastAPI 主服务
worker-document      ← Celery worker, queue=document
worker-ai            ← Celery worker, queue=ai
worker-ragflow       ← Celery worker, queue=ragflow
worker-notification  ← Celery worker, queue=notification
scheduler            ← Celery beat
outbox-dispatcher    ← Outbox → RabbitMQ 投递器
postgres             ← 主数据库
rabbitmq             ← Celery broker + 事件总线
redis                ← Celery result + 缓存 + 锁 + 限流
minio                ← 对象存储
```

- 应用服务（前 9 个）必须共用同一个 backend 镜像，只是启动命令不同
- 基础设施服务（后 4 个）用官方镜像
- 统计走 API 实时查询，无独立 worker（原 worker-statistics 已移除）

## 3. 环境变量管理

- 所有可配置项必须列在 `.env.example`
- 本机覆盖用 `.env`（已 `.gitignore`）
- 生产用 K8s secret 或服务器环境变量
- ❌ 禁止把 secret 硬编码到 `docker-compose.yml`
- ❌ 禁止 `.env` 进版本控制

## 4. 健康检查（强制）

每个服务必须配 `healthcheck`：

```yaml
backend-api:
  healthcheck:
    test: ["CMD", "curl", "-fsS", "http://localhost:8000/api/system/health"]
    interval: 30s
    timeout: 5s
    retries: 3
    start_period: 30s
```

依赖关系用 `depends_on` + `condition: service_healthy`。

## 5. 卷映射

- 本机开发：`./backend:/app` 实现热重载（仅 dev）
- 生产：不映射代码，镜像内固化
- 数据卷（postgres-data / minio-data / rabbitmq-data）必须命名（非匿名）
- 本机用 docker volume，DGX 用 host 挂载点

## 6. 端口约定

| 服务 | 容器内端口 | 本机映射 | 说明 |
|---|---|---|---|
| nginx | 80 | 8080 | 入口 |
| backend-api | 8000 | 8001 | 直连调试用 |
| postgres | 5432 | 5433 | psql 直连 |
| redis | 6379 | 6380 | redis-cli |
| rabbitmq | 5672 / 15672 | 5673 / 15673 | 管理 UI 在 15673 |
| minio | 9000 / 9001 | 9000 / 9001 | console 在 9001 |

生产环境只暴露 nginx，其他全部内网。

## 7. Nginx 配置

- 最大上传：`client_max_body_size 100M`（与 `MAX_UPLOAD_SIZE_MB` 一致）
- 超时：`proxy_read_timeout 600s`（兼容 RAGFlow 长操作）
- gzip 启用，静态资源 cache-control 长期
- API 路径前缀：`/api/` → 反代到 `backend-api:8000`
- 其他路径：返回前端 SPA index.html

## 8. Docker Compose 双套

- `docker-compose.yml`：本机默认（amd64，挂载源码）
- `docker-compose.arm64.yml`：DGX 参考（arm64，无源码挂载）
- 本机：`docker compose up`
- DGX：`docker compose -f docker-compose.yml -f docker-compose.arm64.yml up`

## 9. Buildx 命令

```powershell
# 本机仅 amd64
docker buildx build --platform linux/amd64 -t knowledge-backend:dev -f backend/Dockerfile backend/ --load

# CI multi-arch
docker buildx build --platform linux/amd64,linux/arm64 -t registry/knowledge-backend:$VERSION -f backend/Dockerfile backend/ --push
```

## 10. CI 必跑项

- 后端 lint + test + ARM64 wheel 检查
- 前端 lint + test + type check
- 镜像 buildx amd64（PR）
- 镜像 buildx amd64 + arm64 push（main 分支）
