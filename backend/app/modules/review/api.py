from __future__ import annotations

from typing import Annotated, Literal, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access_scope import (
    ScopedAdminDep,
    build_department_access_scope,
    get_department_scope_store,
)
from app.core.database import get_session
from app.core.deps import get_current_user
from app.core.permissions import AdminUserDep
from app.core.responses import success_response
from app.modules.document.schemas import (
    FileDraftUpdateRequest,
    FileListResponse,
    FileResponse,
    effective_expiry_status,
)
from app.modules.user.schemas import AuthUserRecord

from .exceptions import ReviewError
from .models import Category, DatasetMapping, Tag
from .records import ReviewFileRecord
from .repository import ReviewRepository  # noqa: TID251 - same-module repository dependency
from .schemas import (
    CategoryCreateRequest,
    CategoryListResponse,
    CategoryResponse,
    CategoryUpdateRequest,
    DatasetMappingCreateRequest,
    DatasetMappingListResponse,
    DatasetMappingResponse,
    DatasetMappingUpdateRequest,
    RejectFileRequest,
    ReleaseReviewClaimRequest,
    ReviewDecisionRequest,
    ReviewDecisionResponse,
    SubmitReviewRequest,
    TagCreateRequest,
    TagListResponse,
    TagMergeRequest,
    TagResponse,
    TagUpdateRequest,
    UpdateFileClassificationRequest,
)
from .service import RequestContext, ReviewService  # noqa: TID251 - same-module service dependency

