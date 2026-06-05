from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_admin_audit_log
from app.modules.user.models import User
from app.modules.user.schemas import AuthUserRecord

from .repository import UserRepository


class UserNotFoundError(Exception):
    pass


class UserPermissionError(Exception):
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

    async def list_users_for_admin(
        self,
        *,
        actor: AuthUserRecord,
        ip_address: str,
        user_agent: str,
    ) -> list[User]:
        users = await self.list_users()
        await record_admin_audit_log(
            self._session,
            actor_id=actor.id,
            action="user.list",
            target_type="user_collection",
            target_id=actor.id,
            ip_address=ip_address,
            user_agent=user_agent,
            metadata_json={"result_count": len(users)},
        )
        await self._session.commit()
        return users

    async def get_user(self, user_id: uuid.UUID) -> User:
        user = await self._repository.get_by_id(user_id)
        if user is None:
            raise UserNotFoundError
        return user

    async def get_user_for_admin(
        self,
        *,
        actor: AuthUserRecord,
        target_id: uuid.UUID,
        ip_address: str,
        user_agent: str,
    ) -> User:
        target = await self.get_user(target_id)
        await record_admin_audit_log(
            self._session,
            actor_id=actor.id,
            action="user.view",
            target_type="user",
            target_id=target.id,
            ip_address=ip_address,
            user_agent=user_agent,
            metadata_json={"target_email": target.email},
        )
        await self._session.commit()
        return target

    async def disable_user(
        self,
        *,
        actor: AuthUserRecord,
        target_id: uuid.UUID,
        ip_address: str,
        user_agent: str,
    ) -> User:
        target = await self.get_user(target_id)
        await self._ensure_can_disable(actor, target)
        target.status = "disabled"
        target.session_version += 1
        await record_admin_audit_log(
            self._session,
            actor_id=actor.id,
            action="user.disable",
            target_type="user",
            target_id=target.id,
            ip_address=ip_address,
            user_agent=user_agent,
            metadata_json={"target_email": target.email},
        )
        await self._session.commit()
        return target

    async def _ensure_can_disable(self, actor: AuthUserRecord, target: User) -> None:
        if actor.id == target.id:
            raise UserPermissionError
        if role_rank(actor.role) <= role_rank(target.role):
            raise UserPermissionError
        if target.role == "system_admin":
            active_system_admins = await self._repository.count_active_system_admins()
            if active_system_admins <= 1:
                raise UserPermissionError

    async def enable_user(
        self,
        *,
        actor: AuthUserRecord,
        target_id: uuid.UUID,
        ip_address: str,
        user_agent: str,
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
            user_agent=user_agent,
            metadata_json={"target_email": target.email},
        )
        await self._session.commit()
        return target


def role_rank(role: str) -> int:
    return {"employee": 0, "knowledge_admin": 1, "system_admin": 2}.get(role, -1)
