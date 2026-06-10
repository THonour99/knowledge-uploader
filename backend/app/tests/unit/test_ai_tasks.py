from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import import_module
from uuid import UUID

import pytest
from redis.asyncio import from_url
from sqlalchemy import select

pytestmark = pytest.mark.asyncio


@dataclass
class FakeAiStorage:
    content: bytes
    fail: bool = False
    calls: int = 0

    async def get_object(self, *, bucket: str, object_key: str) -> bytes:
        _ = (bucket, object_key)
        self.calls += 1
        if self.fail:
            raise RuntimeError("storage unavailable")
        return self.content


async def _reset_database() -> None:
    import_module("app.db.models")

    from app.core.database import engine
    from app.db.base import Base

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()

    redis_client = from_url(  # type: ignore[no-untyped-call]
        os.environ["CACHE_REDIS_URL"],
        encoding="utf-8",
        decode_responses=True,
    )
    try:
        await redis_client.flushdb()
    finally:
        await redis_client.aclose()


@pytest.fixture(autouse=True)
async def clean_database() -> AsyncGenerator[None, None]:
    await _reset_database()
    yield
    from app.core.database import engine
    from app.db.base import Base

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def _create_user() -> UUID:
    from app.core.database import AsyncSessionFactory
    from app.core.security import hash_password
    from app.modules.user.models import User

    user = User(
        name="uploader",
        email="uploader@company.com",
        email_domain="company.com",
        password_hash=hash_password("password123"),
        role="employee",
        status="active",
        email_verified=True,
    )
    async with AsyncSessionFactory() as session:
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user.id


async def _create_category(*, keyword: str = "handbook") -> UUID:
    from app.core.database import AsyncSessionFactory
    from app.modules.review.models import Category

    category = Category(
        name="员工手册",
        code="handbook",
        default_visibility="company",
        keywords=[keyword],
        allow_ai_recommend=True,
        ai_analysis_enabled=True,
        sensitive_detection_enabled=True,
    )
    async with AsyncSessionFactory() as session:
        session.add(category)
        await session.commit()
        await session.refresh(category)
        return category.id


async def _create_file(
    *,
    uploader_id: UUID,
    ai_enabled: bool = True,
    hash_value: str = "c" * 64,
    status_value: str = "uploaded",
) -> UUID:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File

    file = File(
        original_name="handbook.txt",
        stored_name="file-handbook.txt",
        extension="txt",
        mime_type="text/plain",
        size=128,
        hash=hash_value,
        storage_type="minio",
        bucket="knowledge-files",
        object_key=f"uploads/{uploader_id}/file-handbook.txt",
        uploader_id=uploader_id,
        department="QA",
        visibility="private",
        description="phase6 target",
        tags=[],
        status=status_value,
        review_status="pending",
        ai_analysis_enabled_at_upload=ai_enabled,
    )
    async with AsyncSessionFactory() as session:
        session.add(file)
        await session.commit()
        await session.refresh(file)
        return file.id


async def _run_analysis(file_id: UUID, storage: FakeAiStorage) -> None:
    from app.core.config import Settings
    from app.core.database import AsyncSessionFactory
    from app.modules.ai.repository import AiRepository  # noqa: TID251 - direct service test
    from app.modules.ai.service import AiAnalysisService  # noqa: TID251 - direct service test

    settings = Settings(
        allowed_email_domains="company.com",
        jwt_secret="test-jwt-secret-with-more-than-32-bytes",
        cache_redis_url=os.environ["CACHE_REDIS_URL"],
        require_email_verification=False,
        llm_provider="mock",
    )
    async with AsyncSessionFactory() as session:
        service = AiAnalysisService(
            session=session,
            repository=AiRepository(session),
            settings=settings,
        )
        await service.run_file_analysis(file_id, storage=storage)


async def _run_analysis_for_id(file_id: UUID, storage: FakeAiStorage) -> UUID:
    from app.core.config import Settings
    from app.core.database import AsyncSessionFactory
    from app.modules.ai.repository import AiRepository  # noqa: TID251 - direct service test
    from app.modules.ai.service import AiAnalysisService  # noqa: TID251 - direct service test

    settings = Settings(
        allowed_email_domains="company.com",
        jwt_secret="test-jwt-secret-with-more-than-32-bytes",
        cache_redis_url=os.environ["CACHE_REDIS_URL"],
        require_email_verification=False,
        llm_provider="mock",
    )
    async with AsyncSessionFactory() as session:
        service = AiAnalysisService(
            session=session,
            repository=AiRepository(session),
            settings=settings,
        )
        return await service.run_file_analysis(file_id, storage=storage)


