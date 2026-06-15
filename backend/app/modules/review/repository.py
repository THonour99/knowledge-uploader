from __future__ import annotations

import uuid
from datetime import datetime
from typing import cast

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    MetaData,
    String,
    Table,
    Text,
    delete,
    func,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Category, DatasetMapping, FileTag, Tag
from .records import ReviewFileRecord

FILES = Table(
    "files",
    MetaData(),
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("original_name", String(255), nullable=False),
    Column("extension", String(20), nullable=False),
    Column("mime_type", String(120), nullable=False),
    Column("size", BigInteger, nullable=False),
    Column("uploader_id", UUID(as_uuid=True), nullable=False),
    Column("department", String(100)),
    Column("category_id", UUID(as_uuid=True)),
    Column("dataset_mapping_id", UUID(as_uuid=True)),
    Column("visibility", String(20), nullable=False),
    Column("description", Text),
    Column("tags", JSONB, nullable=False),
    Column("status", String(40), nullable=False),
    Column("review_status", String(40), nullable=False),
    Column("ragflow_dataset_id", String(120)),
    Column("ragflow_document_id", String(120)),
    Column("ragflow_parse_status", String(40)),
    Column("ai_analysis_enabled_at_upload", Boolean, nullable=False),
    Column("expires_at", DateTime(timezone=True)),
    Column("expiry_status", String(20), nullable=False),
    Column("uploaded_at", DateTime(timezone=True), nullable=False),
    Column("last_sync_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

FILE_COLUMNS = tuple(FILES.c)

DOCUMENT_ANALYSIS = Table(
    "document_analysis",
    MetaData(),
    Column("file_id", UUID(as_uuid=True), primary_key=True),
    Column("status", String(30), nullable=False),
    Column("sensitive_risk_level", String(20), nullable=False),
)

AI_FEATURE_CONFIGS = Table(
    "ai_feature_configs",
    MetaData(),
    Column("feature_name", String(80), nullable=False),
    Column("enabled", Boolean, nullable=False),
)


class ReviewRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_category(self, category: Category) -> Category:
        self._session.add(category)
        await self._session.flush()
        await self._session.refresh(category)
        return category

    async def get_category(self, category_id: uuid.UUID) -> Category | None:
        result = await self._session.execute(select(Category).where(Category.id == category_id))
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
            pattern = f"%{search}%"
            stmt = stmt.where(Tag.name.ilike(pattern))
            count_stmt = count_stmt.where(Tag.name.ilike(pattern))
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
        extension: str | None = None,
        tag_id: uuid.UUID | None = None,
    ) -> list[ReviewFileRecord]:
        stmt = (
            select(*FILE_COLUMNS)
            .select_from(FILES)
            .where(FILES.c.status.not_in(HIDDEN_FILE_STATUSES))
        )
        if extension:
            stmt = stmt.where(FILES.c.extension == extension)
        if tag_id is not None:
            stmt = stmt.join(FileTag, FileTag.file_id == FILES.c.id).where(FileTag.tag_id == tag_id)
        result = await self._session.execute(stmt.order_by(FILES.c.uploaded_at.desc()))
        return [file_record_from_row(row) for row in result.mappings()]

    async def get_file(self, file_id: uuid.UUID) -> ReviewFileRecord | None:
        result = await self._session.execute(
            select(*FILE_COLUMNS).where(
                FILES.c.id == file_id,
                FILES.c.status.not_in(HIDDEN_FILE_STATUSES),
            )
        )
        row = result.mappings().one_or_none()
        return file_record_from_row(row) if row is not None else None

    async def update_file(self, file: ReviewFileRecord) -> ReviewFileRecord:
        result = await self._session.execute(
            update(FILES)
            .where(FILES.c.id == file.id)
            .values(
                status=file.status,
                review_status=file.review_status,
                category_id=file.category_id,
                dataset_mapping_id=file.dataset_mapping_id,
                ragflow_dataset_id=file.ragflow_dataset_id,
                updated_at=func.now(),
            )
            .returning(*FILE_COLUMNS)
        )
        return file_record_from_row(result.mappings().one())

    async def get_file_sensitive_risk_level(self, file_id: uuid.UUID) -> str | None:
        result = await self._session.execute(
            select(DOCUMENT_ANALYSIS.c.sensitive_risk_level).where(
                DOCUMENT_ANALYSIS.c.file_id == file_id
            )
        )
        return cast(str | None, result.scalar_one_or_none())

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
        extension=cast(str, row["extension"]),
        mime_type=cast(str, row["mime_type"]),
        size=cast(int, row["size"]),
        uploader_id=cast(uuid.UUID, row["uploader_id"]),
        department=cast(str | None, row["department"]),
        category_id=cast(uuid.UUID | None, row["category_id"]),
        dataset_mapping_id=cast(uuid.UUID | None, row["dataset_mapping_id"]),
        visibility=cast(str, row["visibility"]),
        description=cast(str | None, row["description"]),
        tags=cast(list[str], row["tags"]),
        status=cast(str, row["status"]),
        review_status=cast(str, row["review_status"]),
        ragflow_dataset_id=cast(str | None, row["ragflow_dataset_id"]),
        ragflow_document_id=cast(str | None, row["ragflow_document_id"]),
        ragflow_parse_status=cast(str | None, row["ragflow_parse_status"]),
        ai_analysis_enabled_at_upload=cast(bool, row["ai_analysis_enabled_at_upload"]),
        expires_at=cast(datetime | None, row["expires_at"]),
        expiry_status=cast(str, row["expiry_status"]),
        uploaded_at=cast(datetime, row["uploaded_at"]),
        last_sync_at=cast(datetime | None, row["last_sync_at"]),
        created_at=cast(datetime, row["created_at"]),
        updated_at=cast(datetime, row["updated_at"]),
    )


HIDDEN_FILE_STATUSES = ("deleted", "ragflow_cleanup_failed")
