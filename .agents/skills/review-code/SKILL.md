---
description: 按项目约定评审当前 diff 或指定文件。包装 quality-reviewer agent，自动跑 ruff + mypy + 项目特定检查。提交前必跑。
---

# Review Code

按项目约定评审代码。

## 使用时机

- 写完一个模块 / 修完一个 bug 准备提交前
- 想检查"按项目规范有没有漏"
- 想知道当前 diff 风险等级

## 流程

```
1. 确认 scope
   - 默认: git diff HEAD（未提交 + 已暂存）
   - 指定: 文件路径 / 函数名 / 模块

2. 跑自动化检查（并行）
   - invoke lint
   - invoke fmt --check（不修改）
   - mypy backend/app
   - 前端: npm run lint + tsc --noEmit

3. 启动 quality-reviewer agent
   - 把 diff 输入
   - 让它输出 BLOCK / HIGH / LOW 三档

4. 启动 security-auditor agent（如改了认证 / 上传 / 外部 API / 配置）

5. 汇总输出
```

## 评审 checklist（手动 + 自动）

### 自动（工具能查的）
- [ ] ruff check 0 errors
- [ ] mypy --strict 0 errors
- [ ] pytest 全绿
- [ ] coverage 没降
- [ ] 前端 ESLint 0 errors
- [ ] 前端 tsc --noEmit 0 errors

### 半自动（grep 能查的）
- [ ] 无 `os.path.join` 字符串拼接（用 pathlib）
- [ ] 无 `print(` 在 backend/app/（用 structlog）
- [ ] 无 `from app.modules.X.service` 跨模块 import
- [ ] 无 `api_key` / `password` / `token` 出现在 logger
- [ ] 无硬编码颜色在 frontend（用 tokens）
- [ ] 无硬编码 `latest` tag 在 docker-compose

### 人工（要 quality-reviewer agent）
- [ ] 状态变更走 DocumentStateMachine
- [ ] 管理员操作写 audit_logs
- [ ] AI 关闭分支正确
- [ ] 测试覆盖了 happy + error + permission
- [ ] 文档（如改了 API）同步更新

## 命令模板

```powershell
# 一键检查
invoke review

# 等价于
invoke lint
invoke fmt --check
docker compose exec backend-api mypy app
docker compose exec backend-api pytest --cov
docker compose exec frontend npm run lint
docker compose exec frontend npx tsc --noEmit
```

## 报告格式

```
# Code Review Summary

## ✅ 通过
- ruff: 0 errors
- mypy: 0 errors
- pytest: 247/247 passed (cov 84%, +1%)
- ESLint: 0 errors
- tsc: 0 errors

## ⚠️ 半自动发现
- (无)

## 🤖 quality-reviewer 发现
🔴 0  🟠 1  🟢 3
- 🟠 backend/app/modules/document/api.py:88 — POST /files/upload 缺幂等性 key
- 🟢 ...

## 🔒 security-auditor 发现
🔴 0  🟠 0  🟡 1
- 🟡 重置密码 token 过期时间应从 30min 配置项读取

## 📊 推荐处理
- 必修: 0
- 强烈建议: 1 (幂等 key)
- 可选: 4

合并意见: 修完上面 1 项就可以合并 ✓
```

## 不要做

- ❌ 重复 ruff / mypy 已经报的问题
- ❌ 评审审美偏好
- ❌ 把这个 skill 当成开发 skill（这是只读评审）
