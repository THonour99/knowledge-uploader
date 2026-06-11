"""add tags tables

Revision ID: d4e7a9b2c5f8
Revises: e5b8c0d1f2a3
Create Date: 2026-06-11 00:00:00.000000

建 tags / file_tags 两表, 并把 files.tags JSONB 回填为正式标签关联:
- 去重建 tags (is_system_generated=true), ON CONFLICT DO NOTHING 保证可重入
- 按 (file_id, tag_id) 建 file_tags 关联, 同样可重入
- usage_count 按 file_tags 关联数初始化
downgrade 仅删除两张新表与索引, 不回写也不删除 files.tags 列
(files.tags 语义降级为 AI 建议标签)。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e7a9b2c5f8"
down_revision: str | None = "e5b8c0d1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 回填 SQL: 全部可重入, 连续执行多次结果一致 (幂等)。
BACKFILL_STATEMENTS: tuple[str, ...] = (
    """
    INSERT INTO tags (id, name, description, is_system_generated, enabled, usage_count)
    SELECT gen_random_uuid(), names.name, NULL, TRUE, TRUE, 0
    FROM (
        SELECT DISTINCT btrim(tag_element.value) AS name
        FROM files
        CROSS JOIN LATERAL jsonb_array_elements_text(files.tags) AS tag_element(value)
        WHERE btrim(tag_element.value) <> ''
    ) AS names
    ON CONFLICT (name) DO NOTHING
    """,
    """
    INSERT INTO file_tags (file_id, tag_id)
    SELECT DISTINCT files.id, tags.id
    FROM files
    CROSS JOIN LATERAL jsonb_array_elements_text(files.tags) AS tag_element(value)
    JOIN tags ON tags.name = btrim(tag_element.value)
    WHERE btrim(tag_element.value) <> ''
    ON CONFLICT (file_id, tag_id) DO NOTHING
    """,
    """
    UPDATE tags
    SET usage_count = counted.usage_count
    FROM (
        SELECT tag_id, count(*) AS usage_count
        FROM file_tags
        GROUP BY tag_id
    ) AS counted
    WHERE tags.id = counted.tag_id
      AND tags.usage_count IS DISTINCT FROM counted.usage_count
    """,
)


def upgrade() -> None:
    op.create_table(
        "tags",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_system_generated", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("usage_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("usage_count >= 0", name="ck_tags_usage_count_non_negative"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_tags_name", "tags", ["name"], unique=True)
    op.create_index("idx_tags_enabled", "tags", ["enabled"])

    op.create_table(
        "file_tags",
        sa.Column("file_id", sa.Uuid(), nullable=False),
        sa.Column("tag_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["file_id"], ["files.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tag_id"], ["tags.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("file_id", "tag_id"),
    )
    op.create_index("idx_file_tags_file_id", "file_tags", ["file_id"])
    op.create_index("idx_file_tags_tag_id", "file_tags", ["tag_id"])

    for statement in BACKFILL_STATEMENTS:
        op.execute(sa.text(statement))


def downgrade() -> None:
    op.drop_index("idx_file_tags_tag_id", table_name="file_tags")
    op.drop_index("idx_file_tags_file_id", table_name="file_tags")
    op.drop_table("file_tags")
    op.drop_index("idx_tags_enabled", table_name="tags")
    op.drop_index("uq_tags_name", table_name="tags")
    op.drop_table("tags")
