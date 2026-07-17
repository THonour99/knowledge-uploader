# 02. 最终架构设计

> 版本：2.0 · 2026-07-16。本文定义不可逾越的系统边界；业务状态与 HTTP 字段以 [05 规范](./05_DATABASE_API_SPEC_数据库与API规范.md) 为准。

## 1. 架构定版

系统采用“模块化单体 API + 事件 outbox + 专用 Celery Worker + 外部 Adapter”的形态：

```text
Browser
  │ HTTPS /api（同源）
Nginx ── React 静态资源
  │
FastAPI modular monolith ── PostgreSQL 16（业务、审计、outbox）
  │                    ├── Redis（限流、短缓存、分布式锁）
  │                    └── MinIO（所有正式原件）
  │ event_outbox
Outbox Dispatcher ── RabbitMQ ── worker-document / worker-ai /
                                  worker-ragflow / worker-notification
                                              │
                                  RAGFlow / SMTP / approved LLM
```

前端只访问后端。MinIO、RAGFlow 与 AI 密钥不进入浏览器。核心长任务不使用 FastAPI `BackgroundTasks`。

## 2. 模块边界

`auth`、`user`、`department`、`document`、`review`、`ragflow`、`ai`、`notification`、`statistics`、`config`、`audit` 是独立业务模块。模块之间只通过共享 schema、事务 outbox 事件或 Celery task 通信，不跨模块导入 service/repository。

认证共享用户表的唯一例外：`auth` 依赖 `app.core.identity.UserIdentityStore` 协议，具体 ORM 实现在 `user` 模块。`core` 不依赖任何业务模块。

## 3. 一致性与幂等

- 业务写入与领域事件在同一 PostgreSQL 事务；dispatcher 至少一次投递，所以 handler 必须幂等。
- 每个消费者用事件 id/业务幂等键去重；失败超过阈值进入 DLQ，不得永久卡在“处理中”。
- 同一文件最多一个活跃 RAGFlow 上传任务，Redis 锁 `lock:sync:{file_id}` 只是并发保护，数据库唯一性/状态前置条件仍是最终防线。
- 领取审核和审批使用行锁或乐观版本；并发冲突返回 409，不允许后写覆盖先写。

## 4. 数据与文件边界

- PostgreSQL 16 是唯一正式关系数据库，不使用 SQLite。
- MinIO 是唯一正式原件存储。对象 key 不含未经清洗的用户文件名；数据库保存原名和对象 key。
- 原件访问经后端鉴权流式代理，管理员查看他人文件写审计；临时签名 URL 如未来引入，必须短期、单对象、单用途。
- 文档状态只能由 service 调用 `DocumentStateMachine.transition` 改变；repository 不接受任意状态 patch。

## 5. 部署与跨平台

开发目标 Windows x64，生产目标 DGX Spark Linux ARM64。路径使用 `pathlib.Path`，文本显式 UTF-8，行尾 LF；Python 依赖必须有 ARM64 wheel 或可重复构建证据。镜像使用官方多架构 base，并分别验证 build platform 与 target platform 的原生依赖。

服务健康分层：`/api/system/health` 只表示进程存活；`/api/system/ready` 检查 PostgreSQL、Redis、RabbitMQ 与 MinIO；指标端点另行暴露队列、outbox、DLQ、SLA 和同步结果。

## 6. 安全边界

- Argon2id 密码、短期 JWT、SHA256 token hash、Fernet 字段加密。
- 所有管理员变更和越出本人范围的原件读取写 `audit_logs`。
- 日志结构化且密钥统一脱敏；错误响应不回显连接串、对象 key、模型 prompt 原文或堆栈。
- 数据域过滤在 repository 查询条件中成立，不能先查全量再在前端或 Python 列表中过滤。

## 7. 架构验收

架构完成不以 compose 能启动为准，而以 [验收矩阵](../docs/product/ACCEPTANCE_MATRIX.md) 的真实基础设施 E2E、故障注入、ARM64 实机、备份恢复和安全负例全部有证据为准。
