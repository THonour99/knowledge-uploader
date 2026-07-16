from __future__ import annotations

import re
import uuid
import zipfile
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from io import BytesIO
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Protocol, cast

import filetype
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access_scope import DepartmentAccessScope
from app.core.audit import record_admin_audit_log, record_audit_log
from app.core.config import Settings
from app.core.document_state import DocumentStateError, DocumentStateMachine
from app.core.outbox import OutboxRepository
from app.core.runtime_config import get_config
from app.modules.user.schemas import AuthUserRecord

from . import events, exceptions
from .models import File
from .repository import HIDDEN_FILE_STATUSES, DocumentRepository, ExpiryScanCandidate
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
    ) -> UploadedFileResult:
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

        effective_ai_analysis_enabled = await self._resolve_upload_ai_analysis_enabled(
            submit_after_upload=submit_after_upload,
            requested_ai_analysis_enabled=ai_analysis_enabled,
        )

        file = File(
            id=file_id,
            original_name=sanitized_name,
            stored_name=stored_name,
            extension=extension,
            mime_type=mime_type,
            size=len(data),
            hash=file_hash,
            storage_type="minio",
            bucket=bucket,
            object_key=object_key,
            uploader_id=current_user.id,
            department_id=current_user.department_id,
            department=current_user.department_name or current_user.department,
            visibility=visibility,
            description=clean_optional_text(description),
            tags=[],
            status="uploaded",
            review_status="pending",
            ai_analysis_enabled_at_upload=effective_ai_analysis_enabled,
            # uploaded 是产品语义上的草稿。AI 开启时必须持久化自动提交意图,
            # 否则异步 worker 完成分析后无法判断是否应进入审核队列。
            ai_config_snapshot={"submit_after_upload": submit_after_upload},
        )
        try:
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
        except Exception:
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
        submit_after_upload: bool,
        requested_ai_analysis_enabled: bool | None,
    ) -> bool:
        if not submit_after_upload:
            return False
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
        extension: str | None = None,
        tag_id: uuid.UUID | None = None,
    ) -> list[File]:
        return await self._repository.list_for_uploader(
            current_user.id,
            extension=clean_optional_text(extension),
            tag_id=tag_id,
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
        file = await self._repository.get_by_id(file_id)
        if file is None or file.status in HIDDEN_FILE_STATUSES:
            raise exceptions.file_not_found()
        is_owner = file.uploader_id == current_user.id
        is_admin_view = current_user.role in ADMIN_ROLES and not is_owner
        if not is_owner:
            if current_user.role not in ADMIN_ROLES:
                raise exceptions.file_not_found()
            if not self._can_admin_access_file(current_user=current_user, file=file):
                # 越权访问他部门文件统一伪装成不存在(404), 避免 403/404 差异泄露存在性
                raise exceptions.file_not_found()

        analysis = await self._repository.get_analysis_for_file(file.id)
        result = FileDetailResult(
            file=file,
            category_name=await self._repository.get_category_name(file.id),
            analysis=(
                FileAnalysisDetail(
                    status=analysis.status,
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
        )
        # 审计在全部查询成功后写入并与之同事务提交, 避免 admin/员工两条路径事务边界不一致,
        # 也保证只记录实际成功返回的查看操作。
        if is_admin_view:
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
                },
            )
            await self._session.commit()
        return result

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
        sent_at: datetime,
    ) -> bool:
        updated = await self._repository.mark_expiry_notification_sent(
            file_id=file_id,
            notification_kind=notification_kind,
            sent_at=sent_at,
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
        file = await self._repository.get_by_id(file_id)
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
        file = await self._repository.get_by_id(file_id)
        if file is None or file.status in HIDDEN_FILE_STATUSES:
            raise exceptions.file_not_found()
        self._require_scope_for_file(scope=scope, file=file)
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
        file = await self._repository.get_by_id(file_id)
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
            },
        )


async def resolve_upload_max_size_bytes(settings: Settings) -> int:
    """解析单文件大小上限并统一换算为字节。

    配置键 ``upload.max_file_size_mb`` 的单位是 MB (PRD 6.14.1),
    而既有 ``settings.upload_max_file_size_bytes`` 的单位是字节,
    比较前在此统一乘 1024*1024 换算; 非法值回退环境变量字节值。
    """
    value = await get_config("upload.max_file_size_mb")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return settings.upload_max_file_size_bytes
    return value * 1024 * 1024


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
