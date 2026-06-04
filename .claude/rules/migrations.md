---
description: Alembic 数据库迁移规则
paths:
  - backend/app/db/migrations/**
  - backend/alembic.ini
---

# Alembic 迁移规则

## 1. 命名规范

- 文件名自动：`<revision>_<slug>.py`
- 提交 message 必须描述意图：`invoke migrate --msg="add files table"`
- ❌ 禁止：`migrate --msg="update"` / `"fix"` / `"."`

## 2. 必含 downgrade

每个迁移必须有可逆的 `downgrade()`：

```python
def upgrade() -> None:
    op.create_table(
        "files",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("original_name", sa.String(255), nullable=False),
        ...
    )

def downgrade() -> None:
    op.drop_table("files")
```

特殊情况（如生产数据迁移）无法逆向时，必须在 docstring 中说明：

```python
"""revision: 20260615_xxxx
单向迁移：拆分 files.tags 字符串到 tags 关联表。
不可逆，因为字符串解析可能丢失原始顺序。
"""
```

## 3. 数据类型选择

| 用途 | 类型 |
|---|---|
| ID（主键） | `sa.UUID(as_uuid=True)` 默认；PostgreSQL `uuid_generate_v4()` |
| 时间戳 | `sa.DateTime(timezone=True)`（永远带时区） |
| 枚举 | `sa.String(40)` + Python `Enum`（不用 PG `ENUM` 类型，迁移痛苦） |
| JSON | `sa.JSON()` → PG 自动 `jsonb` |
| 大文本 | `sa.Text()` |
| 钱 / 精度数 | `sa.Numeric(precision, scale)`，不用 `Float` |

## 4. 索引规则

- 外键列必须建索引
- 频繁查询的列（如 `uploader_id`、`status`）建索引
- 复合索引顺序：选择性高的列在前
- 部分索引（如只索引未发布的 outbox）：
  ```python
  op.create_index(
      "idx_outbox_pending",
      "event_outbox",
      ["occurred_at"],
      postgresql_where=sa.text("published_at IS NULL"),
  )
  ```

## 5. 约束

- NOT NULL：所有业务字段默认 NOT NULL，nullable 必须有业务理由
- UNIQUE：邮箱、SHA256 hash、外部 ID 等
- CHECK：状态枚举值校验
- FOREIGN KEY：必须明确 `ondelete` 行为（`RESTRICT` / `CASCADE` / `SET NULL`）

## 6. 不向后兼容的变更（生产慎用）

以下操作在生产数据上会引发风险，需要"两步走"迁移：

- 改列类型（如 VARCHAR → TEXT）
- 列改名
- 删除列
- 加 NOT NULL 到已有列

**两步走模式**：
1. 第一次迁移：新增列 / 双写
2. 部署应用层切换读写
3. 第二次迁移：删除旧列

## 7. 自动生成检查表

`invoke migrate --msg="..."` 后：

- [ ] 打开生成的 `versions/<rev>_<slug>.py`
- [ ] 检查 `upgrade()` 是否符合预期
- [ ] 补全 `downgrade()`
- [ ] 检查没有意外的 `drop_*`
- [ ] 检查索引和约束齐全
- [ ] 跑 `invoke migrate` 在本地数据库验证
- [ ] 跑 `alembic downgrade -1` 验证可逆
- [ ] 跑 `invoke migrate` 重新升级

## 8. 第一次迁移（阶段 0 baseline）

第一次迁移建议命名 `baseline.py`，含所有表的初始 schema。

包含表：详见补充 spec §7.1（含 v1.0 文档列出的 9 张 + 新增 `event_outbox` 和 `dead_letter_events`）。

## 9. 永远不要做

- ❌ 手改 `versions/` 已合并的迁移文件
- ❌ `alembic stamp` 跳过未运行的迁移
- ❌ 在迁移文件中写业务逻辑（迁移仅做 schema 变更）
- ❌ 在迁移中调用外部服务（RAGFlow / MinIO / Redis）

## 10. 数据迁移分离

如需迁移数据（如把字符串拆分到关联表）：

- schema 变更 = Alembic 迁移
- 数据变更 = 独立 Python 脚本 `scripts/data_migrations/<version>_<slug>.py`
- 数据脚本必须幂等
- 大数据集用分批 + 进度日志
