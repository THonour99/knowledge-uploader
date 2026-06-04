---
description: 创建一个 Alembic 迁移。当涉及新增表 / 修改字段 / 加索引 / 加约束时使用。自动 autogenerate + 人工 review + downgrade 校验。
---

# New Migration

创建并验证一个 Alembic 迁移。

## 使用时机

- 新增 / 修改了 `backend/app/modules/*/models.py`
- 需要加索引 / 约束 / 触发器
- 数据迁移（拆字段、改类型）

## 流程

```
1. 确认 message 清晰
   ✅ "add files table with hash unique"
   ✅ "rename users.dingtalk_id to users.ding_user_id"
   ❌ "update"  ❌ "fix"  ❌ "."

2. 跑 autogenerate
   invoke migrate --msg="<message>"
   → 生成 backend/app/db/migrations/versions/<rev>_<slug>.py

3. 人工 review 生成的迁移文件
   - upgrade() 是否符合预期
   - 有没有意外的 drop_*
   - 索引是否齐全
   - 约束（CHECK / FK）是否正确
   - downgrade() 是否完整（autogenerate 通常会生成，但要手动 check）

4. 跑升级
   invoke migrate
   → 应该 OK

5. 验证可逆（重要！）
   docker compose exec backend-api alembic downgrade -1
   docker compose exec backend-api alembic upgrade head
   → 两次都应该 OK

6. 跑测试
   invoke test
   → 全绿

7. 提交（一次提交 = 一个迁移）
   git add backend/app/db/migrations/versions/<rev>_<slug>.py backend/app/modules/<module>/models.py
   git commit -m "feat(<module>): add files table"
```

## Review Checklist（每条都过）

- [ ] 文件名 + revision 唯一（autogenerate 自动生成 hash）
- [ ] `upgrade()` 包含所有预期变更
- [ ] `downgrade()` 完整可逆（写不出可逆的，docstring 说明原因）
- [ ] 所有外键有 `ondelete` 行为
- [ ] 所有外键列有索引
- [ ] 大表查询字段（如 `files.uploader_id`, `files.status`）有索引
- [ ] 时间戳带 timezone
- [ ] 枚举用 `VARCHAR(40)` 而非 PG `ENUM`
- [ ] JSON 字段用 `JSONB`（如要查）
- [ ] CHECK 约束限制枚举取值
- [ ] 没有破坏性变更（DROP NOT NULL / ALTER TYPE 不兼容 / RENAME 列）—— 有的话两步走

## 模板

```python
"""<message>

Revision ID: <rev>
Revises: <down_rev>
Create Date: <date>
"""
from __future__ import annotations
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "<rev>"
down_revision: str | None = "<down_rev>"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "files",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("original_name", sa.String(255), nullable=False),
        sa.Column("hash", sa.String(64), nullable=False, unique=True),
        sa.Column("uploader_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(40), nullable=False, server_default="uploaded"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["uploader_id"], ["users.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "status IN ('uploaded', 'extracting_text', 'analysis_queued', 'analyzing', "
            "'analysis_failed', 'analyzed', 'pending_review', 'sensitive_review_required', "
            "'approved', 'rejected', 'queued', 'syncing', 'uploaded_to_ragflow', "
            "'parsing', 'parsed', 'failed', 'disabled', 'deleted')",
            name="files_status_check",
        ),
    )
    op.create_index("idx_files_uploader_status", "files", ["uploader_id", "status"])
    op.create_index("idx_files_created_at", "files", ["created_at"])


def downgrade() -> None:
    op.drop_index("idx_files_created_at", table_name="files")
    op.drop_index("idx_files_uploader_status", table_name="files")
    op.drop_table("files")
```

## 不要做

- ❌ 改已合并的迁移文件（应该新建迁移）
- ❌ `alembic stamp` 跳过未跑的迁移
- ❌ 在迁移里调外部服务（RAGFlow / MinIO / Redis）
- ❌ 在迁移里跑业务逻辑（仅 schema）
- ❌ 用 `Float` 存金额（用 `Numeric`）
- ❌ `String` 不带长度（默认无限长，PG 没问题但 MySQL 兼容差）

## 报告格式

```
✅ 迁移已创建：
- backend/app/db/migrations/versions/20260604_a1b2_add_files_table.py

📝 包含：
- 新表 files（17 字段 + 5 约束）
- 2 个索引：files(uploader_id, status), files(created_at DESC)
- 1 个 CHECK 约束：status 取值

🧪 验证：
- alembic upgrade head ✅
- alembic downgrade -1 ✅
- alembic upgrade head ✅
- invoke test ✅
```
