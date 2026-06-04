---
description: 脚手架一个新的后端模块（auth / document / review 等），按模块化单体的标准 9 文件结构生成骨架。当 03_BACKEND_SPEC 列出的 10 个模块还没创建、或后期新增独立模块时使用。
---

# New Module Skeleton

按 `03_BACKEND_SPEC §4` + 补充 spec §4.5 的标准结构脚手架一个后端模块。

## 使用时机

- 阶段 0 / 1 初始化 auth / user / document 等模块
- 后期新增独立功能（如 notification 模块后加 webhook 子模块）

## 标准模块结构（9 个文件）

```
backend/app/modules/<module_name>/
├── __init__.py
├── api.py           ← FastAPI Router 路由定义
├── schemas.py       ← Pydantic 请求/响应/共享 DTO
├── models.py        ← SQLAlchemy ORM
├── repository.py    ← 数据访问，无业务逻辑
├── service.py       ← 业务编排
├── events.py        ← 本模块发布的域事件
├── handlers.py      ← 本模块订阅的事件处理函数
├── permissions.py   ← 模块特定权限（可选）
├── tasks.py         ← 本模块 Celery task（可选）
└── exceptions.py    ← 模块特定异常（可选）
```

## 流程

```
1. 确认模块名称（kebab → snake）
   user 输入: "我要加 webhook 模块"
   → module_name = "webhook"

2. 确认是否在 03_BACKEND_SPEC 的 10 模块清单中
   - 在清单：执行标准脚手架
   - 不在：先和用户确认是否真要独立模块（而不是塞进现有模块）

3. 创建目录 + 9 个文件（每个都有最小可工作骨架）

4. 注册到 main.py
   - 在 app.main 中 import router
   - app.include_router(router, prefix="/api/<module>", tags=["<module>"])

5. 写一个 placeholder 测试
   - backend/app/tests/integration/test_<module>_health.py
   - 验证至少有一个 endpoint 能 ping 通

6. 创建首张表的 Alembic 迁移（如有 models）
```

## 模板：api.py

```python
"""<Module> API routes."""
from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"module": "<module>", "status": "ok"}
```

## 模板：schemas.py

```python
"""<Module> Pydantic schemas."""
from pydantic import BaseModel, ConfigDict


class BaseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
```

## 模板：models.py

```python
"""<Module> SQLAlchemy ORM models."""
from __future__ import annotations

from app.db.base import Base

# Models will be added here.
```

## 模板：repository.py

```python
"""<Module> data access layer. No business logic."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
```

## 模板：service.py

```python
"""<Module> business logic."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import events
```

## 模板：events.py

```python
"""<Module> domain events published to the outbox."""
from __future__ import annotations
from typing import ClassVar
from uuid import UUID

from app.core.events import DomainEvent

# Example event:
# class XxxCreated(DomainEvent):
#     ROUTING_KEY: ClassVar[str] = "<module>.xxx.created"
#     id: UUID
```

## 模板：handlers.py

```python
"""<Module> event handlers (subscribers to events from other modules)."""
from __future__ import annotations

from app.core.events import event_handler

# Example:
# @event_handler(SomeOtherEvent)
# async def handle_xxx(event: SomeOtherEvent) -> None:
#     ...
```

## 模板：permissions.py

```python
"""<Module>-specific RBAC dependencies."""
from __future__ import annotations
from fastapi import Depends

from app.core.permissions import require_role, Roles
```

## 模板：tasks.py

```python
"""<Module> Celery tasks."""
from __future__ import annotations

from app.workers.celery_app import celery_app
```

## 模板：exceptions.py

```python
"""<Module>-specific exceptions."""
from __future__ import annotations

from app.core.exceptions import AppException, ErrorCode
```

## 注册到 main.py

```python
# backend/app/main.py
from app.modules.<module> import api as <module>_api

app.include_router(
    <module>_api.router,
    prefix="/api/<module>",
    tags=["<module>"],
)
```

## 测试占位

```python
# backend/app/tests/integration/test_<module>_health.py
import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_<module>_health(async_client: AsyncClient) -> None:
    resp = await async_client.get("/api/<module>/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
```

## 完成清单

- [ ] 9 个文件已创建
- [ ] main.py 已注册 router
- [ ] `invoke up` 后 `/api/<module>/health` 返回 200
- [ ] 测试通过
- [ ] 如有 ORM models → Alembic 迁移已创建并跑通

## 不要做

- ❌ 创建空文件（每个文件至少有 docstring 和最小骨架）
- ❌ 跳过 events.py / handlers.py（即使为空也要保留，固化结构）
- ❌ 跨模块 import 其他模块的 service（破坏模块边界）
