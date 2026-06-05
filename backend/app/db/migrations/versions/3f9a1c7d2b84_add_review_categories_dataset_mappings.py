"""add review categories and dataset mappings

Revision ID: 3f9a1c7d2b84
Revises: 6d8f2a4c1e90
Create Date: 2026-06-05 15:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "3f9a1c7d2b84"
down_revision: str | None = "6d8f2a4c1e90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "categories",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("require_review", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("default_dataset_id", sa.String(length=120), nullable=True),
        sa.Column("allow_employee_select", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("allow_ai_recommend", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "default_visibility",
            sa.String(length=20),
            server_default="private",
            nullable=False,
        ),
        sa.Column(
            "keywords",
            postgresql.JSONB(astext_type=sa.Text()),  # type: ignore[no-untyped-call]
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("classification_prompt", sa.Text(), nullable=True),
        sa.Column("ai_analysis_enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "sensitive_detection_enabled",
            sa.Boolean(),
            server_default="true",
            nullable=False,
        ),
        sa.Column("auto_sync_enabled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "default_visibility IN ('private', 'department', 'company')",
            name="ck_categories_default_visibility",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["categories.id"],
            name="fk_categories_parent_id_categories",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_categories_parent_id", "categories", ["parent_id"], unique=False)
    op.create_index("uq_categories_code", "categories", ["code"], unique=True)

    op.create_table(
        "dataset_mappings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("category_id", sa.Uuid(), nullable=False),
        sa.Column("ragflow_dataset_id", sa.String(length=120), nullable=False),
        sa.Column("ragflow_dataset_name", sa.String(length=120), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["categories.id"],
            name="fk_dataset_mappings_category_id_categories",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_dataset_mappings_category_id",
        "dataset_mappings",
        ["category_id"],
        unique=False,
    )
    op.create_index(
        "idx_dataset_mappings_enabled",
        "dataset_mappings",
        ["enabled"],
        unique=False,
    )
    op.create_index(
        "uq_dataset_mappings_ragflow_dataset_id",
        "dataset_mappings",
        ["ragflow_dataset_id"],
        unique=True,
    )
    op.create_index("idx_files_category_id", "files", ["category_id"], unique=False)
    op.create_index(
        "idx_files_dataset_mapping_id",
        "files",
        ["dataset_mapping_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_files_category_id_categories",
        "files",
        "categories",
        ["category_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_files_dataset_mapping_id_dataset_mappings",
        "files",
        "dataset_mappings",
        ["dataset_mapping_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_files_dataset_mapping_id_dataset_mappings", "files", type_="foreignkey")
    op.drop_constraint("fk_files_category_id_categories", "files", type_="foreignkey")
    op.drop_index("idx_files_dataset_mapping_id", table_name="files")
    op.drop_index("idx_files_category_id", table_name="files")
    op.drop_index("uq_dataset_mappings_ragflow_dataset_id", table_name="dataset_mappings")
    op.drop_index("idx_dataset_mappings_enabled", table_name="dataset_mappings")
    op.drop_index("idx_dataset_mappings_category_id", table_name="dataset_mappings")
    op.drop_table("dataset_mappings")
    op.drop_index("uq_categories_code", table_name="categories")
    op.drop_index("idx_categories_parent_id", table_name="categories")
    op.drop_table("categories")
