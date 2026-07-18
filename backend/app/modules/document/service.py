from __future__ import annotations

import re
import uuid
import zipfile
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from io import BytesIO
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Literal, Protocol, cast

import filetype
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access_scope import DepartmentAccessScope
from app.core.audit import record_admin_audit_log, record_audit_log
from app.core.config import Settings
from app.core.document_state import DocumentStateError, DocumentStateMachine
from app.core.identity import has_assigned_department
from app.core.outbox import OutboxRepository
from app.core.review_policy import review_submission_times
from app.core.runtime_config import get_config, stored_config_is_exact_false
from app.modules.user.schemas import AuthUserRecord

from . import events, exceptions
from .models import File
from .repository import (
    ABANDONED_VERSION_STATUSES,
    HIDDEN_FILE_STATUSES,
    DocumentRepository,
    ExpiryScanCandidate,
)
from .schemas import FileAnalysisDetail

WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
FILENAME_UNSAFE_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
EXTENSION_MIME_MAP = {
    "pdf": {"application/pdf"},
    "docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    "xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
    "pptx": {"application/vnd.openxmlformats-officedocument.presentationml.presentation"},
    "txt": {"text/plain"},
    "md": {"text/markdown", "text/plain"},
    "csv": {"text/csv", "text/plain"},
}
VALID_VISIBILITIES = {"private", "department", "company"}
REVIEW_FILE_SUBMITTED_EVENT = "review.file.submitted"
PDF_MAGIC = b"%PDF-"
PDF_EOF_MARKER = b"%%EOF"
PDF_STARTXREF_MARKER = b"startxref"
OOXML_REQUIRED_ENTRIES = {
    "docx": "word/document.xml",
    "xlsx": "xl/workbook.xml",
    "pptx": "ppt/presentation.xml",
}
UNSUPPORTED_LEGACY_EXTENSIONS = {"doc", "xls", "ppt"}
TEXT_EXTENSIONS = {"txt", "md", "csv"}
ADMIN_ROLES = {"dept_admin", "system_admin"}
EXTRACTED_TEXT_PREVIEW_CHARS = 500
MAX_IN_MEMORY_UPLOAD_BYTES = 200 * 1024 * 1024
# 重新分析的合法源状态: 失败/已分析可重跑; 中间态属于 R1 遗留卡死文件, 允许救回
REANALYZE_SOURCE_STATUSES = {
    "analysis_failed",
    "analyzed",
    "extracting_text",
    "analysis_queued",
    "analyzing",
}


class DocumentStorage(Protocol):
    async def put_object(
        self,
        *,
        bucket: str,
        object_key: str,
        data: bytes,
        content_type: str,
    ) -> None:
        pass

    async def delete_object(self, *, bucket: str, object_key: str) -> None:
        pass

    async def get_object(self, *, bucket: str, object_key: str) -> bytes:
        pass

    async def open_object(
        self,
        *,
        bucket: str,
        object_key: str,
        offset: int = 0,
        length: int | None = None,
    ) -> DocumentContentStream:
        pass


class DocumentContentStream(Protocol):
    def __aiter__(self) -> AsyncIterator[bytes]:
        pass

    async def aclose(self) -> None:
        pass


@dataclass(frozen=True)
class UploadedFileResult:
    file: File
    duplicate_file_id: uuid.UUID | None


@dataclass(frozen=True)
class FileDetailResult:
    file: File
    category_name: str | None
    analysis: FileAnalysisDetail | None
    sync_error: str | None
    version_chain: list[File]


@dataclass(frozen=True)
class FileContentResult:
    file: File


@dataclass(frozen=True)
class FilePage:
    items: list[File]
    total: int
    page: int
    page_size: int


@dataclass(frozen=True)
class OwnerOptionPage:
    items: list[tuple[uuid.UUID, str]]
    total: int
    page: int
    page_size: int


