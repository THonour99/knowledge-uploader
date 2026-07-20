from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Announcement(Base):
    __tablename__ = "announcements"
    __table_args__ = (
        CheckConstraint(
            "audience_type IN ('all', 'departments', 'roles')",
            name="ck_announcements_audience_type",
        ),
        CheckConstraint(
            "lifecycle_state IN ('draft', 'released', 'withdrawn')",
            name="ck_announcements_lifecycle_state",
        ),
        CheckConstraint(
            "char_length(btrim(title)) BETWEEN 1 AND 200", name="ck_announcements_title"
        ),
        CheckConstraint(
            "char_length(btrim(body_markdown)) BETWEEN 1 AND 50000",
            name="ck_announcements_body_markdown",
        ),
        CheckConstraint("row_version >= 1", name="ck_announcements_row_version"),
        CheckConstraint(
            "expires_at IS NULL OR (visible_from IS NOT NULL AND expires_at > visible_from)",
            name="ck_announcements_time_window",
        ),
        CheckConstraint(
            "lifecycle_state = 'draft' OR visible_from IS NOT NULL",
            name="ck_announcements_released_visible_from",
        ),
        Index("idx_announcements_public_window", "lifecycle_state", "visible_from", "expires_at"),
        Index("idx_announcements_pinned", "is_pinned", "visible_from"),
        Index("idx_announcements_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    audience_type: Mapped[str] = mapped_column(String(20), nullable=False, server_default="all")
    lifecycle_state: Mapped[str] = mapped_column(String(20), nullable=False, server_default="draft")
    visible_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    updated_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    published_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    withdrawn_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    withdraw_reason: Mapped[str | None] = mapped_column(String(500))
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    departments: Mapped[list[AnnouncementDepartment]] = relationship(
        back_populates="announcement", cascade="all, delete-orphan", lazy="selectin"
    )
    roles: Mapped[list[AnnouncementRole]] = relationship(
        back_populates="announcement", cascade="all, delete-orphan", lazy="selectin"
    )


class AnnouncementDepartment(Base):
    __tablename__ = "announcement_departments"
    __table_args__ = (Index("idx_announcement_departments_department_id", "department_id"),)

    announcement_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("announcements.id", ondelete="CASCADE"), primary_key=True
    )
    department_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("departments.id", ondelete="RESTRICT"), primary_key=True
    )
    announcement: Mapped[Announcement] = relationship(back_populates="departments")


class AnnouncementRole(Base):
    __tablename__ = "announcement_roles"
    __table_args__ = (
        CheckConstraint(
            "role IN ('employee', 'dept_admin', 'system_admin')",
            name="ck_announcement_roles_role",
        ),
        Index("idx_announcement_roles_role", "role"),
    )

    announcement_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("announcements.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(40), primary_key=True)
    announcement: Mapped[Announcement] = relationship(back_populates="roles")


class AnnouncementRead(Base):
    __tablename__ = "announcement_reads"
    __table_args__ = (Index("idx_announcement_reads_user_id", "user_id", "read_at"),)

    announcement_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("announcements.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    read_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
