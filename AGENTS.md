# AGENTS.md — Knowledge Uploader

> 项目级 AI 工程师规则文件。每次工作开始前必读。

## 1. 项目一句话

公司员工通过 Web 上传文档 → 校验/去重/可选 AI 分析 → 管理员审核 → 同步到 RAGFlow Dataset → 提供给下游问答服务。

## 2. 必读文档（按优先级）

1. `需求文档/01_PRD_产品需求文档.md` — 产品范围与验收基线
2. `需求文档/02_ARCHITECTURE_最终架构设计.md` — 架构定版
3. `需求文档/03_BACKEND_SPEC_后端开发规范.md` — 后端模块边界
4. `需求文档/05_DATABASE_API_SPEC_数据库与API规范.md` — 表与 API
5. `需求文档/07_DEPLOYMENT_ENV_部署与环境配置.md` — 服务与环境变量
6. `需求文档/08_TASK_BREAKDOWN_开发任务拆解.md` — 9 阶段任务
7. `docs/design/design.md` — UI 视觉权威源
8. `docs/spark/2026-06-04-p0-implementation-supplement.md` — 跨平台 / 事件总线 / 目录 / 版本锁 / 前端设计落地

## 3. 项目根与目录

- **项目根**：当前工作目录 `E:\知识库系统搭建\RAGFlow\`（即 `.Codex/` 所在目录）
- 后端：`backend/`
- 前端：`frontend/`
- 配置：`docker-compose.yml` + `nginx/` + `deploy/`
- 文档：`需求文档/`（spec）、`docs/design/`（设计稿）、`docs/spark/`（补充 spec）

## 4. 架构红线（不可逾越）

- 不使用 SQLite。数据库统一 **PostgreSQL 16**。
- 不使用本地文件系统作为正式存储。文件统一 **MinIO**。
- 不使用 FastAPI BackgroundTasks 承担核心长任务。长任务统一 **Celery + RabbitMQ**。
- 前端不直接访问 RAGFlow、AI 模型。
- RAGFlow API Key 与 AI Provider API Key **绝不返回前端**，**绝不打日志**。
- 文件状态机变更只能通过 service 层方法，不能直接 update ORM。
- 所有管理员操作必须写 `audit_logs`。
- AI 关闭时（`AI_ANALYSIS_ENABLED=false`），文件不能进入任何 AI 相关状态。

## 5. 跨平台规则（Windows 本机 → DGX Spark ARM64 生产）

- 路径用 `pathlib.Path`，禁止字符串拼接。
- 文件读写明确 `encoding="utf-8"`。
- 行尾全部 LF（`.gitattributes` 强制）。
- 新加 Python 依赖前必须检查 ARM64 wheel：`invoke check-arm64`。
- 禁用清单：`psycopg2*`、`python-magic*`、`mysqlclient`、`pycrypto`、`m2crypto`。
- 文件名清洗必须过滤 Windows 保留名（CON / PRN / AUX / NUL / COM* / LPT*）。
- Docker base 必须官方多架构；仅纯文本源码准备 stage 可使用 `BUILDPLATFORM`，安装原生依赖
  与运行 stage 必须使用 `TARGETPLATFORM`，禁止跨架构复制二进制依赖。

## 6. 模块边界（硬规则）

```
modules/<module>/
├── api.py / schemas.py / models.py
├── repository.py / service.py
├── events.py / handlers.py
├── permissions.py / tasks.py / exceptions.py
```

- ❌ 禁止跨模块 `from app.modules.X.service import ...`
- ❌ 禁止跨模块 `from app.modules.X.repository import ...`
- ✅ 允许跨模块 `from app.modules.X.schemas import ...`
- ✅ `auth` / `user` 共享 `users` 主表的同步认证特例：`auth` 只能依赖 `app.core.identity.UserIdentityStore` 协议；具体 ORM 实现只能放在 `user` 模块内；禁止在 `auth` / `core` 直接导入 `user.models` / `user.repository` / `user.service`
- ✅ 模块间通信只走：(1) 事件总线 (2) Celery task (3) 共享 schemas
- `ruff` 配置中已 ban 跨模块 service import，违规 CI 阻塞

## 7. 域事件规则

- 模块发布事件用 `event_outbox` 表（同事务），由 `outbox-dispatcher` 投递 RabbitMQ
- 模块订阅事件用 `@event_handler(EventClass)` 装饰器，定义在 `handlers.py`
- 事件命名：`<module>.<aggregate>.<action>`（routing key）
- 核心事件清单：`UserRegistered`, `UserVerified`, `FileUploaded`, `TextExtracted`, `FileAnalyzed`, `SensitiveDetected`, `FileSubmittedForReview`, `FileApproved`, `FileRejected`, `RAGFlowDocumentUploaded`, `RAGFlowParseStarted`, `RAGFlowParseCompleted`, `RAGFlowParseFailed`, `ConfigChanged`

## 8. 文件状态机硬规则

完整文件状态见 `需求文档/05_DATABASE_API_SPEC_数据库与API规范.md §2`。规则：

- 状态变更只能通过 `DocumentStateMachine.transition(from, to)` 调用
- AI 关闭：跳过 `extracting_text` / `analysis_queued` / `analyzing` / `analysis_failed` / `analyzed`
- 敏感等级 `critical`：默认阻止同步 RAGFlow
- 同一文件不能同时存在多个 `ragflow_upload` 任务（用 Redis 分布式锁，key 模式 `lock:sync:{file_id}`）

## 9. 安全规则

- 密码：Argon2id（`argon2-cffi`）
- JWT：HS256，secret 至少 32 字节随机；过期 24h（可配）
- API Key 字段级加密：Fernet（key 从环境变量 `ENCRYPTION_KEY` 加载）
- 邮箱验证 token（兼容能力）/ 重置密码 token：入库前 SHA256 hash，原文只在邮件中出现一次
- 文件上传：扩展名白名单 + `filetype` 二次校验 + 文件名清洗 + 大小限制
- 限流：登录失败 5 次锁 15 分钟；上传 10 次/分钟/用户
- 日志中所有 API Key 字段统一脱敏为 `sk-****abcd`

## 10. 代码规范

### Python
- `ruff` (lint + format) + `mypy --strict`
- 行宽 100，字符串引号统一 `"`
- 导入顺序：stdlib → 第三方 → app（ruff isort 规则集）
- 函数注解：所有 public 函数必须有完整类型注解
- 异步：API 路由、Service、Repository 全 `async def`，DB 用 `AsyncSession`
- 日志：用 `structlog.get_logger()`，不能 `print()`

