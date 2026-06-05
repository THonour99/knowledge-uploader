from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_admin_audit_log
from app.core.document_state import DocumentStateError, DocumentStateMachine
from app.core.outbox import OutboxRepository
from app.modules.ragflow.tasks import RagflowSyncLockBusy, create_ragflow_upload_sync_task
from app.modules.user.schemas import AuthUserRecord

from . import events, exceptions
from .models import Category, DatasetMapping
from .records import ReviewFileRecord
from .repository import ReviewRepository  # noqa: TID251 - same-module repository dependency
from .schemas import (
    CategoryCreateRequest,
    CategoryUpdateRequest,
    DatasetMappingCreateRequest,
    DatasetMappingUpdateRequest,
    ReviewDecisionRequest,
    UpdateFileClassificationRequest,
)

ADMIN_ROLES = {"knowledge_admin", "system_admin"}
SYSTEM_ADMIN_ROLE = "system_admin"
VALID_VISIBILITIES = {"private", "department", "company"}


@dataclass(frozen=True)
class RequestContext:
    ip_address: str
    user_agent: str


class ReviewService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        repository: ReviewRepository,
    ) -> None:
        self._session = session
        self._repository = repository

    async def list_categories(
        self,
        *,
        current_user: AuthUserRecord,
        context: RequestContext,
    ) -> list[Category]:
        self._require_admin(current_user)
        categories = await self._repository.list_categories()
        await self._record_admin_audit(
            current_user=current_user,
            action="category.list",
            target_type="category_collection",
            target_id=current_user.id,
            context=context,
            metadata_json={"result_count": len(categories)},
        )
        await self._session.commit()
        return categories

    async def create_category(
        self,
        *,
        current_user: AuthUserRecord,
        request: CategoryCreateRequest,
        context: RequestContext,
    ) -> Category:
        self._require_system_admin(current_user)
        self._validate_visibility(request.default_visibility)
        category = Category(
            name=request.name.strip(),
            code=request.code.strip().lower(),
            description=clean_optional_text(request.description),
            parent_id=request.parent_id,
            require_review=request.require_review,
            default_dataset_id=clean_optional_text(request.default_dataset_id),
            allow_employee_select=request.allow_employee_select,
            allow_ai_recommend=request.allow_ai_recommend,
            default_visibility=request.default_visibility,
            keywords=[keyword.strip() for keyword in request.keywords if keyword.strip()],
            classification_prompt=clean_optional_text(request.classification_prompt),
            ai_analysis_enabled=request.ai_analysis_enabled,
            sensitive_detection_enabled=request.sensitive_detection_enabled,
            auto_sync_enabled=request.auto_sync_enabled,
        )
        await self._repository.add_category(category)
        await self._record_admin_audit(
            current_user=current_user,
            action="category.create",
            target_type="category",
            target_id=category.id,
            context=context,
            metadata_json={
                "code": category.code,
                "require_review": category.require_review,
                "ai_analysis_enabled": category.ai_analysis_enabled,
            },
        )
        await self._session.commit()
        await self._session.refresh(category)
        return category

    async def update_category(
        self,
        *,
        current_user: AuthUserRecord,
        category_id: uuid.UUID,
        request: CategoryUpdateRequest,
        context: RequestContext,
    ) -> Category:
        self._require_system_admin(current_user)
        category = await self._get_category_or_raise(category_id)
        if request.name is not None:
            category.name = request.name.strip()
        fields_set = request.model_fields_set
        if "description" in fields_set:
            category.description = clean_optional_text(request.description)
        if "parent_id" in fields_set:
            category.parent_id = request.parent_id
        if request.require_review is not None:
            category.require_review = request.require_review
        if "default_dataset_id" in fields_set:
            category.default_dataset_id = clean_optional_text(request.default_dataset_id)
        if request.allow_employee_select is not None:
            category.allow_employee_select = request.allow_employee_select
        if request.allow_ai_recommend is not None:
            category.allow_ai_recommend = request.allow_ai_recommend
        if request.default_visibility is not None:
            self._validate_visibility(request.default_visibility)
            category.default_visibility = request.default_visibility
        if request.keywords is not None:
            category.keywords = [keyword.strip() for keyword in request.keywords if keyword.strip()]
        if "classification_prompt" in fields_set:
            category.classification_prompt = clean_optional_text(request.classification_prompt)
        if request.ai_analysis_enabled is not None:
            category.ai_analysis_enabled = request.ai_analysis_enabled
        if request.sensitive_detection_enabled is not None:
            category.sensitive_detection_enabled = request.sensitive_detection_enabled
        if request.auto_sync_enabled is not None:
            category.auto_sync_enabled = request.auto_sync_enabled
        await self._record_admin_audit(
            current_user=current_user,
            action="category.update",
            target_type="category",
            target_id=category.id,
            context=context,
            metadata_json={
                "code": category.code,
                "require_review": category.require_review,
                "ai_analysis_enabled": category.ai_analysis_enabled,
            },
        )
        await self._session.commit()
        await self._session.refresh(category)
        return category

    async def list_dataset_mappings(
        self,
        *,
        current_user: AuthUserRecord,
        context: RequestContext,
    ) -> list[DatasetMapping]:
        self._require_admin(current_user)
        mappings = await self._repository.list_dataset_mappings()
        await self._record_admin_audit(
            current_user=current_user,
            action="dataset_mapping.list",
            target_type="dataset_mapping_collection",
            target_id=current_user.id,
            context=context,
            metadata_json={"result_count": len(mappings)},
        )
        await self._session.commit()
        return mappings

    async def create_dataset_mapping(
        self,
        *,
        current_user: AuthUserRecord,
        request: DatasetMappingCreateRequest,
        context: RequestContext,
    ) -> DatasetMapping:
        self._require_system_admin(current_user)
        await self._get_category_or_raise(request.category_id)
        mapping = DatasetMapping(
            name=request.name.strip(),
            category_id=request.category_id,
            ragflow_dataset_id=request.ragflow_dataset_id.strip(),
            ragflow_dataset_name=request.ragflow_dataset_name.strip(),
            enabled=request.enabled,
        )
        await self._repository.add_dataset_mapping(mapping)
        await self._record_admin_audit(
            current_user=current_user,
            action="dataset_mapping.create",
            target_type="dataset_mapping",
            target_id=mapping.id,
            context=context,
            metadata_json={
                "category_id": str(mapping.category_id),
                "ragflow_dataset_id": mapping.ragflow_dataset_id,
                "enabled": mapping.enabled,
            },
        )
        await self._session.commit()
        await self._session.refresh(mapping)
        return mapping

    async def update_dataset_mapping(
        self,
        *,
        current_user: AuthUserRecord,
        mapping_id: uuid.UUID,
        request: DatasetMappingUpdateRequest,
        context: RequestContext,
    ) -> DatasetMapping:
        self._require_system_admin(current_user)
        mapping = await self._get_dataset_mapping_record_or_raise(mapping_id)
        if request.name is not None:
            mapping.name = request.name.strip()
        if request.category_id is not None:
            await self._get_category_or_raise(request.category_id)
            mapping.category_id = request.category_id
        if request.ragflow_dataset_id is not None:
            mapping.ragflow_dataset_id = request.ragflow_dataset_id.strip()
        if request.ragflow_dataset_name is not None:
            mapping.ragflow_dataset_name = request.ragflow_dataset_name.strip()
        if request.enabled is not None:
            mapping.enabled = request.enabled
        await self._record_admin_audit(
            current_user=current_user,
            action="dataset_mapping.update",
            target_type="dataset_mapping",
            target_id=mapping.id,
            context=context,
            metadata_json={
                "category_id": str(mapping.category_id),
                "ragflow_dataset_id": mapping.ragflow_dataset_id,
                "enabled": mapping.enabled,
            },
        )
        await self._session.commit()
        await self._session.refresh(mapping)
        return mapping

    async def delete_dataset_mapping(
        self,
        *,
        current_user: AuthUserRecord,
        mapping_id: uuid.UUID,
        context: RequestContext,
    ) -> None:
        self._require_system_admin(current_user)
        mapping = await self._get_dataset_mapping_record_or_raise(mapping_id)
        mapping.enabled = False
        await self._record_admin_audit(
            current_user=current_user,
            action="dataset_mapping.disable",
            target_type="dataset_mapping",
            target_id=mapping.id,
            context=context,
            metadata_json={
                "category_id": str(mapping.category_id),
                "ragflow_dataset_id": mapping.ragflow_dataset_id,
                "enabled": mapping.enabled,
            },
        )
        await self._session.commit()

    async def list_review_files(
        self,
        *,
        current_user: AuthUserRecord,
        context: RequestContext,
    ) -> list[ReviewFileRecord]:
        self._require_admin(current_user)
        files = await self._repository.list_files()
        await self._record_admin_audit(
            current_user=current_user,
            action="file.review_list",
            target_type="file_collection",
            target_id=current_user.id,
            context=context,
            metadata_json={"result_count": len(files)},
        )
        await self._session.commit()
        return files

    async def submit_file_for_review(
        self,
        *,
        current_user: AuthUserRecord,
        file_id: uuid.UUID,
        context: RequestContext,
    ) -> ReviewFileRecord:
        self._require_admin(current_user)
        file = await self._get_file_or_raise(file_id)
        self._transition_file(file, "pending_review")
        await self._record_audit(
            current_user=current_user,
            file=file,
            action="file.submit_review",
            context=context,
        )
        await self._append_review_event(
            event_type=events.REVIEW_FILE_SUBMITTED,
            file=file,
            current_user=current_user,
            metadata_json={"review_status": file.review_status},
        )
        file = await self._repository.update_file(file)
        await self._session.commit()
        return file

    async def approve_file(
        self,
        *,
        current_user: AuthUserRecord,
        file_id: uuid.UUID,
        request: ReviewDecisionRequest,
        context: RequestContext,
    ) -> ReviewFileRecord:
        self._require_admin(current_user)
        file = await self._get_file_or_raise(file_id)
        category = (
            await self._get_category_or_raise(request.category_id)
            if request.category_id
            else None
        )
        mapping = (
            await self._get_dataset_mapping_or_raise(request.dataset_mapping_id)
            if request.dataset_mapping_id
            else None
        )
        if mapping is not None and category is not None and mapping.category_id != category.id:
            raise exceptions.dataset_mapping_not_found()
        self._transition_file(file, "approved")
        file.review_status = "approved"
        if category is not None:
            file.category_id = category.id
        elif mapping is not None:
            file.category_id = mapping.category_id
        if mapping is not None:
            file.dataset_mapping_id = mapping.id
            file.ragflow_dataset_id = mapping.ragflow_dataset_id
        await self._record_audit(
            current_user=current_user,
            file=file,
            action="file.approve",
            context=context,
            reason=clean_optional_text(request.reason),
            metadata_json={
                "category_id": str(file.category_id) if file.category_id else None,
                "dataset_mapping_id": str(file.dataset_mapping_id)
                if file.dataset_mapping_id
                else None,
            },
        )
        await self._append_review_event(
            event_type=events.REVIEW_FILE_APPROVED,
            file=file,
            current_user=current_user,
            metadata_json={
                "category_id": str(file.category_id) if file.category_id else None,
                "dataset_mapping_id": str(file.dataset_mapping_id)
                if file.dataset_mapping_id
                else None,
                "ragflow_dataset_id": file.ragflow_dataset_id,
            },
        )
        if file.ragflow_dataset_id is not None:
            self._transition_file(file, "queued")
        file = await self._repository.update_file(file)
        if file.ragflow_dataset_id is not None:
            try:
                await create_ragflow_upload_sync_task(session=self._session, file_id=file.id)
            except RagflowSyncLockBusy as exc:
                raise exceptions.sync_task_busy() from exc
        await self._session.commit()
        return file

    async def reject_file(
        self,
        *,
        current_user: AuthUserRecord,
        file_id: uuid.UUID,
        reason: str,
        context: RequestContext,
    ) -> ReviewFileRecord:
        self._require_admin(current_user)
        file = await self._get_file_or_raise(file_id)
        self._transition_file(file, "rejected")
        file.review_status = "rejected"
        await self._record_audit(
            current_user=current_user,
            file=file,
            action="file.reject",
            context=context,
            reason=reason.strip(),
        )
        await self._append_review_event(
            event_type=events.REVIEW_FILE_REJECTED,
            file=file,
            current_user=current_user,
            metadata_json={"reason": reason.strip()},
        )
        file = await self._repository.update_file(file)
        await self._session.commit()
        return file

    async def update_file_classification(
        self,
        *,
        current_user: AuthUserRecord,
        file_id: uuid.UUID,
        request: UpdateFileClassificationRequest,
        context: RequestContext,
    ) -> ReviewFileRecord:
        self._require_admin(current_user)
        file = await self._get_file_or_raise(file_id)
        category = (
            await self._get_category_or_raise(request.category_id)
            if request.category_id
            else None
        )
        mapping = (
            await self._get_dataset_mapping_or_raise(request.dataset_mapping_id)
            if request.dataset_mapping_id
            else None
        )
        if mapping is not None and category is not None and mapping.category_id != category.id:
            raise exceptions.dataset_mapping_not_found()
        if category is not None:
            file.category_id = category.id
        elif mapping is not None:
            file.category_id = mapping.category_id
        else:
            file.category_id = None
        file.dataset_mapping_id = mapping.id if mapping is not None else None
        file.ragflow_dataset_id = mapping.ragflow_dataset_id if mapping is not None else None
        await self._record_audit(
            current_user=current_user,
            file=file,
            action="file.update_classification",
            context=context,
            metadata_json={
                "category_id": str(file.category_id) if file.category_id else None,
                "dataset_mapping_id": str(file.dataset_mapping_id)
                if file.dataset_mapping_id
                else None,
            },
        )
        file = await self._repository.update_file(file)
        await self._session.commit()
        return file

    async def _get_category_or_raise(self, category_id: uuid.UUID) -> Category:
        category = await self._repository.get_category(category_id)
        if category is None:
            raise exceptions.category_not_found()
        return category

    async def _get_dataset_mapping_or_raise(self, mapping_id: uuid.UUID) -> DatasetMapping:
        mapping = await self._get_dataset_mapping_record_or_raise(mapping_id)
        if not mapping.enabled:
            raise exceptions.dataset_mapping_not_found()
        return mapping

    async def _get_dataset_mapping_record_or_raise(
        self,
        mapping_id: uuid.UUID,
    ) -> DatasetMapping:
        mapping = await self._repository.get_dataset_mapping(mapping_id)
        if mapping is None:
            raise exceptions.dataset_mapping_not_found()
        return mapping

    async def _get_file_or_raise(self, file_id: uuid.UUID) -> ReviewFileRecord:
        file = await self._repository.get_file(file_id)
        if file is None:
            raise exceptions.file_not_found()
        return file

    def _transition_file(self, file: ReviewFileRecord, to_status: str) -> None:
        try:
            file.status = DocumentStateMachine.transition(file.status, to_status)
        except DocumentStateError as exc:
            raise exceptions.invalid_state() from exc

    async def _append_review_event(
        self,
        *,
        event_type: str,
        file: ReviewFileRecord,
        current_user: AuthUserRecord,
        metadata_json: dict[str, object] | None = None,
    ) -> None:
        payload: dict[str, object] = {
            "file_id": str(file.id),
            "actor_id": str(current_user.id),
            "status": file.status,
            "review_status": file.review_status,
        }
        payload.update(metadata_json or {})
        await OutboxRepository(self._session).append(
            event_type=event_type,
            aggregate_type="file",
            aggregate_id=str(file.id),
            payload=payload,
        )

    async def _record_audit(
        self,
        *,
        current_user: AuthUserRecord,
        file: ReviewFileRecord,
        action: str,
        context: RequestContext,
        metadata_json: dict[str, object] | None = None,
        reason: str | None = None,
    ) -> None:
        await record_admin_audit_log(
            self._session,
            actor_id=current_user.id,
            action=action,
            target_type="file",
            target_id=file.id,
            ip_address=context.ip_address,
            user_agent=context.user_agent,
            metadata_json=metadata_json,
            reason=reason,
        )

    async def _record_admin_audit(
        self,
        *,
        current_user: AuthUserRecord,
        action: str,
        target_type: str,
        target_id: uuid.UUID,
        context: RequestContext,
        metadata_json: dict[str, object] | None = None,
    ) -> None:
        await record_admin_audit_log(
            self._session,
            actor_id=current_user.id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            ip_address=context.ip_address,
            user_agent=context.user_agent,
            metadata_json=metadata_json,
        )

    def _require_admin(self, current_user: AuthUserRecord) -> None:
        if current_user.role not in ADMIN_ROLES:
            raise exceptions.permission_denied()

    def _require_system_admin(self, current_user: AuthUserRecord) -> None:
        if current_user.role != SYSTEM_ADMIN_ROLE:
            raise exceptions.permission_denied()

    def _validate_visibility(self, visibility: str) -> None:
        if visibility not in VALID_VISIBILITIES:
            raise exceptions.invalid_visibility()


def clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None
