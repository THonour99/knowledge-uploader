from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from app.core.database import get_session
from app.core.exceptions import ErrorCode
from app.core.permissions import SystemAdminDep
from app.core.responses import success_response

from .repository import AuditRepository
from .schemas import AuditLogListResponse
from .service import AuditPermissionError, AuditService

router = APIRouter(prefix="/api/admin", tags=["audit"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _service(session: AsyncSession) -> AuditService:
    return AuditService(repository=AuditRepository(session))


@router.get("/audit-logs", response_model=None)
async def list_audit_logs(
    request: Request,
    session: SessionDep,
    current_user: SystemAdminDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    actor_id: Annotated[uuid.UUID | None, Query()] = None,
    action: Annotated[str | None, Query()] = None,
    target_type: Annotated[str | None, Query()] = None,
    created_from: Annotated[datetime | None, Query()] = None,
    created_to: Annotated[datetime | None, Query()] = None,
) -> dict[str, object]:
    """Query audit logs.

    Accessible by system_admin only.

    NOTE: This endpoint does NOT produce an audit log entry for the read
    operation — see AuditService.search_logs for the rationale.
    """
    svc = _service(session)
    try:
        result: AuditLogListResponse = await svc.search_logs(
            caller_role=current_user.role,
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            created_from=created_from,
            created_to=created_to,
            page=page,
            page_size=page_size,
        )
    except AuditPermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": ErrorCode.PERMISSION_DENIED, "message": str(exc)},
        ) from exc

    return success_response(result.model_dump(), request)