### TypeScript / React
- `tsconfig.json` strict 模式
- UI 状态用 Zustand，服务端状态用 TanStack Query
- 所有 API 调用走 `api/client.ts`（含 JWT 注入和错误处理）
- 状态展示走 `<StatusTag>` 组件，不可硬编码颜色
- 颜色 / 圆角 / 间距走 `theme/tokens.ts`

## 11. 测试要求

- 后端：pytest + pytest-asyncio + httpx + factory-boy
- 前端：Vitest + React Testing Library
- 覆盖：core / utils / repository 必有单测；每个 API 至少 1 happy + 1 failure
- E2E：上传 → 审核 → RAGFlow 同步全链路（mock RAGFlow + mock LLM）
- 测试不依赖外网（CI 无外网）
- 命令：日常聚焦用 `invoke test-backend -k "..."` / `invoke test-frontend`；提交前用 `invoke check`；发布前用 `invoke ship`。

## 12. 提交规范

### 格式

```text
type(scope):中文描述
```

- **type**（英文，必填）：`feat` / `fix` / `refactor` / `docs` / `test` / `chore` / `perf` / `style` / `ci` / `build` / `revert`
- **scope**（英文，可选）：模块或子系统名，如 `auth` / `document` / `ragflow` / `ai` / `Codex` / `git` / `infra` / `spark` / `design` / `spec`
- **冒号 + 中文描述**（中文，必填）：简洁、动词开头、不带句号

### 示例

```text
feat(auth): 添加账号登录流程
fix(document): 修复连续 5 次失败登录未锁定的问题
refactor(ragflow): 把 RagflowClient 拆分为接口与实现
docs(spark): 添加 P0 实施补充 spec v1.1
chore(Codex): 添加 .Codex/ 骨架（rules/agents/skills/hooks）
test(ai): 补全 Prompt 模板渲染单测
```

### 其他规则

- AI 完成代码必须主动提交（每个原子变更立刻 commit），不要堆积
- 一个提交一个原子变更（小到能独立 review 和回滚）
- DB schema 变更必须含 Alembic 迁移
- 新增 Python 依赖必须 `invoke check-arm64` 通过

### 不允许

- ❌ 英文描述（如 `feat(auth): add email verification`）
- ❌ 缺 type（如 `添加注册流程`）
- ❌ 模糊 type（如 `update / improve / change`）
- ❌ 句末有句号
- ❌ 多个原子变更塞一个 commit
- ❌ 任何 trailer（`Co-Authored-By:` / `Signed-off-by:` / `Reviewed-by:` 等都不要带）

## 13. 阶段化开发（不可跳）

按 `需求文档/08_TASK_BREAKDOWN_开发任务拆解.md` 9 阶段顺序推进。每阶段必须：

- `invoke up` 能起所有容器
- `/api/system/health` 返回 200
- Alembic 迁移可逐步前进
- 阶段验收点全部通过
- 提交一次 PR 等 review

## 14. 常用命令

```powershell
# 启停
invoke up
invoke down
invoke logs --service=backend-api

# 数据库
invoke migrate --msg="add users"   # 创建迁移
invoke migrate                      # 升级到最新

# 日常聚焦测试与质量
invoke test-backend -k "test_login"
invoke test-frontend
invoke lint-backend
invoke lint-frontend

# 聚合门禁
invoke check
invoke ship

# 格式化
invoke fmt

# 跨架构
invoke check-arm64
invoke build-arm64 --version=0.1.0
```

## 15. 找帮助前先看

- 文件状态相关：`需求文档/05_DATABASE_API_SPEC_数据库与API规范.md §2` + `需求文档/03_BACKEND_SPEC_后端开发规范.md §5`
- API 设计：`需求文档/05_DATABASE_API_SPEC_数据库与API规范.md §3`
- AI Provider：`需求文档/06_AI_RAGFLOW_SPEC_AI与RAGFlow集成规范.md §2`
- 部署/环境：`需求文档/07_DEPLOYMENT_ENV_部署与环境配置.md`
- 前端设计：`docs/design/design.md` + 补充 spec §9
- 测试 fixture：`backend/app/tests/conftest.py`
- 跨平台坑：补充 spec §2
- 域事件：补充 spec §3

## 16. AGENTS.md 维护原则

- 每完成一个阶段后 review 一次
- 发现 AI 反复犯同样错误 → 加入 §4-§10 对应规则
- 不写"建议"类规则，只写"必须 / 禁止"
- 详细 path 特定规则在 `.Codex/rules/` 中按 paths frontmatter 加载，不要塞到此文件
