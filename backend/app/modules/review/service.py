from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access_scope import DepartmentAccessScope, get_department_scope_store
from app.core.audit import record_admin_audit_log, record_audit_log
from app.core.config import get_settings
from app.core.document_state import DocumentStateError, DocumentStateMachine
from app.core.identity import has_assigned_department
from app.core.outbox import OutboxRepository
from app.core.ragflow_runtime import (
    is_ragflow_dataset_allowed,
    resolve_ragflow_runtime_settings,
)
from app.core.review_policy import review_claim_expiry, review_submission_times
from app.core.runtime_config import get_config
from app.modules.document.schemas import FileDraftUpdateRequest, effective_expiry_status
from app.modules.user.schemas import AuthUserRecord

from . import events, exceptions
from .models import Category, DatasetMapping, Tag
from .records import ReviewFileRecord
from .repository import ReviewRepository  # noqa: TID251 - same-module repository dependency
from .schemas import (
    CategoryCreateRequest,
    CategoryUpdateRequest,
    DatasetMappingCreateRequest,
    DatasetMappingUpdateRequest,
    ReviewDecisionRequest,
    TagCreateRequest,
    TagUpdateRequest,
    UpdateFileClassificationRequest,
)

ADMIN_ROLES = {"dept_admin", "system_admin"}
SYSTEM_ADMIN_ROLE = "system_admin"
VALID_VISIBILITIES = {"private", "department", "company"}
EXPIRED_CLAIM_RELEASE_BATCH_SIZE = 100
DRAFT_EDITABLE_STATUSES = frozenset(
    {
        "uploaded",
        "analyzed",
        "analysis_failed",
        "sensitive_review_required",
        "rejected",
    }
)


@dataclass(frozen=True)
class RequestContext:
    ip_address: str
    user_agent: str


