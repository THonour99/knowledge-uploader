"""add ai tables quality similarity

Revision ID: d2f6a7c8b9e0
Revises: b8c9d2e1f4a6
Create Date: 2026-06-15 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d2f6a7c8b9e0"
down_revision: str | None = "b8c9d2e1f4a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "document_analysis",
        sa.Column(
            "tables_json",
            postgresql.JSONB(astext_type=sa.Text()),  # type: ignore[no-untyped-call]
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "document_analysis",
        sa.Column("table_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("document_analysis", sa.Column("quality_score", sa.Integer(), nullable=True))
    op.add_column(
        "document_analysis",
        sa.Column(
            "quality_detail",
            postgresql.JSONB(astext_type=sa.Text()),  # type: ignore[no-untyped-call]
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "document_analysis",
        sa.Column(
            "similar_file_ids",
            postgresql.JSONB(astext_type=sa.Text()),  # type: ignore[no-untyped-call]
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_document_analysis_table_count_non_negative",
        "document_analysis",
        "table_count >= 0",
    )
    op.create_check_constraint(
        "ck_document_analysis_quality_score_range",
        "document_analysis",
        "quality_score IS NULL OR (quality_score >= 0 AND quality_score <= 100)",
    )

    op.add_column("files", sa.Column("simhash", sa.BigInteger(), nullable=True))
    op.add_column("files", sa.Column("simhash_band_0", sa.SmallInteger(), nullable=True))
    op.add_column("files", sa.Column("simhash_band_1", sa.SmallInteger(), nullable=True))
    op.add_column("files", sa.Column("simhash_band_2", sa.SmallInteger(), nullable=True))
    op.add_column("files", sa.Column("simhash_band_3", sa.SmallInteger(), nullable=True))
    op.create_index("idx_files_simhash", "files", ["simhash"])
    op.create_index("idx_files_simhash_band_0", "files", ["simhash_band_0"])
    op.create_index("idx_files_simhash_band_1", "files", ["simhash_band_1"])
    op.create_index("idx_files_simhash_band_2", "files", ["simhash_band_2"])
    op.create_index("idx_files_simhash_band_3", "files", ["simhash_band_3"])


def downgrade() -> None:
    op.drop_index("idx_files_simhash_band_3", table_name="files")
    op.drop_index("idx_files_simhash_band_2", table_name="files")
    op.drop_index("idx_files_simhash_band_1", table_name="files")
    op.drop_index("idx_files_simhash_band_0", table_name="files")
    op.drop_index("idx_files_simhash", table_name="files")
    op.drop_column("files", "simhash_band_3")
    op.drop_column("files", "simhash_band_2")
    op.drop_column("files", "simhash_band_1")
    op.drop_column("files", "simhash_band_0")
    op.drop_column("files", "simhash")

    op.drop_constraint(
        "ck_document_analysis_quality_score_range",
        "document_analysis",
        type_="check",
    )
    op.drop_constraint(
        "ck_document_analysis_table_count_non_negative",
        "document_analysis",
        type_="check",
    )
    op.drop_column("document_analysis", "similar_file_ids")
    op.drop_column("document_analysis", "quality_detail")
    op.drop_column("document_analysis", "quality_score")
    op.drop_column("document_analysis", "table_count")
    op.drop_column("document_analysis", "tables_json")
