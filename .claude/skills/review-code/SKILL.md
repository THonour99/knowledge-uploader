---
description: 按项目约定做四方评审当前 diff：事实层 + quality-reviewer + security-auditor + red-team。输出问题清单，不做放行决策（放行 + 清完成门标记走 /ship-gate）。提交前必跑。
---

# Review Code

按项目约定做**四方评审**。这是"评审视图"——输出问题与风险清单，**不**清除完成门标记、**不**做放行决策。
要"放行 + 清标记"的完整完成门，跑 [/ship-gate](../ship-gate/SKILL.md)（它复用本流程并加放行动作）。

## 使用时机

- 写完一个模块 / 修完一个 bug，想检查"按项目规范有没有漏"
- 想知道当前 diff 的风险等级
- 不一定要结束（结束放行用 /ship-gate）

## 流程

```
1. 确认 scope
   - 默认: git diff HEAD（未提交 + 已暂存）
   - 指定: 文件路径 / 函数名 / 模块

2. 事实层（工具客观判定）
   - invoke review        # = invoke lint + invoke test
   - 如需单看: invoke lint / invoke test / invoke check-arm64

3. 审计：启动 quality-reviewer agent（sonnet）
   - 输入 diff，输出 BLOCK / HIGH / LOW

4. 安全：启动 security-auditor agent（opus）
   - 改了认证 / 上传 / 外部 API / 配置时必跑
   - 输出 CRITICAL / HIGH / MEDIUM

5. 红队：启动 red-team agent（opus）
   - 改了业务逻辑 / 状态机 / 权限 / 上传 / 同步时必跑
   - 攻击者视角，写并跑会失败的 pytest；跑红 = 命中真实漏洞

6. 汇总四方输出
```

**为什么是四方、为什么异质**：审计查"该做的做了没"，红队找"我怎么攻破它"——性质不同，不可互替。
三个 agent 故意用不同模型 + 不同视角（quality=sonnet 查项目红线、security=opus 查 OWASP、red-team=opus 攻击）。
**独立性来自差异，不来自能力**——这是"完成判定权不在执行者手里"的落地。

## 评审 checklist

### 自动（工具能查的）
- [ ] ruff check 0 errors
- [ ] mypy --strict 0 errors
- [ ] pytest 全绿（含 red_team/）
- [ ] coverage 没降
- [ ] 前端 ESLint 0 errors + tsc --noEmit 0 errors

### 半自动（grep 能查的）
- [ ] 无 `os.path.join` 字符串拼接（用 pathlib）
- [ ] 无 `print(` 在 backend/app/（用 structlog）
- [ ] 无 `from app.modules.X.service` 跨模块 import
- [ ] 无 `api_key` / `password` / `token` 出现在 logger
- [ ] 无硬编码颜色在 frontend（用 tokens）

### 人工（要 agent）
- [ ] 状态变更走 DocumentStateMachine（quality-reviewer）
- [ ] 管理员操作写 audit_logs（quality-reviewer）
- [ ] AI 关闭分支正确（quality-reviewer）
- [ ] 认证/上传/外部 API 无安全红线（security-auditor）
- [ ] 攻击向量被防御（red-team 跑红测试）

## 命令模板

```powershell
# 事实层一键预检（只读）
invoke review        # = invoke lint + invoke test

# 完整完成门（事实层 + 三 agent + 放行决策 + 清标记）
# 走 /ship-gate skill，不是本 skill
```

## 报告格式

```
# Code Review Summary

## ✅ 事实层
- ruff: 0 / mypy: 0 / pytest: 247/247 (cov 84%) / 前端: 0

## 🤖 quality-reviewer
🔴 0  🟠 1  🟢 3
- 🟠 backend/app/modules/document/api.py:88 — POST /files/upload 缺幂等性 key

## 🔒 security-auditor
🔴 0  🟠 0  🟡 1
- 🟡 重置密码 token 过期应从配置项读取

## 💣 red-team
- 攻击测试: 写 N / 跑红 M / 确认漏洞 K
- 🔴 ...（确认漏洞 + 重现）

## 📊 推荐处理
- 必修: x  强烈建议: y  可选: z
- 放行决策请走 /ship-gate
```

## 不要做

- ❌ 用 review-code 代替 ship-gate 做"放行"或清 `pending.json`（本 skill 只评审）
- ❌ 跳过 red-team 只跑审计（审计 ≠ 红队）
- ❌ 重复 ruff / mypy 已报的问题
- ❌ 评审审美偏好
