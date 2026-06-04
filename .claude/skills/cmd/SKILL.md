---
description: 项目常用命令速查。当用户问"怎么启动""怎么测试""怎么建迁移"等命令类问题时使用。
---

# Commands Cheatsheet

项目常用命令一站速查。所有命令基于 `invoke`（跨平台 Python 任务执行器）。

## 启停

```powershell
invoke up                          # 启动所有容器
invoke up --service=backend-api    # 启单个服务
invoke down                        # 停止
invoke restart                     # 重启
invoke logs --service=backend-api  # 看日志
invoke logs --service=backend-api --follow  # 实时跟随
invoke ps                          # 容器状态
invoke health                      # 各服务健康检查
```

## 数据库

```powershell
# 迁移
invoke migrate --msg="add files table"  # 创建迁移（自动生成）
invoke migrate                           # 升级到最新
invoke migrate --target=<rev>            # 升级到指定版本
invoke migrate-down                      # 回退一步
invoke migrate-down --target=<rev>       # 回退到指定版本

# 连接 / 调试
invoke psql                              # 进 psql
invoke db-shell                          # 在 backend-api 内开 IPython（已注入 session）
invoke db-reset                          # 重建测试数据库（危险，本机限定）
```

## 测试

```powershell
invoke test                              # 全部测试
invoke test -k "test_login"              # 关键字过滤
invoke test --path=backend/app/tests/unit/  # 指定目录
invoke test --cov                        # 覆盖率
invoke test --cov --cov-report=html      # HTML 报告
invoke test-frontend                     # 前端测试
invoke test-frontend --watch             # watch 模式
invoke e2e                               # E2E 测试（用 mock）
```

## 代码质量

```powershell
invoke lint                              # ruff check + mypy
invoke fmt                               # ruff format
invoke fmt --check                       # 不修改，只检查
invoke review                            # 一键全部（lint + fmt + test + frontend）
```

## 跨架构

```powershell
invoke check-arm64                       # 依赖 ARM64 wheel 检查
invoke build                             # 本机 amd64 镜像
invoke build --arch=arm64                # ARM64 镜像（QEMU 仿真）
invoke build --arch=both --push          # multi-arch + push 到 registry
```

## Celery / Worker

```powershell
invoke worker --queue=ragflow            # 启动指定 queue 的 worker
invoke flower                            # Celery monitoring（如装了）
invoke task-status --task-id=<id>        # 查任务状态
invoke task-retry --task-id=<id>         # 重试
invoke purge-queue --queue=<name>        # 清队列（危险）
```

## 工具

```powershell
invoke seed-admin                        # 创建第一个 system_admin（首次启动用）
invoke check-deps                        # 列出 backend / frontend 依赖版本
invoke generate-secret                   # 生成 JWT secret / Fernet key
invoke export-openapi --output=docs/api/openapi.json
```

## Docker 直接命令（不通过 invoke）

```powershell
# 进容器
docker compose exec backend-api bash
docker compose exec backend-api ipython

# 看日志
docker compose logs -f backend-api worker-ai

# 清理
docker compose down -v          # 删除 volumes（重置数据）
docker system prune -a          # 全局清理（危险）
```

## Git 速查（项目约定）

```powershell
# 创建分支
git checkout -b feat/<阶段>-<模块>     # feat/p1-auth
git checkout -b fix/<issue>            # fix/login-lockout

# 提交
git add backend/app/modules/auth
git commit -m "feat(auth): add email verification flow"

# 提 PR
git push -u origin feat/p1-auth
gh pr create --title "feat(auth): email verification" --body-file PR_BODY.md
```

## 常见问题

### "invoke 没装"
```powershell
pip install invoke
```

### "docker compose up 卡住"
1. 检查 Docker Desktop 是否启动
2. 检查端口是否被占用：`netstat -ano | findstr :5432`
3. `invoke down` + `invoke up`

### "迁移失败 / 数据库异常"
1. `invoke logs --service=postgres` 看错误
2. 本机重置（会丢数据）：`invoke db-reset`
3. 生产/重要数据：手动 `psql` 排查

### "ARM64 wheel 检查失败"
1. 看哪个依赖：错误信息会列
2. 找替代（参考补充 spec §2.5.2）
3. 实在无 → 在 Dockerfile 加 `apt-get install build-essential` 让 ARM64 编译

### "前端 build 慢"
1. 用 Vite 而不是 webpack
2. 确认 `node_modules` 在容器卷而不是 bind mount