@dataclass(frozen=True)
class ReviewFilePage:
    items: list[ReviewFileRecord]
    total: int
    page: int
    page_size: int


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
        ragflow_dataset_id = request.ragflow_dataset_id.strip()
        await self._ensure_ragflow_dataset_allowed(
            ragflow_dataset_id,
            current_user=current_user,
            context=context,
            target_type="category",
            target_id=request.category_id,
        )
        mapping = DatasetMapping(
            name=request.name.strip(),
            category_id=request.category_id,
            ragflow_dataset_id=ragflow_dataset_id,
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
        target_category_id = mapping.category_id
        if request.category_id is not None:
            await self._get_category_or_raise(request.category_id)
            target_category_id = request.category_id
        target_ragflow_dataset_id = mapping.ragflow_dataset_id
        if request.ragflow_dataset_id is not None:
            target_ragflow_dataset_id = request.ragflow_dataset_id.strip()
            await self._ensure_ragflow_dataset_allowed(
                target_ragflow_dataset_id,
                current_user=current_user,
                context=context,
                target_type="dataset_mapping",
                target_id=mapping.id,
            )
        if request.name is not None:
            mapping.name = request.name.strip()
        if request.category_id is not None:
            mapping.category_id = target_category_id
        if request.ragflow_dataset_id is not None:
            mapping.ragflow_dataset_id = target_ragflow_dataset_id
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

    async def list_tags(
        self,
        *,
        current_user: AuthUserRecord,
        enabled: bool | None,
        search: str | None,
        page: int,
        page_size: int,
        context: RequestContext,
    ) -> tuple[list[tuple[Tag, int]], int]:
        items, total = await self._repository.list_tags(
            enabled=enabled,
            search=clean_optional_text(search),
            page=page,
            page_size=page_size,
        )
        if current_user.role in ADMIN_ROLES:
            await self._record_admin_audit(
                current_user=current_user,
                action="tag.list",
                target_type="tag_collection",
                target_id=current_user.id,
                context=context,
                metadata_json={"result_count": len(items), "total": total},
            )
            await self._session.commit()
        return items, total

    async def create_tag(
        self,
        *,
        current_user: AuthUserRecord,
        request: TagCreateRequest,
        context: RequestContext,
    ) -> tuple[Tag, int]:
        self._require_system_admin(current_user)
        name = request.name.strip()
        if not name:
            raise exceptions.tag_name_empty()
        if await self._repository.get_tag_by_name(name) is not None:
            raise exceptions.tag_name_conflict()
        tag = Tag(
            name=name,
            description=clean_optional_text(request.description),
            is_system_generated=False,
            enabled=True,
            usage_count=0,
        )
        await self._repository.add_tag(tag)
        await self._record_admin_audit(
            current_user=current_user,
            action="tag.create",
            target_type="tag",
            target_id=tag.id,
            context=context,
            metadata_json={"name": tag.name},
        )
        await self._session.commit()
        await self._session.refresh(tag)
        return tag, 0

    async def update_tag(
        self,
        *,
        current_user: AuthUserRecord,
        tag_id: uuid.UUID,
        request: TagUpdateRequest,
        context: RequestContext,
    ) -> tuple[Tag, int]:
        self._require_system_admin(current_user)
        tag = await self._get_tag_or_raise(tag_id)
        if request.name is not None:
            name = request.name.strip()
            if not name:
                raise exceptions.tag_name_empty()
            existing = await self._repository.get_tag_by_name(name)
            if existing is not None and existing.id != tag.id:
                raise exceptions.tag_name_conflict()
            tag.name = name
        if "description" in request.model_fields_set:
            tag.description = clean_optional_text(request.description)
        if request.enabled is not None:
            tag.enabled = request.enabled
        await self._record_admin_audit(
            current_user=current_user,
            action="tag.update",
            target_type="tag",
            target_id=tag.id,
            context=context,
            metadata_json={"name": tag.name, "enabled": tag.enabled},
        )
        await self._session.commit()
        await self._session.refresh(tag)
        usage_count = await self._repository.count_tag_files(tag.id)
        return tag, usage_count

    async def merge_tags(
        self,
        *,
        current_user: AuthUserRecord,
        source_tag_id: uuid.UUID,
        target_tag_id: uuid.UUID,
        context: RequestContext,
    ) -> tuple[Tag, int]:
        """合并标签: 迁移关联(去重)、重算目标 usage_count、删除源标签, 同一事务提交。"""
        self._require_system_admin(current_user)
        if source_tag_id == target_tag_id:
            raise exceptions.tag_merge_self()
        source = await self._get_tag_or_raise(source_tag_id)
        target = await self._get_tag_or_raise(target_tag_id)
        source_usage = await self._repository.count_tag_files(source.id)
        await self._repository.move_file_tag_links(
            source_tag_id=source.id,
            target_tag_id=target.id,
        )
        usage_count = await self._repository.count_tag_files(target.id)
        await self._repository.set_tag_usage_count(target.id, usage_count)
        source_name = source.name
        await self._repository.delete_tag(source)
        await self._record_admin_audit(
            current_user=current_user,
            action="tag.merge",
            target_type="tag",
            target_id=target.id,
            context=context,
            metadata_json={
                "source_tag_id": str(source_tag_id),
                "source_name": source_name,
                "source_usage_count": source_usage,
                "target_usage_count": usage_count,
            },
        )
        await self._session.commit()
        await self._session.refresh(target)
        return target, usage_count

    async def delete_tag(
        self,
        *,
        current_user: AuthUserRecord,
        tag_id: uuid.UUID,
        context: RequestContext,
    ) -> None:
        self._require_system_admin(current_user)
        tag = await self._get_tag_or_raise(tag_id)
        usage_count = await self._repository.count_tag_files(tag.id)
        if usage_count > 0:
            raise exceptions.tag_in_use()
        tag_name = tag.name
        await self._repository.delete_tag(tag)
        await self._record_admin_audit(
            current_user=current_user,
            action="tag.delete",
            target_type="tag",
            target_id=tag_id,
            context=context,
            metadata_json={"name": tag_name},
        )
        await self._session.commit()

    async def list_review_files(
        self,
        *,
        current_user: AuthUserRecord,
        scope: DepartmentAccessScope,
        context: RequestContext,
        page: int,
        page_size: int,
        search: str | None = None,
        queue: str | None = None,
        extension: str | None = None,
        tag_id: uuid.UUID | None = None,
        department_id: uuid.UUID | None = None,
        sensitive_risk_level: str | None = None,
        sort: str | None = None,
        order: str = "asc",
    ) -> ReviewFilePage:
        self._require_admin(current_user)
        now = datetime.now(UTC)
        expired_claims = await self._repository.release_expired_claims(
            now=now,
            department_ids=scope.query_department_ids(),
            limit=EXPIRED_CLAIM_RELEASE_BATCH_SIZE,
        )
        for file_id, previous_claimant_id in expired_claims:
            await self._record_admin_audit(
                current_user=current_user,
                action="file.review_claim_expired",
                target_type="file",
                target_id=file_id,
                context=context,
                metadata_json={
                    "previous_claimed_by": str(previous_claimant_id),
                    "auto_released": True,
                },
            )
        normalized_extension = clean_optional_text(extension)
        files, total = await self._repository.list_files(
            page=page,
            page_size=page_size,
            now=now,
            current_user_id=current_user.id,
            search=clean_optional_text(search),
            queue=queue,
            extension=normalized_extension.lower() if normalized_extension is not None else None,
            tag_id=tag_id,
            department_ids=scope.query_department_ids(),
            department_id=department_id,
            sensitive_risk_level=sensitive_risk_level,
            sort=sort,
            order=order,
        )
        await self._record_admin_audit(
            current_user=current_user,
            action="file.review_list",
            target_type="file_collection",
            target_id=current_user.id,
            context=context,
            metadata_json={
                "result_count": len(files),
                "total": total,
                "page": page,
                "page_size": page_size,
                "queue": queue,
                "expired_claims_released": len(expired_claims),
                **scope.audit_metadata(),
            },
        )
        await self._session.commit()
        return ReviewFilePage(items=files, total=total, page=page, page_size=page_size)

    async def submit_file_for_review(
        self,
        *,
        current_user: AuthUserRecord,
        file_id: uuid.UUID,
        acknowledge_sensitive_risk: bool = False,
        context: RequestContext,
    ) -> ReviewFileRecord:
        if not has_assigned_department(current_user):
            raise exceptions.department_assignment_required()
        file = await self._get_file_for_update_or_raise(file_id)
        self._require_submit_permission(current_user, file)
        if file.status not in {
            "uploaded",
            "analyzed",
            "analysis_failed",
            "sensitive_review_required",
            "rejected",
        }:
            raise exceptions.review_already_decided()
        previous_status = file.status
        previous_review_status = file.review_status
        await self._ensure_analysis_failed_submission_allowed(file)
        sensitive_risk_level = await self._repository.get_file_sensitive_risk_level(file.id)
        sensitive_submission = (
            previous_status == "sensitive_review_required"
            or await self._repository.file_requires_sensitive_acknowledgement(file.id)
        )
        if sensitive_submission and not acknowledge_sensitive_risk:
            raise exceptions.sensitive_risk_acknowledgement_required()
        self._transition_file(file, "pending_review")
        file.review_status = "pending"
        file.submitted_at, file.review_due_at = await review_submission_times()
        self._clear_claim(file)
        file.review_version += 1
        await self._record_audit(
            current_user=current_user,
            file=file,
            action="file.submit_review",
            context=context,
            metadata_json={
                "previous_status": previous_status,
                "previous_review_status": previous_review_status,
                "review_status": file.review_status,
                "actor_role": current_user.role,
                "submitted_by_owner": current_user.id == file.uploader_id,
                "sensitive_risk_level": sensitive_risk_level,
                "sensitive_risk_acknowledged": (
                    acknowledge_sensitive_risk if sensitive_submission else False
                ),
            },
        )
        await self._append_review_event(
            event_type=events.REVIEW_FILE_SUBMITTED,
            file=file,
            current_user=current_user,
            metadata_json={
                "previous_status": previous_status,
                "previous_review_status": previous_review_status,
                "review_status": file.review_status,
                "sensitive_risk_level": sensitive_risk_level,
                "sensitive_risk_acknowledged": (
                    acknowledge_sensitive_risk if sensitive_submission else False
                ),
            },
        )
        file = await self._repository.update_file(file)
        await self._session.commit()
        return file

    async def approve_file(
        self,
        *,
        current_user: AuthUserRecord,
        scope: DepartmentAccessScope,
        file_id: uuid.UUID,
        request: ReviewDecisionRequest,
        context: RequestContext,
    ) -> ReviewFileRecord:
        self._require_admin(current_user)
        file = await self._get_file_for_update_or_raise(file_id)
        if file.status != "pending_review":
            raise exceptions.review_already_decided()
        self._require_scope_for_file(scope=scope, file=file)
        now = datetime.now(UTC)
        claim_expired = await self._expire_claim_if_needed(
            file=file,
            current_user=current_user,
            context=context,
            now=now,
        )
        try:
            self_review_deadlock_exempt = await self._require_review_decision_permission(
                current_user=current_user,
                scope=scope,
                file=file,
                require_claim=True,
            )
        except exceptions.ReviewError:
            if claim_expired:
                file = await self._repository.update_file(file)
                await self._session.commit()
            raise
        reason = clean_optional_text(request.reason)
        if request.sync_decision == "approve_only" and request.dataset_mapping_id is not None:
            raise exceptions.approve_only_dataset_forbidden()
        if request.sync_decision == "sync" and request.dataset_mapping_id is None:
            raise exceptions.dataset_mapping_required()
        mapping = (
            await self._get_dataset_mapping_or_raise(request.dataset_mapping_id)
            if request.sync_decision == "sync" and request.dataset_mapping_id is not None
            else None
        )
        # 所有同时依赖 mapping/category 的决策统一按 mapping -> category 加锁,
        # 与 mapping 更新路径保持一致, 避免并发审核与配置修改形成锁顺序反转。
        category = (
            await self._get_category_or_raise(request.category_id) if request.category_id else None
        )
        effective_category_id = category.id if category is not None else file.category_id
        if (
            mapping is not None
            and effective_category_id is not None
            and mapping.category_id != effective_category_id
        ):
            raise exceptions.dataset_mapping_not_found()
        if mapping is not None:
            await self._ensure_ragflow_dataset_allowed(
                mapping.ragflow_dataset_id,
                current_user=current_user,
                context=context,
                target_type="file",
                target_id=file.id,
            )
            await self._ensure_ragflow_sync_allowed(file, reason=reason)
        self._transition_file(file, "approved")
        file.review_status = "approved"
        if category is not None:
            file.category_id = category.id
        elif mapping is not None:
            file.category_id = mapping.category_id
        if request.sync_decision == "approve_only":
            file.dataset_mapping_id = None
            file.ragflow_dataset_id = None
        elif mapping is not None:
            file.dataset_mapping_id = mapping.id
            file.ragflow_dataset_id = mapping.ragflow_dataset_id
        if request.sync_decision == "sync":
            if mapping is None:
                raise exceptions.dataset_mapping_required()
            self._transition_file(file, "queued")
        self._clear_claim(file)
        file.review_version += 1
        await self._record_audit(
            current_user=current_user,
            file=file,
            action="file.approve",
            context=context,
            reason=reason,
            metadata_json={
                "sync_decision": request.sync_decision,
                "category_id": str(file.category_id) if file.category_id else None,
                "dataset_mapping_id": str(file.dataset_mapping_id)
                if file.dataset_mapping_id
                else None,
                "file_uploader_id": str(file.uploader_id),
                "is_self_upload": current_user.id == file.uploader_id,
                "self_review_deadlock_exempt": self_review_deadlock_exempt,
                **scope.audit_metadata(file_department_id=file.department_id),
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
                "sync_decision": request.sync_decision,
                "file_department_id": str(file.department_id),
            },
        )
        file = await self._repository.update_file(file)
        await self._session.commit()
        return file

    async def claim_file_for_review(
        self,
        *,
        current_user: AuthUserRecord,
        scope: DepartmentAccessScope,
        file_id: uuid.UUID,
        context: RequestContext,
    ) -> ReviewFileRecord:
        self._require_admin(current_user)
        file = await self._get_file_for_update_or_raise(file_id)
        if file.status != "pending_review":
            raise exceptions.review_already_decided()
        await self._require_review_decision_permission(
            current_user=current_user,
            scope=scope,
            file=file,
            require_claim=False,
        )
        now = datetime.now(UTC)
        await self._expire_claim_if_needed(
            file=file,
            current_user=current_user,
            context=context,
            now=now,
        )
        if file.claimed_by == current_user.id and self._claim_is_active(file, now=now):
            await self._record_audit(
                current_user=current_user,
                file=file,
                action="file.review_claim",
                context=context,
                metadata_json={
                    "claimed_by": str(current_user.id),
                    "idempotent": True,
                    **scope.audit_metadata(file_department_id=file.department_id),
                },
            )
            await self._session.commit()
            return file
        if file.claimed_by is not None:
            raise exceptions.review_claim_conflict()
        file.claimed_at, file.claim_expires_at = await review_claim_expiry(now=now)
        file.claimed_by = current_user.id
        file.review_status = "in_review"
        file.review_version += 1
        await self._record_audit(
            current_user=current_user,
            file=file,
            action="file.review_claim",
            context=context,
            metadata_json={
                "claimed_by": str(current_user.id),
                "claimed_at": file.claimed_at.isoformat(),
                "claim_expires_at": file.claim_expires_at.isoformat(),
                **scope.audit_metadata(file_department_id=file.department_id),
            },
        )
        file = await self._repository.update_file(file)
        await self._session.commit()
        return file

    async def release_file_review_claim(
        self,
        *,
        current_user: AuthUserRecord,
        scope: DepartmentAccessScope,
        file_id: uuid.UUID,
        reason: str | None,
        context: RequestContext,
    ) -> ReviewFileRecord:
        self._require_admin(current_user)
        file = await self._get_file_for_update_or_raise(file_id)
        if file.status != "pending_review":
            raise exceptions.review_already_decided()
        self._require_scope_for_file(scope=scope, file=file)
        now = datetime.now(UTC)
        if await self._expire_claim_if_needed(
            file=file,
            current_user=current_user,
            context=context,
            now=now,
        ):
            file = await self._repository.update_file(file)
            await self._session.commit()
            return file
        if file.claimed_by is None:
            await self._record_audit(
                current_user=current_user,
                file=file,
                action="file.review_claim_release",
                context=context,
                metadata_json={
                    "idempotent": True,
                    "no_claim": True,
                    **scope.audit_metadata(file_department_id=file.department_id),
                },
            )
            await self._session.commit()
            return file
        cleaned_reason = clean_optional_text(reason)
        force_release = file.claimed_by != current_user.id
        if force_release and current_user.role != SYSTEM_ADMIN_ROLE:
            raise exceptions.permission_denied()
        if force_release and cleaned_reason is None:
            raise exceptions.force_release_reason_required()
        previous_claimant_id = file.claimed_by
        self._clear_claim(file)
        file.review_status = "pending"
        file.review_version += 1
        await self._record_audit(
            current_user=current_user,
            file=file,
            action="file.review_claim_release",
            context=context,
            reason=cleaned_reason,
            metadata_json={
                "previous_claimed_by": str(previous_claimant_id),
                "force_release": force_release,
                **scope.audit_metadata(file_department_id=file.department_id),
            },
        )
        file = await self._repository.update_file(file)
        await self._session.commit()
        return file

    async def reject_file(
        self,
        *,
        current_user: AuthUserRecord,
        scope: DepartmentAccessScope,
        file_id: uuid.UUID,
        reason: str,
        context: RequestContext,
    ) -> ReviewFileRecord:
        self._require_admin(current_user)
        file = await self._get_file_for_update_or_raise(file_id)
        if file.status != "pending_review":
            raise exceptions.review_already_decided()
        self._require_scope_for_file(scope=scope, file=file)
        now = datetime.now(UTC)
        claim_expired = await self._expire_claim_if_needed(
            file=file,
            current_user=current_user,
            context=context,
            now=now,
        )
        try:
            self_review_deadlock_exempt = await self._require_review_decision_permission(
                current_user=current_user,
                scope=scope,
                file=file,
                require_claim=True,
            )
        except exceptions.ReviewError:
            if claim_expired:
                file = await self._repository.update_file(file)
                await self._session.commit()
            raise
        cleaned_reason = clean_optional_text(reason)
        if cleaned_reason is None:
            raise exceptions.rejection_reason_required()
        self._transition_file(file, "rejected")
        file.review_status = "rejected"
        self._clear_claim(file)
        file.review_version += 1
        await self._record_audit(
            current_user=current_user,
            file=file,
            action="file.reject",
            context=context,
            reason=cleaned_reason,
            metadata_json={
                "file_uploader_id": str(file.uploader_id),
                "is_self_upload": current_user.id == file.uploader_id,
                "self_review_deadlock_exempt": self_review_deadlock_exempt,
                **scope.audit_metadata(file_department_id=file.department_id),
            },
        )
        await self._append_review_event(
            event_type=events.REVIEW_FILE_REJECTED,
            file=file,
            current_user=current_user,
            metadata_json={"reason": cleaned_reason},
        )
        file = await self._repository.update_file(file)
        await self._session.commit()
        return file

    async def update_file_classification(
        self,
        *,
        current_user: AuthUserRecord,
        scope: DepartmentAccessScope,
        file_id: uuid.UUID,
        request: UpdateFileClassificationRequest,
        context: RequestContext,
    ) -> ReviewFileRecord:
        self._require_admin(current_user)
        fields_set = request.model_fields_set
        if not fields_set & {"category_id", "dataset_mapping_id"}:
            raise exceptions.classification_patch_empty()
        file = await self._get_file_for_update_or_raise(file_id)
        if file.status != "pending_review":
            raise exceptions.classification_draft_locked()
        self._require_scope_for_file(scope=scope, file=file)
        now = datetime.now(UTC)
        claim_expired = await self._expire_claim_if_needed(
            file=file,
            current_user=current_user,
            context=context,
            now=now,
        )
        try:
            await self._require_review_decision_permission(
                current_user=current_user,
                scope=scope,
                file=file,
                require_claim=True,
            )
        except exceptions.ReviewError:
            if claim_expired:
                file = await self._repository.update_file(file)
                await self._session.commit()
            raise
        if file.ragflow_document_id is not None:
            raise exceptions.classification_draft_locked()
        if await self._repository.has_active_ragflow_upload_task(file.id):
            raise exceptions.classification_draft_locked()
        mapping = None
        if "dataset_mapping_id" in fields_set and request.dataset_mapping_id is not None:
            mapping = await self._get_dataset_mapping_or_raise(request.dataset_mapping_id)
        category = None
        if "category_id" in fields_set and request.category_id is not None:
            category = await self._get_category_or_raise(request.category_id)

        target_category_id = file.category_id
        target_mapping_id = file.dataset_mapping_id
        if "category_id" in fields_set:
            target_category_id = category.id if category is not None else None
            if "dataset_mapping_id" not in fields_set:
                target_mapping_id = None
        if "dataset_mapping_id" in fields_set:
            target_mapping_id = mapping.id if mapping is not None else None
            if mapping is not None and "category_id" not in fields_set:
                target_category_id = mapping.category_id

        if mapping is not None and target_category_id != mapping.category_id:
            raise exceptions.dataset_mapping_not_found()
        if mapping is not None:
            await self._ensure_ragflow_dataset_allowed(
                mapping.ragflow_dataset_id,
                current_user=current_user,
                context=context,
                target_type="file",
                target_id=file.id,
            )
        file.category_id = target_category_id
        file.dataset_mapping_id = target_mapping_id
        file.ragflow_dataset_id = None
        file.review_version += 1
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
                "draft": True,
                "review_version": file.review_version,
                "file_uploader_id": str(file.uploader_id),
                **scope.audit_metadata(file_department_id=file.department_id),
            },
        )
        file = await self._repository.update_file(file)
        await self._session.commit()
        return file

    async def update_file_draft(
        self,
        *,
        current_user: AuthUserRecord,
        file_id: uuid.UUID,
        request: FileDraftUpdateRequest,
        context: RequestContext,
    ) -> ReviewFileRecord:
        if not has_assigned_department(current_user):
            raise exceptions.department_assignment_required()
        current = await self._repository.get_file(file_id)
        if current is None or current.uploader_id != current_user.id:
            raise exceptions.file_not_found()
        if current.status not in DRAFT_EDITABLE_STATUSES:
            raise exceptions.draft_metadata_locked()

        values: dict[str, object] = {}
        expiry_notification_basis_changed = False
        changed_fields = request.model_fields_set & {
            "title",
            "description",
            "visibility",
            "owner_id",
            "expires_at",
        }
        if "title" in changed_fields:
            values["title"] = request.title
        if "description" in changed_fields:
            values["description"] = clean_optional_text(request.description)
        if "visibility" in changed_fields:
            if request.visibility is None:
                raise exceptions.invalid_visibility()
            values["visibility"] = request.visibility
        if "owner_id" in changed_fields:
            owner_id = request.owner_id
            if owner_id is None or not await self._repository.is_valid_document_owner(
                owner_id=owner_id,
                department_id=current.department_id,
            ):
                raise exceptions.invalid_document_owner()
            values["owner_id"] = owner_id
            expiry_notification_basis_changed = owner_id != current.owner_id
        if "expires_at" in changed_fields:
            expires_at = request.expires_at
            if expires_at is not None and expires_at.tzinfo is None:
                raise exceptions.invalid_expiry()
            normalized_expiry = expires_at.astimezone(UTC) if expires_at is not None else None
            current_expiry = (
                current.expires_at.astimezone(UTC) if current.expires_at is not None else None
            )
            expiry_changed = normalized_expiry != current_expiry
            expiry_notification_basis_changed = expiry_notification_basis_changed or expiry_changed
            if expiry_changed:
                values["expires_at"] = normalized_expiry
                values["expiry_status"] = effective_expiry_status(
                    expires_at=normalized_expiry,
                    stored_status=None,
                )
        if expiry_notification_basis_changed:
            values["expiry_warning_sent_at"] = None
            values["expiry_expired_sent_at"] = None

        file = await self._repository.update_owner_draft_metadata(
            file_id=file_id,
            uploader_id=current_user.id,
            expected_version=request.expected_version,
            editable_statuses=DRAFT_EDITABLE_STATUSES,
            values=values,
        )
        if file is None:
            raise exceptions.file_version_conflict()

        await record_audit_log(
            self._session,
            actor_id=current_user.id,
            action="file.update_draft",
            target_type="file",
            target_id=file.id,
            ip_address=context.ip_address,
            user_agent=context.user_agent,
            metadata_json={
                "changed_fields": sorted(changed_fields),
                "expected_version": request.expected_version,
                "review_version": file.review_version,
                "file_department_id": str(file.department_id),
            },
        )
        await self._session.commit()
        return file

    def _require_scope_for_file(
        self,
        *,
        scope: DepartmentAccessScope,
        file: ReviewFileRecord,
    ) -> None:
        # 越权访问他部门文件统一伪装成不存在(404), 避免 403/404 差异泄露存在性;
        # 自审拒绝(本人上传)仍走下方 403, 因 owner 已知文件存在, 非信息泄露
        if not scope.covers_department(file.department_id):
            raise exceptions.file_not_found()

    async def _require_review_decision_permission(
        self,
        *,
        current_user: AuthUserRecord,
        scope: DepartmentAccessScope,
        file: ReviewFileRecord,
        require_claim: bool,
    ) -> bool:
        self._require_scope_for_file(scope=scope, file=file)
        self_review_deadlock_exempt = False
        if current_user.id == file.uploader_id:
            if current_user.role != SYSTEM_ADMIN_ROLE:
                raise exceptions.permission_denied()
            has_reviewer = await get_department_scope_store(self._session).has_non_self_reviewer(
                file_department_id=file.department_id,
                uploader_id=file.uploader_id,
            )
            if not has_reviewer:
                self_review_deadlock_exempt = True
            else:
                raise exceptions.permission_denied()
        if require_claim and file.claimed_by != current_user.id:
            raise exceptions.review_claim_required()
        return self_review_deadlock_exempt

    async def _get_tag_or_raise(self, tag_id: uuid.UUID) -> Tag:
        tag = await self._repository.get_tag(tag_id)
        if tag is None:
            raise exceptions.tag_not_found()
        return tag

    async def _get_category_or_raise(self, category_id: uuid.UUID) -> Category:
        category = await self._repository.get_category_for_update(category_id)
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
        mapping = await self._repository.get_dataset_mapping_for_update(mapping_id)
        if mapping is None:
            raise exceptions.dataset_mapping_not_found()
        return mapping

    async def _get_file_or_raise(self, file_id: uuid.UUID) -> ReviewFileRecord:
        file = await self._repository.get_file(file_id)
        if file is None:
            raise exceptions.file_not_found()
        return file

    async def _get_file_for_update_or_raise(self, file_id: uuid.UUID) -> ReviewFileRecord:
        file = await self._repository.get_file_for_update(file_id)
        if file is None:
            raise exceptions.file_not_found()
        return file

    async def _is_critical_sensitive_file(self, file_id: uuid.UUID) -> bool:
        risk_level = await self._repository.get_file_sensitive_risk_level(file_id)
        return risk_level == "critical"

    async def _ensure_ragflow_sync_allowed(
        self,
        file: ReviewFileRecord,
        *,
        reason: str | None = None,
    ) -> None:
        risk_level = await self._repository.get_file_sensitive_risk_level(file.id)
        if risk_level == "critical":
            raise exceptions.invalid_state()
        if risk_level == "high":
            if not await allow_high_risk_sync():
                raise exceptions.high_risk_sync_not_allowed()
            if reason is None:
                raise exceptions.high_risk_reason_required()
        analysis_status = await self._repository.get_file_analysis_status(file.id)
        if analysis_status != "failed":
            return
        allow_sync = await self._repository.get_ai_feature_enabled(
            "allow_sync_when_analysis_failed"
        )
        if allow_sync is None:
            allow_sync = get_settings().ai_allow_sync_when_analysis_failed
        if not allow_sync:
            raise exceptions.invalid_state()

    async def _ensure_analysis_failed_submission_allowed(self, file: ReviewFileRecord) -> None:
        if file.status != "analysis_failed":
            return
        allow_submit = await self._repository.get_ai_feature_enabled(
            "allow_sync_when_analysis_failed"
        )
        if allow_submit is None:
            allow_submit = get_settings().ai_allow_sync_when_analysis_failed
        if not allow_submit:
            raise exceptions.analysis_failed_submission_disabled()

    async def _expire_claim_if_needed(
        self,
        *,
        file: ReviewFileRecord,
        current_user: AuthUserRecord,
        context: RequestContext,
        now: datetime,
    ) -> bool:
        claim_values = (file.claimed_by, file.claimed_at, file.claim_expires_at)
        if all(value is None for value in claim_values):
            return False
        malformed_claim = any(value is None for value in claim_values)
        if (
            not malformed_claim
            and file.claim_expires_at is not None
            and file.claim_expires_at > now
        ):
            return False
        previous_claimant_id = file.claimed_by
        self._clear_claim(file)
        file.review_status = "pending"
        file.review_version += 1
        await self._record_audit(
            current_user=current_user,
            file=file,
            action="file.review_claim_expired",
            context=context,
            metadata_json={
                "previous_claimed_by": (
                    str(previous_claimant_id) if previous_claimant_id is not None else None
                ),
                "auto_released": True,
                "invalid_claim_state": malformed_claim,
            },
        )
        return True

    @staticmethod
    def _claim_is_active(file: ReviewFileRecord, *, now: datetime) -> bool:
        return (
            file.claimed_by is not None
            and file.claimed_at is not None
            and file.claim_expires_at is not None
            and file.claim_expires_at > now
        )

    @staticmethod
    def _clear_claim(file: ReviewFileRecord) -> None:
        file.claimed_by = None
        file.claimed_by_name = None
        file.claimed_at = None
        file.claim_expires_at = None

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

    def _require_submit_permission(
        self,
        current_user: AuthUserRecord,
        file: ReviewFileRecord,
    ) -> None:
        if file.uploader_id == current_user.id:
            return
        raise exceptions.permission_denied()

    def _require_system_admin(self, current_user: AuthUserRecord) -> None:
        if current_user.role != SYSTEM_ADMIN_ROLE:
            raise exceptions.permission_denied()

    def _validate_visibility(self, visibility: str) -> None:
        if visibility not in VALID_VISIBILITIES:
            raise exceptions.invalid_visibility()

    async def _ensure_ragflow_dataset_allowed(
        self,
        dataset_id: str,
        *,
        current_user: AuthUserRecord,
        context: RequestContext,
        target_type: str,
        target_id: uuid.UUID,
    ) -> None:
        runtime_settings = await resolve_ragflow_runtime_settings()
        if is_ragflow_dataset_allowed(dataset_id, runtime_settings):
            return
        await self._session.rollback()
        await self._record_admin_audit(
            current_user=current_user,
            action="dataset_mapping.ragflow_dataset_denied",
            target_type=target_type,
            target_id=target_id,
            context=context,
            metadata_json={
                "ragflow_dataset_id": dataset_id,
                "allowed_dataset_ids_count": len(runtime_settings.allowed_dataset_ids),
            },
        )
        await self._session.commit()
        raise exceptions.dataset_not_allowed()


async def allow_high_risk_sync() -> bool:
    value = await get_config("ragflow.allow_high_risk_sync")
    return value is True


def clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None
