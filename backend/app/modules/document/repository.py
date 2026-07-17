from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import cast

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    case,
    func,
    or_,
    select,
)
from sqlalchemy import update as sql_update
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

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
    Column("engine_type", String(20), nullable=False),
    Column("provider_name", String(120)),
    Column("model_name", String(120)),
    Column("prompt_template_key", String(80)),
    Column("prompt_version", Integer),
    Column("input_char_count", Integer),
    Column("input_sha256", String(64)),
    Column("category_count", Integer),
    Column("input_truncated", Boolean),
    Column("attempt_number", Integer, nullable=False),
    Column("prompt_tokens", Integer, nullable=False),
    Column("completion_tokens", Integer, nullable=False),
    Column("latency_ms", Integer, nullable=False),
    Column("failure_category", String(40)),
    Column("estimated_cost_microunits", BigInteger, nullable=False),
    Column("cost_currency", String(3), nullable=False),
    Column("summary", Text),
    Column("sensitive_risk_level", String(20), nullable=False),
    Column("extracted_text", Text),
    Column("error_message", Text),
    Column("finished_at", DateTime(timezone=True)),
    Column("quality_score", Integer),
    Column("tables_json", JSONB),
    Column("table_count", Integer),
    Column("similar_file_ids", JSONB),
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

