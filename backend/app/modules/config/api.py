from __future__ import annotations

from typing import Annotated, Literal, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.responses import success_response

from .dlq_service import DeadLetterService
from .exceptions import ConfigError
from .permissions import SystemAdminDep
from .rabbitmq_dlq_service import RabbitDeadLetterService
from .repository import ConfigRepository  # noqa: TID251 - same-module repository dependency
from .schemas import (
    ConfigUpdateRequest,
    DeadLetterReplayRequest,
    RabbitDeadLetterReplayRequest,
)
from .service import (  # noqa: TID251 - same-module service dependency
    ConfigService,
    RequestContext,
)

router = APIRouter(tags=["config"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _service(session: AsyncSession) -> ConfigService:
    return ConfigService(session=session, repository=ConfigRepository(session))


def _dead_letter_service(session: AsyncSession) -> DeadLetterService:
    return DeadLetterService(session=session)


def _rabbit_dead_letter_service(session: AsyncSession) -> RabbitDeadLetterService:
    return RabbitDeadLetterService(session=session)


def _raise_config_error(error: ConfigError) -> NoReturn:
    raise HTTPException(
        status_code=error.status_code,
        detail={"error_code": error.error_code, "message": error.message},
    )


def _context_from(request: Request) -> RequestContext:
    client_host = request.client.host if request.client is not None else ""
    ip_address = client_host.strip()[:45] or "unknown"
    user_agent = request.headers.get("user-agent", "").strip()[:512] or "unknown"
    return RequestContext(
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.get("/api/admin/configs")
async def get_configs(
    request: Request,
    current_user: SystemAdminDep,
    session: SessionDep,
    group: Annotated[str, Query(min_length=1)],
) -> dict[str, object]:
    try:
        response = await _service(session).get_group(
            group=group,
            current_user=current_user,
            context=_context_from(request),
        )
    except ConfigError as error:
        _raise_config_error(error)
    return success_response(response.model_dump(mode="json"), request)


@router.put("/api/admin/configs/{group}")
async def update_configs(
    group: str,
    payload: ConfigUpdateRequest,
    request: Request,
    current_user: SystemAdminDep,
    session: SessionDep,
) -> dict[str, object]:
    try:
        response = await _service(session).update_group(
            group=group,
            items=payload.items,
            current_user=current_user,
            context=_context_from(request),
        )
    except ConfigError as error:
        _raise_config_error(error)
    return success_response(response.model_dump(mode="json"), request)


@router.get("/api/admin/outbox/dead-letters")
async def list_outbox_dead_letters(
    request: Request,
    current_user: SystemAdminDep,
    session: SessionDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status_filter: Annotated[
        Literal["pending", "requeued", "resolved"] | None,
        Query(alias="status"),
    ] = None,
) -> dict[str, object]:
    try:
        response = await _dead_letter_service(session).list_dead_letters(
            page=page,
            page_size=page_size,
            status=status_filter,
            current_user=current_user,
            context=_context_from(request),
        )
    except ConfigError as error:
        _raise_config_error(error)
    return success_response(response.model_dump(mode="json"), request)


@router.get("/api/admin/outbox/dead-letters/{dead_letter_id}")
async def get_outbox_dead_letter(
    dead_letter_id: UUID,
    request: Request,
    current_user: SystemAdminDep,
    session: SessionDep,
) -> dict[str, object]:
    try:
        response = await _dead_letter_service(session).get_dead_letter(
            dead_letter_id=dead_letter_id,
            current_user=current_user,
            context=_context_from(request),
        )
    except ConfigError as error:
        _raise_config_error(error)
    return success_response(response.model_dump(mode="json"), request)


@router.post("/api/admin/outbox/dead-letters/{dead_letter_id}/replay")
async def replay_outbox_dead_letter(
    dead_letter_id: UUID,
    payload: DeadLetterReplayRequest,
    request: Request,
    current_user: SystemAdminDep,
    session: SessionDep,
) -> dict[str, object]:
    try:
        response = await _dead_letter_service(session).replay_dead_letter(
            dead_letter_id=dead_letter_id,
            reason=payload.reason,
            current_user=current_user,
            context=_context_from(request),
        )
    except ConfigError as error:
        _raise_config_error(error)
    return success_response(response.model_dump(mode="json"), request)


@router.post("/api/admin/rabbitmq/dead-letters/{queue_name}/replay-next")
async def replay_next_rabbitmq_dead_letter(
    queue_name: str,
    payload: RabbitDeadLetterReplayRequest,
    request: Request,
    current_user: SystemAdminDep,
    session: SessionDep,
) -> dict[str, object]:
    try:
        response = await _rabbit_dead_letter_service(session).replay_next(
            queue_name=queue_name,
            reason=payload.reason,
            current_user=current_user,
            context=_context_from(request),
        )
    except ConfigError as error:
        _raise_config_error(error)
    return success_response(response.model_dump(mode="json"), request)
