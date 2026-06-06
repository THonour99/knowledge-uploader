from __future__ import annotations

from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.permissions import AdminUserDep
from app.core.responses import success_response

from .exceptions import RagflowTaskError
from .repository import RagflowTaskRepository  # noqa: TID251 - same-module repository dependency
from .schemas import SyncTaskListResponse, SyncTaskLogResponse, SyncTaskResponse
from .service import (  # noqa: TID251 - same-module service dependency
    RagflowTaskService,
    RequestContext,
    SyncTaskBundle,
)

router = APIRouter(tags=["ragflow"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _service(session: AsyncSession) -> RagflowTaskService:
    return RagflowTaskService(session=session, repository=RagflowTaskRepository(session))


def _raise_ragflow_task_error(error: RagflowTaskError) -> NoReturn:
    raise HTTPException(
        status_code=error.status_code,
        detail={"error_code": error.error_code, "message": error.message},
    )


def _context_from(request: Request) -> RequestContext:
    client_host = request.client.host if request.client is not None else ""
    ip_address = client_host.strip()[:45] or "unknown"
    user_agent = request.headers.get("user-agent", "").strip()[:512] or "unknown"
    return RequestContext(ip_address=ip_address, user_agent=user_agent)


def _task_response(bundle: SyncTaskBundle) -> SyncTaskResponse:
    task = bundle.task
    return SyncTaskResponse(
        id=task.id,
        file_id=task.file_id,
        task_type=task.task_type,
        status=task.status,
        retry_count=task.retry_count,
        max_retry_count=task.max_retry_count,
        error_message=task.error_message,
        started_at=task.started_at,
        finished_at=task.finished_at,
        created_at=task.created_at,
        updated_at=task.updated_at,
        logs=[
            SyncTaskLogResponse(
                id=log.id,
                task_id=log.task_id,
                status=log.status,
                message=log.message,
                created_at=log.created_at,
            )
            for log in bundle.logs
        ],
    )


@router.get("/api/tasks")
async def list_tasks(
    request: Request,
    current_user: AdminUserDep,
    session: SessionDep,
) -> dict[str, object]:
    try:
        tasks = await _service(session).list_tasks(
            current_user=current_user,
            context=_context_from(request),
        )
    except RagflowTaskError as error:
        _raise_ragflow_task_error(error)
    response = SyncTaskListResponse(
        items=[_task_response(task) for task in tasks],
        total=len(tasks),
    )
    return success_response(response.model_dump(mode="json"), request)


@router.get("/api/tasks/{task_id}")
async def get_task(
    task_id: UUID,
    request: Request,
    current_user: AdminUserDep,
    session: SessionDep,
) -> dict[str, object]:
    try:
        task = await _service(session).get_task(
            current_user=current_user,
            task_id=task_id,
            context=_context_from(request),
        )
    except RagflowTaskError as error:
        _raise_ragflow_task_error(error)
    return success_response(_task_response(task).model_dump(mode="json"), request)


@router.post("/api/tasks/{task_id}/retry")
async def retry_task(
    task_id: UUID,
    request: Request,
    current_user: AdminUserDep,
    session: SessionDep,
) -> dict[str, object]:
    try:
        task = await _service(session).retry_task(
            current_user=current_user,
            task_id=task_id,
            context=_context_from(request),
        )
    except RagflowTaskError as error:
        _raise_ragflow_task_error(error)
    return success_response(_task_response(task).model_dump(mode="json"), request)


@router.post("/api/tasks/{task_id}/cancel")
async def cancel_task(
    task_id: UUID,
    request: Request,
    current_user: AdminUserDep,
    session: SessionDep,
) -> dict[str, object]:
    try:
        task = await _service(session).cancel_task(
            current_user=current_user,
            task_id=task_id,
            context=_context_from(request),
        )
    except RagflowTaskError as error:
        _raise_ragflow_task_error(error)
    return success_response(_task_response(task).model_dump(mode="json"), request)