router = APIRouter(tags=["review"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
CurrentUserDep = Annotated[AuthUserRecord, Depends(get_current_user)]


def _service(session: AsyncSession) -> ReviewService:
    return ReviewService(session=session, repository=ReviewRepository(session))


def _raise_review_error(error: ReviewError) -> NoReturn:
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


def _category_response(category: Category) -> CategoryResponse:
    return CategoryResponse(
        id=category.id,
        name=category.name,
        code=category.code,
        description=category.description,
        parent_id=category.parent_id,
        require_review=category.require_review,
        default_dataset_id=category.default_dataset_id,
        allow_employee_select=category.allow_employee_select,
        allow_ai_recommend=category.allow_ai_recommend,
        default_visibility=category.default_visibility,
        keywords=category.keywords,
        classification_prompt=category.classification_prompt,
        ai_analysis_enabled=category.ai_analysis_enabled,
        sensitive_detection_enabled=category.sensitive_detection_enabled,
        auto_sync_enabled=category.auto_sync_enabled,
        created_at=category.created_at,
        updated_at=category.updated_at,
    )


def _dataset_mapping_response(mapping: DatasetMapping) -> DatasetMappingResponse:
    return DatasetMappingResponse(
        id=mapping.id,
        name=mapping.name,
        category_id=mapping.category_id,
        ragflow_dataset_id=mapping.ragflow_dataset_id,
        ragflow_dataset_name=mapping.ragflow_dataset_name,
        enabled=mapping.enabled,
        created_at=mapping.created_at,
        updated_at=mapping.updated_at,
    )


def _file_response(file: ReviewFileRecord) -> FileResponse:
    return FileResponse(
        id=file.id,
        original_name=file.original_name,
        title=file.title,
        extension=file.extension,
        mime_type=file.mime_type,
        size=file.size,
        uploader_id=file.uploader_id,
        uploader_name=file.uploader_name,
        department_id=file.department_id,
        department_name=file.department,
        department_code=None,
        department=file.department,
        category_id=file.category_id,
        dataset_mapping_id=file.dataset_mapping_id,
        visibility=file.visibility,
        description=file.description,
        tags=file.tags,
        status=file.status,
        review_status=file.review_status,
        submitted_at=file.submitted_at,
        review_due_at=file.review_due_at,
        claimed_by=file.claimed_by,
        claimed_by_name=file.claimed_by_name,
        claimed_at=file.claimed_at,
        claim_expires_at=file.claim_expires_at,
        review_version=file.review_version,
        sensitive_risk_level=file.sensitive_risk_level,
        ragflow_dataset_id=file.ragflow_dataset_id,
        ragflow_document_id=file.ragflow_document_id,
        ragflow_parse_status=file.ragflow_parse_status,
        ai_analysis_enabled_at_upload=file.ai_analysis_enabled_at_upload,
        expires_at=file.expires_at,
        expiry_status=effective_expiry_status(
            expires_at=file.expires_at,
            stored_status=file.expiry_status,
        ),
        uploaded_at=file.uploaded_at,
        last_sync_at=file.last_sync_at,
        created_at=file.created_at,
        updated_at=file.updated_at,
    )


def _tag_response(tag: Tag, usage_count: int) -> TagResponse:
    return TagResponse(
        id=tag.id,
        name=tag.name,
        description=tag.description,
        usage_count=usage_count,
        is_system_generated=tag.is_system_generated,
        enabled=tag.enabled,
        created_at=tag.created_at,
        updated_at=tag.updated_at,
    )


@router.get("/api/tags")
async def list_tags(
    request: Request,
    current_user: CurrentUserDep,
    session: SessionDep,
    enabled: Annotated[bool | None, Query()] = None,
    search: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, object]:
    try:
        items, total = await _service(session).list_tags(
            current_user=current_user,
            enabled=enabled,
            search=search,
            page=page,
            page_size=page_size,
            context=_context_from(request),
        )
    except ReviewError as error:
        _raise_review_error(error)
    response = TagListResponse(
        items=[_tag_response(tag, usage_count) for tag, usage_count in items],
        total=total,
        page=page,
        page_size=page_size,
    )
    return success_response(response.model_dump(mode="json"), request)


@router.post("/api/tags", status_code=201)
async def create_tag(
    payload: TagCreateRequest,
    request: Request,
    current_user: AdminUserDep,
    session: SessionDep,
) -> dict[str, object]:
    try:
        tag, usage_count = await _service(session).create_tag(
            current_user=current_user,
            request=payload,
            context=_context_from(request),
        )
    except ReviewError as error:
        _raise_review_error(error)
    return success_response(_tag_response(tag, usage_count).model_dump(mode="json"), request)


@router.patch("/api/tags/{tag_id}")
async def update_tag(
    tag_id: UUID,
    payload: TagUpdateRequest,
    request: Request,
    current_user: AdminUserDep,
    session: SessionDep,
) -> dict[str, object]:
    try:
        tag, usage_count = await _service(session).update_tag(
            current_user=current_user,
            tag_id=tag_id,
            request=payload,
            context=_context_from(request),
        )
    except ReviewError as error:
        _raise_review_error(error)
    return success_response(_tag_response(tag, usage_count).model_dump(mode="json"), request)


@router.post("/api/tags/{tag_id}/merge")
async def merge_tag(
    tag_id: UUID,
    payload: TagMergeRequest,
    request: Request,
    current_user: AdminUserDep,
    session: SessionDep,
) -> dict[str, object]:
    try:
        tag, usage_count = await _service(session).merge_tags(
            current_user=current_user,
            source_tag_id=tag_id,
            target_tag_id=payload.target_tag_id,
            context=_context_from(request),
        )
    except ReviewError as error:
        _raise_review_error(error)
    return success_response(_tag_response(tag, usage_count).model_dump(mode="json"), request)


@router.delete("/api/tags/{tag_id}")
async def delete_tag(
    tag_id: UUID,
    request: Request,
    current_user: AdminUserDep,
    session: SessionDep,
) -> dict[str, object]:
    try:
        await _service(session).delete_tag(
            current_user=current_user,
            tag_id=tag_id,
            context=_context_from(request),
        )
    except ReviewError as error:
        _raise_review_error(error)
    return success_response({}, request)


@router.get("/api/categories")
async def list_categories(
    request: Request,
    current_user: AdminUserDep,
    session: SessionDep,
) -> dict[str, object]:
    try:
        categories = await _service(session).list_categories(
            current_user=current_user,
            context=_context_from(request),
        )
    except ReviewError as error:
        _raise_review_error(error)
    response = CategoryListResponse(
        items=[_category_response(category) for category in categories],
        total=len(categories),
    )
    return success_response(response.model_dump(mode="json"), request)


@router.post("/api/categories", status_code=201)
async def create_category(
    payload: CategoryCreateRequest,
    request: Request,
    current_user: AdminUserDep,
    session: SessionDep,
) -> dict[str, object]:
    try:
        category = await _service(session).create_category(
            current_user=current_user,
            request=payload,
            context=_context_from(request),
        )
    except ReviewError as error:
        _raise_review_error(error)
    return success_response(_category_response(category).model_dump(mode="json"), request)


@router.patch("/api/categories/{category_id}")
async def update_category(
    category_id: UUID,
    payload: CategoryUpdateRequest,
    request: Request,
    current_user: AdminUserDep,
    session: SessionDep,
) -> dict[str, object]:
    try:
        category = await _service(session).update_category(
            current_user=current_user,
            category_id=category_id,
            request=payload,
            context=_context_from(request),
        )
    except ReviewError as error:
        _raise_review_error(error)
    return success_response(_category_response(category).model_dump(mode="json"), request)


@router.get("/api/datasets")
async def list_dataset_mappings(
    request: Request,
    current_user: AdminUserDep,
    session: SessionDep,
) -> dict[str, object]:
    try:
        mappings = await _service(session).list_dataset_mappings(
            current_user=current_user,
            context=_context_from(request),
        )
    except ReviewError as error:
        _raise_review_error(error)
    response = DatasetMappingListResponse(
        items=[_dataset_mapping_response(mapping) for mapping in mappings],
        total=len(mappings),
    )
    return success_response(response.model_dump(mode="json"), request)


@router.post("/api/datasets", status_code=201)
async def create_dataset_mapping(
    payload: DatasetMappingCreateRequest,
    request: Request,
    current_user: AdminUserDep,
    session: SessionDep,
) -> dict[str, object]:
    try:
        mapping = await _service(session).create_dataset_mapping(
            current_user=current_user,
            request=payload,
            context=_context_from(request),
        )
    except ReviewError as error:
        _raise_review_error(error)
    return success_response(_dataset_mapping_response(mapping).model_dump(mode="json"), request)


@router.patch("/api/datasets/{mapping_id}")
async def update_dataset_mapping(
    mapping_id: UUID,
    payload: DatasetMappingUpdateRequest,
    request: Request,
    current_user: AdminUserDep,
    session: SessionDep,
) -> dict[str, object]:
    try:
        mapping = await _service(session).update_dataset_mapping(
            current_user=current_user,
            mapping_id=mapping_id,
            request=payload,
            context=_context_from(request),
        )
    except ReviewError as error:
        _raise_review_error(error)
    return success_response(_dataset_mapping_response(mapping).model_dump(mode="json"), request)


@router.delete("/api/datasets/{mapping_id}", status_code=204)
async def delete_dataset_mapping(
    mapping_id: UUID,
    request: Request,
    current_user: AdminUserDep,
    session: SessionDep,
) -> Response:
    try:
        await _service(session).delete_dataset_mapping(
            current_user=current_user,
            mapping_id=mapping_id,
            context=_context_from(request),
        )
    except ReviewError as error:
        _raise_review_error(error)
    return Response(status_code=204)


@router.get("/api/review/files")
async def list_review_files(
    request: Request,
    current_user: CurrentUserDep,
    scope: ScopedAdminDep,
    session: SessionDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    q: Annotated[str | None, Query(max_length=200)] = None,
    queue: Annotated[
        Literal["unclaimed", "mine", "due_soon", "overdue"] | None,
        Query(),
    ] = None,
    extension: Annotated[
        str | None,
        Query(min_length=1, max_length=20, pattern=r"^[A-Za-z0-9]+$"),
    ] = None,
    tag_id: Annotated[UUID | None, Query()] = None,
    department_id: Annotated[UUID | None, Query()] = None,
    sensitive_risk_level: Annotated[
        Literal["none", "low", "medium", "high", "critical"] | None,
        Query(),
    ] = None,
    sort: Annotated[
        Literal["submitted_at", "review_due_at", "uploaded_at", "original_name", "risk"] | None,
        Query(),
    ] = None,
    order: Annotated[Literal["asc", "desc"], Query()] = "asc",
) -> dict[str, object]:
    try:
        result = await _service(session).list_review_files(
            current_user=current_user,
            scope=scope,
            context=_context_from(request),
            page=page,
            page_size=page_size,
            search=q,
            queue=queue,
            extension=extension,
            tag_id=tag_id,
            department_id=department_id,
            sensitive_risk_level=sensitive_risk_level,
            sort=sort,
            order=order,
        )
    except ReviewError as error:
        _raise_review_error(error)
    response = FileListResponse(
        items=[_file_response(file) for file in result.items],
        total=result.total,
        page=result.page,
        page_size=result.page_size,
        total_pages=(result.total + result.page_size - 1) // result.page_size,
    )
    return success_response(response.model_dump(mode="json"), request)


@router.post("/api/review/files/{file_id}/claim")
async def claim_review_file(
    file_id: UUID,
    request: Request,
    current_user: CurrentUserDep,
    scope: ScopedAdminDep,
    session: SessionDep,
) -> dict[str, object]:
    try:
        file = await _service(session).claim_file_for_review(
            current_user=current_user,
            scope=scope,
            file_id=file_id,
            context=_context_from(request),
        )
    except ReviewError as error:
        _raise_review_error(error)
    return success_response(_file_response(file).model_dump(mode="json"), request)


@router.delete("/api/review/files/{file_id}/claim")
async def release_review_file_claim(
    file_id: UUID,
    request: Request,
    current_user: CurrentUserDep,
    scope: ScopedAdminDep,
    session: SessionDep,
    payload: ReleaseReviewClaimRequest | None = None,
) -> dict[str, object]:
    try:
        file = await _service(session).release_file_review_claim(
            current_user=current_user,
            scope=scope,
            file_id=file_id,
            reason=payload.reason if payload is not None else None,
            context=_context_from(request),
        )
    except ReviewError as error:
        _raise_review_error(error)
    return success_response(_file_response(file).model_dump(mode="json"), request)


@router.post("/api/files/{file_id}/submit-review")
async def submit_file_for_review(
    file_id: UUID,
    request: Request,
    current_user: CurrentUserDep,
    session: SessionDep,
    payload: SubmitReviewRequest | None = None,
) -> dict[str, object]:
    try:
        file = await _service(session).submit_file_for_review(
            current_user=current_user,
            file_id=file_id,
            acknowledge_sensitive_risk=(
                payload.acknowledge_sensitive_risk if payload is not None else False
            ),
            context=_context_from(request),
        )
    except ReviewError as error:
        _raise_review_error(error)
    return success_response(_file_response(file).model_dump(mode="json"), request)


@router.post("/api/files/{file_id}/approve")
async def approve_file(
    file_id: UUID,
    payload: ReviewDecisionRequest,
    request: Request,
    current_user: CurrentUserDep,
    scope: ScopedAdminDep,
    session: SessionDep,
) -> dict[str, object]:
    try:
        file = await _service(session).approve_file(
            current_user=current_user,
            scope=scope,
            file_id=file_id,
            request=payload,
            context=_context_from(request),
        )
    except ReviewError as error:
        _raise_review_error(error)
    response = ReviewDecisionResponse(
        **_file_response(file).model_dump(),
        sync_decision=payload.sync_decision,
        sync_task_id=None,
    )
    return success_response(response.model_dump(mode="json"), request)


@router.post("/api/files/{file_id}/reject")
async def reject_file(
    file_id: UUID,
    payload: RejectFileRequest,
    request: Request,
    current_user: CurrentUserDep,
    scope: ScopedAdminDep,
    session: SessionDep,
) -> dict[str, object]:
    try:
        file = await _service(session).reject_file(
            current_user=current_user,
            scope=scope,
            file_id=file_id,
            reason=payload.reason,
            context=_context_from(request),
        )
    except ReviewError as error:
        _raise_review_error(error)
    return success_response(_file_response(file).model_dump(mode="json"), request)


@router.patch("/api/files/{file_id}")
async def update_file_classification(
    file_id: UUID,
    payload: UpdateFileClassificationRequest | FileDraftUpdateRequest,
    request: Request,
    current_user: CurrentUserDep,
    session: SessionDep,
) -> dict[str, object]:
    try:
        service = _service(session)
        if isinstance(payload, FileDraftUpdateRequest):
            file = await service.update_file_draft(
                current_user=current_user,
                file_id=file_id,
                request=payload,
                context=_context_from(request),
            )
        else:
            scope = await build_department_access_scope(
                current_user=current_user,
                store=get_department_scope_store(session),
            )
            file = await service.update_file_classification(
                current_user=current_user,
                scope=scope,
                file_id=file_id,
                request=payload,
                context=_context_from(request),
            )
    except ReviewError as error:
        _raise_review_error(error)
    return success_response(_file_response(file).model_dump(mode="json"), request)
