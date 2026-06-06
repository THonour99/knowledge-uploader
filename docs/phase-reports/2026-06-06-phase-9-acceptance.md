# 阶段 9 验收报告：联调与文档

日期：2026-06-06  
分支：`codex/phase-9-integration-docs`  
PR：https://github.com/THonour99/knowledge-uploader/pull/12  
状态：待补 Docker 容器验收

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
- 新增 `backend/scripts/seed_admin.py`：通过 `SEED_ADMIN_PASSWORD` 创建或提升首个 `system_admin`，校验 `ALLOWED_EMAIL_DOMAINS` 和 `PASSWORD_MIN_LENGTH`，并写入 `user.seed_system_admin` 审计日志。

## 提交记录

- `2f77dca test(ragflow): 补充上传审核同步全链路测试`
- `5c1699d feat(auth): 添加首个系统管理员初始化脚本`
- `ce7fedd docs(deploy): 完善阶段九联调部署文档`

## 已完成验收

| 命令 | 结果 |
|---|---|
| `docker compose run --rm backend-api pytest -q app/tests/e2e/test_full_pipeline.py` | 通过，`1 passed`。后续只调整了格式、类型断言和文档/seed 脚本 |
| `ruff check app scripts --no-cache` | 通过 |
| `python scripts/check_module_boundaries.py` | 通过 |
| `python scripts/seed_admin.py --help` | 通过 |
| `python scripts/seed_admin.py --email admin@company.com` | 预期失败：未设置 `SEED_ADMIN_PASSWORD` 时提前报错，不触库 |
| `npm --prefix frontend test -- --run` | 通过，3 files / 9 tests |
| `npm --prefix frontend run lint` | 通过 |
| `npm --prefix frontend run build` | 通过；保留既有 Vite large chunk warning |
| `git diff --check` | 通过 |

## 阻塞项

从 2026-06-06 阶段 9 验收中途开始，Docker Desktop Linux engine 持续返回 500：

```text
request returned 500 Internal Server Error for API route and version
http://%2F%2F.%2Fpipe%2FdockerDesktopLinuxEngine/v1.54/version
```

受影响的命令：

- `docker version`
- `docker compose ps`
- `docker compose build backend-api`
- `docker compose run --rm backend-api ruff check app`
- `docker compose run --rm backend-api mypy app`
- `docker compose run --rm backend-api pytest -q`
- `docker compose up -d --build`
- `docker compose exec backend-api alembic upgrade head`
- `curl http://localhost:18000/api/system/health`

已尝试：

- 等待 Docker 自恢复。
- 重新唤起 Docker Desktop。
- 再次查询 `docker version` 和 `docker compose ps`。

未执行强制停止 Docker Desktop，因为审批器拒绝该操作，理由是会影响本机所有 Docker workload。

## 待补验收

Docker Desktop engine 恢复后必须补跑：

```powershell
docker compose build backend-api
docker compose run --rm backend-api ruff check app
python scripts/check_module_boundaries.py
docker compose run --rm backend-api mypy app
docker compose run --rm backend-api pytest -q
docker compose up -d --build
docker compose exec backend-api alembic upgrade head
curl http://localhost:18000/api/system/health
```

并使用 Codex 浏览器复核：

- `http://localhost/login`
- `http://localhost/datasets`
- `http://localhost/statistics`
- `http://localhost/settings`

检查页面非空、无控制台错误、桌面/移动视口无异常横向溢出。

## 风险与说明

- `.codex/config.toml` 是既有未提交改动，未纳入任何阶段 9 提交。
- 未操作 RAGFlow 服务器 `http://192.168.4.46:8092` 上的既有知识库；文档明确要求只创建新的测试 Dataset，并通过 `RAGFLOW_ALLOWED_DATASET_IDS` 限制。
- 阶段 9 目前不能标记完成，必须等待 Docker Desktop 恢复后补齐容器验收与浏览器验收。
