from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated, Literal, Protocol

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.deps import get_current_user
from app.core.permissions import Role, permission_denied
from app.modules.user.schemas import AuthUserRecord

AccessScopeKind = Literal["all", "departments"]
CurrentUserDep = Annotated[AuthUserRecord, Depends(get_current_user)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]


class DepartmentScopeStore(Protocol):
    async def list_managed_department_ids(self, user_id: uuid.UUID) -> frozenset[uuid.UUID]: ...

    async def has_non_self_reviewer(
        self,
        *,
        file_department_id: uuid.UUID,
        uploader_id: uuid.UUID,
    ) -> bool: ...


@dataclass(frozen=True)
class DepartmentAccessScope:
    actor_id: uuid.UUID
    actor_role: str
    kind: AccessScopeKind
    department_ids: frozenset[uuid.UUID]

    @property
    def is_global(self) -> bool:
        return self.kind == "all"

    @property
    def is_empty(self) -> bool:
        return self.kind == "departments" and not self.department_ids

    def query_department_ids(self) -> frozenset[uuid.UUID] | None:
        if self.is_global:
            return None
        return self.department_ids

    def covers_department(self, department_id: uuid.UUID | None) -> bool:
        if self.is_global:
            return True
        if department_id is None:
            return False
        return department_id in self.department_ids

    def audit_metadata(self, *, file_department_id: uuid.UUID | None = None) -> dict[str, object]:
        return {
            "actor_role": self.actor_role,
            "actor_department_ids": [str(department_id) for department_id in self.department_ids],
            "scope_all_departments": self.is_global,
            "file_department_id": str(file_department_id)
            if file_department_id is not None
            else None,
        }


async def build_department_access_scope(
    *,
    current_user: AuthUserRecord,
    store: DepartmentScopeStore,
) -> DepartmentAccessScope:
    if current_user.role == Role.SYSTEM_ADMIN.value:
        return DepartmentAccessScope(
            actor_id=current_user.id,
            actor_role=current_user.role,
            kind="all",
            department_ids=frozenset(),
        )
    if current_user.role == Role.DEPT_ADMIN.value:
        return DepartmentAccessScope(
            actor_id=current_user.id,
            actor_role=current_user.role,
            kind="departments",
            department_ids=await store.list_managed_department_ids(current_user.id),
        )
    raise permission_denied()


def get_department_scope_store(session: AsyncSession) -> DepartmentScopeStore:
    from app.modules.department.identity import SqlDepartmentScopeStore

    return SqlDepartmentScopeStore(session)


async def get_scoped_admin(
    current_user: CurrentUserDep,
    session: SessionDep,
) -> DepartmentAccessScope:
    return await build_department_access_scope(
        current_user=current_user,
        store=get_department_scope_store(session),
    )


ScopedAdminDep = Annotated[DepartmentAccessScope, Depends(get_scoped_admin)]
