# Phase 0 验收报告

## 阶段范围

Phase 0 目标是完成 Knowledge Uploader 可运行工程骨架，包含后端、前端、Docker Compose 编排、CI 骨架、质量命令和跨平台/ARM64 依赖检查入口。业务功能仍按阶段 1-9 继续实现，当前不能跳阶段。

## 当前分支

- 分支：`codex-phase-0-initialization`
- 报告生成前最新提交：`a365476 ci(infra): 支持本地 act 验收`
- Git remote：未配置，`git remote -v` 输出为空

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
| test | `python -m invoke test` 成功；后端 2 tests passed，前端 2 tests passed | 通过 |
| CI 本地模拟 | `act -j local-act --bind ...` 成功，输出 `Job succeeded` | 通过 |

说明：补充 spec 文本写“12 个容器”，但当前按 `07_DEPLOYMENT_ENV` 服务清单加补充 spec 新增 `outbox-dispatcher` 实际为 14 个 Compose 服务。`outbox-dispatcher` 是事件总线规则要求的独立容器，保留。

## 临时操作与恢复

- 为释放 `localhost:8000`，临时停止过无关容器 `xiaosheng-esp32-server`。
- Phase 0 验收结束后已重新启动 `xiaosheng-esp32-server`。
- 验收结束后已执行 `python -m invoke down`，当前 Knowledge Uploader Compose 无运行容器。
- `act.exe` 临时下载到 `C:\tmp\act-cli-0.2.89`，未进入仓库。

## PR 草稿

标题：

```text
feat(infra): 完成阶段零工程骨架
```

正文：

```markdown
## Summary
- 添加 Phase 0 后端、前端、Docker Compose、Nginx、CI、任务命令和 ARM64 检查骨架
- 落地 10 个后端模块目录、共享 core/db/adapters/workers 结构与前端设计 token/Layout/路由占位
- 补齐容器内 mypy 配置、storage adapter 跟踪、Phase 0 登录 mock、本地 act 验收 job

## Verification
- python -m invoke up
- docker compose ps
- curl.exe -s http://localhost:8000/api/system/health
- curl.exe -s -o NUL -w "%{http_code}" http://localhost/login
- python -m invoke migrate
- python -m invoke check-arm64
- python -m invoke lint
- python -m invoke test
- C:\tmp\act-cli-0.2.89\act.exe -W deploy/ci/github-actions.yml -j local-act --bind --container-architecture linux/amd64 -P ubuntu-24.04=catthehacker/ubuntu:act-24.04

## Notes
- 本机通过忽略的 .env 使用 Docker 镜像代理，避免 Docker Hub token 请求超时
- 当前仓库未配置 remote，需要添加 origin 后再 push 并创建 PR
```

## 阶段边界状态

Phase 0 技术验收已通过。阶段边界仍缺少 PR/review，因为当前仓库没有 Git remote，无法 push 或创建 PR。配置 remote 后，按上方 PR 草稿创建 PR 并等待 review；在此之前不进入 Phase 1。
