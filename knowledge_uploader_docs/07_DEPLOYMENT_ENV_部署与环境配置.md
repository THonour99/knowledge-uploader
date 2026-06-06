# 07. 部署与环境配置

## 1. 部署方式

使用 Docker Compose，从第一天就按生产型架构部署。

不使用：

- SQLite
- 本地文件系统作为正式存储
- FastAPI BackgroundTasks 承担核心任务

---

## 2. 服务列表

```yaml
services:
  nginx:
  frontend:
  backend-api:

  worker-document:
  worker-ai:
  worker-ragflow:
  worker-statistics:
  worker-notification:
  scheduler:

  postgres:
  rabbitmq:
  redis:
  minio:
```

---

## 3. 服务职责

| 服务 | 职责 |
|---|---|
| nginx | 反向代理、静态资源、上传大小限制 |
| frontend | React 前端 |
| backend-api | FastAPI API 服务 |
| worker-document | 文本抽取、文件预处理 |
| worker-ai | AI 摘要、分类、标签、敏感检测 |
| worker-ragflow | RAGFlow 上传、解析、状态轮询 |
| worker-statistics | 统计快照、报表聚合 |
| worker-notification | 邮件通知 |
| scheduler | 定时任务 |
| postgres | 业务数据库 |
| rabbitmq | Celery Broker |
| redis | 缓存、锁、限流、结果后端 |
| minio | 文件对象存储 |

---

## 4. 核心环境变量

### 4.1 应用

```env
APP_NAME=knowledge-uploader
APP_ENV=production
APP_BASE_URL=https://knowledge.company.com
JWT_SECRET=
ENCRYPTION_KEY=
```

### 4.2 数据库

```env
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=knowledge_uploader
POSTGRES_USER=knowledge
POSTGRES_PASSWORD=
DATABASE_URL=postgresql+psycopg://knowledge:password@postgres:5432/knowledge_uploader
```

### 4.3 RabbitMQ

```env
RABBITMQ_HOST=rabbitmq
RABBITMQ_PORT=5672
RABBITMQ_USER=knowledge
RABBITMQ_PASSWORD=
CELERY_BROKER_URL=amqp://knowledge:password@rabbitmq:5672//
```

### 4.4 Redis

```env
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_PASSWORD=
CELERY_RESULT_BACKEND=redis://redis:6379/0
CACHE_REDIS_URL=redis://redis:6379/1
```

### 4.5 MinIO

```env
MINIO_ENDPOINT=minio:9000
MINIO_ACCESS_KEY=
MINIO_SECRET_KEY=
MINIO_BUCKET=knowledge-files
MINIO_SECURE=false
```

### 4.6 RAGFlow

```env
RAGFLOW_BASE_URL=
RAGFLOW_API_KEY=
RAGFLOW_ALLOWED_DATASET_IDS=
DEFAULT_DATASET_ID=
RAGFLOW_REQUEST_TIMEOUT=300
RAGFLOW_MAX_RETRY_COUNT=3
```

`RAGFLOW_API_KEY` 非空时必须配置 `RAGFLOW_ALLOWED_DATASET_IDS`，只填写允许同步的新测试或目标 Dataset id。

### 4.7 Auth

```env
AUTH_PROVIDER=local
ALLOW_REGISTER=true
REQUIRE_EMAIL_VERIFICATION=true
ALLOWED_EMAIL_DOMAINS=company.com,corp.company.com
PASSWORD_MIN_LENGTH=8
LOGIN_MAX_FAILED_ATTEMPTS=5
LOGIN_LOCK_MINUTES=15
EMAIL_VERIFICATION_EXPIRE_HOURS=24
PASSWORD_RESET_EXPIRE_MINUTES=30
JWT_EXPIRE_MINUTES=1440
```

### 4.8 AI

```env
AI_ANALYSIS_ENABLED=true
ALLOW_EXTERNAL_LLM=false
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=
LLM_API_KEY=
LLM_MODEL=
EMBEDDING_PROVIDER=openai_compatible
EMBEDDING_BASE_URL=
EMBEDDING_API_KEY=
EMBEDDING_MODEL=
AI_REQUEST_TIMEOUT=120
AI_MAX_RETRY_COUNT=3
```

### 4.9 SMTP

```env
SMTP_HOST=
SMTP_PORT=
SMTP_USER=
SMTP_PASSWORD=
SMTP_FROM=
SMTP_TLS=true
```

---

## 5. Worker 队列划分

```text
document_queue
ai_queue
ragflow_queue
statistics_queue
notification_queue
```

不同 Worker 只监听自己的队列。

---

## 6. 扩容方式

AI 慢：扩容 `worker-ai`。  
RAGFlow 同步慢：扩容 `worker-ragflow`。  
统计慢：扩容 `worker-statistics`。  
API 压力大：扩容 `backend-api`。

不需要改变架构。