async def _create_document_analysis(
    *,
    file_id: UUID,
    status: str = "succeeded",
    error_message: str | None = None,
) -> UUID:
    from app.core.database import AsyncSessionFactory
    from app.modules.ai.models import DocumentAnalysis

    now = datetime.now(UTC)
    analysis = DocumentAnalysis(
        file_id=file_id,
        status=status,
        extracted_text="existing extracted text" if status == "succeeded" else None,
        summary="existing summary" if status == "succeeded" else None,
        suggested_tags=["existing"] if status == "succeeded" else [],
        sensitive_risk_level="none",
        sensitive_hits=[],
        error_message=error_message,
        started_at=now,
        finished_at=None if status == "running" else now,
    )
    async with AsyncSessionFactory() as session:
        session.add(analysis)
        await session.commit()
        await session.refresh(analysis)
        return analysis.id


async def test_ai_analysis_generates_summary_category_and_tags() -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ai.models import DocumentAnalysis
    from app.modules.document.models import File

    uploader_id = await _create_user()
    category_id = await _create_category(keyword="handbook")
    file_id = await _create_file(uploader_id=uploader_id)

    await _run_analysis(
        file_id,
        FakeAiStorage(content=b"handbook onboarding policy and employee benefits"),
    )

    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        assert file is not None
        assert file.status == "analyzed"
        assert file.category_id == category_id
        assert "handbook" in file.tags

        result = await session.execute(
            select(DocumentAnalysis).where(DocumentAnalysis.file_id == file_id)
        )
        analysis = result.scalar_one()
        assert analysis.status == "succeeded"
        assert analysis.summary == "handbook onboarding policy and employee benefits"
        assert analysis.suggested_category_id == category_id
        assert analysis.sensitive_risk_level == "none"


async def test_sensitive_high_risk_requires_sensitive_review() -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ai.models import DocumentAnalysis
    from app.modules.document.models import File

    uploader_id = await _create_user()
    file_id = await _create_file(uploader_id=uploader_id)

    await _run_analysis(
        file_id,
        FakeAiStorage(content=b"api key should never be uploaded into the knowledge base"),
    )

    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        assert file is not None
        assert file.status == "sensitive_review_required"

        result = await session.execute(
            select(DocumentAnalysis).where(DocumentAnalysis.file_id == file_id)
        )
        analysis = result.scalar_one()
        assert analysis.sensitive_risk_level == "high"
        assert analysis.sensitive_hits[0]["action"] == "require_review"


async def test_ai_failure_marks_analysis_failed_without_deleting_file() -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ai.models import DocumentAnalysis
    from app.modules.document.models import File

    uploader_id = await _create_user()
    file_id = await _create_file(uploader_id=uploader_id)

    await _run_analysis(file_id, FakeAiStorage(content=b"", fail=True))

    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        assert file is not None
        assert file.status == "analysis_failed"

        result = await session.execute(
            select(DocumentAnalysis).where(DocumentAnalysis.file_id == file_id)
        )
        analysis = result.scalar_one()
        assert analysis.status == "failed"
        assert analysis.error_message == "RuntimeError"


@pytest.mark.parametrize("status_value", ["analyzed", "pending_review"])
async def test_succeeded_analysis_repeated_delivery_is_noop(status_value: str) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ai.models import DocumentAnalysis
    from app.modules.document.models import File

    uploader_id = await _create_user()
    file_id = await _create_file(uploader_id=uploader_id, status_value=status_value)
    analysis_id = await _create_document_analysis(file_id=file_id)
    storage = FakeAiStorage(content=b"new content should not be fetched")

    returned_id = await _run_analysis_for_id(file_id, storage)

    assert returned_id == analysis_id
    assert storage.calls == 0
    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        assert file is not None
        assert file.status == status_value

        result = await session.execute(
            select(DocumentAnalysis).where(DocumentAnalysis.file_id == file_id)
        )
        analysis = result.scalar_one()
        assert analysis.id == analysis_id
        assert analysis.status == "succeeded"
        assert analysis.error_message is None
        assert analysis.summary == "existing summary"


@pytest.mark.parametrize("status_value", ["extracting_text", "analysis_queued", "analyzing"])
async def test_running_analysis_repeated_delivery_is_noop(status_value: str) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ai.models import DocumentAnalysis
    from app.modules.document.models import File

    uploader_id = await _create_user()
    file_id = await _create_file(uploader_id=uploader_id, status_value=status_value)
    analysis_id = await _create_document_analysis(file_id=file_id, status="running")
    storage = FakeAiStorage(content=b"running content should not be fetched")

    returned_id = await _run_analysis_for_id(file_id, storage)

    assert returned_id == analysis_id
    assert storage.calls == 0
    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        assert file is not None
        assert file.status == status_value

        result = await session.execute(
            select(DocumentAnalysis).where(DocumentAnalysis.file_id == file_id)
        )
        analysis = result.scalar_one()
        assert analysis.id == analysis_id
        assert analysis.status == "running"
        assert analysis.finished_at is None


