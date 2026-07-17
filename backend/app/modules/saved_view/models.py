from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    func,
)
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SavedView(Base):
    __tablename__ = "saved_views"
    __table_args__ = (
        CheckConstraint(
            "scope IN ('private', 'department')",
            name="ck_saved_views_scope",
        ),
        CheckConstraint(
            "page_key IN ('my_files', 'review_files', 'task_logs', 'statistics')",
            name="ck_saved_views_page_key",
        ),
        CheckConstraint(
            "scope = 'private' OR page_key IN ('review_files', 'task_logs')",
            name="ck_saved_views_department_page_scope",
        ),
        CheckConstraint(
            "(scope = 'private' AND department_id IS NULL) OR "
            "(scope = 'department' AND department_id IS NOT NULL)",
            name="ck_saved_views_scope_department",
        ),
        CheckConstraint(
            "definition_schema_version > 0",
            name="ck_saved_views_schema_version_positive",
        ),
        CheckConstraint("row_version > 0", name="ck_saved_views_row_version_positive"),
        CheckConstraint(
            "length(btrim(name)) BETWEEN 1 AND 80",
            name="ck_saved_views_name_length",
        ),
        CheckConstraint(
            "jsonb_typeof(query_definition) = 'object'",
            name="ck_saved_views_query_definition_object",
        ),
        CheckConstraint(
            "jsonb_typeof(column_preferences) = 'object'",
            name="ck_saved_views_column_preferences_object",
        ),
        CheckConstraint(
            "octet_length(query_definition::text) <= 8192",
            name="ck_saved_views_query_definition_size",
        ),
        CheckConstraint(
            "octet_length(column_preferences::text) <= 4096",
            name="ck_saved_views_column_preferences_size",
        ),
        Index("idx_saved_views_owner_page", "owner_id", "page_key"),
        Index("idx_saved_views_department_page", "department_id", "page_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    scope: Mapped[str] = mapped_column(String(20), nullable=False)
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("departments.id", ondelete="RESTRICT"),
    )
    page_key: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    definition_schema_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    query_definition: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    column_preferences: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    row_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="1",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


Index(
    "uq_saved_views_private_name",
    SavedView.owner_id,
    SavedView.page_key,
    func.lower(SavedView.name),
    unique=True,
    postgresql_where=sql_text("scope = 'private'"),
)
Index(
    "uq_saved_views_department_name",
    SavedView.department_id,
    SavedView.page_key,
    func.lower(SavedView.name),
    unique=True,
    postgresql_where=sql_text("scope = 'department'"),
)
