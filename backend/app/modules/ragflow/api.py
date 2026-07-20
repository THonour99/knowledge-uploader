from __future__ import annotations

import time
from typing import Annotated, Literal, NoReturn
from uuid import NAMESPACE_URL, UUID, uuid5

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.ragflow.base import RagflowClientError
from app.adapters.ragflow.http import HttpRagflowClient, redact_secret
from app.adapters.ragflow.instrumented import InstrumentedRagflowClient
from app.core.access_scope import ScopedAdminDep
from app.core.audit import record_admin_audit_log
from app.core.config import approved_ragflow_base_url, get_settings
from app.core.database import get_session
from app.core.deps import get_current_user
from app.core.permissions import SystemAdminDep
from app.core.ragflow_endpoint import (
    ragflow_endpoint_identity,
    ragflow_tls_spki_pins_for_endpoint,
)
from app.core.ragflow_runtime import resolve_ragflow_runtime_settings
from app.core.responses import success_response
from app.modules.user.schemas import AuthUserRecord

from .exceptions import RagflowTaskError
from .repository import RagflowTaskRepository  # noqa: TID251 - same-module repository dependency
from .schemas import (
    ManualSyncRequest,
    RagflowDatasetDiscoveryRequest,
    RagflowDatasetDiscoveryResponse,
    RagflowDatasetOptionResponse,
    SyncTaskListResponse,
    SyncTaskLogResponse,
    SyncTaskResponse,
    SyncTaskStatusCountsResponse,
    VersionSwitchReconcileRequest,
)
from .service import (  # noqa: TID251 - same-module service dependency
    RagflowTaskService,
    RequestContext,
    SyncTaskBundle,
)

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["ragflow"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
CurrentUserDep = Annotated[AuthUserRecord, Depends(get_current_user)]


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
    current_user: CurrentUserDep,
    scope: ScopedAdminDep,
    session: SessionDep,
    file_id: UUID | None = None,
    task_type: Annotated[
        Literal["ragflow_upload", "ragflow_parse", "ragflow_status_check", "ragflow_delete"] | None,
        Query(),
    ] = None,
    status: Annotated[
        Literal["queued", "running", "succeeded", "failed", "canceled"] | None,
        Query(),
    ] = None,
    department_id: UUID | None = None,
    sort: Annotated[
        Literal["created_at", "updated_at", "started_at", "finished_at"], Query()
    ] = "created_at",
    order: Annotated[Literal["asc", "desc"], Query()] = "desc",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, object]:
    try:
        tasks, total, status_counts = await _service(session).list_tasks(
            current_user=current_user,
            scope=scope,
            context=_context_from(request),
            file_id=file_id,
            task_type=task_type,
            status=status,
            department_id=department_id,
            sort=sort,
            order=order,
            page=page,
            page_size=page_size,
        )
    except RagflowTaskError as error:
        _raise_ragflow_task_error(error)
    response = SyncTaskListResponse(
        items=[_task_response(task) for task in tasks],
        total=total,
        status_counts=SyncTaskStatusCountsResponse(**status_counts),
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
    )
    return success_response(response.model_dump(mode="json"), request)


@router.get("/api/tasks/{task_id}")
async def get_task(
    task_id: UUID,
    request: Request,
    current_user: CurrentUserDep,
    scope: ScopedAdminDep,
    session: SessionDep,
) -> dict[str, object]:
    try:
        task = await _service(session).get_task(
            current_user=current_user,
            scope=scope,
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
    current_user: CurrentUserDep,
    scope: ScopedAdminDep,
    session: SessionDep,
) -> dict[str, object]:
    try:
        task = await _service(session).retry_task(
            current_user=current_user,
            scope=scope,
            task_id=task_id,
            context=_context_from(request),
        )
    except RagflowTaskError as error:
        _raise_ragflow_task_error(error)
    return success_response(_task_response(task).model_dump(mode="json"), request)


@router.post("/api/tasks/{task_id}/reconcile-version-switch")
async def reconcile_version_switch_task(
    task_id: UUID,
    payload: VersionSwitchReconcileRequest,
    request: Request,
    current_user: CurrentUserDep,
    scope: ScopedAdminDep,
    session: SessionDep,
) -> dict[str, object]:
    try:
        task = await _service(session).reconcile_version_switch_task(
            current_user=current_user,
            scope=scope,
            task_id=task_id,
            reason=payload.reason,
            context=_context_from(request),
        )
    except RagflowTaskError as error:
        _raise_ragflow_task_error(error)
    return success_response(_task_response(task).model_dump(mode="json"), request)


@router.post("/api/tasks/{task_id}/cancel")
async def cancel_task(
    task_id: UUID,
    request: Request,
    current_user: CurrentUserDep,
    scope: ScopedAdminDep,
    session: SessionDep,
) -> dict[str, object]:
    try:
        task = await _service(session).cancel_task(
            current_user=current_user,
            scope=scope,
            task_id=task_id,
            context=_context_from(request),
        )
    except RagflowTaskError as error:
        _raise_ragflow_task_error(error)
    return success_response(_task_response(task).model_dump(mode="json"), request)


