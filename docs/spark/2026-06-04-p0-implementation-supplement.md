# P0 实施补充：跨平台、事件与前端落地

> 恢复并合并版 2.0 · 2026-07-16。若与 AGENTS.md 或需求文档冲突，以 AGENTS.md 红线及 05 状态/API 契约为准。

## 1. Windows 到 DGX Spark

- 路径只用 `pathlib.Path`，文本显式 UTF-8，仓库 LF；文件名清理覆盖 CON/PRN/AUX/NUL/COM*/LPT*。
- Docker base 必须官方多架构。含原生依赖的构建/运行 stage 按 `TARGETPLATFORM` 产生，不跨架构复制 build platform 的二进制。
- 禁止 `psycopg2*`、`python-magic*`、`mysqlclient`、`pycrypto`、`m2crypto`；新增依赖执行 `invoke check-arm64`。
- buildx arm64 仅为预检；DGX Spark 必须启动全栈并跑迁移、ready 与主链 E2E。

## 2. Outbox 与事件

模块在业务事务中写 `event_outbox`，dispatcher 投递 RabbitMQ。事件 routing key 为 `<module>.<aggregate>.<action>`，payload 带 `event_id/schema_version/occurred_at/aggregate_id/correlation_id`。消费者至少一次、必须幂等。

核心事件：UserRegistered/UserVerified、FileUploaded/TextExtracted/FileAnalyzed/SensitiveDetected/FileSubmittedForReview/FileApproved/FileRejected、RAGFlowDocumentUploaded/RAGFlowParseStarted/RAGFlowParseCompleted/RAGFlowParseFailed、ConfigChanged。超过 attempt 上限进入 DLQ并告警。

自动提交只由 FileAnalyzed/SensitiveDetected 后续 handler 基于上传快照继续，不能在 AI 未完成时提前标 `pending_review`。

## 3. 目录与模块

模块保持 api/schemas/models/repository/service/events/handlers/permissions/tasks/exceptions 边界；禁止跨模块 service/repository import。外部依赖实现放 adapters，core 仅保留协议、数据库、事件、状态、配置与安全基础能力。

## 4. 版本锁与离线 CI

Python 与 Node lockfile 是可重复构建输入；CI 无公网测试。升级依赖单独提交，含 x64/ARM64 构建和安全扫描证据。测试 fixture 不访问真实企业 RAGFlow、LLM、SMTP。

## 5. 前端设计落地

`frontend/src/theme/tokens.ts` 是色彩/间距/圆角的代码单一源，[docs/design/design.md](../design/design.md) 是视觉与布局权威说明。路由导航共源、页面懒加载、服务端状态 TanStack Query、UI/auth 状态 Zustand、API 统一 client。

移动端 ≤768px 使用抽屉导航与单列内容；认证页不保留横向品牌面板。StatusTag 中央映射状态，`uploaded` 对用户显示“草稿”。

## 6. P0 退出条件

跨平台 lint、模块边界、状态单测、API 权限负例、真实基础设施主链、ARM64 实机和恢复证据全部进入验收矩阵。任一缺失不得用“本地通过”替代。
