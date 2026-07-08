from __future__ import annotations

from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.database import get_session
from app.core.deps import SettingsDep
from app.core.permissions import SystemAdminDep
from app.core.responses import success_response

from . import exceptions
from .repository import AiRepository  # noqa: TID251 - same-module repository dependency
from .schemas import (
    AiFeatureUpdateRequest,
    AiProviderCreateRequest,
    AiProviderUpdateRequest,
    PromptTemplateCreateRequest,
    PromptTemplateUpdateRequest,
    SensitiveRuleCreateRequest,
    SensitiveRuleTestRequest,
    SensitiveRuleUpdateRequest,
)
from .service import (  # noqa: TID251 - same-module service dependency
    AiConfigService,
    RequestContext,
)

router = APIRouter(prefix="/api/admin/ai", tags=["ai"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _service(session: AsyncSession, settings: Settings) -> AiConfigService:
    return AiConfigService(
        session=session,
        repository=AiRepository(session),
        settings=settings,
    )


def _raise_ai_error(error: exceptions.AiError) -> NoReturn:
    raise HTTPException(
        status_code=error.status_code,
        detail={"error_code": error.error_code, "message": error.message},
    )


def _context_from(request: Request) -> RequestContext:
    client_host = request.client.host if request.client is not None else ""
    ip_address = client_host.strip()[:45] or "unknown"
    user_agent = request.headers.get("user-agent", "").strip()[:512] or "unknown"
    return RequestContext(ip_address=ip_address, user_agent=user_agent)


@router.get("/config")
async def get_ai_config(
    request: Request,
    current_user: SystemAdminDep,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, object]:
    try:
        response = await _service(session, settings).get_config(
            current_user=current_user,
            context=_context_from(request),
        )
    except exceptions.AiError as error:
        _raise_ai_error(error)
    return success_response(response.model_dump(mode="json", by_alias=True), request)


@router.patch("/features/{feature_key}")
async def update_ai_feature(
    feature_key: str,
    payload: AiFeatureUpdateRequest,
    request: Request,
    current_user: SystemAdminDep,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, object]:
    try:
        response = await _service(session, settings).update_feature(
            current_user=current_user,
            feature_key=feature_key,
            request=payload,
            context=_context_from(request),
        )
    except exceptions.AiError as error:
        _raise_ai_error(error)
    return success_response(response.model_dump(mode="json"), request)


@router.post("/providers", status_code=201)
async def create_ai_provider(
    payload: AiProviderCreateRequest,
    request: Request,
    current_user: SystemAdminDep,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, object]:
    service = _service(session, settings)
    try:
        provider = await service.create_provider(
            current_user=current_user,
            request=payload,
            context=_context_from(request),
        )
    except exceptions.AiError as error:
        _raise_ai_error(error)
    return success_response(service._provider_response(provider).model_dump(mode="json"), request)


@router.patch("/providers/{provider_id}")
async def update_ai_provider(
    provider_id: UUID,
    payload: AiProviderUpdateRequest,
    request: Request,
    current_user: SystemAdminDep,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, object]:
    service = _service(session, settings)
    try:
        provider = await service.update_provider(
            current_user=current_user,
            provider_id=provider_id,
            request=payload,
            context=_context_from(request),
        )
    except exceptions.AiError as error:
        _raise_ai_error(error)
    return success_response(service._provider_response(provider).model_dump(mode="json"), request)


@router.post("/providers/{provider_id}/test")
async def test_ai_provider(
    provider_id: UUID,
    request: Request,
    current_user: SystemAdminDep,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, object]:
    try:
        response = await _service(session, settings).test_provider(
            current_user=current_user,
            provider_id=provider_id,
            context=_context_from(request),
        )
    except exceptions.AiError as error:
        _raise_ai_error(error)
    return success_response(response.model_dump(mode="json"), request)


@router.post("/prompt-templates", status_code=201)
async def create_prompt_template(
    payload: PromptTemplateCreateRequest,
    request: Request,
    current_user: SystemAdminDep,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, object]:
    try:
        response = await _service(session, settings).create_prompt_template(
            current_user=current_user,
            request=payload,
            context=_context_from(request),
        )
    except exceptions.AiError as error:
        _raise_ai_error(error)
    return success_response(response.model_dump(mode="json"), request)


@router.patch("/prompt-templates/{template_id}")
async def update_prompt_template(
    template_id: UUID,
    payload: PromptTemplateUpdateRequest,
    request: Request,
    current_user: SystemAdminDep,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, object]:
    try:
        response = await _service(session, settings).update_prompt_template(
            current_user=current_user,
            template_id=template_id,
            request=payload,
            context=_context_from(request),
        )
    except exceptions.AiError as error:
        _raise_ai_error(error)
    return success_response(response.model_dump(mode="json"), request)


@router.post("/prompt-templates/{template_id}/restore-default")
async def restore_prompt_template_default(
    template_id: UUID,
    request: Request,
    current_user: SystemAdminDep,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, object]:
    try:
        response = await _service(session, settings).restore_prompt_template_default(
            current_user=current_user,
            template_id=template_id,
            context=_context_from(request),
        )
    except exceptions.AiError as error:
        _raise_ai_error(error)
    return success_response(response.model_dump(mode="json"), request)


@router.delete("/prompt-templates/{template_id}")
async def delete_prompt_template(
    template_id: UUID,
    request: Request,
    current_user: SystemAdminDep,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, object]:
    try:
        await _service(session, settings).delete_prompt_template(
            current_user=current_user,
            template_id=template_id,
            context=_context_from(request),
        )
    except exceptions.AiError as error:
        _raise_ai_error(error)
    return success_response({}, request)


@router.post("/sensitive-rules", status_code=201)
async def create_sensitive_rule(
    payload: SensitiveRuleCreateRequest,
    request: Request,
    current_user: SystemAdminDep,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, object]:
    try:
        response = await _service(session, settings).create_sensitive_rule(
            current_user=current_user,
            request=payload,
            context=_context_from(request),
        )
    except exceptions.AiError as error:
        _raise_ai_error(error)
    return success_response(response.model_dump(mode="json"), request)


@router.post("/sensitive-rules/test")
async def test_sensitive_rules(
    payload: SensitiveRuleTestRequest,
    request: Request,
    current_user: SystemAdminDep,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, object]:
    try:
        response = await _service(session, settings).test_sensitive_rules(
            current_user=current_user,
            request=payload,
            context=_context_from(request),
        )
    except exceptions.AiError as error:
        _raise_ai_error(error)
    return success_response(response.model_dump(mode="json"), request)


@router.patch("/sensitive-rules/{rule_id}")
async def update_sensitive_rule(
    rule_id: UUID,
    payload: SensitiveRuleUpdateRequest,
    request: Request,
    current_user: SystemAdminDep,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, object]:
    try:
        response = await _service(session, settings).update_sensitive_rule(
            current_user=current_user,
            rule_id=rule_id,
            request=payload,
            context=_context_from(request),
        )
    except exceptions.AiError as error:
        _raise_ai_error(error)
    return success_response(response.model_dump(mode="json"), request)


@router.delete("/sensitive-rules/{rule_id}")
async def delete_sensitive_rule(
    rule_id: UUID,
    request: Request,
    current_user: SystemAdminDep,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, object]:
    try:
        await _service(session, settings).delete_sensitive_rule(
            current_user=current_user,
            rule_id=rule_id,
            context=_context_from(request),
        )
    except exceptions.AiError as error:
        _raise_ai_error(error)
    return success_response({}, request)
