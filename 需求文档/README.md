# Knowledge Uploader 开发文档索引

项目名称：企业知识库文件贡献与 RAGFlow 同步平台  
项目代号：Knowledge Uploader  
推荐架构：生产型模块化服务架构  
适用对象：Claude Code、Codex、Cursor、Multica 智能体、后端开发、前端开发、架构评审

---

## 1. 项目一句话说明

本项目用于让公司员工通过 Web 页面上传文档，系统完成校验、去重、可选 AI 分析、管理员审核、RAGFlow 同步和统计审计，最终持续丰富钉钉客服机器人的知识库。

```text
员工上传文件
  ↓
系统校验与去重
  ↓
可选 AI 摘要 / 分类 / 标签 / 敏感检测
  ↓
管理员审核
  ↓
同步 RAGFlow
  ↓
Dify / LangBot / 钉钉机器人获得更完整知识
```

本项目只负责向 RAGFlow 同步知识内容，不实现钉钉文档拉取、钉钉机器人、Dify 或 LangBot 集成。

---

## 2. 文档列表

| 文档 | 用途 |
|---|---|
| `01_PRD_产品需求文档.md` | 给产品、项目负责人、开发整体理解需求 |
| `02_ARCHITECTURE_最终架构设计.md` | 固定最终技术架构，不走后续大迁移路线 |
| `03_BACKEND_SPEC_后端开发规范.md` | 后端模块、任务、权限、服务边界规范 |
| `04_FRONTEND_SPEC_前端开发规范.md` | 前端页面、路由、交互、组件规范 |
| `05_DATABASE_API_SPEC_数据库与API规范.md` | 数据表、核心 API、状态流转设计 |
| `06_AI_RAGFLOW_SPEC_AI与RAGFlow集成规范.md` | AI 可配置能力、RAGFlow 同步、Provider 架构 |
| `07_DEPLOYMENT_ENV_部署与环境配置.md` | Docker Compose、环境变量、服务划分 |
| `08_TASK_BREAKDOWN_开发任务拆解.md` | 给开发使用的分阶段任务清单 |
| `09_CLAUDE_CODE_PROMPT.md` | 可直接给 Claude Code 使用的总 Prompt |
| `10_CODEX_IMPLEMENTATION_PROMPT.md` | 可直接给 Codex 使用的实现 Prompt |

---

## 3. 推荐使用方式

### 给 Claude Code

优先提供：

1. `01_PRD_产品需求文档.md`
2. `02_ARCHITECTURE_最终架构设计.md`
3. `08_TASK_BREAKDOWN_开发任务拆解.md`
4. `09_CLAUDE_CODE_PROMPT.md`

Claude Code 更适合先做架构设计、项目结构、模块拆分、代码审查和复杂任务规划。

### 给 Codex

优先提供：

1. `01_PRD_产品需求文档.md`
2. `02_ARCHITECTURE_最终架构设计.md`
3. `03_BACKEND_SPEC_后端开发规范.md`
4. `04_FRONTEND_SPEC_前端开发规范.md`
5. `05_DATABASE_API_SPEC_数据库与API规范.md`
6. `06_AI_RAGFLOW_SPEC_AI与RAGFlow集成规范.md`
7. `07_DEPLOYMENT_ENV_部署与环境配置.md`
8. `08_TASK_BREAKDOWN_开发任务拆解.md`
9. `10_CODEX_IMPLEMENTATION_PROMPT.md`

Codex 更适合按明确任务实现代码、补接口、补页面、写测试和修复问题。

当文档出现冲突时，优先级为：PRD 产品范围 → 架构红线 → 数据库/API 规范 → 后端/前端实现规范 → 任务拆解与提示词。

---

## 4. 架构定版

最终架构固定为：

```text
React + TypeScript + Ant Design
FastAPI + SQLAlchemy + Alembic
PostgreSQL
RabbitMQ + Celery
Redis
MinIO
OpenAI-compatible AI Provider
RAGFlow Adapter
JWT + RBAC
Docker Compose
```

后续扩展功能时，不改变这些核心边界：

- 前端不直接访问 RAGFlow
- 前端不直接访问 AI 模型
- 文件统一进入 MinIO
- 业务数据统一进入 PostgreSQL
- 长任务统一进入 RabbitMQ / Celery
- Redis 用于缓存、锁、限流、任务结果
- AI 统一走 AI Provider
- RAGFlow 操作统一走 RAGFlow Client
- 权限统一走 RBAC
- 配置统一走 ConfigService
- 审计统一走 AuditService

---

## 5. 开发总原则

- 不使用 SQLite。
- 不使用本地文件存储作为正式方案。
- 不使用 FastAPI BackgroundTasks 承担核心长任务。
- 不将 AI 能力写死为强依赖。
- 不让用户上传后直接阻塞等待 RAGFlow 解析。
- 不在前端保存或暴露 RAGFlow / AI API Key。
- 所有管理员操作必须记录审计日志。
- 所有文件状态必须可追踪、可重试、可解释。
