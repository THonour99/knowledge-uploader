from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('employee', 'dept_admin', 'system_admin')",
            name="ck_users_role",
        ),
        CheckConstraint(
            "status IN ('pending_email_verification', 'active', 'disabled', 'locked')",
            name="ck_users_status",
        ),
        CheckConstraint(
            "auth_provider IN ('local', 'dingtalk', 'external')",
            name="ck_users_auth_provider",
        ),
        CheckConstraint("email = lower(email)", name="ck_users_email_lowercase"),
        CheckConstraint(
            "email_domain = lower(email_domain)", name="ck_users_email_domain_lowercase"
        ),
        CheckConstraint(
            "failed_login_count >= 0",
            name="ck_users_failed_login_count_non_negative",
        ),
        CheckConstraint(
            "session_version >= 0",
            name="ck_users_session_version_non_negative",
        ),
        Index("uq_users_email", "email", unique=True),
        Index("idx_users_email_domain", "email_domain"),
        Index("idx_users_department_id", "department_id"),
        Index("idx_users_department_role_status", "department_id", "role", "status"),
        Index("idx_users_role_status", "role", "status"),
        Index("idx_users_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    email_domain: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    department_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("departments.id", ondelete="RESTRICT"),
        nullable=False,
        server_default="00000000-0000-0000-0000-000000000001",
    )
    department: Mapped[str | None] = mapped_column(String(100))
    phone: Mapped[str | None] = mapped_column(String(40))
    role: Mapped[str] = mapped_column(String(40), nullable=False, server_default="employee")
    status: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        server_default="pending_email_verification",
    )
    email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    auth_provider: Mapped[str] = mapped_column(String(40), nullable=False, server_default="local")
    external_user_id: Mapped[str | None] = mapped_column(String(120))
    ding_user_id: Mapped[str | None] = mapped_column(String(120))
    employee_no: Mapped[str | None] = mapped_column(String(80))
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    session_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_ip: Mapped[str | None] = mapped_column(String(45))
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