class DocumentService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        repository: DocumentRepository,
        settings: Settings,
        storage: DocumentStorage,
    ) -> None:
        self._session = session
        self._repository = repository
        self._settings = settings
        self._storage = storage

    async def upload_file(
        self,
        *,
        current_user: AuthUserRecord,
        original_filename: str,
        content_type: str | None,
        data: bytes,
        description: str | None,
        visibility: str,
        submit_after_upload: bool,
        ai_analysis_enabled: bool | None,
        client_ip: str,
        user_agent: str,
        replaces_file_id: uuid.UUID | None = None,
    ) -> UploadedFileResult:
        await ensure_upload_allowed(current_user)
        await self._validate_size(len(data))
        sanitized_name, extension = sanitize_filename(original_filename)
        await self._validate_extension(extension)
        mime_type = self._validate_mime_type(
            data=data,
            extension=extension,
            declared_mime_type=content_type,
        )
        if visibility not in VALID_VISIBILITIES:
            raise exceptions.invalid_visibility()
        await self._validate_quota(uploader_id=current_user.id, incoming_size=len(data))

        predecessor = None
        if replaces_file_id is not None:
            predecessor = await self._repository.get_by_id(replaces_file_id)
            self._validate_replacement_predecessor(
                current_user=current_user, predecessor=predecessor
            )
        effective_ai_analysis_enabled = await self._resolve_upload_ai_analysis_enabled(
            requested_ai_analysis_enabled=ai_analysis_enabled,
        )
        replacement_remote_action = None
        if predecessor is not None:
            keep_replaced_remote = await get_config("ragflow.keep_replaced_remote")
            delete_authorized = (
                keep_replaced_remote is False
                and await stored_config_is_exact_false("ragflow.keep_replaced_remote")
            )
            # Both resolved and stored observations must be exactly false.
            replacement_remote_action = "delete" if delete_authorized else "archive"

        file_hash = sha256(data).hexdigest()
        duplicate = await self._repository.find_first_by_hash_for_uploader(
            file_hash=file_hash,
            uploader_id=current_user.id,
        )
        file_id = uuid.uuid4()
        if duplicate is None:
            stored_name = f"{file_id}-{sanitized_name}"
            object_key = f"uploads/{current_user.id}/{file_id}/{stored_name}"
            bucket = self._settings.minio_bucket
            try:
                await self._storage.put_object(
                    bucket=bucket,
                    object_key=object_key,
                    data=data,
                    content_type=mime_type,
                )
            except Exception as exc:
                raise exceptions.storage_error() from exc
            duplicate_file_id = None
        else:
            stored_name = duplicate.stored_name
            object_key = duplicate.object_key
            bucket = duplicate.bucket
            duplicate_file_id = duplicate.id

        file = File(
            id=file_id,
            original_name=sanitized_name,
            title=sanitized_name,
            stored_name=stored_name,
            extension=extension,
            mime_type=mime_type,
            size=len(data),
            hash=file_hash,
            storage_type="minio",
            bucket=bucket,
            object_key=object_key,
            uploader_id=current_user.id,
            owner_id=current_user.id,
            department_id=current_user.department_id,
            department=current_user.department_name or current_user.department,
            visibility=visibility,
            description=clean_optional_text(description),
            tags=[],
            status="uploaded",
            review_status="pending",
            ai_analysis_enabled_at_upload=effective_ai_analysis_enabled,
            series_id=predecessor.series_id if predecessor is not None else file_id,
            version_number=(predecessor.version_number + 1 if predecessor is not None else 1),
            replaces_file_id=replaces_file_id,
            replacement_remote_action=replacement_remote_action,
            is_current_version=predecessor is None,
            remote_visibility="candidate",
            version_switch_status="pending" if predecessor is not None else "not_required",
            # uploaded 是产品语义上的草稿。AI 开启时必须持久化自动提交意图,
            # 否则异步 worker 完成分析后无法判断是否应进入审核队列。
            ai_config_snapshot={"submit_after_upload": submit_after_upload},
        )
        try:
            if replaces_file_id is not None:
                locked_predecessor = await self._repository.get_by_id_for_update(replaces_file_id)
                self._validate_replacement_predecessor(
                    current_user=current_user,
                    predecessor=locked_predecessor,
                )
                if locked_predecessor is None:
                    raise exceptions.invalid_replacement()
                if await self._repository.has_direct_replacement(locked_predecessor.id):
                    raise exceptions.replacement_conflict()
                chain = await self._repository.lock_version_series(locked_predecessor.series_id)
                self._validate_version_chain(
                    chain=chain,
                    expected_current_id=locked_predecessor.id,
                )
                file.series_id = locked_predecessor.series_id
                file.version_number = max(item.version_number for item in chain) + 1
                file.replaces_file_id = locked_predecessor.id
                inherited_owner = (
                    await self._repository.get_valid_owner(
                        owner_id=locked_predecessor.owner_id,
                        department_id=locked_predecessor.department_id,
                    )
                    if locked_predecessor.owner_id is not None
                    else None
                )
                file.owner_id = (
                    inherited_owner.id if inherited_owner is not None else current_user.id
                )
                file.expires_at = locked_predecessor.expires_at
                file.expiry_status = locked_predecessor.expiry_status
                file.expiry_warning_sent_at = None
                file.expiry_expired_sent_at = None
                file.is_current_version = False
            await self._repository.add(file)
            await self._append_file_uploaded_event(file)
            await self._record_upload_audit(
                file=file,
                current_user=current_user,
                duplicate_file_id=duplicate_file_id,
                client_ip=client_ip,
                user_agent=user_agent,
            )
            if submit_after_upload and not effective_ai_analysis_enabled:
                previous_status = file.status
                file.status = self._transition_or_raise(file.status, "pending_review")
                file.submitted_at, file.review_due_at = await review_submission_times()
                file.review_version += 1
                await self._record_submit_review_audit(
                    file=file,
                    current_user=current_user,
                    previous_status=previous_status,
                    client_ip=client_ip,
                    user_agent=user_agent,
                )
                await self._append_review_submitted_event(
                    file=file,
                    current_user=current_user,
                    previous_status=previous_status,
                )
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            if duplicate is None:
                with suppress(Exception):
                    await self._storage.delete_object(bucket=bucket, object_key=object_key)
            if replaces_file_id is not None:
                raise exceptions.replacement_conflict() from exc
            raise
        except Exception:
            await self._session.rollback()
            if duplicate is None:
                with suppress(Exception):
                    await self._storage.delete_object(bucket=bucket, object_key=object_key)
            raise
        await self._session.refresh(file)
        dynamic_file = cast(Any, file)
        dynamic_file.department_name = current_user.department_name or current_user.department
        dynamic_file.department_code = current_user.department_code
        return UploadedFileResult(file=file, duplicate_file_id=duplicate_file_id)

    async def _resolve_upload_ai_analysis_enabled(
        self,
        *,
        requested_ai_analysis_enabled: bool | None,
    ) -> bool:
        if requested_ai_analysis_enabled is False:
            return False
        return await self._is_ai_analysis_enabled()

    async def _record_upload_audit(
        self,
        *,
        file: File,
        current_user: AuthUserRecord,
        duplicate_file_id: uuid.UUID | None,
        client_ip: str,
        user_agent: str,
    ) -> None:
        await record_audit_log(
            self._session,
            actor_id=current_user.id,
            action="file.upload",
            target_type="file",
            target_id=file.id,
            ip_address=client_ip,
            user_agent=user_agent[:512] or "unknown",
            metadata_json={
                "original_name": file.original_name,
                "extension": file.extension,
                "mime_type": file.mime_type,
                "size": file.size,
                "visibility": file.visibility,
                "file_department_id": str(file.department_id),
                "series_id": str(file.series_id),
                "version_number": file.version_number,
                "replaces_file_id": str(file.replaces_file_id) if file.replaces_file_id else None,
                "replacement_remote_action": file.replacement_remote_action,
                "owner_id": str(file.owner_id) if file.owner_id is not None else None,
                "expires_at": file.expires_at.isoformat() if file.expires_at is not None else None,
                "expiry_status": file.expiry_status,
                "governance_inherited_from_predecessor": (file.replaces_file_id is not None),
                "duplicate": duplicate_file_id is not None,
                "duplicate_file_id": (
                    str(duplicate_file_id) if duplicate_file_id is not None else None
                ),
                "ai_analysis_enabled_at_upload": file.ai_analysis_enabled_at_upload,
            },
        )

    async def _record_submit_review_audit(
        self,
        *,
        file: File,
        current_user: AuthUserRecord,
        previous_status: str,
        client_ip: str,
        user_agent: str,
    ) -> None:
        await record_audit_log(
            self._session,
            actor_id=current_user.id,
            action="file.submit_review",
            target_type="file",
            target_id=file.id,
            ip_address=client_ip,
            user_agent=user_agent[:512] or "unknown",
            metadata_json={
                "original_name": file.original_name,
                "previous_status": previous_status,
                "status": file.status,
                "review_status": file.review_status,
                "actor_role": current_user.role,
                "file_department_id": str(file.department_id),
            },
        )

    async def list_my_files(
        self,
        current_user: AuthUserRecord,
        *,
        page: int,
        page_size: int,
        search: str | None = None,
        status: str | None = None,
        extension: str | None = None,
        tag_id: uuid.UUID | None = None,
        expiry_status: str | None = None,
        sort: str = "uploaded_at",
        order: str = "desc",
    ) -> FilePage:
        items, total = await self._repository.list_for_uploader(
            current_user.id,
            page=page,
            page_size=page_size,
            search=clean_optional_text(search),
            status=clean_optional_text(status),
            extension=clean_optional_text(extension),
            tag_id=tag_id,
            expiry_status=expiry_status,
            sort=sort,
            order=order,
        )
        await self._attach_owner_names(items)
        return FilePage(items=items, total=total, page=page, page_size=page_size)

    async def list_responsible_files(
        self,
        current_user: AuthUserRecord,
        *,
        page: int,
        page_size: int,
        search: str | None = None,
        status: str | None = None,
        extension: str | None = None,
        expiry_status: str | None = None,
        sort: str = "uploaded_at",
        order: str = "desc",
    ) -> FilePage:
        if not has_assigned_department(current_user):
            return FilePage(items=[], total=0, page=page, page_size=page_size)
        items, total = await self._repository.list_for_owner(
            current_user.id,
            department_id=current_user.department_id,
            page=page,
            page_size=page_size,
            search=clean_optional_text(search),
            status=clean_optional_text(status),
            extension=clean_optional_text(extension),
            expiry_status=expiry_status,
            sort=sort,
            order=order,
        )
        await self._attach_owner_names(items)
        return FilePage(items=items, total=total, page=page, page_size=page_size)

    async def list_owner_options(
        self,
        *,
        current_user: AuthUserRecord,
        search: str | None,
        page: int,
        page_size: int,
    ) -> OwnerOptionPage:
        if not has_assigned_department(current_user):
            raise exceptions.department_assignment_required()
        owners, total = await self._repository.list_owner_options(
            department_id=current_user.department_id,
            search=clean_optional_text(search),
            page=page,
            page_size=page_size,
        )
        return OwnerOptionPage(
            items=[(owner.id, owner.name) for owner in owners],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def get_my_file(self, *, current_user: AuthUserRecord, file_id: uuid.UUID) -> File:
        file = await self._repository.get_for_uploader(
            file_id=file_id,
            uploader_id=current_user.id,
        )
        if file is None:
            raise exceptions.file_not_found()
        return file

    async def get_file_detail(
        self,
        *,
        current_user: AuthUserRecord,
        file_id: uuid.UUID,
        client_ip: str,
        user_agent: str,
    ) -> FileDetailResult:
        # 详情查看是只读操作, 不需要阻塞审核或归档等状态变更。
        file = await self._repository.get_by_id(file_id)
        if file is None or file.status in HIDDEN_FILE_STATUSES:
            raise exceptions.file_not_found()
        access_role = self._resolve_file_read_access(
            current_user=current_user,
            file=file,
        )
        is_delegated_owner = access_role == "delegated_owner"
        is_admin_action = current_user.role in ADMIN_ROLES

        await self._attach_owner_names([file])
        version_chain = (
            [file]
            if is_delegated_owner
            else await self._repository.list_version_chain(file.series_id)
        )
        analysis = await self._repository.get_analysis_for_file(file.id)
        result = FileDetailResult(
            file=file,
            category_name=await self._repository.get_category_name(file.id),
            analysis=(
                FileAnalysisDetail(
                    status=analysis.status,
                    engine_type=analysis.engine_type,
                    provider_name=analysis.provider_name,
                    model_name=analysis.model_name,
                    prompt_template_key=analysis.prompt_template_key,
                    prompt_version=analysis.prompt_version,
                    input_char_count=analysis.input_char_count,
                    input_sha256=analysis.input_sha256,
                    category_count=analysis.category_count,
                    input_truncated=analysis.input_truncated,
                    attempt_number=analysis.attempt_number,
                    prompt_tokens=analysis.prompt_tokens,
                    completion_tokens=analysis.completion_tokens,
                    latency_ms=analysis.latency_ms,
                    failure_category=analysis.failure_category,
                    cost_status=analysis.cost_status,
                    estimated_cost_microunits=(
                        analysis.estimated_cost_microunits
                        if analysis.cost_status == "known"
                        else None
                    ),
                    cost_currency=analysis.cost_currency,
                    summary=analysis.summary,
                    sensitive_risk_level=analysis.sensitive_risk_level,
                    quality_score=analysis.quality_score,
                    extracted_text_preview=(
                        analysis.extracted_text[:EXTRACTED_TEXT_PREVIEW_CHARS]
                        if analysis.extracted_text is not None
                        else None
                    ),
                    tables_json=analysis.tables_json,
                    table_count=analysis.table_count,
                    similar_file_ids=analysis.similar_file_ids,
                    error_message=analysis.error_message,
                    finished_at=analysis.finished_at,
                )
                if analysis is not None
                else None
            ),
            sync_error=await self._repository.get_latest_failed_sync_error(file.id),
            version_chain=version_chain,
        )
        # 审计在全部查询成功后写入并与之同事务提交, 避免 admin/员工两条路径事务边界不一致,
        # 也保证只记录实际成功返回的查看操作。
        if is_admin_action:
            await record_admin_audit_log(
                self._session,
                actor_id=current_user.id,
                action="file.view_detail",
                target_type="file",
                target_id=file.id,
                ip_address=client_ip,
                user_agent=user_agent[:512] or "unknown",
                metadata_json={
                    "original_name": file.original_name,
                    "uploader_id": str(file.uploader_id),
                    "access_role": access_role,
                },
            )
        elif is_delegated_owner:
            await record_audit_log(
                self._session,
                actor_id=current_user.id,
                action="file.view_detail",
                target_type="file",
                target_id=file.id,
                ip_address=client_ip,
                user_agent=user_agent[:512] or "unknown",
                metadata_json={
                    "original_name": file.original_name,
                    "uploader_id": str(file.uploader_id),
                    "access_role": "delegated_owner",
                },
            )
        if is_admin_action or is_delegated_owner:
            await self._session.commit()
        return result

    async def get_file_content(
        self,
        *,
        current_user: AuthUserRecord,
        file_id: uuid.UUID,
        disposition: str,
        client_ip: str,
        user_agent: str,
    ) -> FileContentResult:
        # 原件响应是长生命周期流, 授权查询不能持有行锁直到客户端读完。
        file = await self._repository.get_by_id(file_id)
        if file is None or file.status in HIDDEN_FILE_STATUSES:
            raise exceptions.file_not_found()
        access_role = self._resolve_file_read_access(
            current_user=current_user,
            file=file,
        )
        is_delegated_owner = access_role == "delegated_owner"
        is_admin_action = current_user.role in ADMIN_ROLES
        if is_admin_action:
            await record_admin_audit_log(
                self._session,
                actor_id=current_user.id,
                action="file.view_content",
                target_type="file",
                target_id=file.id,
                ip_address=client_ip,
                user_agent=user_agent[:512] or "unknown",
                metadata_json={
                    "original_name": file.original_name,
                    "uploader_id": str(file.uploader_id),
                    "disposition": disposition,
                    "audit_semantics": "access_authorized_before_stream_open",
                    "stream_completion_confirmed": False,
                    "access_role": access_role,
                },
            )
        elif is_delegated_owner:
            await record_audit_log(
                self._session,
                actor_id=current_user.id,
                action="file.view_content",
                target_type="file",
                target_id=file.id,
                ip_address=client_ip,
                user_agent=user_agent[:512] or "unknown",
                metadata_json={
                    "original_name": file.original_name,
                    "uploader_id": str(file.uploader_id),
                    "disposition": disposition,
                    "audit_semantics": "access_authorized_before_stream_open",
                    "stream_completion_confirmed": False,
                    "access_role": "delegated_owner",
                },
            )
        # StreamingResponse 在 service 返回后才开始读取对象。这里统一结束授权查询事务,
        # 此审计仅表示访问已授权, 不代表对象已打开或流已完整传输。先持久化可避免慢客户端
        # 长期占用连接/快照或延迟管理员访问尝试的可见性。
        await self._session.commit()
        return FileContentResult(file=file)

    async def refresh_expiry_statuses(
        self,
        *,
        now: datetime,
        warning_window_days: int = 7,
    ) -> int:
        warning_deadline = now + timedelta(days=warning_window_days)
        updated = await self._repository.refresh_expiry_statuses(
            now=now,
            warning_deadline=warning_deadline,
        )
        await self._session.commit()
        return updated

    async def list_expiry_scan_candidates(
        self,
        *,
        now: datetime,
        warning_window_days: int = 7,
        limit: int = 500,
    ) -> list[ExpiryScanCandidate]:
        if limit < 1:
            raise ValueError("limit must be greater than 0")
        warning_deadline = now + timedelta(days=warning_window_days)
        return await self._repository.list_expiry_scan_candidates(
            now=now,
            warning_deadline=warning_deadline,
            limit=limit,
        )

    async def mark_expiry_notification_sent(
        self,
        *,
        file_id: uuid.UUID,
        notification_kind: str,
        expected_expires_at: datetime,
        now: datetime,
        warning_deadline: datetime,
        sent_at: datetime,
    ) -> bool:
        updated = await self._repository.mark_expiry_notification_sent(
            file_id=file_id,
            notification_kind=notification_kind,
            sent_at=sent_at,
            expected_expires_at=expected_expires_at,
            now=now,
            warning_deadline=warning_deadline,
        )
        if updated:
            await self._session.commit()
        return updated

    async def delete_file(
        self,
        *,
        current_user: AuthUserRecord,
        file_id: uuid.UUID,
        client_ip: str,
        user_agent: str,
    ) -> None:
        """软删文件: 状态机转 deleted, MinIO 对象保留, 远端清理交 ragflow 模块异步执行。"""
        file = await self._repository.get_by_id_for_update(file_id)
        if file is None or file.status in HIDDEN_FILE_STATUSES:
            raise exceptions.file_not_found()
        is_owner = file.uploader_id == current_user.id
        is_admin = current_user.role in ADMIN_ROLES
        if not is_owner:
            if not is_admin:
                raise exceptions.file_not_found()
            if not self._can_admin_access_file(current_user=current_user, file=file):
                # 越权删他部门文件统一伪装成不存在(404), 消除存在性枚举 oracle
                raise exceptions.file_not_found()
        if not is_admin and await get_config("upload.allow_user_delete") is not True:
            raise exceptions.permission_denied()
        if file.status == "pending_review":
            raise exceptions.review_in_progress()
        await self._require_version_lifecycle_stable(file)
        self._require_resolved_ragflow_upload_outcome(file)
        previous_status = file.status
        file.status = self._transition_or_raise(file.status, "deleted")
        delete_remote = (
            file.ragflow_document_id is not None
            and await get_config("ragflow.delete_remote_on_file_delete") is True
        )
        await OutboxRepository(self._session).append(
            event_type=events.DOCUMENT_FILE_DELETED,
            aggregate_type="file",
            aggregate_id=str(file.id),
            payload={
                "file_id": str(file.id),
                "ragflow_document_id": file.ragflow_document_id,
                "ragflow_dataset_id": file.ragflow_dataset_id,
                "delete_remote": delete_remote,
            },
        )
        await record_audit_log(
            self._session,
            actor_id=current_user.id,
            action="file.delete",
            target_type="file",
            target_id=file.id,
            ip_address=client_ip,
            user_agent=user_agent[:512] or "unknown",
            metadata_json={
                "original_name": file.original_name,
                "previous_status": previous_status,
                "actor_role": current_user.role,
                "file_department_id": str(file.department_id),
                "delete_remote": delete_remote,
            },
        )
        await self._session.commit()

    async def archive_file(
        self,
        *,
        current_user: AuthUserRecord,
        scope: DepartmentAccessScope,
        file_id: uuid.UUID,
        client_ip: str,
        user_agent: str,
    ) -> File:
        """归档文件 (-> disabled), 是否保留远端文档由配置决策后写入事件 payload。"""
        self._require_admin(current_user)
        file = await self._repository.get_by_id_for_update(file_id)
        if file is None or file.status in HIDDEN_FILE_STATUSES:
            raise exceptions.file_not_found()
        self._require_scope_for_file(scope=scope, file=file)
        if file.status == "pending_review":
            raise exceptions.review_in_progress()
        await self._require_version_lifecycle_stable(file)
        self._require_resolved_ragflow_upload_outcome(file)
        previous_status = file.status
        file.status = self._transition_or_raise(file.status, "disabled")
        keep_remote = await get_config("ragflow.keep_remote_on_archive") is not False
        await OutboxRepository(self._session).append(
            event_type=events.DOCUMENT_FILE_ARCHIVED,
            aggregate_type="file",
            aggregate_id=str(file.id),
            payload={
                "file_id": str(file.id),
                "ragflow_document_id": file.ragflow_document_id,
                "keep_remote": keep_remote,
                **scope.audit_metadata(file_department_id=file.department_id),
            },
        )
        await record_admin_audit_log(
            self._session,
            actor_id=current_user.id,
            action="file.archive",
            target_type="file",
            target_id=file.id,
            ip_address=client_ip,
            user_agent=user_agent[:512] or "unknown",
            metadata_json={
                "original_name": file.original_name,
                "previous_status": previous_status,
                "keep_remote": keep_remote,
                **scope.audit_metadata(file_department_id=file.department_id),
            },
        )
        await self._session.commit()
        await self._session.refresh(file)
        return file

    @staticmethod
    def _require_resolved_ragflow_upload_outcome(file: File) -> None:
        if file.ragflow_parse_status == "UPLOADING" and not file.ragflow_document_id:
            raise exceptions.ragflow_reconciliation_pending()

    async def _require_version_lifecycle_stable(self, file: File) -> None:
        if file.replaces_file_id is not None and file.version_switch_status != "completed":
            if not await self._can_abandon_local_candidate(file):
                raise exceptions.version_switch_in_progress()
        if await self._repository.has_incomplete_direct_replacement(file.id):
            raise exceptions.version_switch_in_progress()

    async def _can_abandon_local_candidate(self, file: File) -> bool:
        if (
            file.replaces_file_id is None
            or file.is_current_version
            or file.remote_visibility != "candidate"
            or file.ragflow_document_id is not None
            or file.ragflow_parse_status == "UPLOADING"
            or file.version_switch_status not in {"pending", "failed_old_deactivate"}
            or file.predecessor_remote_deactivated_at is not None
            or file.local_version_activated_at is not None
            or file.remote_version_activated_at is not None
        ):
            return False
        return not await self._repository.has_blocking_version_operation(file.id)

    async def reanalyze_file(
        self,
        *,
        current_user: AuthUserRecord,
        scope: DepartmentAccessScope,
        file_id: uuid.UUID,
        client_ip: str,
        user_agent: str,
        audit_action: str = "file.reanalyze",
    ) -> None:
        """重置文件到 analysis_queued 并经 outbox 事件重新入 AI 分析队列。

        当前文本解析在分析流水线内执行, 因此 reparse 与 reanalyze 共用本路径;
        卡死在 extracting_text/analyzing 等中间态的文件也允许经此救回 (R1 遗留)。
        """
        self._require_admin(current_user)
        file = await self._repository.get_by_id_for_update(file_id)
        if file is None or file.status in HIDDEN_FILE_STATUSES:
            raise exceptions.file_not_found()
        self._require_scope_for_file(scope=scope, file=file)
        if not await self._is_ai_analysis_enabled():
            raise exceptions.ai_analysis_disabled()
        if file.status not in REANALYZE_SOURCE_STATUSES:
            raise exceptions.invalid_state()
        previous_status = file.status
        if file.status != "analysis_queued":
            file.status = self._transition_or_raise(file.status, "analysis_queued")
        await OutboxRepository(self._session).append(
            event_type=events.DOCUMENT_FILE_REANALYZE_REQUESTED,
            aggregate_type="file",
            aggregate_id=str(file.id),
            payload={"file_id": str(file.id)},
        )
        await record_admin_audit_log(
            self._session,
            actor_id=current_user.id,
            action=audit_action,
            target_type="file",
            target_id=file.id,
            ip_address=client_ip,
            user_agent=user_agent[:512] or "unknown",
            metadata_json={
                "original_name": file.original_name,
                "previous_status": previous_status,
                **scope.audit_metadata(file_department_id=file.department_id),
            },
        )
        await self._session.commit()

    def _validate_replacement_predecessor(
        self,
        *,
        current_user: AuthUserRecord,
        predecessor: File | None,
    ) -> None:
        if predecessor is None:
            raise exceptions.invalid_replacement()
        if predecessor.uploader_id != current_user.id:
            raise exceptions.file_not_found()
        if predecessor.department_id != current_user.department_id:
            raise exceptions.invalid_replacement()
        if (
            predecessor.status != "parsed"
            or not predecessor.is_current_version
            or predecessor.remote_visibility != "current"
            or not predecessor.ragflow_dataset_id
            or not predecessor.ragflow_document_id
        ):
            raise exceptions.invalid_replacement()

    @staticmethod
    def _validate_version_chain(
        *,
        chain: list[File],
        expected_current_id: uuid.UUID,
    ) -> None:
        ordered = sorted(chain, key=lambda item: item.version_number)
        if not ordered:
            raise exceptions.invalid_replacement()
        root = ordered[0]
        if (
            root.series_id != root.id
            or root.version_number != 1
            or root.replaces_file_id is not None
            or root.replacement_remote_action is not None
        ):
            raise exceptions.invalid_replacement()
        current = [item for item in ordered if item.is_current_version]
        if len(current) != 1 or current[0].id != expected_current_id:
            raise exceptions.replacement_conflict()
        if [item.version_number for item in ordered] != list(range(1, len(ordered) + 1)):
            raise exceptions.invalid_replacement()
        by_id = {item.id: item for item in ordered}
        active_children: dict[uuid.UUID, int] = {}
        for item in ordered:
            if (
                item.series_id != root.id
                or item.department_id != root.department_id
                or item.uploader_id != root.uploader_id
            ):
                raise exceptions.invalid_replacement()
            if item.id == root.id:
                continue
            if item.replaces_file_id is None:
                raise exceptions.invalid_replacement()
            predecessor = by_id.get(item.replaces_file_id)
            if (
                predecessor is None
                or predecessor.version_number >= item.version_number
                or item.replacement_remote_action not in {"delete", "archive"}
            ):
                raise exceptions.invalid_replacement()
            if item.status not in ABANDONED_VERSION_STATUSES:
                predecessor_id = predecessor.id
                active_children[predecessor_id] = active_children.get(predecessor_id, 0) + 1
                if active_children[predecessor_id] > 1:
                    raise exceptions.replacement_conflict()

    async def _attach_owner_names(self, files: list[File]) -> None:
        owner_ids = {file.owner_id for file in files if file.owner_id is not None}
        names = await self._repository.get_owner_names(owner_ids)
        for file in files:
            dynamic_file = cast(Any, file)
            dynamic_file.owner_name = (
                names.get(file.owner_id) if file.owner_id is not None else None
            )

    def _resolve_file_read_access(
        self,
        *,
        current_user: AuthUserRecord,
        file: File,
    ) -> Literal["uploader", "delegated_owner", "administrator"]:
        if file.uploader_id == current_user.id:
            return "uploader"
        # A department administrator who is also the delegated owner uses the
        # narrower delegated-owner grant. This prevents role fallback from
        # exposing historical versions or bypassing a later department move.
        if file.owner_id == current_user.id and current_user.role != "system_admin":
            if (
                file.is_current_version
                and has_assigned_department(current_user)
                and file.department_id == current_user.department_id
            ):
                return "delegated_owner"
            raise exceptions.file_not_found()
        if current_user.role in ADMIN_ROLES and self._can_admin_access_file(
            current_user=current_user,
            file=file,
        ):
            return "administrator"
        # Unauthorized reads always look absent to avoid disclosing existence.
        raise exceptions.file_not_found()

    def _can_admin_access_file(self, *, current_user: AuthUserRecord, file: File) -> bool:
        if current_user.role == "system_admin":
            return True
        if current_user.role != "dept_admin":
            return False
        return file.department_id in set(current_user.managed_department_ids)

    def _require_scope_for_file(self, *, scope: DepartmentAccessScope, file: File) -> None:
        if not scope.covers_department(file.department_id):
            # 越权统一伪装成不存在(404), 与 get/delete 路径一致, 避免存在性泄露
            raise exceptions.file_not_found()

    def _require_admin(self, current_user: AuthUserRecord) -> None:
        if current_user.role not in ADMIN_ROLES:
            raise exceptions.permission_denied()

    def _transition_or_raise(self, from_status: str, to_status: str) -> str:
        try:
            return DocumentStateMachine.transition(from_status, to_status)
        except DocumentStateError as exc:
            raise exceptions.invalid_state() from exc

    async def _is_ai_analysis_enabled(self) -> bool:
        """与 ai 模块同语义: 环境总开关与 ai_analysis 特性行 (缺省回退环境值) 同时为开。"""
        if not self._settings.ai_analysis_enabled:
            return False
        feature_enabled = await self._repository.get_ai_analysis_feature_enabled()
        return feature_enabled if feature_enabled is not None else True

    async def _validate_quota(self, *, uploader_id: uuid.UUID, incoming_size: int) -> None:
        quota_mb = await resolve_user_quota_mb()
        if quota_mb is None:
            return
        quota_bytes = quota_mb * 1024 * 1024
        await self._repository.lock_uploader_quota(uploader_id)
        used_bytes = await self._repository.sum_size_for_uploader(uploader_id)
        if used_bytes + incoming_size > quota_bytes:
            raise exceptions.quota_exceeded(used_bytes=used_bytes, quota_bytes=quota_bytes)

    async def _validate_size(self, size: int) -> None:
        if size <= 0:
            raise exceptions.file_empty()
        max_size_bytes = await resolve_upload_max_size_bytes(self._settings)
        if size > max_size_bytes:
            raise exceptions.file_too_large(max_size_bytes)

    async def _validate_extension(self, extension: str) -> None:
        if extension in UNSUPPORTED_LEGACY_EXTENSIONS:
            raise exceptions.extension_not_allowed(extension)
        if extension not in await resolve_allowed_extensions(self._settings):
            raise exceptions.extension_not_allowed(extension)

    def _validate_mime_type(
        self,
        *,
        data: bytes,
        extension: str,
        declared_mime_type: str | None,
    ) -> str:
        allowed_mimes = normalized_csv(self._settings.upload_allowed_mime_types)
        declared = normalize_mime_type(declared_mime_type)
        if declared not in allowed_mimes:
            raise exceptions.mime_not_allowed(declared)

        expected_mimes = EXTENSION_MIME_MAP.get(extension, set())
        if not expected_mimes or declared not in expected_mimes:
            expected = ", ".join(sorted(expected_mimes))
            raise exceptions.mime_mismatch(expected, declared)

        if extension in TEXT_EXTENSIONS and is_text_content(data):
            return declared

        detected = detect_mime_type(data, extension)
        if detected != declared:
            raise exceptions.mime_mismatch(declared, detected)
        return declared

    async def _append_file_uploaded_event(self, file: File) -> None:
        await OutboxRepository(self._session).append(
            event_type=events.DOCUMENT_FILE_UPLOADED,
            aggregate_type="file",
            aggregate_id=str(file.id),
            payload={
                "file_id": str(file.id),
                "uploader_id": str(file.uploader_id),
                "hash": file.hash,
                "bucket": file.bucket,
                "object_key": file.object_key,
                "status": file.status,
                "ai_analysis_enabled_at_upload": file.ai_analysis_enabled_at_upload,
                "submit_after_upload": bool(
                    (file.ai_config_snapshot or {}).get("submit_after_upload", False)
                ),
                "series_id": str(file.series_id),
                "version_number": file.version_number,
                "replaces_file_id": str(file.replaces_file_id) if file.replaces_file_id else None,
            },
        )

    async def _append_review_submitted_event(
        self,
        *,
        file: File,
        current_user: AuthUserRecord,
        previous_status: str,
    ) -> None:
        await OutboxRepository(self._session).append(
            event_type=REVIEW_FILE_SUBMITTED_EVENT,
            aggregate_type="file",
            aggregate_id=str(file.id),
            payload={
                "file_id": str(file.id),
                "actor_id": str(current_user.id),
                "previous_status": previous_status,
                "status": file.status,
                "review_status": file.review_status,
                "submitted_at": file.submitted_at.isoformat() if file.submitted_at else None,
                "review_due_at": file.review_due_at.isoformat() if file.review_due_at else None,
            },
        )


async def resolve_upload_max_size_bytes(settings: Settings) -> int:
    """解析单文件大小上限并统一换算为字节。

    配置键 ``upload.max_file_size_mb`` 的单位是 MB (PRD 6.14.1),
    而既有 ``settings.upload_max_file_size_bytes`` 的单位是字节,
    比较前在此统一乘 1024*1024 换算; 非法值回退环境变量字节值。
    """
    value = await get_config("upload.max_file_size_mb")
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 200:
        return min(settings.upload_max_file_size_bytes, MAX_IN_MEMORY_UPLOAD_BYTES)
    return min(value * 1024 * 1024, MAX_IN_MEMORY_UPLOAD_BYTES)


async def resolve_upload_enabled() -> bool:
    value = await get_config("upload.enabled")
    if value is None:
        # This key was added after uploads were already enabled by default.
        # Missing preserves that behavior; any present non-boolean still fails closed.
        return True
    return value is True


async def ensure_upload_allowed(current_user: AuthUserRecord) -> None:
    if not await resolve_upload_enabled():
        raise exceptions.upload_disabled()
    if not has_assigned_department(current_user):
        raise exceptions.department_assignment_required()


async def resolve_user_quota_mb() -> int | None:
    """解析单用户配额 (upload.user_quota_mb, 单位 MB); 0 或非法值表示不限。"""
    value = await get_config("upload.user_quota_mb")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


async def resolve_allowed_extensions(settings: Settings) -> set[str]:
    """解析允许上传的扩展名白名单 (upload.allowed_extensions), 非法值回退环境变量。"""
    value = await get_config("upload.allowed_extensions")
    if isinstance(value, list):
        normalized = {str(item).strip().lower().lstrip(".") for item in value if str(item).strip()}
        if normalized:
            return normalized
    return normalized_csv(settings.upload_allowed_extensions)


def normalized_csv(raw_value: str) -> set[str]:
    return {item.strip().lower().lstrip(".") for item in raw_value.split(",") if item.strip()}


def normalize_mime_type(mime_type: str | None) -> str:
    if not mime_type:
        return "application/octet-stream"
    return mime_type.split(";", 1)[0].strip().lower()


def clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def detect_mime_type(data: bytes, extension: str) -> str:
    if extension == "pdf":
        if is_pdf_document(data):
            return "application/pdf"
        return "application/octet-stream"

    if extension in OOXML_REQUIRED_ENTRIES:
        if is_ooxml_document(data, OOXML_REQUIRED_ENTRIES[extension]):
            return next(iter(EXTENSION_MIME_MAP[extension]))
        return detected_or_unknown(data)

    if extension in TEXT_EXTENSIONS:
        if is_text_content(data):
            return "text/plain" if extension != "md" else "text/markdown"
        return detected_or_unknown(data)

    return detected_or_unknown(data)


def detected_or_unknown(data: bytes) -> str:
    detected_kind = filetype.guess(data)
    if detected_kind is None:
        return "application/octet-stream"
    return normalize_mime_type(detected_kind.mime)


def is_pdf_document(data: bytes) -> bool:
    if not data.startswith(PDF_MAGIC):
        return False
    tail = data[-2048:]
    return PDF_EOF_MARKER in tail and PDF_STARTXREF_MARKER in tail


def is_ooxml_document(data: bytes, required_entry: str) -> bool:
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            names = set(archive.namelist())
    except zipfile.BadZipFile:
        return False
    return "[Content_Types].xml" in names and required_entry in names


def is_text_content(data: bytes) -> bool:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return False
    if "\x00" in text:
        return False
    return True


def sanitize_filename(filename: str) -> tuple[str, str]:
    candidate = PureWindowsPath(filename).name
    candidate = PurePosixPath(candidate).name
    candidate = candidate.strip()
    if not candidate:
        candidate = "upload"
    if "." not in candidate:
        raise exceptions.extension_not_allowed("")

    stem, extension = candidate.rsplit(".", 1)
    extension = extension.lower().strip()
    cleaned_stem = sanitize_filename_part(stem)
    cleaned_name = f"{cleaned_stem}.{extension}"
    return cleaned_name, extension


def sanitize_filename_part(value: str) -> str:
    cleaned = FILENAME_UNSAFE_RE.sub("_", value).strip(" ._")
    if not cleaned:
        cleaned = "file"
    if cleaned.upper() in WINDOWS_RESERVED_NAMES:
        cleaned = f"{cleaned}_file"
    return cleaned[:180]
