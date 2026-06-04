---
description: 按问题描述定位代码、最小修复、加测试。当用户报告 bug 或描述一个具体问题（不是模糊的"优化下"）时使用。
---

# Fix Issue

修复一个具体问题。**最小改动 + 加回归测试**。

## 使用时机

用户说：
- "登录失败 5 次没有锁定" / "上传超大文件没报错" / "审核拒绝后状态没变"
- "PR 上 quality-reviewer 找的 #3 问题修一下"
- 一个明确的错误堆栈 / 错误码

## 流程

```
1. 复述问题（用户原话 → 你的理解，确认对齐）
   "确认：登录连续失败 5 次后用户应该被锁定 15 分钟，目前没有锁定。"

2. 定位（用 Grep / Glob 找相关代码）
   - 入口（API route）
   - 业务逻辑（service）
   - 数据访问（repository）
   - 状态变更（state machine 或 ORM update）

3. 复现（如有可能）
   - 写一个失败的测试（先红后绿）
   - 不能写测试时，描述如何手动复现

4. 修复（最小改动）
   - ❌ 不顺手重构无关代码
   - ❌ 不顺手优化性能
   - ❌ 不新增配置项
   - ✅ 改最少的代码让测试通过

5. 验证
   - invoke test -k <相关测试>
   - invoke lint
   - 如涉及 schema：invoke migrate 双向跑通

6. 加回归测试
   - 测试名称必须能反映 bug 本身
   - 注释引用 issue / PR 编号
```

## 修复模板

```python
# tests/integration/test_login_lockout.py
async def test_login_locks_user_after_5_failed_attempts(async_client, db):
    """回归测试：连续 5 次失败登录后用户被锁定 15 分钟（fix #N）"""
    for _ in range(5):
        resp = await async_client.post("/api/auth/login", json={
            "email": "test@company.com",
            "password": "wrong",
        })
        assert resp.status_code == 401
    # 第 6 次必须返回锁定
    resp = await async_client.post("/api/auth/login", json={
        "email": "test@company.com",
        "password": "correct",  # 即使正确也应该被拒
    })
    assert resp.status_code == 423  # Locked
    assert "locked" in resp.json()["error_code"].lower()
```

## 报告格式

```
🐛 Bug: <一句话描述>
📍 根因: <文件:行号> <原因>
🔧 修复: <修改的文件 + 修改要点>
✅ 测试: <新增测试名称>
📊 影响: <仅本 issue / 还涉及其他模块>
```

## 不要做

- ❌ "顺便"重构无关代码（开 separate PR）
- ❌ 跳过测试（必须能复现 + 测试）
- ❌ 在 fix 里加 feature（YAGNI）
- ❌ 改 schema 不写迁移
