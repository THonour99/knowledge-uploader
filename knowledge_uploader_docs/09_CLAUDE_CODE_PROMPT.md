# 09. Claude Code 开发 Prompt

你是一名资深全栈架构师和后端工程师。请基于当前仓库实现“企业知识库文件贡献与 RAGFlow 同步平台”。

## 项目目标

实现一个公司内部 Web 平台，让员工可以注册、登录、上传文档，系统完成文件校验、去重、可选 AI 分析、管理员审核、同步 RAGFlow、状态追踪和统计分析。

最终链路：

```text
员工上传文件
  ↓
系统校验与去重
  ↓
可选 AI 摘要 / 分类 / 标签 / 敏感检测
  ↓
管理员审核
  ↓
同步到 RAGFlow
  ↓
Dify / LangBot / 钉钉机器人获得更完整知识
```

## 架构定版

必须使用以下架构，不要替换成简化方案：

```text
React + TypeScript + Ant Design
FastAPI + SQLAlchemy + Alembic
PostgreSQL
RabbitMQ + Celery
Redis
MinIO
OpenAI-compatible AI Provider
RAGFlow Client Adapter
JWT + RBAC
Docker Compose
```

## 关键约束

- 不使用 SQLite。
- 不使用本地文件存储作为正式方案。
- 不使用 FastAPI BackgroundTasks 承担核心长任务。
- 前端不能直接访问 RAGFlow。
- 前端不能直接访问 AI 模型。
- RAGFlow API Key 和 AI API Key 不允许返回前端。
- 文件统一存储到 MinIO。
- 长任务统一进入 Celery。
- RabbitMQ 作为 Celery Broker。
- Redis 用作 Result Backend、缓存、锁、限流。
- PostgreSQL 存储业务最终状态。
- AI 分析必须可配置开启/关闭。
- AI 关闭时，文件不能进入 AI 相关状态。
- 所有管理员操作必须记录审计日志。

## 开发方式

请按阶段开发，不要一次性生成不可运行的大量代码。

每完成一个阶段必须保证：

- 项目可以启动
- 数据库迁移可执行
- API 有基本测试或可手动验证
- README 同步更新

## 优先阶段

1. 项目骨架、Docker Compose、PostgreSQL、RabbitMQ、Redis、MinIO
2. 注册、登录、JWT、RBAC、邮箱验证、忘记密码
3. 文件上传到 MinIO、hash 去重、我的文件列表
4. 管理员审核、Dataset 映射、文件状态流转
5. Celery 任务队列、任务状态、失败重试
6. RAGFlow Client、上传、解析、状态轮询
7. AI Provider 配置、AI 总开关、摘要/分类/标签/敏感检测
8. 统计分析、用户上传数量、部门统计、失败任务统计
9. 审计日志、安全加固、测试、文档

## 输出要求

实现代码时请优先输出：

- 项目目录结构
- 需要新增/修改的文件列表
- 数据库模型
- Alembic 迁移
- API 路由
- Service 层逻辑
- Worker 任务
- 前端页面
- 测试用例

遇到不确定的第三方 API 时，封装 Adapter，并在 README 中标注待配置项，不要把外部接口写死。
