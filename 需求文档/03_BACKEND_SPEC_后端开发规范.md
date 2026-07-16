# 03. 后端开发规范

> 本文定义实现方式。业务语义不得复制改写，统一引用 [05 状态机与 API](./05_DATABASE_API_SPEC_数据库与API规范.md)。

## 1. 技术与目录

- Python 3.11、FastAPI、SQLAlchemy 2 async、Alembic、Pydantic v2、Celery、RabbitMQ、Redis、PostgreSQL 16、MinIO。
- API/Service/Repository 全异步；public 函数完整注解；`ruff` + `mypy --strict`。
- 每个模块使用 `api.py / schemas.py / models.py / repository.py / service.py / events.py / handlers.py / permissions.py / tasks.py / exceptions.py`，只创建确有职责的文件。

## 2. 分层职责

| 层 | 可以做 | 不可以做 |
|---|---|---|
| API | 解析/验证请求、依赖注入、响应 envelope | 直接 ORM 更新、拼业务状态、调用外部系统 |
| Service | 权限、状态机、事务、审计、outbox | 跨模块导入 service/repository |
| Repository | 带数据域的查询、持久化 | 决定权限、任意状态 patch、发消息 |
| Handler/Task | 幂等消费事件、调用本模块 service/adapter | 假定消息只投递一次 |
| Adapter | 封装 MinIO/RAGFlow/LLM/SMTP 协议 | 泄露密钥或业务 ORM |

## 3. 事务、事件与状态

- Service 在一个事务中写聚合、审计与 outbox。外部调用不放在持有长数据库事务的临界区。
- 状态转换只调用 `DocumentStateMachine.transition(from, to)`。完整允许边见 05 §2；禁止模块在运行时修改 `_allowed_transitions`，所需边必须静态声明并测试。
- 每次状态变化记录 `from_status`、`to_status`、actor/event、reason、timestamp；管理员动作写审计。
- AI 关闭时不得排队或进入 AI 状态；自动提交必须由分析完成事件继续，而不是在上传请求中误标待审核。

## 4. 权限与并发

- `employee` 查询必须含 `uploader_id=current_user.id`；`dept_admin` 查询必须含授权部门集合；`system_admin` 可全局。
- 资源不存在和数据域外统一 404。无角色能力但资源无关时返回 403。
- 审核领取/决定和同步建任务使用行锁或版本条件；冲突返回 `review_claim_conflict` / `review_already_decided` 等稳定错误码和 HTTP 409。
- 同步同时具备数据库活跃任务约束与 Redis 锁。锁丢失不能造成重复远端文档。

## 5. 文件与安全

- 扩展名白名单后必须用 `filetype` 检测实际 MIME；流式限制大小，不信任 `Content-Length`。
- 文件名过滤路径分隔符、控制字符、双扩展伪装和 Windows 保留名；响应使用安全 `Content-Disposition`。
- 原件流式读取，不整体装入内存；支持取消连接和可选 Range。管理员跨用户访问写只读审计。
- token 原文不可入库/outbox/log；API Key 仅密文存储，响应只给 `has_api_key` 与 mask。

## 6. 配置读取

运行时配置只通过 `app.core.runtime_config.get_config` 读取。每个暴露在设置页的 key 必须有至少一个业务消费者、类型/范围验证、环境 fallback、缓存失效测试和契约登记；没有消费者的 key 在上线前删除或实现。

安全配置缓存最长 5 秒，其他热更新配置最长 15 秒。标记“重启”的基础设施变量不得伪装成热更新。

## 7. 测试门禁

- 每个 API 至少 happy、验证失败、无权/越域用例；状态转换每条边至少一条单测，非法边参数化覆盖。
- repository 使用真实 PostgreSQL 特性测试行锁、JSONB、唯一约束；不能仅靠 SQLite 替身。
- E2E 走真实 PostgreSQL/RabbitMQ/Redis/MinIO 与真实 worker；只允许对外部 RAGFlow/LLM 做协议级 mock。
- 迁移必须验证 upgrade、downgrade（若不可逆则明确阻断与恢复方案）及从上一发布版本升级。
