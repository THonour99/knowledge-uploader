# 10. Codex 实现 Prompt

请在当前仓库中实现“企业知识库文件贡献与 RAGFlow 同步平台”。

## 当前任务执行原则

你需要按小步提交的方式实现。每一步要保证代码可运行，不要一次性改动过大。

优先遵循这些文档：

1. `01_PRD_产品需求文档.md`
2. `02_ARCHITECTURE_最终架构设计.md`
3. `03_BACKEND_SPEC_后端开发规范.md`
4. `04_FRONTEND_SPEC_前端开发规范.md`
5. `05_DATABASE_API_SPEC_数据库与API规范.md`
6. `06_AI_RAGFLOW_SPEC_AI与RAGFlow集成规范.md`
7. `07_DEPLOYMENT_ENV_部署与环境配置.md`
8. `08_TASK_BREAKDOWN_开发任务拆解.md`

本项目只负责向 RAGFlow 同步知识内容，不实现钉钉文档拉取、钉钉机器人、Dify 或 LangBot 集成。

## 技术栈固定

```text
Frontend: React + TypeScript + Ant Design
Backend: FastAPI + SQLAlchemy + Alembic
DB: PostgreSQL
Queue: RabbitMQ + Celery
Cache/Result: Redis
Storage: MinIO
Deploy: Docker Compose
Auth: JWT + RBAC
AI: OpenAI-compatible Provider
RAGFlow: RAGFlow Client Adapter
```

## 禁止事项

- 不要用 SQLite 替代 PostgreSQL。
- 不要把文件长期保存到后端本地目录。
- 不要用 BackgroundTasks 代替 Celery。
- 不要让前端直接请求 RAGFlow。
- 不要让前端直接请求 AI 模型。
- 不要把 API Key 写死到代码。
- 不要在日志里打印 API Key。
- 不要让 AI 分析成为上传和同步的强依赖。

## 首批实现任务

请优先实现以下内容：

以下首批任务是工程启动切片，不等于完整 MVP。PRD 第一阶段 MVP 的验收仍以 `01_PRD_产品需求文档.md §10.1` 为准，必须包含审核、RAGFlow 基础配置、手动同步、同步状态、基础操作日志和基础上传统计。

### 任务 1：基础项目结构

- backend FastAPI 项目
- frontend React 项目
- docker-compose.yml
- postgres
- rabbitmq
- redis
- minio
- nginx，可先预留
- .env.example

### 任务 2：认证模块

- users 表
- 注册接口
- 公司邮箱域名限制
- 登录接口
- JWT
- `/api/auth/me`
- RBAC 基础中间件

### 任务 3：文件上传模块

- MinIO Client
- `/api/files/upload`
- 文件类型校验
- 文件大小校验
- SHA256 hash
- files 表
- 我的文件列表接口

### 任务 4：审核模块

- categories 表
- dataset_mappings 表
- 文件审核通过
- 文件审核拒绝
- 修改分类
- 修改目标 Dataset

### 任务 5：任务队列

- Celery app
- RabbitMQ broker
- Redis result backend
- sync_tasks 表
- ragflow_upload task 占位
- 任务状态查询接口

## 文件状态规则

文件状态枚举和合法流转以 `05_DATABASE_API_SPEC_数据库与API规范.md §2` 为唯一来源，不要在实现中手写另一套状态机。

AI 关闭时不能创建 AI 任务，不能进入 `extracting_text`、`analysis_queued`、`analyzing`、`analysis_failed`、`analyzed` 等 AI 状态。

所有状态变更必须通过 service 层调用 `DocumentStateMachine.transition(from, to)`。

## 代码质量要求

- Service 层不要写在 API route 里。
- 外部系统必须封装 Adapter。
- 数据库操作要有事务。
- 上传和同步任务要幂等。
- 错误返回统一格式。
- 权限检查必须在后端完成。
- 管理员操作必须写 audit_logs。
- 关键逻辑要有测试。

## 每次完成后输出

请输出：

- 完成了哪些文件
- 如何启动
- 如何测试
- 有哪些待完成项
- 是否存在需要人工配置的环境变量
