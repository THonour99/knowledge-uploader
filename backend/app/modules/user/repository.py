from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Column, DateTime, MetaData, Table, func, select
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.user.models import User

# ---------------------------------------------------------------------------
# Shadow table for files — read-only aggregate, no cross-module service import.
# Same pattern as audit/repository.py and document/repository.py.
# ---------------------------------------------------------------------------

_FILES = Table(
    "files",
    MetaData(),
    Column("id", PG_UUID(as_uuid=True), primary_key=True),
    Column("uploader_id", PG_UUID(as_uuid=True), nullable=False),
    Column("uploaded_at", DateTime(timezone=True), nullable=False),
)


@dataclass(frozen=True)
class UserWithStats:
    """User ORM instance together with upload statistics."""

    user: User
    upload_count: int
    last_upload_at: datetime | None


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        result = await self._session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def list_users(self) -> list[User]:
        result = await self._session.execute(select(User).order_by(User.created_at.desc()))
        return list(result.scalars())

    async def count_active_system_admins(self) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(User)
            .where(
                User.role == "system_admin",
                User.status == "active",
            )
        )
        return int(result.scalar_one())

    # ------------------------------------------------------------------
    # Paginated + filtered list with upload statistics
    # ------------------------------------------------------------------

    async def list_users_with_stats(
        self,
        *,
        page: int,
        page_size: int,
        search: str | None = None,
        role: str | None = None,
        status: str | None = None,
    ) -> tuple[list[UserWithStats], int]:
        """Return (rows, total_count) with per-user upload statistics.

        Uses a single LEFT JOIN + GROUP BY aggregate to avoid N+1 queries.
        """
        users_table = User.__table__

        # Subquery: per-uploader upload count and latest uploaded_at
        stats_sq = (
            select(
                _FILES.c.uploader_id.label("uploader_id"),
                func.count(_FILES.c.id).label("upload_count"),
                func.max(_FILES.c.uploaded_at).label("last_upload_at"),
            )
            .group_by(_FILES.c.uploader_id)
            .subquery("file_stats")
        )

        base = select(
            users_table,
            func.coalesce(stats_sq.c.upload_count, 0).label("upload_count"),
            stats_sq.c.last_upload_at.label("last_upload_at"),
        ).select_from(users_table.outerjoin(stats_sq, users_table.c.id == stats_sq.c.uploader_id))

        # Apply filters
        if search:
            pattern = f"%{search}%"
            base = base.where(User.name.ilike(pattern) | User.email.ilike(pattern))
        if role is not None:
            base = base.where(User.role == role)
        if status is not None:
            base = base.where(User.status == status)

        # Count total before pagination
        count_q = select(func.count()).select_from(base.subquery())
        total = int((await self._session.execute(count_q)).scalar_one())

        # Paginate
        offset = (page - 1) * page_size
        data_q = base.order_by(User.created_at.desc()).offset(offset).limit(page_size)
        rows = (await self._session.execute(data_q)).mappings().all()

        result: list[UserWithStats] = []
        for row in rows:
            user = User(
                id=row["id"],
                name=row["name"],
                email=row["email"],
                email_domain=row["email_domain"],
                password_hash=row["password_hash"],
                department=row["department"],
                phone=row["phone"],
                role=row["role"],
                status=row["status"],
                email_verified=row["email_verified"],
                auth_provider=row["auth_provider"],
                external_user_id=row["external_user_id"],
                ding_user_id=row["ding_user_id"],
                employee_no=row["employee_no"],
                failed_login_count=row["failed_login_count"],
                session_version=row["session_version"],
                locked_until=row["locked_until"],
                last_login_at=row["last_login_at"],
                last_login_ip=row["last_login_ip"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            result.append(
                UserWithStats(
                    user=user,
                    upload_count=int(row["upload_count"]),
                    last_upload_at=row["last_upload_at"],
                )
            )

        return result, total
