# Phase 0 验收报告

## 阶段范围

Phase 0 目标是完成 Knowledge Uploader 可运行工程骨架，包含后端、前端、Docker Compose 编排、CI 骨架、质量命令和跨平台/ARM64 依赖检查入口。业务功能仍按阶段 1-9 继续实现，当前不能跳阶段。

## 当前分支

- 分支：`codex-phase-0-initialization`
- PR 状态更新前最新提交：`e966be8 chore(codex): 添加 codex 与 agents 工具配置`
- Git remote：`origin https://github.com/THonour99/knowledge-uploader.git`
- PR：[#1 feat(infra): 完成阶段零工程骨架](https://github.com/THonour99/knowledge-uploader/pull/1)
- PR 状态：`OPEN`，base `main`，head `codex-phase-0-initialization`，`mergeStateStatus=CLEAN`
- Review 状态：GitHub `reviews=[]`；本轮已完成 subagent 质量评审与安全评审，并修复 BLOCK/HIGH 项

## 本机环境说明

本机 Docker Hub 访问不稳定，Phase 0 验收通过本地忽略的 `.env` 覆盖基础镜像来源：

```text
PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.11-slim-bookworm
NODE_IMAGE=docker.m.daocloud.io/library/node:20-alpine
```

该 `.env` 不进入版本控制。Dockerfile 和 `.env.example` 仍保持官方默认镜像名，生产环境可直接使用官方多架构镜像。

## 验收结果

| 验收项 | 证据 | 状态 |
|---|---|---|
| `invoke up` 能启动全部容器 | `python -m invoke up` 成功返回 | 通过 |
| 容器健康状态 | `docker compose ps` 显示 14 个容器全部 `healthy` | 通过 |
| 后端健康接口 | `curl.exe -s http://localhost:8000/api/system/health` 返回 `{"status":"ok"}` | 通过 |
| 前端登录页占位 | `curl.exe -s -o NUL -w "%{http_code}" http://localhost/login` 返回 `200` | 通过 |
| Alembic 可前进 | `python -m invoke migrate` 成功，输出 `Context impl PostgresqlImpl` | 通过 |
| ARM64 依赖检查 | `python -m invoke check-arm64` 显示 31 个直接依赖 allowlisted | 通过 |
| lint | `python -m invoke lint` 成功；后端 ruff/mypy 0 errors，前端 ESLint 0 errors | 通过 |
| test | `python -m invoke test` 成功；后端 6 tests passed，前端 2 tests passed | 通过 |
| CI 本地模拟 | `act -W .github/workflows/knowledge-uploader.yml -j local-act --bind ...` 成功，输出 `Job succeeded` | 通过 |

说明：补充 spec 文本写“12 个容器”，但当前按 `07_DEPLOYMENT_ENV` 服务清单加补充 spec 新增 `outbox-dispatcher` 实际为 14 个 Compose 服务。`outbox-dispatcher` 是事件总线规则要求的独立容器，保留。

## 临时操作与恢复

- 为释放 `localhost:8000`，临时停止过无关容器 `xiaosheng-esp32-server`。
- Phase 0 验收结束后已重新启动 `xiaosheng-esp32-server`。
- 验收结束后已执行 `python -m invoke down`，当前 Knowledge Uploader Compose 无运行容器。
- `act.exe` 临时下载到 `C:\tmp\act-cli-0.2.89`，未进入仓库。

## Review 修复记录

本轮 subagent 质量评审和安全评审指出的 BLOCK/HIGH 项已处理：

- 移除 Phase 0 后端 mock auth 挂载，真实 app 仅暴露 `/api/system/health`；新增测试确保 `/api/auth/login` 返回 404。
- 在 `Settings` 层校验生产/staging 环境的 `JWT_SECRET` 和 `ENCRYPTION_KEY`，拒绝 placeholder、过短 JWT secret、无效 Fernet key 和公开开发示例 key。
- 将 GitHub Actions workflow 放到 `.github/workflows/knowledge-uploader.yml`，PR 可自动触发。
- 后端和前端 Dockerfile 所有 `FROM` 都显式声明 `--platform=$BUILDPLATFORM`。
- `local-act` job 使用 `$RUNNER_TEMP` 创建 Python venv，不再硬编码 `/tmp/knowledge-uploader-ci-venv`。
- 前端 API client 关闭 `withCredentials`，`StatusTag` 颜色改为引用 `theme/tokens.ts`。

## PR 信息

标题：

```text
feat(infra): 完成阶段零工程骨架
```

URL：

<https://github.com/THonour99/knowledge-uploader/pull/1>

本轮阶段门复核：

- `python -m invoke lint` 通过：后端 ruff/mypy 0 errors，前端 ESLint 0 errors。
- `python -m invoke check-arm64` 通过：31 个依赖 allowlisted。
- `npm run build` 通过。
- `python -m invoke test` 通过：后端 6 tests passed，前端 2 tests passed。
- `git rev-list --left-right --count origin/codex-phase-0-initialization...HEAD` 用于推送后复核本地与远端一致。
- `gh pr view 1 --json ...` 返回 PR `OPEN`、非 draft、`mergeStateStatus=CLEAN`。
- `statusCheckRollup=[]`，当前 GitHub PR 未挂接状态检查。
- `reviews=[]`，当前仍等待 review。

## 阶段边界状态

Phase 0 技术验收已通过，PR 已创建并处于 `OPEN` 状态。阶段边界仍等待 review；在 review gate 通过或明确批准继续前，不进入 Phase 1。
