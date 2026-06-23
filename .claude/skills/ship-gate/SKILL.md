---
description: 完成门。宣称"完成"前必跑：编排 事实层 + 审计 + 安全 + 红队 四方独立审查，全绿才放行并清除完成门标记。被 Stop hook 打回时跑它。
---

# Ship Gate — 对抗式完成门

把"完成"重定义为"四方审查全绿"。这是 `adversarial-gate.ps1`（Stop hook）打回后主代理要跑的门。
权威标准见 [docs/quality/definition-of-done.md](../../../docs/quality/definition-of-done.md)。

## 使用时机

- 改了 `backend/app/**` 或 `frontend/src/**` 后，准备向用户宣称"完成 / 可以提交"前
- 被 Stop hook 以 `{"decision":"block"}` 打回时（提示语会指向本 skill）
- 想知道"按完成门标准，这批改动能不能放行"

## 流程

```
1. 确认 scope
   - 默认: git diff HEAD（未提交 + 已暂存）
   - 指定: 文件 / 模块 / PR

2. 事实层（并行，工具客观判定）
   - invoke lint           # ruff + mypy --strict + 模块边界
   - invoke test           # pytest + 前端 Vitest
   - invoke check-arm64    # 依赖 ARM64 wheel + == 锚定
   - alembic upgrade head  # 迁移可前进（若有 schema 变更）

3. 审计：启动 quality-reviewer agent（sonnet）
   - 输入 diff，输出 BLOCK / HIGH / LOW

4. 安全：启动 security-auditor agent（opus）
   - 改了认证 / 上传 / 外部 API / 配置时必跑
   - 输出 CRITICAL / HIGH / MEDIUM

5. 红队：启动 red-team agent（opus）
   - 针对改动面写并跑攻击测试（backend/app/tests/red_team/）
   - 跑红 = 命中真实漏洞 → 必须修到转绿

6. 判定（见下）

7. 若放行：
   - 删除标记：rm -f .claude/artifacts/gate-state/pending.json .claude/artifacts/gate-state/block_count
   - 写门禁报告：.claude/artifacts/ship-gate-report.md
```

## 判定规则

| 条件 | 结果 |
|---|---|
| 事实层任一红（lint/test/arm64/迁移） | 🔴 门关闭 |
| quality-reviewer 有 BLOCK | 🔴 门关闭 |
| security-auditor 有 CRITICAL | 🔴 门关闭 |
| red-team 有跑红未修的攻击测试 | 🔴 门关闭 |
| 以上全绿 | ✅ 放行，清除标记 |

🔴 时**不清除** `pending.json` —— Stop hook 会继续拦，直到真正修好。
HIGH / MEDIUM / LOW 不阻断放行，但必须在报告里列出，由人决定是否跟进。

## 报告格式

```markdown
# Ship Gate Report — <branch / scope>

## 门禁决议: ✅ 放行 / 🔴 拒绝

## 事实层
- ruff: 0 / mypy: 0 / 模块边界: 0
- pytest: 247/247 (cov 84%) / 前端: 0
- check-arm64: PASS / alembic: head OK

## 🤖 quality-reviewer
🔴 0  🟡 1  🟢 3
- 🟡 ...

## 🔒 security-auditor
🔴 0  🟠 0  🟡 1
- 🟡 ...

## 💣 red-team
- 攻击测试: 12 写 / 12 绿（修复 3 个：越权枚举 / 非法跃迁 / 抢锁）
- 残留风险: 无

## 必修项（放行前清零）
- (无)

## 决议: 四方全绿 → 标记已清除，可向用户宣称完成 ✓
```

## 不要做

- ❌ 有 BLOCK / CRITICAL / 红队跑红未修时清除 `pending.json`（等于伪造完成）
- ❌ 用 `override` 文件自我放行（那是给人的逃生阀，主代理不得创建）
- ❌ 跳过红队只跑审计（审计查合规 ≠ 红队找漏洞，不可互替）
- ❌ 重复 ruff / mypy 已报的问题