@router.post("/api/admin/files/{file_id}/sync")
async def manual_sync_file(
    file_id: UUID,
    payload: ManualSyncRequest,
    request: Request,
    current_user: CurrentUserDep,
    scope: ScopedAdminDep,
    session: SessionDep,
) -> dict[str, object]:
    try:
        task = await _service(session).manual_sync_file(
            current_user=current_user,
            scope=scope,
            file_id=file_id,
            dataset_mapping_id=payload.dataset_mapping_id,
            reason=payload.reason,
            context=_context_from(request),
        )
    except RagflowTaskError as error:
        _raise_ragflow_task_error(error)
    return success_response(_task_response(task).model_dump(mode="json"), request)


@router.post("/api/admin/ragflow/test-connection")
async def test_ragflow_connection(
    request: Request,
    current_user: SystemAdminDep,
) -> dict[str, object]:
    runtime_settings = await resolve_ragflow_runtime_settings()
    base_url = runtime_settings.base_url
    api_key = runtime_settings.api_key

    logger.info(
        "ragflow_test_connection_started",
        endpoint_configured=bool(base_url),
        user_id=str(current_user.id),
    )

    start = time.monotonic()
    ok = True
    error_summary: str | None = None
    client = InstrumentedRagflowClient(
        HttpRagflowClient(
            base_url=base_url,
            api_key=api_key,
            timeout_seconds=runtime_settings.timeout_seconds,
            protected_environment=runtime_settings.protected_environment,
            tls_spki_pins=runtime_settings.tls_spki_pins,
        )
    )
    try:
        await client.check_connection()
    except RagflowClientError as exc:
        # HttpRagflowClient 抛错前已自行脱敏; 这里复用 redact_secret 兜底 (空 key 原样返回)
        ok = False
        error_summary = redact_secret(str(exc), api_key)

    latency_ms = (time.monotonic() - start) * 1000.0

    logger.info(
        "ragflow_test_connection_finished",
        ok=ok,
        latency_ms=round(latency_ms, 1),
        endpoint_configured=bool(base_url),
        user_id=str(current_user.id),
    )

    return success_response(
        {
            "ok": ok,
            "latency_ms": round(latency_ms, 1),
            "error": error_summary,
        },
        request,
    )


@router.post("/api/admin/ragflow/discover-datasets")
async def discover_ragflow_datasets(
    payload: RagflowDatasetDiscoveryRequest,
    request: Request,
    current_user: SystemAdminDep,
    session: SessionDep,
) -> dict[str, object]:
    runtime_settings = await resolve_ragflow_runtime_settings()
    settings = get_settings()
    base_url_candidate = (
        payload.base_url.strip()
        if payload.base_url is not None and payload.base_url.strip()
        else runtime_settings.base_url
    )
    api_key = (
        payload.api_key.strip()
        if payload.api_key is not None and payload.api_key.strip()
        else runtime_settings.api_key
    )

    ok = True
    error_summary: str | None = None
    datasets: list[RagflowDatasetOptionResponse] = []
    try:
        base_url = approved_ragflow_base_url(base_url_candidate, settings)
        tls_spki_pins = ragflow_tls_spki_pins_for_endpoint(
            base_url,
            settings.ragflow_tls_spki_pins,
        )
        if runtime_settings.protected_environment and (
            ragflow_endpoint_identity(base_url)[0] != "https" or not tls_spki_pins
        ):
            raise ValueError("RAGFlow endpoint transport is not approved")
        client = InstrumentedRagflowClient(
            HttpRagflowClient(
                base_url=base_url,
                api_key=api_key,
                timeout_seconds=runtime_settings.timeout_seconds,
                protected_environment=runtime_settings.protected_environment,
                tls_spki_pins=tls_spki_pins,
            )
        )
        datasets = [
            RagflowDatasetOptionResponse(dataset_id=item.dataset_id, name=item.name)
            for item in await client.list_datasets()
        ]
    except (RagflowClientError, ValueError) as exc:
        ok = False
        error_summary = redact_secret(str(exc), api_key)

    await record_admin_audit_log(
        session,
        actor_id=current_user.id,
        action="ragflow.dataset.discover",
        target_type="ragflow_config",
        target_id=uuid5(NAMESPACE_URL, "ragflow-config:dataset-discovery"),
        ip_address=_context_from(request).ip_address,
        user_agent=_context_from(request).user_agent,
        metadata_json={"ok": ok, "dataset_count": len(datasets)},
    )
    await session.commit()
    response = RagflowDatasetDiscoveryResponse(
        ok=ok,
        items=datasets,
        error=error_summary,
    )
    return success_response(response.model_dump(mode="json"), request)
