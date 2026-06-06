# 阶段 9 验收报告：联调与文档

日期：2026-06-06
分支：`codex/phase-9-integration-docs`
PR：https://github.com/THonour99/knowledge-uploader/pull/12
状态：通过

## 目标

按 `knowledge_uploader_docs/08_TASK_BREAKDOWN_开发任务拆解.md` 阶段 9 要求交付：

- README
- `.env.example`
- API 文档
- Docker Compose 完善
- 测试用例
- 部署说明
- 常见问题

验收标准：

- 新开发者按 README 可以启动项目。
- 主要流程有测试覆盖。
- 生产部署参数清晰。

## 完成内容

### 文档与环境

- 更新 `README.md`：补全阶段 9 当前交付、前置条件、启动、迁移、健康检查、首个管理员初始化、前端路由、RAGFlow 联调和文档索引。
- 更新 `.env.example`：补齐端口、上传限制、认证限流、AI 功能开关、队列等实际配置项，并把本地后端宿主机端口固定为 `18000`，避免占用 `8000`。
- 更新 `docker-compose.override.yml.example`：后端宿主机端口改为 `${BACKEND_API_HOST:-127.0.0.1}:${BACKEND_API_PORT:-18000}:8000`。
- 新增 `docs/api.md`：记录当前实现 API、响应 envelope、权限边界和安全约束。
- 新增 `docs/deployment.md`：记录 14 个 Compose 服务、生产环境变量、RAGFlow allowlist、AI Provider、首个管理员、前端 API 地址、ARM64 和扩容说明。
- 新增 `docs/testing.md`：记录验收命令、测试覆盖矩阵和 Codex 浏览器验收点。
- 新增 `docs/faq.md`：记录端口、迁移、上传、RAGFlow、首个管理员、权限、AI、前端构建和密钥脱敏排查。

### 测试与启动工具

- 新增 `backend/app/tests/e2e/test_full_pipeline.py`：覆盖上传、AI mock 分析、提交审核、审核通过、outbox 派发、RAGFlow mock 上传解析。
- 新增 `backend/scripts/seed_admin.py`：通过 `SEED_ADMIN_PASSWORD` 创建首个 `system_admin`，校验 `ALLOWED_EMAIL_DOMAINS` 和 `PASSWORD_MIN_LENGTH`，并写入 `user.seed_system_admin` 审计日志。系统内已存在 `system_admin` 时默认拒绝，只有显式 `--force-existing-system-admin` 才允许恢复既有 `system_admin` 账号。
- `POST/PATCH /api/datasets` 已在保存映射前校验 `RAGFLOW_ALLOWED_DATASET_IDS`，文件审核通过和分类更新时也会重新校验既有映射，避免把不允许的 RAGFlow Dataset id 写入或排入同步。
- E2E 清库逻辑增加测试环境 guard，要求 `APP_ENV=test`、数据库名以 `_test` 结尾、Redis DB 为 `15`。
- 前端 Docker 镜像已把 `VITE_API_BASE_URL` 接入构建阶段，避免静态 Nginx runtime env 造成误导。

## 提交记录

- `2f77dca test(ragflow): 补充上传审核同步全链路测试`
- `5c1699d feat(auth): 添加首个系统管理员初始化脚本`
- `ce7fedd docs(deploy): 完善阶段九联调部署文档`
- `3317e7d docs(report): 添加阶段九验收报告`
- `f492655 docs(report): 更新阶段九 PR 链接`
- `c04d352 fix(security): 收紧RAGFlow映射与管理员恢复`

## 已完成验收

| 命令 | 结果 |
|---|---|
| `docker compose build backend-api` | 通过 |
| `docker compose run --rm backend-api ruff check app scripts --no-cache` | 通过 |
| `python scripts/check_module_boundaries.py` | 通过 |
| `docker compose run --rm backend-api mypy app` | 通过，`Success: no issues found in 194 source files` |
| `docker compose run --rm backend-api pytest -q app/tests/unit/test_review_api.py::test_dataset_mapping_requires_allowed_ragflow_dataset_id app/tests/unit/test_review_api.py::test_review_rejects_dataset_mapping_removed_from_allowlist app/tests/unit/test_seed_admin_script.py app/tests/e2e/test_full_pipeline.py` | 通过，`6 passed` |
| `docker compose run --rm backend-api pytest -q` | 通过，`127 passed, 1 skipped` |
| `python -m py_compile backend/scripts/seed_admin.py` | 通过 |
| `npm test -- --run`（工作目录 `frontend/`） | 通过，3 files / 9 tests |
| `npm run lint`（工作目录 `frontend/`） | 通过 |
| `npm run build`（工作目录 `frontend/`） | 通过；保留既有 Vite large chunk warning |
| `docker compose up -d --build` | 通过，backend/frontend 镜像均重建并启动 |
| `docker compose exec backend-api alembic upgrade head` | 通过 |
| `curl.exe -s -S http://localhost:18000/api/system/health` | 通过，返回 `{"status":"ok"}` |
| `docker compose ps` | 通过，14 个服务均 healthy |
| `git diff --check` | 通过 |

## Codex 浏览器验收

使用 Codex 内置浏览器访问 `http://localhost`，浏览器保持可见。验收范围：

| 路由 | 桌面默认视口 | 移动视口 390x844 |
|---|---|---|
| `/login` | 有登录态时自动进入 `/dashboard`，页面非空，无 console error，无横向溢出 | 同左 |
| `/datasets` | 页面非空，无 console error，无横向溢出 | 页面非空，无 console error，无横向溢出 |
| `/statistics` | 页面非空，无 console error，无横向溢出 | 页面非空，无 console error，无横向溢出 |
| `/settings` | 系统设置占位页非空，无 console error，无横向溢出 | 系统设置占位页非空，无 console error，无横向溢出 |

重建后再次使用本地测试 `system_admin` 登录并访问 `http://localhost/datasets`，页面标题为 `Dataset 配置`，无 console error，无横向溢出。

## 已解决阻塞

阶段 9 验收中途 Docker Desktop Linux engine 曾返回 500 / EOF，随后定位到 E 盘可用空间耗尽：

```text
request returned 500 Internal Server Error for API route and version
http://%2F%2F.%2Fpipe%2FdockerDesktopLinuxEngine/v1.54/version
```

磁盘排查结果显示 `E:\DockerData` 约 71.14 GB、`E:\$RECYCLE.BIN` 约 18.83 GB，当前仓库仅约 0.52 GB。用户释放空间并恢复 Docker 后，所有 Docker Compose 验收命令已补跑通过。

## 风险与说明

- `.codex/config.toml` 是既有未提交改动，未纳入任何阶段 9 提交。
- 未操作 RAGFlow 服务器 `http://192.168.4.46:8092` 上的既有知识库；文档明确要求只创建新的测试 Dataset，并通过 `RAGFLOW_ALLOWED_DATASET_IDS` 限制。
- `npm --prefix frontend test -- --run` 和 `npm --prefix frontend run build` 曾在项目根目录触发本机 esbuild `spawn EPERM`；在 `frontend/` 工作目录直接执行 `npm test -- --run`、`npm run lint`、`npm run build` 均通过。
- `.tmp/npm-cache` 是 Playwright CLI 临时缓存目录，删除时被系统拒绝；目录未出现在 git 状态中，不纳入提交。
