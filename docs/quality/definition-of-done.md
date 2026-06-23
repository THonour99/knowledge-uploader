# Definition of Done — 完成门（DoD）

> 本文件是 Knowledge Uploader 的"完成判定"权威标准。
> `ship-gate` skill 与 `adversarial-gate.ps1`（Stop hook）均以此为准。

## 0. 核心原则

**完成判定权不在执行者手里。**

AI 编程天然"任务优先"——倾向产出"能跑但不优秀"的代码，且对"我完成了吗"的自评不可信
（生成与评判同源，共享同样的盲区）。因此本项目把"完成"重新定义为：

> 一项改动只有在通过**四方独立审查**（事实层 + 审计 + 安全 + 红队）且全绿后，才算"完成"。
> 主代理无权自称完成；绕过完成门的权力只在**人**手里（`override` 逃生阀）。

"能跑"被重新定义为 **"在对抗性测试下仍然能跑"**。

## 1. 四方门

| 方 | 角色 | 性质 | 执行者 |
|---|---|---|---|
| 事实层 | 工具客观判定 | 可证伪、无主观 | `invoke lint` / `invoke test` / `invoke check-arm64` |
| 审计 | 查"该做的做了没" | 对清单查合规 | `quality-reviewer`（sonnet） |
| 安全 | 查 OWASP + 项目安全红线 | 对清单查合规 | `security-auditor`（opus） |
| 红队 | "我就要弄坏它" | 攻击者视角、写会失败的测试 | `red-team`（opus） |

审计与红队**性质不同**：审计查合规，红队找漏洞。两者不可互相替代。

## 2. 硬关卡（任一不过 → 不算完成）

源自 `08_TASK_BREAKDOWN` 各阶段验收点 + `CLAUDE.md` §4/§8/§9。

1. `invoke lint` 全绿（ruff + mypy --strict + 模块边界检查）
2. `invoke test` 全绿（pytest + 前端 Vitest）
3. `invoke check-arm64` 通过（无 ARM64 wheel 缺失、依赖锚定 `==`）
4. Alembic 迁移可前进（`alembic upgrade head`），schema 变更含迁移
5. 文件状态变更**只**通过 `DocumentStateMachine.transition(from, to)`，无直接 update status
6. 所有管理员操作写 `audit_logs`（actor / action / target / ip / ua / ts）
7. AI 关闭时（`AI_ANALYSIS_ENABLED=false`）不创建 AI 任务、不进入 AI 相关状态
8. 敏感等级 `critical` 默认阻断同步 RAGFlow（`allow_critical_risk_sync=false`）
9. 同一文件不能并存多个 `ragflow_upload` 任务（Redis 锁 `lock:sync:{file_id}` 生效）
10. 上传校验链完整：扩展名白名单 + filetype 二次校验 + 文件名清洗 + 大小限制
11. API Key **绝不**出现在日志、API 响应、前端、文件
12. E2E 全链路可跑通（上传 → 审核 → 同步，mock RAGFlow + mock LLM）
13. **红队攻击测试全绿**（`backend/app/tests/red_team/` 全通过 = 已知漏洞已修且防回归）
14. 当前阶段的所有验收点全部通过（见 §3）

## 3. 阶段验收点映射

每阶段额外满足（`08_TASK_BREAKDOWN`）；跨阶段恒定约束：Alembic 可前进、提交 PR 等 review。

| 阶段 | 验收点 |
|---|---|
| 0 | `invoke up` 起所有容器；`/api/system/health` 返回 200；前端可访问登录页 |
| 1 | 公司邮箱可注册、非公司邮箱拒绝；可登录；可重置密码；disabled 用户不能登录 |
| 2 | 文件上传到 MinIO；DB 存 object_key；重复文件可识别；员工只能看自己的文件 |
| 3 | 管理员可审核；审核后状态正确；分类可绑 RAGFlow Dataset；审核写审计日志 |
| 4 | 审核通过创建任务；Worker 可执行；失败可重试；任务状态可查 |
| 5 | 文件可同步到指定 Dataset；可见 document_id；可见解析状态；失败可重试；可查同步日志 |
| 6 | AI 关闭不创建 AI 任务；AI 开启可生成摘要/分类/标签；敏感文件进敏感审核；AI 失败不影响上传 |
| 7 | 管理员可见用户上传数；可按时间/用户/分类筛选；可见系统概览 |
| 8 | 管理员操作有审计；API Key 不出现在日志和前端；普通用户不能访问管理接口 |
| 9 | 新开发者按 README 可启动；主要流程有测试覆盖；生产部署参数清晰 |

## 4. 完成门工作机制

```
改 backend/app/** 或 frontend/src/**
  └─[PostToolUse: mark-pending-gate.ps1]→ 写 .claude/artifacts/gate-state/pending.json
主代理想结束
  └─[Stop hook: adversarial-gate.ps1]
        ├─ 子代理(agent_id)        → 放行（门只对顶层主代理生效）
        ├─ 无 pending.json          → 放行
        ├─ override 文件存在        → 放行 + 强警告（仅人可创建）
        ├─ 连续打回 > 5 次          → 强制放行防死循环 + 记录 last-forced-release.txt
        └─ pending.json 存在        → {"decision":"block"} 打回，要求先跑 /ship-gate
跑 /ship-gate 且四方全绿
  └─→ 删除 pending.json + block_count，写门禁报告到 artifacts/
```

**逃生阀**：人可手动创建 `.claude/artifacts/gate-state/override` 文件放行一次。
主代理不被告知此机制，故无法自我放行——这正是"裁决权不在执行者"的落地。

## 5. 何时算"完成"

`/ship-gate` 输出四方全绿、`pending.json` 已清除、门禁报告写入 `artifacts/`，
此时（且仅此时）主代理可向用户宣称完成。任何"我觉得应该没问题"都不算数。
