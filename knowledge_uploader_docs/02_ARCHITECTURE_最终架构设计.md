# 02. 最终架构设计

## 1. 架构定版

本项目采用：

> 生产型模块化服务架构：前后端分离 + 模块化单体代码 + 多 Worker 容器部署 + 事件驱动异步任务 + 对象存储 + 插件式 AI Provider + RAGFlow Adapter。

最终技术栈：

| 层级 | 技术 |
|---|---|
| 前端 | React + TypeScript + Ant Design |
| 网关 | Nginx |
| 后端 API | FastAPI |
| ORM / 迁移 | SQLAlchemy + Alembic |
| 数据库 | PostgreSQL |
| 任务 Broker | RabbitMQ |
| 缓存 / 锁 / Result Backend | Redis |
| Worker | Celery |
| 文件存储 | MinIO |
| AI 接入 | OpenAI-compatible Provider |
| RAGFlow 接入 | RAGFlow Client Adapter |
| 权限 | JWT + RBAC |
| 部署 | Docker Compose |
| 图表 | ECharts |

---

## 2. 总体架构图

```text
┌──────────────────────────┐
│        员工 / 管理员       │
└────────────┬─────────────┘
             ↓
┌──────────────────────────┐
│      Nginx / API Gateway │
└────────────┬─────────────┘
             ↓
┌──────────────────────────┐
│      React + TypeScript  │
│ 上传 / 审核 / 配置 / 统计   │
└────────────┬─────────────┘
             ↓
┌──────────────────────────┐
│        Backend API        │
│ 认证 / 文件 / 审核 / 配置    │
└───────┬───────────┬──────┘
        ↓           ↓
┌─────────────┐   ┌────────────────┐
│ PostgreSQL  │   │ RabbitMQ        │
│ 主业务数据    │   │ Celery Broker   │
└─────────────┘   └───────┬────────┘
                          ↓
        ┌─────────────────────────────────┐
        │           Worker 集群            │
        ├─────────────────────────────────┤
        │ worker-document                 │
        │ worker-ai                       │
        │ worker-ragflow                  │
        │ worker-statistics               │
        │ worker-notification             │
        └───────┬───────────┬─────────────┘
                ↓           ↓
        ┌────────────┐   ┌───────────────┐
        │ MinIO      │   │ AI Provider    │
        │ 文件存储     │   │ OpenAI格式/本地 │
        └─────┬──────┘   └───────────────┘
              ↓
        ┌──────────────┐
        │ RAGFlow API  │
        └──────┬───────┘
               ↓
        ┌──────────────┐
        │ RAGFlow Dataset │
        └──────┬───────┘
               ↓
        ┌────────────────────┐
        │ Dify / LangBot / 钉钉 │
        └────────────────────┘
```

---

## 3. Redis + RabbitMQ 分工

使用 Redis + RabbitMQ，而不是 Redis only。

| 组件 | 职责 |
|---|---|
| RabbitMQ | Celery Broker，承载可靠任务队列、任务确认、重试、死信队列 |
| Redis | Celery Result Backend、缓存、分布式锁、限流、临时状态 |
| PostgreSQL | 业务最终状态、任务最终状态、审计日志 |

推荐：

```text
Celery Broker        = RabbitMQ
Celery ResultBackend = Redis
业务数据库             = PostgreSQL
缓存 / 锁 / 限流        = Redis
```

---

## 4. 模块化单体 + 多容器部署

代码层面：模块化单体。  
部署层面：多个容器角色。  
不从第一天拆成多个微服务和多个数据库。

```text
一个后端代码仓库
一个 PostgreSQL
一套权限体系
一套配置体系
多个 Worker 容器
多个任务队列
```

部署角色：

```text
backend-api
worker-document
worker-ai
worker-ragflow
worker-statistics
worker-notification
scheduler
```

好处：

- 不需要后续架构迁移
- 可以按 Worker 类型独立扩容
- 避免微服务拆分带来的复杂度
- 保持统一数据模型和统一权限

---

## 5. 不可改变的核心边界

后续扩展功能时，不改变以下边界：

```text
前端不直接访问 RAGFlow
前端不直接访问 AI 模型
文件统一进入 MinIO
业务数据统一进入 PostgreSQL
长任务统一进入 RabbitMQ / Celery
Redis 只做缓存、锁、限流和结果后端
AI 能力统一走 AI Provider
RAGFlow 操作统一走 RAGFlow Client
权限统一走 RBAC
配置统一走 ConfigService
审计统一走 AuditService
统计统一走 StatisticsService
```

---

## 6. 后端模块

```text
modules/
  auth/
  user/
  document/
  review/
  ragflow/
  ai/
  statistics/
  notification/
  config/
  audit/
```

每个模块内部包含：

```text
api.py
schemas.py
models.py
service.py
repository.py，可选
tasks.py，可选
```

---

## 7. 外部系统 Adapter

所有外部系统必须封装成 Adapter：

```text
RagflowClient
OpenAICompatibleClient
MinioClient
EmailClient
DingTalkClient，后续
```

业务代码不能散落外部 HTTP 请求。

---

## 8. 参考资料

- RAGFlow 提供 HTTP API，用于 Dataset、Documents、Chunks 等操作。
- Celery 支持使用 RabbitMQ、Redis 等作为 Broker / Backend。
- MinIO 提供 S3 兼容对象存储和 Python SDK。
