from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from app.core.audit import record_admin_audit_log
from app.core.exceptions import ErrorCode
from app.modules.department.models import UNASSIGNED_DEPARTMENT_ID, Department
from app.modules.department.schemas import DepartmentListResponse, DepartmentResponse
from app.modules.user.schemas import AuthUserRecord

from . import exceptions
from .repository import DepartmentRepository


@dataclass(frozen=True)
class RequestContext:
    ip_address: str
    user_agent: str


def _clean_required_text(value: str, field_name: str) -> str:
    clean_value = value.strip()
    if not clean_value:
        raise exceptions.DepartmentError(
            ErrorCode.VALIDATION_ERROR,
            f"department {field_name} cannot be blank",
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    return clean_value


class DepartmentService:
    def __init__(self, *, session: AsyncSession, repository: DepartmentRepository) -> None:
        self._session = session
        self._repository = repository

    async def list_departments(
        self,
        *,
        actor: AuthUserRecord,
        page: int,
        page_size: int,
        search: str | None,
        status: str | None,
        context: RequestContext,
    ) -> DepartmentListResponse:
        items, total = await self._repository.list_departments(
            page=page,
            page_size=page_size,
            search=search,
            status=status,
        )
        await self._audit(
            actor=actor,
            action="department.list",
            target_type="department_collection",
            target_id=actor.id,
            context=context,
            metadata_json={"result_count": len(items), "total": total, "page": page},
        )
        await self._session.commit()
        return DepartmentListResponse(
            items=[DepartmentResponse.model_validate(item) for item in items],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def get_department(
        self,
        *,
        actor: AuthUserRecord,
        department_id: uuid.UUID,
        context: RequestContext,
    ) -> Department:
        department = await self._get_department_or_raise(department_id)
        await self._audit(
            actor=actor,
            action="department.view",
            target_type="department",
            target_id=department.id,
            context=context,
            metadata_json={"department_code": department.code},
        )
        await self._session.commit()
        return department

    async def create_department(
        self,
        *,
        actor: AuthUserRecord,
        name: str,
        code: str,
        context: RequestContext,
    ) -> Department:
        clean_name = _clean_required_text(name, "name")
        clean_code = _clean_required_text(code, "code")
        if await self._repository.get_by_name(clean_name) is not None:
            raise exceptions.name_conflict()
        if await self._repository.get_by_code(clean_code) is not None:
            raise exceptions.code_conflict()
        department = await self._repository.add_department(
            Department(name=clean_name, code=clean_code, status="active")
        )
        await self._audit(
            actor=actor,
            action="department.create",
            target_type="department",
            target_id=department.id,
            context=context,
            metadata_json={"name": department.name, "code": department.code},
        )
        await self._session.commit()
        await self._session.refresh(department)
        return department

    async def update_department(
        self,
        *,
        actor: AuthUserRecord,
        department_id: uuid.UUID,
        name: str | None,
        status: str | None,
        context: RequestContext,
    ) -> Department:
        department = await self._get_department_or_raise(department_id)
        if department.id == UNASSIGNED_DEPARTMENT_ID and status == "disabled":
            raise exceptions.unassigned_immutable()
        before = {"name": department.name, "code": department.code, "status": department.status}
        if name is not None:
            clean_name = _clean_required_text(name, "name")
            existing = await self._repository.get_by_name(clean_name)
            if existing is not None and existing.id != department.id:
                raise exceptions.name_conflict()
            department.name = clean_name
        if status is not None:
            department.status = status
        await self._audit(
            actor=actor,
            action="department.update",
            target_type="department",
            target_id=department.id,
            context=context,
            metadata_json={
                "before": before,
                "after": {
                    "name": department.name,
                    "code": department.code,
                    "status": department.status,
                },
            },
        )
        await self._session.commit()
        await self._session.refresh(department)
        return department

    async def disable_department(
        self,
        *,
        actor: AuthUserRecord,
        department_id: uuid.UUID,
        context: RequestContext,
    ) -> None:
        department = await self._get_department_or_raise(department_id)
        if department.id == UNASSIGNED_DEPARTMENT_ID:
            raise exceptions.unassigned_immutable()
        old_status = department.status
        department.status = "disabled"
        await self._audit(
            actor=actor,
            action="department.disable",
            target_type="department",
            target_id=department.id,
            context=context,
            metadata_json={"old_status": old_status, "new_status": department.status},
        )
        await self._session.commit()

    async def get_managed_departments(
        self,
        *,
        actor: AuthUserRecord,
        user_id: uuid.UUID,
        context: RequestContext,
    ) -> list[Department]:
        if await self._repository.get_user_role(user_id) is None:
            raise exceptions.user_not_found()
        departments = await self._repository.list_managed_departments(user_id)
        await self._audit(
            actor=actor,
            action="user.managed_departments.view",
            target_type="user",
            target_id=user_id,
            context=context,
            metadata_json={
                "result_count": len(departments),
                "department_ids": [str(item.id) for item in departments],
            },
        )
        await self._session.commit()
        return departments

    async def replace_managed_departments(
        self,
        *,
        actor: AuthUserRecord,
        user_id: uuid.UUID,
        department_ids: list[uuid.UUID],
        context: RequestContext,
    ) -> list[Department]:
        role = await self._repository.get_user_role(user_id)
        if role is None:
            raise exceptions.user_not_found()
        if role != "dept_admin":
            raise exceptions.managed_departments_require_dept_admin()
        requested = set(department_ids)
        if UNASSIGNED_DEPARTMENT_ID in requested:
            raise exceptions.unassigned_immutable()
        active = await self._repository.get_active_departments_by_ids(requested)
        active_ids = {department.id for department in active}
        if active_ids != requested:
            raise exceptions.department_disabled()
        before = await self._repository.list_managed_department_ids(user_id)
        await self._repository.replace_managed_departments(
            user_id=user_id, department_ids=requested
        )
        after = await self._repository.list_managed_departments(user_id)
        after_ids = {department.id for department in after}
        await self._audit(
            actor=actor,
            action="user.managed_departments.replace",
            target_type="user",
            target_id=user_id,
            context=context,
            metadata_json={
                "target_role": role,
                "before_department_ids": [str(item) for item in sorted(before)],
                "after_department_ids": [str(item) for item in sorted(after_ids)],
                "added_department_ids": [str(item) for item in sorted(after_ids - before)],
                "removed_department_ids": [str(item) for item in sorted(before - after_ids)],
            },
        )
        await self._session.commit()
        return after

    async def clear_managed_departments_for_user(self, user_id: uuid.UUID) -> list[uuid.UUID]:
        return await self._repository.clear_managed_departments(user_id)

    async def _get_department_or_raise(self, department_id: uuid.UUID) -> Department:
        department = await self._repository.get_department(department_id)
        if department is None:
            raise exceptions.department_not_found()
        return department

    async def _audit(
        self,
        *,
        actor: AuthUserRecord,
        action: str,
        target_type: str,
        target_id: uuid.UUID,
        context: RequestContext,
        metadata_json: dict[str, object] | None = None,
    ) -> None:
        await record_admin_audit_log(
            self._session,
            actor_id=actor.id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            ip_address=context.ip_address,
            user_agent=context.user_agent,
            metadata_json=metadata_json,
        )