RAGFLOW_VERSION_OPERATIONS = Table(
    "ragflow_version_operations",
    MetaData(),
    Column("file_id", UUID(as_uuid=True), nullable=False),
    Column("operation", String(40), nullable=False),
    Column("status", String(20), nullable=False),
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

USERS = Table(
    "users",
    MetaData(),
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("name", String(100), nullable=False),
    Column("department_id", UUID(as_uuid=True), nullable=False),
    Column("status", String(40), nullable=False),
    Column("email_verified", Boolean, nullable=False),
)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@dataclass(frozen=True)
class DocumentAnalysisRecord:
    status: str
    engine_type: str
    provider_name: str | None
    model_name: str | None
    prompt_template_key: str | None
    prompt_version: int | None
    input_char_count: int | None
    input_sha256: str | None
    category_count: int | None
    input_truncated: bool | None
    attempt_number: int
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    failure_category: str | None
    estimated_cost_microunits: int
    cost_currency: str
    summary: str | None
    sensitive_risk_level: str
    extracted_text: str | None
    error_message: str | None
    finished_at: datetime | None
    quality_score: int | None
    tables_json: list[dict[str, object]]
    table_count: int
    similar_file_ids: list[str]


@dataclass(frozen=True)
class ExpiryScanCandidate:
    file_id: uuid.UUID
    uploader_id: uuid.UUID
    original_name: str
    expires_at: datetime
    expiry_status: str
    notification_kind: str


@dataclass(frozen=True, slots=True)
class OwnerOptionRecord:
    id: uuid.UUID
    name: str


class DocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, file_id: uuid.UUID) -> File | None:
        result = await self._session.execute(select(File).where(File.id == file_id))
        return result.scalar_one_or_none()

    async def get_by_id_for_update(self, file_id: uuid.UUID) -> File | None:
        result = await self._session.execute(
            select(File).where(File.id == file_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_valid_owner(
        self,
        *,
        owner_id: uuid.UUID,
        department_id: uuid.UUID,
    ) -> OwnerOptionRecord | None:
        result = await self._session.execute(
            select(USERS.c.id, USERS.c.name)
            .where(
                USERS.c.id == owner_id,
                USERS.c.department_id == department_id,
                USERS.c.status == "active",
                USERS.c.email_verified.is_(True),
            )
            .with_for_update()
        )
        row = result.one_or_none()
        if row is None:
            return None
        return OwnerOptionRecord(id=cast(uuid.UUID, row.id), name=str(row.name))

    async def list_owner_options(
        self,
        *,
        department_id: uuid.UUID,
        search: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[OwnerOptionRecord], int]:
        statement = select(USERS.c.id, USERS.c.name).where(
            USERS.c.department_id == department_id,
            USERS.c.status == "active",
            USERS.c.email_verified.is_(True),
        )
        count_statement = (
            select(func.count())
            .select_from(USERS)
            .where(
                USERS.c.department_id == department_id,
                USERS.c.status == "active",
                USERS.c.email_verified.is_(True),
            )
        )
        if search:
            predicate = USERS.c.name.ilike(f"%{_escape_like(search)}%", escape="\\")
            statement = statement.where(predicate)
            count_statement = count_statement.where(predicate)
        result = await self._session.execute(
            statement.order_by(USERS.c.name.asc(), USERS.c.id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        total = int((await self._session.execute(count_statement)).scalar_one())
        owners = [
            OwnerOptionRecord(id=cast(uuid.UUID, row.id), name=str(row.name)) for row in result
        ]
        return owners, total

    async def get_owner_names(
        self,
        owner_ids: set[uuid.UUID],
    ) -> dict[uuid.UUID, str]:
        if not owner_ids:
            return {}
        result = await self._session.execute(
            select(USERS.c.id, USERS.c.name).where(USERS.c.id.in_(owner_ids))
        )
        return {cast(uuid.UUID, row.id): str(row.name) for row in result}

    async def list_version_chain(
        self,
        series_id: uuid.UUID,
        *,
        owner_id: uuid.UUID | None = None,
        department_id: uuid.UUID | None = None,
    ) -> list[File]:
        statement = select(File).where(File.series_id == series_id)
        if owner_id is not None:
            statement = statement.where(File.owner_id == owner_id)
        if department_id is not None:
            statement = statement.where(File.department_id == department_id)
        result = await self._session.execute(
            statement.order_by(File.version_number.desc(), File.id.asc())
        )
        return list(result.scalars())

    async def lock_version_series(self, series_id: uuid.UUID) -> list[File]:
        result = await self._session.execute(
            select(File)
            .where(File.series_id == series_id)
            .order_by(File.id.asc())
            .with_for_update()
        )
        return list(result.scalars())

    async def has_direct_replacement(self, file_id: uuid.UUID) -> bool:
        result = await self._session.execute(
            select(File.id)
            .where(
                File.replaces_file_id == file_id,
                File.status.not_in(ABANDONED_VERSION_STATUSES),
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def has_incomplete_direct_replacement(self, file_id: uuid.UUID) -> bool:
        """Return whether a direct child still has an unfinished version switch."""
        result = await self._session.execute(
            select(File.id)
            .where(
                File.replaces_file_id == file_id,
                File.version_switch_status != "completed",
                File.status.not_in(ABANDONED_VERSION_STATUSES),
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def has_blocking_version_operation(self, file_id: uuid.UUID) -> bool:
        """Return whether remote work makes a local candidate unsafe to abandon."""
        result = await self._session.execute(
            select(RAGFLOW_VERSION_OPERATIONS.c.file_id)
            .where(
                RAGFLOW_VERSION_OPERATIONS.c.file_id == file_id,
                or_(
                    RAGFLOW_VERSION_OPERATIONS.c.operation != "deactivate_predecessor",
                    RAGFLOW_VERSION_OPERATIONS.c.status != "failed",
                ),
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

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
        page: int,
        page_size: int,
        search: str | None = None,
        status: str | None = None,
        extension: str | None = None,
        tag_id: uuid.UUID | None = None,
        expiry_status: str | None = None,
        sort: str = "uploaded_at",
        order: str = "desc",
    ) -> tuple[list[File], int]:
        stmt = select(File).where(
            File.uploader_id == uploader_id,
            File.status.not_in(HIDDEN_FILE_STATUSES),
        )
        count_stmt = select(func.count(func.distinct(File.id))).where(
            File.uploader_id == uploader_id,
            File.status.not_in(HIDDEN_FILE_STATUSES),
        )
        if search:
            pattern = f"%{_escape_like(search)}%"
            predicate = or_(
                File.title.ilike(pattern, escape="\\"),
                File.original_name.ilike(pattern, escape="\\"),
                File.description.ilike(pattern, escape="\\"),
            )
            stmt = stmt.where(predicate)
            count_stmt = count_stmt.where(predicate)
        if status:
            stmt = stmt.where(File.status == status)
            count_stmt = count_stmt.where(File.status == status)
        if extension:
            stmt = stmt.where(File.extension == extension)
            count_stmt = count_stmt.where(File.extension == extension)
        if expiry_status:
            stmt = stmt.where(File.expiry_status == expiry_status)
            count_stmt = count_stmt.where(File.expiry_status == expiry_status)
        if tag_id is not None:
            stmt = stmt.join(FILE_TAGS, FILE_TAGS.c.file_id == File.id).where(
                FILE_TAGS.c.tag_id == tag_id
            )
            count_stmt = count_stmt.join(FILE_TAGS, FILE_TAGS.c.file_id == File.id).where(
                FILE_TAGS.c.tag_id == tag_id
            )
        sort_columns = {
            "uploaded_at": File.uploaded_at,
            "updated_at": File.updated_at,
            "original_name": File.original_name,
            "title": File.title,
            "size": File.size,
            "status": File.status,
        }
        sort_column = sort_columns.get(sort, File.uploaded_at)
        order_expression = sort_column.asc() if order == "asc" else sort_column.desc()
        stmt = (
            stmt.order_by(order_expression, File.id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await self._session.execute(stmt)
        total = int((await self._session.execute(count_stmt)).scalar_one())
        return list(result.scalars()), total

    async def list_for_owner(
        self,
        owner_id: uuid.UUID,
        *,
        department_id: uuid.UUID,
        page: int,
        page_size: int,
        search: str | None = None,
        status: str | None = None,
        extension: str | None = None,
        expiry_status: str | None = None,
        sort: str = "uploaded_at",
        order: str = "desc",
    ) -> tuple[list[File], int]:
        predicates = (
            File.owner_id == owner_id,
            File.uploader_id != owner_id,
            File.department_id == department_id,
            File.status.not_in(HIDDEN_FILE_STATUSES),
            File.is_current_version.is_(True),
        )
        stmt = select(File).where(*predicates)
        count_stmt = select(func.count()).select_from(File).where(*predicates)
        if search:
            pattern = f"%{_escape_like(search)}%"
            predicate = or_(
                File.title.ilike(pattern, escape="\\"),
                File.original_name.ilike(pattern, escape="\\"),
                File.description.ilike(pattern, escape="\\"),
            )
            stmt = stmt.where(predicate)
            count_stmt = count_stmt.where(predicate)
        if status:
            stmt = stmt.where(File.status == status)
            count_stmt = count_stmt.where(File.status == status)
        if extension:
            stmt = stmt.where(File.extension == extension)
            count_stmt = count_stmt.where(File.extension == extension)
        if expiry_status:
            stmt = stmt.where(File.expiry_status == expiry_status)
            count_stmt = count_stmt.where(File.expiry_status == expiry_status)
        sort_columns = {
            "uploaded_at": File.uploaded_at,
            "updated_at": File.updated_at,
            "original_name": File.original_name,
            "title": File.title,
            "size": File.size,
            "status": File.status,
        }
        sort_column = sort_columns.get(sort, File.uploaded_at)
        order_expression = sort_column.asc() if order == "asc" else sort_column.desc()
        result = await self._session.execute(
            stmt.order_by(order_expression, File.id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        total = int((await self._session.execute(count_stmt)).scalar_one())
        return list(result.scalars()), total

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
                DOCUMENT_ANALYSIS.c.engine_type,
                DOCUMENT_ANALYSIS.c.provider_name,
                DOCUMENT_ANALYSIS.c.model_name,
                DOCUMENT_ANALYSIS.c.prompt_template_key,
                DOCUMENT_ANALYSIS.c.prompt_version,
                DOCUMENT_ANALYSIS.c.input_char_count,
                DOCUMENT_ANALYSIS.c.input_sha256,
                DOCUMENT_ANALYSIS.c.category_count,
                DOCUMENT_ANALYSIS.c.input_truncated,
                DOCUMENT_ANALYSIS.c.attempt_number,
                DOCUMENT_ANALYSIS.c.prompt_tokens,
                DOCUMENT_ANALYSIS.c.completion_tokens,
                DOCUMENT_ANALYSIS.c.latency_ms,
                DOCUMENT_ANALYSIS.c.failure_category,
                DOCUMENT_ANALYSIS.c.estimated_cost_microunits,
                DOCUMENT_ANALYSIS.c.cost_currency,
                DOCUMENT_ANALYSIS.c.summary,
                DOCUMENT_ANALYSIS.c.sensitive_risk_level,
                DOCUMENT_ANALYSIS.c.extracted_text,
                DOCUMENT_ANALYSIS.c.error_message,
                DOCUMENT_ANALYSIS.c.finished_at,
                DOCUMENT_ANALYSIS.c.quality_score,
                DOCUMENT_ANALYSIS.c.tables_json,
                DOCUMENT_ANALYSIS.c.table_count,
                DOCUMENT_ANALYSIS.c.similar_file_ids,
            ).where(DOCUMENT_ANALYSIS.c.file_id == file_id)
        )
        row = result.mappings().one_or_none()
        if row is None:
            return None
        return DocumentAnalysisRecord(
            status=cast(str, row["status"]),
            engine_type=cast(str, row["engine_type"]),
            provider_name=cast(str | None, row["provider_name"]),
            model_name=cast(str | None, row["model_name"]),
            prompt_template_key=cast(str | None, row["prompt_template_key"]),
            prompt_version=cast(int | None, row["prompt_version"]),
            input_char_count=cast(int | None, row["input_char_count"]),
            input_sha256=cast(str | None, row["input_sha256"]),
            category_count=cast(int | None, row["category_count"]),
            input_truncated=cast(bool | None, row["input_truncated"]),
            attempt_number=cast(int, row["attempt_number"]),
            prompt_tokens=cast(int, row["prompt_tokens"]),
            completion_tokens=cast(int, row["completion_tokens"]),
            latency_ms=cast(int, row["latency_ms"]),
            failure_category=cast(str | None, row["failure_category"]),
            estimated_cost_microunits=cast(int, row["estimated_cost_microunits"]),
            cost_currency=cast(str, row["cost_currency"]),
            summary=cast(str | None, row["summary"]),
            sensitive_risk_level=cast(str, row["sensitive_risk_level"]),
            extracted_text=cast(str | None, row["extracted_text"]),
            error_message=cast(str | None, row["error_message"]),
            finished_at=cast(datetime | None, row["finished_at"]),
            quality_score=cast(int | None, row["quality_score"]),
            tables_json=cast(list[dict[str, object]], row["tables_json"] or []),
            table_count=cast(int, row["table_count"] or 0),
            similar_file_ids=cast(list[str], row["similar_file_ids"] or []),
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
            .where(
                File.status.not_in(EXPIRY_SCAN_EXCLUDED_FILE_STATUSES),
                File.is_current_version.is_(True),
            )
            .values(
                expiry_status=case(
                    (File.expires_at.is_(None), "never"),
                    (File.expires_at <= now, "expired"),
                    (File.expires_at <= warning_deadline, "expiring"),
                    else_="active",
                )
            )
        )
        return int(cast(CursorResult[object], result).rowcount or 0)

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
                File.is_current_version.is_(True),
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
        expected_expires_at: datetime,
        now: datetime,
        warning_deadline: datetime,
        sent_at: datetime,
    ) -> bool:
        time_window_predicate: ColumnElement[bool]
        if notification_kind == "warning":
            timestamp_field = "expiry_warning_sent_at"
            status_value = "expiring"
            idempotency_predicate = File.expiry_warning_sent_at.is_(None)
            time_window_predicate = (File.expires_at > now) & (File.expires_at <= warning_deadline)
        elif notification_kind == "expired":
            timestamp_field = "expiry_expired_sent_at"
            status_value = "expired"
            idempotency_predicate = File.expiry_expired_sent_at.is_(None)
            time_window_predicate = File.expires_at <= now
        else:
            msg = f"invalid expiry notification kind: {notification_kind}"
            raise ValueError(msg)

        result = await self._session.execute(
            sql_update(File)
            .where(
                File.id == file_id,
                File.expires_at == expected_expires_at,
                File.status.not_in(EXPIRY_SCAN_EXCLUDED_FILE_STATUSES),
                File.is_current_version.is_(True),
                idempotency_predicate,
                time_window_predicate,
            )
            .values({timestamp_field: sent_at, "expiry_status": status_value})
        )
        return int(cast(CursorResult[object], result).rowcount) == 1


HIDDEN_FILE_STATUSES = ("deleted", "ragflow_cleanup_failed")
ABANDONED_VERSION_STATUSES = (*HIDDEN_FILE_STATUSES, "disabled")
EXPIRY_SCAN_EXCLUDED_FILE_STATUSES = (*HIDDEN_FILE_STATUSES, "disabled")


def _uploader_quota_lock_key(uploader_id: uuid.UUID) -> int:
    return int.from_bytes(uploader_id.bytes[:8], byteorder="big", signed=True)
