from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import cast

from sqlalchemy import Boolean, Column, DateTime, MetaData, String, Table, Text, case, func, select
from sqlalchemy import update as sql_update
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.document.models import File

# 跨模块表只读联查统一走 Table() 轻量映射, 避免 import 其他模块的 ORM model
CATEGORIES = Table(
    "categories",
    MetaData(),
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("name", String(120), nullable=False),
)

DOCUMENT_ANALYSIS = Table(
    "document_analysis",
    MetaData(),
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("file_id", UUID(as_uuid=True), nullable=False),
    Column("status", String(20), nullable=False),
    Column("summary", Text),
    Column("sensitive_risk_level", String(20), nullable=False),
    Column("extracted_text", Text),
    Column("error_message", Text),
    Column("finished_at", DateTime(timezone=True)),
)

SYNC_TASKS = Table(
    "sync_tasks",
    MetaData(),
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("file_id", UUID(as_uuid=True), nullable=False),
    Column("status", String(40), nullable=False),
    Column("error_message", Text),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

FILE_TAGS = Table(
    "file_tags",
    MetaData(),
    Column("file_id", UUID(as_uuid=True), primary_key=True),
    Column("tag_id", UUID(as_uuid=True), primary_key=True),
)

AI_FEATURE_CONFIGS = Table(
    "ai_feature_configs",
    MetaData(),
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("feature_name", String(80), nullable=False),
    Column("enabled", Boolean, nullable=False),
)


@dataclass(frozen=True)
class DocumentAnalysisRecord:
    status: str
    summary: str | None
    sensitive_risk_level: str
    extracted_text: str | None
    error_message: str | None
    finished_at: datetime | None


@dataclass(frozen=True)
class ExpiryScanCandidate:
    file_id: uuid.UUID
    uploader_id: uuid.UUID
    original_name: str
    expires_at: datetime
    expiry_status: str
    notification_kind: str


class DocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, file_id: uuid.UUID) -> File | None:
        result = await self._session.execute(select(File).where(File.id == file_id))
        return result.scalar_one_or_none()

    async def get_for_uploader(self, *, file_id: uuid.UUID, uploader_id: uuid.UUID) -> File | None:
        result = await self._session.execute(
            select(File).where(
                File.id == file_id,
                File.uploader_id == uploader_id,
                File.status.not_in(HIDDEN_FILE_STATUSES),
            )
        )
        return result.scalar_one_or_none()

    async def list_for_uploader(
        self,
        uploader_id: uuid.UUID,
        *,
        extension: str | None = None,
        tag_id: uuid.UUID | None = None,
    ) -> list[File]:
        stmt = select(File).where(
            File.uploader_id == uploader_id,
            File.status.not_in(HIDDEN_FILE_STATUSES),
        )
        if extension:
            stmt = stmt.where(File.extension == extension)
        if tag_id is not None:
            stmt = stmt.join(FILE_TAGS, FILE_TAGS.c.file_id == File.id).where(
                FILE_TAGS.c.tag_id == tag_id
            )
        result = await self._session.execute(stmt.order_by(File.uploaded_at.desc()))
        return list(result.scalars())

    async def find_first_by_hash_for_uploader(
        self,
        *,
        file_hash: str,
        uploader_id: uuid.UUID,
    ) -> File | None:
        result = await self._session.execute(
            select(File)
            .where(
                File.hash == file_hash,
                File.uploader_id == uploader_id,
                File.status.not_in(HIDDEN_FILE_STATUSES),
            )
            .order_by(File.uploaded_at.asc())
        )
        return result.scalars().first()

    async def add(self, file: File) -> File:
        self._session.add(file)
        await self._session.flush()
        await self._session.refresh(file)
        return file

    async def sum_size_for_uploader(self, uploader_id: uuid.UUID) -> int:
        """统计上传者已占用的存储字节数 (软删/清理失败的删除态不计入配额)。"""
        result = await self._session.execute(
            select(func.coalesce(func.sum(File.size), 0)).where(
                File.uploader_id == uploader_id,
                File.status.not_in(HIDDEN_FILE_STATUSES),
            )
        )
        return int(result.scalar_one())

    async def lock_uploader_quota(self, uploader_id: uuid.UUID) -> None:
        """Serialize quota checks and file inserts per uploader in the current transaction."""
        await self._session.execute(
            select(func.pg_advisory_xact_lock(_uploader_quota_lock_key(uploader_id)))
        )

    async def get_ai_analysis_feature_enabled(self) -> bool | None:
        """只读联查 ai 模块特性开关表; 无行时返回 None 由 service 回退 settings。"""
        result = await self._session.execute(
            select(AI_FEATURE_CONFIGS.c.enabled).where(
                AI_FEATURE_CONFIGS.c.feature_name == "ai_analysis"
            )
        )
        return cast(bool | None, result.scalar_one_or_none())

    async def get_category_name(self, file_id: uuid.UUID) -> str | None:
        result = await self._session.execute(
            select(CATEGORIES.c.name)
            .join_from(File, CATEGORIES, File.category_id == CATEGORIES.c.id, isouter=True)
            .where(File.id == file_id)
        )
        return cast(str | None, result.scalar_one_or_none())

    async def get_analysis_for_file(self, file_id: uuid.UUID) -> DocumentAnalysisRecord | None:
        result = await self._session.execute(
            select(
                DOCUMENT_ANALYSIS.c.status,
                DOCUMENT_ANALYSIS.c.summary,
                DOCUMENT_ANALYSIS.c.sensitive_risk_level,
                DOCUMENT_ANALYSIS.c.extracted_text,
                DOCUMENT_ANALYSIS.c.error_message,
                DOCUMENT_ANALYSIS.c.finished_at,
            ).where(DOCUMENT_ANALYSIS.c.file_id == file_id)
        )
        row = result.mappings().one_or_none()
        if row is None:
            return None
        return DocumentAnalysisRecord(
            status=cast(str, row["status"]),
            summary=cast(str | None, row["summary"]),
            sensitive_risk_level=cast(str, row["sensitive_risk_level"]),
            extracted_text=cast(str | None, row["extracted_text"]),
            error_message=cast(str | None, row["error_message"]),
            finished_at=cast(datetime | None, row["finished_at"]),
        )

    async def get_latest_failed_sync_error(self, file_id: uuid.UUID) -> str | None:
        result = await self._session.execute(
            select(SYNC_TASKS.c.error_message)
            .where(SYNC_TASKS.c.file_id == file_id, SYNC_TASKS.c.status == "failed")
            .order_by(SYNC_TASKS.c.created_at.desc())
            .limit(1)
        )
        return cast(str | None, result.scalars().first())

    async def refresh_expiry_statuses(
        self,
        *,
        now: datetime,
        warning_deadline: datetime,
    ) -> int:
        result = await self._session.execute(
            sql_update(File)
            .where(File.status.not_in(HIDDEN_FILE_STATUSES))
            .values(
                expiry_status=case(
                    (File.expires_at.is_(None), "never"),
                    (File.expires_at <= now, "expired"),
                    (File.expires_at <= warning_deadline, "expiring"),
                    else_="active",
                )
            )
        )
        return int(result.rowcount or 0)

    async def list_expiry_scan_candidates(
        self,
        *,
        now: datetime,
        warning_deadline: datetime,
        limit: int,
    ) -> list[ExpiryScanCandidate]:
        result = await self._session.execute(
            select(File)
            .where(
                File.expires_at.is_not(None),
                File.expires_at <= warning_deadline,
                File.status.not_in(EXPIRY_SCAN_EXCLUDED_FILE_STATUSES),
                (
                    (File.expires_at <= now) & File.expiry_expired_sent_at.is_(None)
                    | (File.expires_at > now) & File.expiry_warning_sent_at.is_(None)
                ),
            )
            .order_by(File.expires_at.asc(), File.uploaded_at.asc())
            .limit(limit)
        )
        candidates: list[ExpiryScanCandidate] = []
        for file in result.scalars():
            expires_at = file.expires_at
            if expires_at is None:
                continue
            candidates.append(
                ExpiryScanCandidate(
                    file_id=file.id,
                    uploader_id=file.uploader_id,
                    original_name=file.original_name,
                    expires_at=expires_at,
                    expiry_status=file.expiry_status,
                    notification_kind="expired" if expires_at <= now else "warning",
                )
            )
        return candidates

    async def mark_expiry_notification_sent(
        self,
        *,
        file_id: uuid.UUID,
        notification_kind: str,
        sent_at: datetime,
    ) -> bool:
        if notification_kind == "warning":
            timestamp_field = "expiry_warning_sent_at"
            status_value = "expiring"
            idempotency_predicate = File.expiry_warning_sent_at.is_(None)
        elif notification_kind == "expired":
            timestamp_field = "expiry_expired_sent_at"
            status_value = "expired"
            idempotency_predicate = File.expiry_expired_sent_at.is_(None)
        else:
            msg = f"invalid expiry notification kind: {notification_kind}"
            raise ValueError(msg)

        result = await self._session.execute(
            sql_update(File)
            .where(
                File.id == file_id,
                File.status.not_in(HIDDEN_FILE_STATUSES),
                idempotency_predicate,
            )
            .values({timestamp_field: sent_at, "expiry_status": status_value})
        )
        return bool(result.rowcount == 1)


HIDDEN_FILE_STATUSES = ("deleted", "ragflow_cleanup_failed")
EXPIRY_SCAN_EXCLUDED_FILE_STATUSES = (*HIDDEN_FILE_STATUSES, "disabled")


def _uploader_quota_lock_key(uploader_id: uuid.UUID) -> int:
    return int.from_bytes(uploader_id.bytes[:8], byteorder="big", signed=True)
