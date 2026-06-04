---
description: 测试规则（pytest + Vitest）
paths:
  - "**/tests/**"
  - "**/*.test.ts"
  - "**/*.test.tsx"
  - "**/*.test.py"
  - "backend/app/tests/**"
  - "frontend/src/**/*.test.*"
---

# 测试规则

## 1. 后端测试栈

- pytest 8.x
- pytest-asyncio
- pytest-cov
- httpx（AsyncClient for API tests）
- factory-boy（fixture 工厂）
- faker（fake 数据）
- freezegun（时间冻结）

## 2. 测试分层

```
backend/app/tests/
├── conftest.py              ← 全局 fixtures
├── factories/               ← factory-boy 工厂
├── unit/                    ← 纯函数 / utils / repository
│   ├── test_filename.py
│   ├── test_security.py
│   └── ...
├── integration/             ← 跨层 / DB / 外部依赖（mock）
│   ├── test_auth_flow.py
│   ├── test_upload_flow.py
│   └── ...
└── e2e/                     ← 完整业务链路
    └── test_full_pipeline.py
```

## 3. 命名

- 文件：`test_<feature>.py`
- 类：`class TestFeature` 或不用类
- 函数：`def test_<action>_<expected_result>()`
  - ✅ `test_upload_rejects_oversize_file`
  - ✅ `test_login_locks_after_5_failures`
  - ❌ `test_upload_1` / `test_it_works`

## 4. fixture 约定

- `conftest.py` 提供：
  - `async_client`：httpx AsyncClient + 测试 app
  - `session`：测试 DB AsyncSession（每个测试一个 transaction，结束 rollback）
  - `mock_ragflow`：mock RagflowClient
  - `mock_llm`：mock BaseLLMProvider
  - `mock_storage`：mock StorageAdapter（内存实现）
  - `mock_email`：mock EmailAdapter
- 用户 fixtures：`employee_user`、`admin_user`、`super_admin_user`（含已签发的 JWT）

## 5. 测试不能依赖外网

- ❌ 不能真调 RAGFlow / OpenAI / SMTP
- ✅ Adapter 必须有 mock 实现
- ✅ HTTP 调用用 `respx` 或 `httpx_mock` 拦截
- CI 在无外网环境跑

## 6. 单元测试要求

每个 `core/` 和 `utils/` 模块必有单测，覆盖：
- happy path
- 边界（空、最大、最小）
- 异常（输入错误、依赖失败）

## 7. 集成测试要求

每个 API 至少：
- 1 个成功路径（正确 payload + 正确权限）
- 1 个权限拒绝路径（错误角色）
- 1 个校验失败路径（错误 payload）

## 8. E2E 测试要求

至少覆盖以下完整链路（用 mock）：
- 注册 → 邮箱验证 → 登录 → 上传 → 审核通过 → 同步 RAGFlow → 解析完成
- 上传敏感文件 → 进入 sensitive_review_required → 管理员忽略 → 同步
- 上传 → AI 分析失败 → 仍可手动审核 → 同步

## 9. 异步测试模板

```python
import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_upload_creates_file_and_publishes_event(
    async_client: AsyncClient,
    employee_user,
    session,
):
    response = await async_client.post(
        "/api/files/upload",
        files={"file": ("test.pdf", b"%PDF-1.4 ...", "application/pdf")},
        data={"category_id": str(category.id), ...},
        headers={"Authorization": f"Bearer {employee_user.token}"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    assert body["data"]["status"] == "uploaded"

    # 验证 outbox 事件
    events = await outbox_repository.list_unpublished(session)
    assert any(e.event_type == "files.file.uploaded" for e in events)
```

## 10. 前端测试栈

- Vitest 1.5+
- @testing-library/react 15+
- jsdom

## 11. 前端测试要求

- 关键交互必有测试：登录、上传、审核操作、AI 配置提交
- 测试文件：与组件同级 `Component.test.tsx`
- 不测样式（CSS-in-JS 难测，靠人眼）
- TanStack Query 用 `QueryClientProvider` 包裹

## 12. 测试覆盖率目标

- 后端：≥80% 总覆盖，`core/` `utils/` `service/` 必 ≥90%
- 前端：关键交互必测，覆盖率不强制（视觉很难测）
- CI 失败条件：核心模块覆盖率下降 > 5%

## 13. 命令

```powershell
# 全部
invoke test

# 按关键字
invoke test -k "test_login"

# 单文件
docker compose exec backend-api pytest backend/app/tests/unit/test_filename.py -v

# 覆盖率
docker compose exec backend-api pytest --cov=app --cov-report=html
```

## 14. 不要做

- ❌ `assert True` / `assert 1 == 1`（空测试）
- ❌ 测试间共享状态（每个测试独立）
- ❌ 测试中 `time.sleep`（用 freezegun）
- ❌ 测试中调用真实 LLM（用 mock）
- ❌ 测试中创建真实 MinIO bucket（用 mock storage adapter）
