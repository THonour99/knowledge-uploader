from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.user.models import User


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
            select(func.count()).select_from(User).where(
                User.role == "system_admin",
                User.status == "active",
            )
        )
        return int(result.scalar_one())
