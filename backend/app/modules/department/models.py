from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, event, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

UNASSIGNED_DEPARTMENT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


class Department(Base):
    __tablename__ = "departments"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'disabled')", name="ck_departments_status"),
        Index("uq_departments_name", "name", unique=True),
        Index("uq_departments_code", "code", unique=True),
        Index("idx_departments_status", "status"),
        Index("idx_departments_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="active")
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


@event.listens_for(Department.__table__, "after_create")
def _seed_unassigned_department(target: Any, connection: Any, **_: object) -> None:
    statement = (
        pg_insert(target)
        .values(
            id=UNASSIGNED_DEPARTMENT_ID,
            name="未分配",
            code="unassigned",
            status="active",
        )
        .on_conflict_do_nothing(index_elements=["id"])
    )
    connection.execute(statement)


class UserManagedDepartment(Base):
    __tablename__ = "user_managed_departments"
    __table_args__ = (Index("idx_user_managed_departments_department_id", "department_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    department_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("departments.id", ondelete="CASCADE"),
        primary_key=True,
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
