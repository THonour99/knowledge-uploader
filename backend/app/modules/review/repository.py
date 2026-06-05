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
    func,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Category, DatasetMapping
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
    Column("uploaded_at", DateTime(timezone=True), nullable=False),
    Column("last_sync_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

FILE_COLUMNS = tuple(FILES.c)


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

    async def list_files(self) -> list[ReviewFileRecord]:
        result = await self._session.execute(
            select(*FILE_COLUMNS).order_by(FILES.c.uploaded_at.desc())
        )
        return [file_record_from_row(row) for row in result.mappings()]

    async def get_file(self, file_id: uuid.UUID) -> ReviewFileRecord | None:
        result = await self._session.execute(select(*FILE_COLUMNS).where(FILES.c.id == file_id))
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
        uploaded_at=cast(datetime, row["uploaded_at"]),
        last_sync_at=cast(datetime | None, row["last_sync_at"]),
        created_at=cast(datetime, row["created_at"]),
        updated_at=cast(datetime, row["updated_at"]),
    )
