from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import cast

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    MetaData,
    SmallInteger,
    String,
    Table,
    Text,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    AiFeatureConfig,
    AiProvider,
    DocumentAnalysis,
    PromptTemplate,
    SensitiveRule,
)

FILES = Table(
    "files",
    MetaData(),
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("original_name", String(255), nullable=False),
    Column("stored_name", String(255), nullable=False),
    Column("extension", String(20), nullable=False),
    Column("mime_type", String(120), nullable=False),
    Column("size", BigInteger, nullable=False),
    Column("bucket", String(100), nullable=False),
    Column("object_key", String(512), nullable=False),
    Column("description", Text),
    Column("tags", JSONB, nullable=False),
    Column("status", String(40), nullable=False),
    Column("category_id", UUID(as_uuid=True)),
    Column("ai_analysis_enabled_at_upload", Boolean, nullable=False),
    Column("ai_config_snapshot", JSONB),
    Column("simhash", BigInteger),
    Column("simhash_band_0", SmallInteger),
    Column("simhash_band_1", SmallInteger),
    Column("simhash_band_2", SmallInteger),
    Column("simhash_band_3", SmallInteger),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

FILE_COLUMNS = tuple(FILES.c)

CATEGORIES = Table(
    "categories",
    MetaData(),
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("name", String(120), nullable=False),
    Column("code", String(80), nullable=False),
    Column("keywords", JSONB, nullable=False),
    Column("allow_ai_recommend", Boolean, nullable=False),
    Column("ai_analysis_enabled", Boolean, nullable=False),
    Column("sensitive_detection_enabled", Boolean, nullable=False),
)


@dataclass
class AiFileRecord:
    id: uuid.UUID
    original_name: str
    stored_name: str
    extension: str
    mime_type: str
    size: int
    bucket: str
    object_key: str
    description: str | None
    tags: list[str]
    status: str
    category_id: uuid.UUID | None
    ai_analysis_enabled_at_upload: bool
    ai_config_snapshot: dict[str, object] | None
    simhash: int | None
    simhash_band_0: int | None
    simhash_band_1: int | None
    simhash_band_2: int | None
    simhash_band_3: int | None


@dataclass(frozen=True)
class AiCategoryRecord:
    id: uuid.UUID
    name: str
    code: str
    keywords: list[str]
    allow_ai_recommend: bool
    ai_analysis_enabled: bool
    sensitive_detection_enabled: bool


class AiRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_provider(self, provider: AiProvider) -> AiProvider:
        self._session.add(provider)
        await self._session.flush()
        await self._session.refresh(provider)
        return provider

    async def get_provider(self, provider_id: uuid.UUID) -> AiProvider | None:
        result = await self._session.execute(select(AiProvider).where(AiProvider.id == provider_id))
        return result.scalar_one_or_none()

    async def list_providers(self) -> list[AiProvider]:
        result = await self._session.execute(
            select(AiProvider).order_by(AiProvider.priority.asc(), AiProvider.created_at.desc())
        )
        return list(result.scalars())

    async def get_enabled_provider(self) -> AiProvider | None:
        result = await self._session.execute(
            select(AiProvider)
            .where(AiProvider.enabled.is_(True), AiProvider.provider_type != "disabled")
            .order_by(AiProvider.priority.asc(), AiProvider.created_at.asc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_feature_configs(self) -> list[AiFeatureConfig]:
        result = await self._session.execute(
            select(AiFeatureConfig).order_by(AiFeatureConfig.feature_name.asc())
        )
        return list(result.scalars())

    async def get_feature_config(self, feature_name: str) -> AiFeatureConfig | None:
        result = await self._session.execute(
            select(AiFeatureConfig).where(AiFeatureConfig.feature_name == feature_name)
        )
        return result.scalar_one_or_none()

    async def add_feature_config(self, feature: AiFeatureConfig) -> AiFeatureConfig:
        self._session.add(feature)
        await self._session.flush()
        await self._session.refresh(feature)
        return feature

    async def list_prompt_templates(self) -> list[PromptTemplate]:
        result = await self._session.execute(
            select(PromptTemplate).order_by(PromptTemplate.template_key.asc())
        )
        return list(result.scalars())

    async def add_prompt_template(self, template: PromptTemplate) -> PromptTemplate:
        self._session.add(template)
        await self._session.flush()
        await self._session.refresh(template)
        return template

    async def list_sensitive_rules(self, *, enabled_only: bool = False) -> list[SensitiveRule]:
        statement = select(SensitiveRule).order_by(
            SensitiveRule.risk_level.desc(),
            SensitiveRule.created_at.asc(),
        )
        if enabled_only:
            statement = statement.where(SensitiveRule.enabled.is_(True))
        result = await self._session.execute(statement)
        return list(result.scalars())

    async def add_sensitive_rule(self, rule: SensitiveRule) -> SensitiveRule:
        self._session.add(rule)
        await self._session.flush()
        await self._session.refresh(rule)
        return rule

    async def increment_sensitive_rule_hits(self, rule_ids: list[uuid.UUID]) -> None:
        if not rule_ids:
            return
        await self._session.execute(
            update(SensitiveRule)
            .where(SensitiveRule.id.in_(rule_ids))
            .values(hit_count=SensitiveRule.hit_count + 1, updated_at=func.now())
        )

    async def get_file_for_update(self, file_id: uuid.UUID) -> AiFileRecord | None:
        result = await self._session.execute(
            select(*FILE_COLUMNS).where(FILES.c.id == file_id).with_for_update()
        )
        row = result.mappings().one_or_none()
        return file_record_from_row(row) if row is not None else None

    async def update_file_analysis_state(self, file: AiFileRecord) -> AiFileRecord:
        result = await self._session.execute(
            update(FILES)
            .where(FILES.c.id == file.id)
            .values(
                status=file.status,
                category_id=file.category_id,
                tags=file.tags,
                ai_config_snapshot=file.ai_config_snapshot,
                simhash=file.simhash,
                simhash_band_0=file.simhash_band_0,
                simhash_band_1=file.simhash_band_1,
                simhash_band_2=file.simhash_band_2,
                simhash_band_3=file.simhash_band_3,
                updated_at=func.now(),
            )
            .returning(*FILE_COLUMNS)
        )
        return file_record_from_row(result.mappings().one())

    async def list_simhash_candidates(
        self,
        *,
        file_id: uuid.UUID,
        bands: tuple[int, int, int, int],
    ) -> list[AiFileRecord]:
        result = await self._session.execute(
            select(*FILE_COLUMNS).where(
                FILES.c.id != file_id,
                FILES.c.status.not_in(("deleted", "disabled")),
                FILES.c.simhash.is_not(None),
                or_(
                    FILES.c.simhash_band_0 == bands[0],
                    FILES.c.simhash_band_1 == bands[1],
                    FILES.c.simhash_band_2 == bands[2],
                    FILES.c.simhash_band_3 == bands[3],
                ),
            )
        )
        return [file_record_from_row(row) for row in result.mappings()]

    async def list_categories(self) -> list[AiCategoryRecord]:
        result = await self._session.execute(
            select(
                CATEGORIES.c.id,
                CATEGORIES.c.name,
                CATEGORIES.c.code,
                CATEGORIES.c.keywords,
                CATEGORIES.c.allow_ai_recommend,
                CATEGORIES.c.ai_analysis_enabled,
                CATEGORIES.c.sensitive_detection_enabled,
            ).where(CATEGORIES.c.allow_ai_recommend.is_(True))
        )
        return [category_record_from_row(row) for row in result.mappings()]

    async def get_document_analysis(self, file_id: uuid.UUID) -> DocumentAnalysis | None:
        result = await self._session.execute(
            select(DocumentAnalysis).where(DocumentAnalysis.file_id == file_id)
        )
        return result.scalar_one_or_none()

    async def add_document_analysis(self, analysis: DocumentAnalysis) -> DocumentAnalysis:
        self._session.add(analysis)
        await self._session.flush()
        await self._session.refresh(analysis)
        return analysis


def file_record_from_row(row: RowMapping) -> AiFileRecord:
    return AiFileRecord(
        id=cast(uuid.UUID, row["id"]),
        original_name=cast(str, row["original_name"]),
        stored_name=cast(str, row["stored_name"]),
        extension=cast(str, row["extension"]),
        mime_type=cast(str, row["mime_type"]),
        size=cast(int, row["size"]),
        bucket=cast(str, row["bucket"]),
        object_key=cast(str, row["object_key"]),
        description=cast(str | None, row["description"]),
        tags=cast(list[str], row["tags"]),
        status=cast(str, row["status"]),
        category_id=cast(uuid.UUID | None, row["category_id"]),
        ai_analysis_enabled_at_upload=cast(bool, row["ai_analysis_enabled_at_upload"]),
        ai_config_snapshot=cast(dict[str, object] | None, row["ai_config_snapshot"]),
        simhash=cast(int | None, row["simhash"]),
        simhash_band_0=cast(int | None, row["simhash_band_0"]),
        simhash_band_1=cast(int | None, row["simhash_band_1"]),
        simhash_band_2=cast(int | None, row["simhash_band_2"]),
        simhash_band_3=cast(int | None, row["simhash_band_3"]),
    )


def category_record_from_row(row: RowMapping) -> AiCategoryRecord:
    return AiCategoryRecord(
        id=cast(uuid.UUID, row["id"]),
        name=cast(str, row["name"]),
        code=cast(str, row["code"]),
        keywords=cast(list[str], row["keywords"]),
        allow_ai_recommend=cast(bool, row["allow_ai_recommend"]),
        ai_analysis_enabled=cast(bool, row["ai_analysis_enabled"]),
        sensitive_detection_enabled=cast(bool, row["sensitive_detection_enabled"]),
    )
