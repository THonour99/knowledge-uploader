from __future__ import annotations

import uuid
from datetime import datetime, timedelta
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
    delete,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.review_policy import REVIEW_DUE_SOON_HOURS

from .models import Category, DatasetMapping, FileTag, Tag
from .records import ReviewFileRecord

FILES = Table(
    "files",
    MetaData(),
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("original_name", String(255), nullable=False),
    Column("title", String(255), nullable=False),
    Column("extension", String(20), nullable=False),
    Column("mime_type", String(120), nullable=False),
    Column("size", BigInteger, nullable=False),
    Column("uploader_id", UUID(as_uuid=True), nullable=False),
    Column("owner_id", UUID(as_uuid=True)),
    Column("department_id", UUID(as_uuid=True), nullable=False),
    Column("department", String(100)),
    Column("category_id", UUID(as_uuid=True)),
    Column("dataset_mapping_id", UUID(as_uuid=True)),
    Column("visibility", String(20), nullable=False),
    Column("description", Text),
    Column("tags", JSONB, nullable=False),
    Column("status", String(40), nullable=False),
    Column("review_status", String(40), nullable=False),
    Column("submitted_at", DateTime(timezone=True)),
    Column("review_due_at", DateTime(timezone=True)),
    Column("claimed_by", UUID(as_uuid=True)),
    Column("claimed_at", DateTime(timezone=True)),
    Column("claim_expires_at", DateTime(timezone=True)),
    Column("review_version", Integer, nullable=False),
    Column("ragflow_dataset_id", String(120)),
    Column("ragflow_document_id", String(120)),
    Column("ragflow_parse_status", String(40)),
    Column("ai_analysis_enabled_at_upload", Boolean, nullable=False),
    Column("expires_at", DateTime(timezone=True)),
    Column("expiry_status", String(20), nullable=False),
    Column("expiry_warning_sent_at", DateTime(timezone=True)),
    Column("expiry_expired_sent_at", DateTime(timezone=True)),
    Column("series_id", UUID(as_uuid=True), nullable=False),
    Column("version_number", Integer, nullable=False),
    Column("replaces_file_id", UUID(as_uuid=True)),
    Column("replacement_remote_action", String(20)),
    Column("is_current_version", Boolean, nullable=False),
    Column("remote_visibility", String(20), nullable=False),
    Column("version_switch_status", String(40), nullable=False),
    Column("version_switch_error", String(120)),
    Column("version_switch_attempt_count", Integer, nullable=False),
    Column("predecessor_remote_deactivated_at", DateTime(timezone=True)),
    Column("local_version_activated_at", DateTime(timezone=True)),
    Column("remote_version_activated_at", DateTime(timezone=True)),
    Column("uploaded_at", DateTime(timezone=True), nullable=False),
    Column("last_sync_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

FILE_COLUMNS = tuple(FILES.c)

USERS = Table(
    "users",
    MetaData(),
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("name", String(100), nullable=False),
    Column("department_id", UUID(as_uuid=True), nullable=False),
    Column("status", String(40), nullable=False),
    Column("email_verified", Boolean, nullable=False),
)

DOCUMENT_ANALYSIS = Table(
    "document_analysis",
    MetaData(),
    Column("file_id", UUID(as_uuid=True), primary_key=True),
    Column("status", String(30), nullable=False),
    Column("sensitive_risk_level", String(20), nullable=False),
    Column("sensitive_hits", JSONB, nullable=False),
)

AI_FEATURE_CONFIGS = Table(
    "ai_feature_configs",
    MetaData(),
    Column("feature_name", String(80), nullable=False),
    Column("enabled", Boolean, nullable=False),
)

SYNC_TASKS = Table(
    "sync_tasks",
    MetaData(),
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("file_id", UUID(as_uuid=True), nullable=False),
    Column("task_type", String(40), nullable=False),
    Column("status", String(40), nullable=False),
)

UPLOADER_NAME = (
    select(USERS.c.name)
    .where(USERS.c.id == FILES.c.uploader_id)
    .correlate(FILES)
    .scalar_subquery()
    .label("uploader_name")
)
OWNER_NAME = (
    select(USERS.c.name)
    .where(USERS.c.id == FILES.c.owner_id)
    .correlate(FILES)
    .scalar_subquery()
    .label("owner_name")
)
CLAIMED_BY_NAME = (
    select(USERS.c.name)
    .where(USERS.c.id == FILES.c.claimed_by)
    .correlate(FILES)
    .scalar_subquery()
    .label("claimed_by_name")
)
SENSITIVE_RISK_LEVEL = (
    select(DOCUMENT_ANALYSIS.c.sensitive_risk_level)
    .where(DOCUMENT_ANALYSIS.c.file_id == FILES.c.id)
    .correlate(FILES)
    .scalar_subquery()
    .label("sensitive_risk_level")
)
FILE_RECORD_COLUMNS = (
    *FILE_COLUMNS,
    UPLOADER_NAME,
    OWNER_NAME,
    CLAIMED_BY_NAME,
    SENSITIVE_RISK_LEVEL,
)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class ReviewRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def is_valid_document_owner(
        self,
        *,
        owner_id: uuid.UUID,
        department_id: uuid.UUID,
    ) -> bool:
        result = await self._session.execute(
            select(USERS.c.id)
            .where(
                USERS.c.id == owner_id,
                USERS.c.department_id == department_id,
                USERS.c.status == "active",
                USERS.c.email_verified.is_(True),
            )
            .with_for_update()
        )
        return result.scalar_one_or_none() is not None

    async def add_category(self, category: Category) -> Category:
        self._session.add(category)
        await self._session.flush()
        await self._session.refresh(category)
        return category

    async def get_category(self, category_id: uuid.UUID) -> Category | None:
        result = await self._session.execute(select(Category).where(Category.id == category_id))
        return result.scalar_one_or_none()

    async def get_category_for_update(self, category_id: uuid.UUID) -> Category | None:
        result = await self._session.execute(
            select(Category).where(Category.id == category_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def list_categories(self) -> list[Category]:
        result = await self._session.execute(select(Category).order_by(Category.created_at.desc()))
        return list(result.scalars())

    async def add_dataset_mapping(self, mapping: DatasetMapping) -> DatasetMapping:
        self._session.add(mapping)
        await self._session.flush()
        await self._session.refresh(mapping)
        return mapping

    async def get_dataset_mapping(self, mapping_id: uuid.UUID) -> DatasetMapping | None:
        result = await self._session.execute(
            select(DatasetMapping).where(DatasetMapping.id == mapping_id)
        )
        return result.scalar_one_or_none()

    async def get_dataset_mapping_for_update(
        self,
        mapping_id: uuid.UUID,
    ) -> DatasetMapping | None:
        result = await self._session.execute(
            select(DatasetMapping).where(DatasetMapping.id == mapping_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def list_dataset_mappings(self) -> list[DatasetMapping]:
        result = await self._session.execute(
            select(DatasetMapping).order_by(DatasetMapping.created_at.desc())
        )
        return list(result.scalars())

    async def add_tag(self, tag: Tag) -> Tag:
        self._session.add(tag)
        await self._session.flush()
        await self._session.refresh(tag)
        return tag

    async def get_tag(self, tag_id: uuid.UUID) -> Tag | None:
        result = await self._session.execute(select(Tag).where(Tag.id == tag_id))
        return result.scalar_one_or_none()

    async def get_tag_by_name(self, name: str) -> Tag | None:
        result = await self._session.execute(select(Tag).where(Tag.name == name))
        return result.scalar_one_or_none()

    async def list_tags(
        self,
        *,
        enabled: bool | None,
        search: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[tuple[Tag, int]], int]:
        """分页查询标签, usage_count 以 file_tags 实时关联数为准。"""
        usage = (
            select(FileTag.tag_id, func.count().label("usage_count"))
            .group_by(FileTag.tag_id)
            .subquery()
        )
        stmt = select(Tag, func.coalesce(usage.c.usage_count, 0)).outerjoin(
            usage, usage.c.tag_id == Tag.id
        )
        count_stmt = select(func.count()).select_from(Tag)
        if enabled is not None:
            stmt = stmt.where(Tag.enabled == enabled)
            count_stmt = count_stmt.where(Tag.enabled == enabled)
        if search:
            pattern = f"%{_escape_like(search)}%"
            stmt = stmt.where(Tag.name.ilike(pattern, escape="\\"))
            count_stmt = count_stmt.where(Tag.name.ilike(pattern, escape="\\"))
        stmt = stmt.order_by(Tag.name.asc()).offset((page - 1) * page_size).limit(page_size)
        result = await self._session.execute(stmt)
        items = [(row[0], int(row[1])) for row in result.all()]
        total = int((await self._session.execute(count_stmt)).scalar_one())
        return items, total

    async def count_tag_files(self, tag_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.count()).select_from(FileTag).where(FileTag.tag_id == tag_id)
        )
        return int(result.scalar_one())

    async def move_file_tag_links(
        self,
        *,
        source_tag_id: uuid.UUID,
        target_tag_id: uuid.UUID,
    ) -> None:
        """把源标签关联迁移到目标标签, 目标已存在的关联去重后丢弃源关联。"""
        already_linked = select(FileTag.file_id).where(FileTag.tag_id == target_tag_id)
        await self._session.execute(
            update(FileTag)
            .where(FileTag.tag_id == source_tag_id, FileTag.file_id.not_in(already_linked))
            .values(tag_id=target_tag_id),
            execution_options={"synchronize_session": False},
        )
        await self._session.execute(
            delete(FileTag).where(FileTag.tag_id == source_tag_id),
            execution_options={"synchronize_session": False},
        )

    async def set_tag_usage_count(self, tag_id: uuid.UUID, usage_count: int) -> None:
        await self._session.execute(
            update(Tag).where(Tag.id == tag_id).values(usage_count=usage_count)
        )

    async def delete_tag(self, tag: Tag) -> None:
        await self._session.delete(tag)
        await self._session.flush()

    async def list_files(
        self,
        *,
        page: int,
        page_size: int,
        now: datetime,
        current_user_id: uuid.UUID,
        search: str | None = None,
        queue: str | None = None,
        extension: str | None = None,
        tag_id: uuid.UUID | None = None,
        department_ids: frozenset[uuid.UUID] | None = None,
        department_id: uuid.UUID | None = None,
        sensitive_risk_level: str | None = None,
        sort: str | None = None,
        order: str = "asc",
    ) -> tuple[list[ReviewFileRecord], int]:
        stmt = select(*FILE_RECORD_COLUMNS).select_from(FILES)
        count_stmt = select(func.count(func.distinct(FILES.c.id))).select_from(FILES)
        predicates = [FILES.c.status == "pending_review"]
        if department_ids is not None:
            if not department_ids:
                return [], 0
            predicates.append(FILES.c.department_id.in_(department_ids))
        if department_id is not None:
            predicates.append(FILES.c.department_id == department_id)
        if extension:
            predicates.append(FILES.c.extension == extension)
        if search:
            pattern = f"%{_escape_like(search)}%"
            predicates.append(
                or_(
                    FILES.c.title.ilike(pattern, escape="\\"),
                    FILES.c.original_name.ilike(pattern, escape="\\"),
                    FILES.c.description.ilike(pattern, escape="\\"),
                    FILES.c.department.ilike(pattern, escape="\\"),
                    UPLOADER_NAME.ilike(pattern, escape="\\"),
                )
            )
        if queue == "unclaimed":
            predicates.append(
                or_(
                    FILES.c.claimed_by.is_(None),
                    FILES.c.claimed_at.is_(None),
                    FILES.c.claim_expires_at.is_(None),
                    FILES.c.claim_expires_at <= now,
                )
            )
        elif queue == "mine":
            predicates.extend(
                (
                    FILES.c.claimed_by == current_user_id,
                    FILES.c.claim_expires_at > now,
                )
            )
        elif queue == "due_soon":
            due_soon = now + timedelta(hours=REVIEW_DUE_SOON_HOURS)
            predicates.extend(
                (
                    FILES.c.review_due_at > now,
                    FILES.c.review_due_at <= due_soon,
                )
            )
        elif queue == "overdue":
            predicates.append(FILES.c.review_due_at <= now)
        if sensitive_risk_level:
            predicates.append(SENSITIVE_RISK_LEVEL == sensitive_risk_level)
        if tag_id is not None:
            stmt = stmt.join(FileTag, FileTag.file_id == FILES.c.id)
            count_stmt = count_stmt.join(FileTag, FileTag.file_id == FILES.c.id)
            predicates.append(FileTag.tag_id == tag_id)
        stmt = stmt.where(*predicates)
        count_stmt = count_stmt.where(*predicates)

        direction = "desc" if order == "desc" else "asc"
        risk_rank = case(
            (SENSITIVE_RISK_LEVEL == "critical", 0),
            (SENSITIVE_RISK_LEVEL == "high", 1),
            (SENSITIVE_RISK_LEVEL == "medium", 2),
            (SENSITIVE_RISK_LEVEL == "low", 3),
            else_=4,
        )
        sort_columns = {
            "submitted_at": FILES.c.submitted_at,
            "review_due_at": FILES.c.review_due_at,
            "uploaded_at": FILES.c.uploaded_at,
            "original_name": FILES.c.original_name,
            "risk": risk_rank,
        }
        if sort in sort_columns:
            sort_column = sort_columns[sort]
            order_expression = (
                sort_column.desc().nullslast()
                if direction == "desc"
                else sort_column.asc().nullslast()
            )
            stmt = stmt.order_by(order_expression, FILES.c.id.asc())
        else:
            overdue_rank = case((FILES.c.review_due_at <= now, 0), else_=1)
            stmt = stmt.order_by(
                overdue_rank.asc(),
                risk_rank.asc(),
                FILES.c.review_due_at.asc().nullslast(),
                FILES.c.submitted_at.asc().nullslast(),
                FILES.c.id.asc(),
            )
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        result = await self._session.execute(stmt)
        total = int((await self._session.execute(count_stmt)).scalar_one())
        return [file_record_from_row(row) for row in result.mappings()], total

    async def get_file(self, file_id: uuid.UUID) -> ReviewFileRecord | None:
        result = await self._session.execute(
            select(*FILE_RECORD_COLUMNS).where(
                FILES.c.id == file_id,
                FILES.c.status.not_in(HIDDEN_FILE_STATUSES),
            )
        )
        row = result.mappings().one_or_none()
        return file_record_from_row(row) if row is not None else None

    async def get_file_for_update(self, file_id: uuid.UUID) -> ReviewFileRecord | None:
        result = await self._session.execute(
            select(*FILE_RECORD_COLUMNS)
            .where(
                FILES.c.id == file_id,
                FILES.c.status.not_in(HIDDEN_FILE_STATUSES),
            )
            .with_for_update(of=FILES)
        )
        row = result.mappings().one_or_none()
        return file_record_from_row(row) if row is not None else None

    async def has_active_ragflow_upload_task(self, file_id: uuid.UUID) -> bool:
        result = await self._session.execute(
            select(SYNC_TASKS.c.id)
            .where(
                SYNC_TASKS.c.file_id == file_id,
                SYNC_TASKS.c.task_type == "ragflow_upload",
                SYNC_TASKS.c.status.in_(("queued", "running")),
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def update_file(self, file: ReviewFileRecord) -> ReviewFileRecord:
        await self._session.execute(
            update(FILES)
            .where(FILES.c.id == file.id)
            .values(
                status=file.status,
                review_status=file.review_status,
                submitted_at=file.submitted_at,
                review_due_at=file.review_due_at,
                claimed_by=file.claimed_by,
                claimed_at=file.claimed_at,
                claim_expires_at=file.claim_expires_at,
                review_version=file.review_version,
                category_id=file.category_id,
                dataset_mapping_id=file.dataset_mapping_id,
                ragflow_dataset_id=file.ragflow_dataset_id,
                updated_at=func.now(),
            )
        )
        updated = await self.get_file(file.id)
        if updated is None:
            msg = "updated review file is not readable"
            raise RuntimeError(msg)
        return updated

    async def update_owner_draft_metadata(
        self,
        *,
        file_id: uuid.UUID,
        uploader_id: uuid.UUID,
        expected_version: int,
        editable_statuses: frozenset[str],
        values: dict[str, object],
    ) -> ReviewFileRecord | None:
        result = await self._session.execute(
            update(FILES)
            .where(
                FILES.c.id == file_id,
                FILES.c.uploader_id == uploader_id,
                FILES.c.status.in_(editable_statuses),
                FILES.c.status.not_in(HIDDEN_FILE_STATUSES),
                FILES.c.review_version == expected_version,
            )
            .values(
                **values,
                review_version=FILES.c.review_version + 1,
                updated_at=func.now(),
            )
            .returning(FILES.c.id)
        )
        updated_id = result.scalar_one_or_none()
        if updated_id is None:
            return None
        return await self.get_file(cast(uuid.UUID, updated_id))

    async def release_expired_claims(
        self,
        *,
        now: datetime,
        department_ids: frozenset[uuid.UUID] | None,
        limit: int,
    ) -> list[tuple[uuid.UUID, uuid.UUID]]:
        predicates = [
            FILES.c.status == "pending_review",
            FILES.c.claimed_by.is_not(None),
            FILES.c.claim_expires_at <= now,
        ]
        if department_ids is not None:
            if not department_ids:
                return []
            predicates.append(FILES.c.department_id.in_(department_ids))
        result = await self._session.execute(
            select(FILES.c.id, FILES.c.claimed_by)
            .where(*predicates)
            .with_for_update(skip_locked=True, of=FILES)
            .limit(limit)
        )
        expired = [
            (cast(uuid.UUID, row.id), cast(uuid.UUID, row.claimed_by))
            for row in result
            if row.claimed_by is not None
        ]
        if expired:
            await self._session.execute(
                update(FILES)
                .where(FILES.c.id.in_([file_id for file_id, _ in expired]))
                .values(
                    claimed_by=None,
                    claimed_at=None,
                    claim_expires_at=None,
                    review_status="pending",
                    review_version=FILES.c.review_version + 1,
                    updated_at=func.now(),
                )
            )
        return expired

    async def get_file_sensitive_risk_level(self, file_id: uuid.UUID) -> str | None:
        result = await self._session.execute(
            select(DOCUMENT_ANALYSIS.c.sensitive_risk_level).where(
                DOCUMENT_ANALYSIS.c.file_id == file_id
            )
        )
        return cast(str | None, result.scalar_one_or_none())

    async def file_requires_sensitive_acknowledgement(self, file_id: uuid.UUID) -> bool:
        result = await self._session.execute(
            select(
                DOCUMENT_ANALYSIS.c.sensitive_risk_level,
                DOCUMENT_ANALYSIS.c.sensitive_hits,
            ).where(DOCUMENT_ANALYSIS.c.file_id == file_id)
        )
        row = result.mappings().one_or_none()
        if row is None:
            return False
        if row["sensitive_risk_level"] in {"high", "critical"}:
            return True
        hits = cast(list[object] | None, row["sensitive_hits"])
        return any(
            isinstance(hit, dict) and hit.get("action") in {"require_review", "block_sync"}
            for hit in (hits or [])
        )

    async def get_file_analysis_status(self, file_id: uuid.UUID) -> str | None:
        result = await self._session.execute(
            select(DOCUMENT_ANALYSIS.c.status).where(DOCUMENT_ANALYSIS.c.file_id == file_id)
        )
        return cast(str | None, result.scalar_one_or_none())

    async def get_ai_feature_enabled(self, feature_name: str) -> bool | None:
        result = await self._session.execute(
            select(AI_FEATURE_CONFIGS.c.enabled).where(
                AI_FEATURE_CONFIGS.c.feature_name == feature_name
            )
        )
        return cast(bool | None, result.scalar_one_or_none())


def file_record_from_row(row: RowMapping) -> ReviewFileRecord:
    return ReviewFileRecord(
        id=cast(uuid.UUID, row["id"]),
        original_name=cast(str, row["original_name"]),
        title=cast(str, row["title"]),
        extension=cast(str, row["extension"]),
        mime_type=cast(str, row["mime_type"]),
        size=cast(int, row["size"]),
        uploader_id=cast(uuid.UUID, row["uploader_id"]),
        uploader_name=cast(str | None, row.get("uploader_name")),
        owner_id=cast(uuid.UUID | None, row["owner_id"]),
        owner_name=cast(str | None, row.get("owner_name")),
        department_id=cast(uuid.UUID, row["department_id"]),
        department=cast(str | None, row["department"]),
        category_id=cast(uuid.UUID | None, row["category_id"]),
        dataset_mapping_id=cast(uuid.UUID | None, row["dataset_mapping_id"]),
        visibility=cast(str, row["visibility"]),
        description=cast(str | None, row["description"]),
        tags=cast(list[str], row["tags"]),
        status=cast(str, row["status"]),
        review_status=cast(str, row["review_status"]),
        submitted_at=cast(datetime | None, row["submitted_at"]),
        review_due_at=cast(datetime | None, row["review_due_at"]),
        claimed_by=cast(uuid.UUID | None, row["claimed_by"]),
        claimed_by_name=cast(str | None, row.get("claimed_by_name")),
        claimed_at=cast(datetime | None, row["claimed_at"]),
        claim_expires_at=cast(datetime | None, row["claim_expires_at"]),
        review_version=cast(int, row["review_version"]),
        sensitive_risk_level=cast(str | None, row.get("sensitive_risk_level")),
        ragflow_dataset_id=cast(str | None, row["ragflow_dataset_id"]),
        ragflow_document_id=cast(str | None, row["ragflow_document_id"]),
        ragflow_parse_status=cast(str | None, row["ragflow_parse_status"]),
        ai_analysis_enabled_at_upload=cast(bool, row["ai_analysis_enabled_at_upload"]),
        expires_at=cast(datetime | None, row["expires_at"]),
        expiry_status=cast(str, row["expiry_status"]),
        series_id=cast(uuid.UUID, row["series_id"]),
        version_number=cast(int, row["version_number"]),
        replaces_file_id=cast(uuid.UUID | None, row["replaces_file_id"]),
        replacement_remote_action=cast(str | None, row["replacement_remote_action"]),
        is_current_version=cast(bool, row["is_current_version"]),
        remote_visibility=cast(str, row["remote_visibility"]),
        version_switch_status=cast(str, row["version_switch_status"]),
        version_switch_error=cast(str | None, row["version_switch_error"]),
        version_switch_attempt_count=cast(int, row["version_switch_attempt_count"]),
        predecessor_remote_deactivated_at=cast(
            datetime | None, row["predecessor_remote_deactivated_at"]
        ),
        local_version_activated_at=cast(datetime | None, row["local_version_activated_at"]),
        remote_version_activated_at=cast(datetime | None, row["remote_version_activated_at"]),
        uploaded_at=cast(datetime, row["uploaded_at"]),
        last_sync_at=cast(datetime | None, row["last_sync_at"]),
        created_at=cast(datetime, row["created_at"]),
        updated_at=cast(datetime, row["updated_at"]),
    )


HIDDEN_FILE_STATUSES = ("deleted", "ragflow_cleanup_failed")
