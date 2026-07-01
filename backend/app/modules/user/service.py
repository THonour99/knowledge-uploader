from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_admin_audit_log
from app.core.outbox import OutboxRepository
from app.modules.user.events import USER_PASSWORD_RESET_REQUESTED
from app.modules.user.models import User
from app.modules.user.repository import UserRepository, UserWithStats
from app.modules.user.schemas import AdminUserItem, AdminUserListResponse, AuthUserRecord


class UserNotFoundError(Exception):
    pass


class UserPermissionError(Exception):
    pass


class UserStateError(Exception):
    """Raised when a requested operation is invalid given the user's current state."""


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

    async def list_users_paginated(
        self,
        *,
        actor: AuthUserRecord,
        ip_address: str,
        user_agent: str,
        page: int,
        page_size: int,
        search: str | None,
        role: str | None,
        status: str | None,
    ) -> AdminUserListResponse:
        rows, total = await self._repository.list_users_with_stats(
            page=page,
            page_size=page_size,
            search=search,
            role=role,
            status=status,
        )
        await record_admin_audit_log(
            self._session,
            actor_id=actor.id,
            action="user.list",
            target_type="user_collection",
            target_id=actor.id,
            ip_address=ip_address,
            user_agent=user_agent,
            metadata_json={
                "result_count": len(rows),
                "total": total,
                "page": page,
                "page_size": page_size,
            },
        )
        await self._session.commit()

        items = [_make_admin_item(row) for row in rows]
        return AdminUserListResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
        )

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

    async def change_user_role(
        self,
        *,
        actor: AuthUserRecord,
        target_id: uuid.UUID,
        new_role: str,
        ip_address: str,
        user_agent: str,
    ) -> User:
        target = await self.get_user(target_id)

        if actor.id == target.id:
            raise UserPermissionError("cannot change own role")

        if target.role == "system_admin" and new_role != "system_admin":
            active_admins = await self._repository.count_active_system_admins()
            if active_admins <= 1:
                raise UserPermissionError("cannot demote the last active system_admin")

        old_role = target.role
        cleared_departments: list[uuid.UUID] = []
        if new_role != "dept_admin":
            cleared_departments = await self._repository.clear_managed_departments(target.id)
        target.role = new_role
        await record_admin_audit_log(
            self._session,
            actor_id=actor.id,
            action="user.role.change",
            target_type="user",
            target_id=target.id,
            ip_address=ip_address,
            user_agent=user_agent,
            metadata_json={
                "target_email": target.email,
                "old_role": old_role,
                "new_role": new_role,
                "cleared_managed_department_ids": [
                    str(department_id) for department_id in sorted(cleared_departments)
                ],
            },
        )
        await self._session.commit()
        return target

    async def set_user_department(
        self,
        *,
        actor: AuthUserRecord,
        target_id: uuid.UUID,
        department_id: uuid.UUID,
        ip_address: str,
        user_agent: str,
    ) -> User:
        target = await self.get_user(target_id)
        department = await self._repository.get_active_department_info(department_id)
        if department is None:
            raise UserStateError("department not found or disabled")
        before = {
            "department_id": str(target.department_id),
            "department": target.department,
            "department_name": getattr(target, "department_name", None),
            "department_code": getattr(target, "department_code", None),
        }
        await self._repository.set_user_department(target, department)
        await record_admin_audit_log(
            self._session,
            actor_id=actor.id,
            action="user.department.change",
            target_type="user",
            target_id=target.id,
            ip_address=ip_address,
            user_agent=user_agent,
            metadata_json={
                "target_email": target.email,
                "before": before,
                "after": {
                    "department_id": str(department.id),
                    "department": department.name,
                    "department_name": department.name,
                    "department_code": department.code,
                },
            },
        )
        await self._session.commit()
        return target

    async def request_password_reset(
        self,
        *,
        actor: AuthUserRecord,
        target_id: uuid.UUID,
        ip_address: str,
        user_agent: str,
    ) -> None:
        target = await self.get_user(target_id)

        if target.status == "disabled":
            raise UserStateError("cannot reset password for a disabled user")

        await OutboxRepository(self._session).append(
            event_type=USER_PASSWORD_RESET_REQUESTED,
            aggregate_type="user",
            aggregate_id=str(target.id),
            payload={
                "user_id": str(target.id),
                "email": target.email,
                "name": target.name,
                "triggered_by_admin": str(actor.id),
            },
        )

        await record_admin_audit_log(
            self._session,
            actor_id=actor.id,
            action="user.password_reset.requested",
            target_type="user",
            target_id=target.id,
            ip_address=ip_address,
            user_agent=user_agent,
            metadata_json={"target_email": target.email},
        )
        await self._session.commit()


def role_rank(role: str) -> int:
    return {"employee": 0, "dept_admin": 1, "system_admin": 2}.get(role, -1)


def _make_admin_item(row: UserWithStats) -> AdminUserItem:
    return AdminUserItem(
        id=row.user.id,
        name=row.user.name,
        email=row.user.email,
        role=row.user.role,
        status=row.user.status,
        department_id=row.user.department_id,
        department_name=row.department_name,
        department_code=row.department_code,
        department=row.user.department,
        email_verified=row.user.email_verified,
        created_at=row.user.created_at,
        upload_count=row.upload_count,
        last_upload_at=row.last_upload_at,
    )
