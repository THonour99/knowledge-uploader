from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_admin_audit_log
from app.modules.user.models import User
from app.modules.user.repository import UserRepository


class UserNotFoundError(Exception):
    pass


class UserService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        repository: UserRepository,
    ) -> None:
        self._session = session
        self._repository = repository

    @classmethod
    def from_session(cls, session: AsyncSession) -> UserService:
        return cls(
            session=session,
            repository=UserRepository(session),
        )

    async def list_users(self) -> list[User]:
        return await self._repository.list_users()

    async def get_user(self, user_id: uuid.UUID) -> User:
        user = await self._repository.get_by_id(user_id)
        if user is None:
            raise UserNotFoundError
        return user

    async def disable_user(
        self,
        *,
        actor: User,
        target_id: uuid.UUID,
        ip_address: str | None,
    ) -> User:
        target = await self.get_user(target_id)
        target.status = "disabled"
        await record_admin_audit_log(
            self._session,
            actor_id=actor.id,
            action="user.disable",
            target_type="user",
            target_id=target.id,
            ip_address=ip_address,
            metadata_json={"target_email": target.email},
        )
        await self._session.commit()
        return target

    async def enable_user(
        self,
        *,
        actor: User,
        target_id: uuid.UUID,
        ip_address: str | None,
    ) -> User:
        target = await self.get_user(target_id)
        target.status = "active" if target.email_verified else "pending_email_verification"
        await record_admin_audit_log(
            self._session,
            actor_id=actor.id,
            action="user.enable",
            target_type="user",
            target_id=target.id,
            ip_address=ip_address,
            metadata_json={"target_email": target.email},
        )
        await self._session.commit()
        return target