async def test_analysis_failed_retry_still_runs_analysis() -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ai.models import DocumentAnalysis
    from app.modules.document.models import File

    uploader_id = await _create_user()
    file_id = await _create_file(uploader_id=uploader_id, status_value="analysis_failed")
    analysis_id = await _create_document_analysis(
        file_id=file_id,
        status="failed",
        error_message="previous failure",
    )
    storage = FakeAiStorage(content=b"handbook retry content")

    returned_id = await _run_analysis_for_id(file_id, storage)

    assert returned_id == analysis_id
    assert storage.calls == 1
    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        assert file is not None
        assert file.status == "analyzed"

        result = await session.execute(
            select(DocumentAnalysis).where(DocumentAnalysis.file_id == file_id)
        )
        analysis = result.scalar_one()
        assert analysis.id == analysis_id
        assert analysis.status == "succeeded"
        assert analysis.error_message is None
        assert analysis.summary == "handbook retry content"


async def test_ai_disabled_at_upload_is_precondition_noop() -> None:
    from app.core.config import Settings
    from app.core.database import AsyncSessionFactory
    from app.modules.ai.exceptions import AiAnalysisPreconditionError
    from app.modules.ai.repository import AiRepository  # noqa: TID251 - direct service test
    from app.modules.ai.service import AiAnalysisService  # noqa: TID251 - direct service test
    from app.modules.document.models import File

    uploader_id = await _create_user()
    file_id = await _create_file(uploader_id=uploader_id, ai_enabled=False)
    settings = Settings(
        allowed_email_domains="company.com",
        jwt_secret="test-jwt-secret-with-more-than-32-bytes",
        cache_redis_url=os.environ["CACHE_REDIS_URL"],
        require_email_verification=False,
    )

    async with AsyncSessionFactory() as session:
        service = AiAnalysisService(
            session=session,
            repository=AiRepository(session),
            settings=settings,
        )
        with pytest.raises(AiAnalysisPreconditionError):
            await service.run_file_analysis(
                file_id,
                storage=FakeAiStorage(content=b"handbook"),
            )

    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        assert file is not None
        assert file.status == "uploaded"


async def test_ai_env_disabled_is_precondition_noop_even_when_feature_enabled() -> None:
    from app.core.config import Settings
    from app.core.database import AsyncSessionFactory
    from app.modules.ai.exceptions import AiAnalysisPreconditionError
    from app.modules.ai.models import AiFeatureConfig, DocumentAnalysis
    from app.modules.ai.repository import AiRepository  # noqa: TID251 - direct service test
    from app.modules.ai.service import AiAnalysisService  # noqa: TID251 - direct service test
    from app.modules.document.models import File

    uploader_id = await _create_user()
    file_id = await _create_file(uploader_id=uploader_id)
    async with AsyncSessionFactory() as session:
        session.add(
            AiFeatureConfig(
                feature_name="ai_analysis",
                enabled=True,
                config_json={},
            )
        )
        await session.commit()

    settings = Settings(
        allowed_email_domains="company.com",
        jwt_secret="test-jwt-secret-with-more-than-32-bytes",
        cache_redis_url=os.environ["CACHE_REDIS_URL"],
        require_email_verification=False,
        ai_analysis_enabled=False,
        llm_provider="mock",
    )
    async with AsyncSessionFactory() as session:
        service = AiAnalysisService(
            session=session,
            repository=AiRepository(session),
            settings=settings,
        )
        with pytest.raises(AiAnalysisPreconditionError):
            await service.run_file_analysis(
                file_id,
                storage=FakeAiStorage(content=b"handbook"),
            )

    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        assert file is not None
        assert file.status == "uploaded"
        result = await session.execute(
            select(DocumentAnalysis).where(DocumentAnalysis.file_id == file_id)
        )
        assert result.scalar_one_or_none() is None


async def test_ai_failure_does_not_revert_file_already_in_review() -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ai.models import DocumentAnalysis
    from app.modules.document.models import File

    uploader_id = await _create_user()
    file_id = await _create_file(uploader_id=uploader_id, status_value="pending_review")

    await _run_analysis(file_id, FakeAiStorage(content=b"handbook"))

    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        assert file is not None
        assert file.status == "pending_review"

        result = await session.execute(
            select(DocumentAnalysis).where(DocumentAnalysis.file_id == file_id)
        )
        analysis = result.scalar_one()
        assert analysis.status == "failed"
        assert analysis.error_message == "DocumentStateError"
