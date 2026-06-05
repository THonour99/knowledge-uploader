# Knowledge Uploader

公司内部知识库文件贡献与 RAGFlow 同步平台。

员工通过 Web 上传文档，平台完成校验、去重、可选 AI 分析、管理员审核，并把审核通过的文档同步到 RAGFlow Dataset，最终供钉钉客服机器人使用。

## 阶段状态

当前按 `knowledge_uploader_docs/08_TASK_BREAKDOWN_开发任务拆解.md` 推进。阶段 0 只交付可运行骨架：

- FastAPI 后端和 `/api/system/health`
- React + TypeScript 前端登录页占位
- PostgreSQL、RabbitMQ、Redis、MinIO、Celery Worker、Nginx 的 Docker Compose 编排
- Alembic、ruff、mypy、pytest、Vitest、ARM64 依赖检查入口

业务功能按阶段 1-9 逐步实现，不能跳阶段。

## 常用命令

```powershell
invoke up
invoke down
invoke logs --service=backend-api

invoke migrate
invoke migrate --msg="add users"

invoke fmt
invoke lint
invoke test
invoke check-arm64
```

## 本地启动

1. 复制 `.env.example` 为 `.env` 并按需修改密钥。
2. 执行 `invoke up`。
3. 访问后端健康检查：`http://localhost:18000/api/system/health`。
4. 访问前端：`http://localhost:5173`，或经 Nginx 访问 `http://localhost`。

## 关键约束

- 数据库统一 PostgreSQL 16，不使用 SQLite。
- 正式文件存储统一 MinIO，不使用本地文件系统。
- 核心长任务统一 Celery + RabbitMQ。
- 前端不直接访问 RAGFlow 或 AI Provider。
- API Key 不返回前端、不打日志。
- 文件状态变更只能通过 service 层状态机。

更多规则见 `AGENTS.md`。
