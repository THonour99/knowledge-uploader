# 开发命令与目录分层

本文说明本仓库的本地开发入口、质量门禁分层和目录职责。`invoke` 是本地统一入口；CI 可以使用等价展开命令，但语义必须与这里保持一致。

## 命令分层

| 场景 | 命令 | 说明 |
|---|---|---|
| 后端聚焦测试 | `invoke test-backend -k "关键字"` | 在 `backend-api` 容器中运行 pytest，适合后端日常开发。 |
| 前端聚焦测试 | `invoke test-frontend` | 运行 Vitest 非 watch 模式。 |
| 后端静态检查 | `invoke lint-backend` | 运行 ruff、模块边界检查和 mypy。 |
| 前端静态检查 | `invoke lint-frontend` | 运行 ESLint。 |
| 全量测试 | `invoke test` | 聚合 `test-backend` 和 `test-frontend`，保留旧入口。 |
| 全量静态检查 | `invoke lint` | 聚合 `lint-backend` 和 `lint-frontend`，保留旧入口。 |
| 提交前事实层门禁 | `invoke check` | 聚合 `lint` 和 `test`。 |
| 发布/合并前事实层门禁 | `invoke ship` | 聚合 `check` 和 `check-arm64`。 |

格式化入口同样分层：`invoke fmt-backend`、`invoke fmt-frontend` 和聚合入口 `invoke fmt`。格式化会改写文件，只在明确需要整理格式时运行。

## CI 等价关系

CI 可以继续展开执行底层命令，例如 `ruff`、`mypy`、`pytest`、`npm run lint`、`npm run test:run` 和镜像构建。CI 展开命令必须覆盖 `invoke check` 与 `invoke check-arm64` 的事实层含义；本地开发优先使用 Invoke，避免不同开发者手动组合命令导致遗漏。

## 目录职责

| 路径 | 职责 |
|---|---|
| `backend/` | FastAPI、Celery、Alembic、后端依赖和后端测试。 |
| `frontend/` | React/Vite 前端、前端测试、构建和 Nginx 静态服务配置。 |
| `scripts/` | 本地开发脚本、边界检查、依赖检查等跨平台辅助命令。 |
| `tasks.py` | Invoke 统一任务入口，只编排命令，不承载业务逻辑。 |
| `docs/` | 当前实现、部署、测试、质量门禁、审计和阶段报告。 |
| `需求文档/` | 产品、架构、后端/前端规范、数据库/API 和阶段拆解的规格源。 |
| `deploy/` | 生产部署资产的预留目录；K8s、TLS、DGX Spark 等资产放这里。 |
| `nginx/` | Compose 入口 Nginx 配置。 |

本次目录治理不移动业务代码目录，不调整 Python import、前端路由或测试文件归属。

## 产物与缓存

以下目录是工具产物或本地缓存，不作为人工整理目标，也不应手工归档到业务目录：`.pytest_cache/`、`.mypy_cache/`、`.ruff_cache/`、`.ruff-cache-local/`、`.vite/`、`frontend/dist/`、`frontend/node_modules/`、`backend/.venv/`。