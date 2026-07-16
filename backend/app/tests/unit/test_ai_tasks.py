from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib import import_module
from io import BytesIO
from uuid import UUID

import pytest
from openpyxl import Workbook
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


@dataclass
class TransientOnceAiStorage:
    content: bytes
    calls: int = 0

    async def get_object(self, *, bucket: str, object_key: str) -> bytes:
        _ = (bucket, object_key)
        self.calls += 1
        if self.calls == 1:
            raise OSError("temporary object storage outage")
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
    original_name: str = "handbook.txt",
    stored_name: str = "file-handbook.txt",
    extension: str = "txt",
    mime_type: str = "text/plain",
    submit_after_upload: bool | None = None,
) -> UUID:
    from app.core.database import AsyncSessionFactory
    from app.modules.document.models import File

    submitted_at = datetime.now(UTC) if status_value == "pending_review" else None
    file = File(
        original_name=original_name,
        title=original_name,
        stored_name=stored_name,
        extension=extension,
        mime_type=mime_type,
        size=128,
        hash=hash_value,
        storage_type="minio",
        bucket="knowledge-files",
        object_key=f"uploads/{uploader_id}/{stored_name}",
        uploader_id=uploader_id,
        department="QA",
        visibility="private",
        description="phase6 target",
        tags=[],
        status=status_value,
        review_status="pending",
        submitted_at=submitted_at,
        review_due_at=(
            submitted_at + timedelta(hours=24) if submitted_at is not None else None
        ),
        ai_analysis_enabled_at_upload=ai_enabled,
        ai_config_snapshot=(
            {"submit_after_upload": submit_after_upload}
            if submit_after_upload is not None
            else None
        ),
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


async def _run_analysis_for_id(
    file_id: UUID,
    storage: FakeAiStorage | TransientOnceAiStorage,
    *,
    delivery_token: str | None = None,
) -> UUID:
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
        return await service.run_file_analysis(
            file_id,
            storage=storage,
            delivery_token=delivery_token,
        )


async def _create_document_analysis(
    *,
    file_id: UUID,
    status: str = "succeeded",
    error_message: str | None = None,
    started_at: datetime | None = None,
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
        started_at=started_at or now,
        finished_at=None if status == "running" else now,
    )
    async with AsyncSessionFactory() as session:
        session.add(analysis)
        await session.commit()
        await session.refresh(analysis)
        return analysis.id


async def _set_ai_feature(
    *,
    feature_name: str,
    enabled: bool,
    config_json: dict[str, object] | None = None,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ai.models import AiFeatureConfig

    async with AsyncSessionFactory() as session:
        session.add(
            AiFeatureConfig(
                feature_name=feature_name,
                enabled=enabled,
                config_json=config_json or {},
            )
        )
        await session.commit()


def _build_xlsx_bytes(sheet_title: str, rows: list[list[str]]) -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    assert worksheet is not None
    worksheet.title = sheet_title
    for row in rows:
        worksheet.append(row)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


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


async def test_ai_analysis_auto_submits_and_emits_outbox_chain() -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.document.models import File

    uploader_id = await _create_user()
    file_id = await _create_file(uploader_id=uploader_id, submit_after_upload=True)

    storage = FakeAiStorage(content=b"ordinary handbook content")
    await _run_analysis(file_id, storage)
    await _run_analysis(file_id, storage)

    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        assert file is not None
        assert file.status == "pending_review"
        assert file.submitted_at is not None
        assert file.review_due_at is not None
        assert file.review_due_at > file.submitted_at
        assert file.review_version == 1
        assert file.ai_config_snapshot is not None
        assert file.ai_config_snapshot["submit_after_upload"] is True
        events_result = await session.execute(
            select(EventOutbox)
            .where(EventOutbox.aggregate_id == str(file_id))
            .order_by(EventOutbox.id)
        )
        outbox_events = list(events_result.scalars())

    assert [event.event_type for event in outbox_events] == [
        "ai.text.extracted",
        "ai.file.analyzed",
        "review.file.submitted",
    ]
    assert outbox_events[-1].payload["auto_submitted"] is True
    assert outbox_events[-1].payload["analysis_failed"] is False
    assert storage.calls == 1


async def test_critical_analysis_blocks_automatic_review_submission() -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.ai.models import SensitiveRule
    from app.modules.document.models import File

    uploader_id = await _create_user()
    file_id = await _create_file(uploader_id=uploader_id, submit_after_upload=True)
    async with AsyncSessionFactory() as session:
        session.add(
            SensitiveRule(
                name="严重风险词",
                rule_type="keyword",
                keywords=["绝密凭证"],
                risk_level="critical",
                action="block_sync",
                enabled=True,
            )
        )
        await session.commit()

    await _run_analysis(file_id, FakeAiStorage(content="包含绝密凭证".encode()))

    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        assert file is not None
        assert file.status == "sensitive_review_required"
        events_result = await session.execute(
            select(EventOutbox)
            .where(EventOutbox.aggregate_id == str(file_id))
            .order_by(EventOutbox.id)
        )
        outbox_events = list(events_result.scalars())

    assert "review.file.submitted" not in {event.event_type for event in outbox_events}
    analyzed_event = next(
        event for event in outbox_events if event.event_type == "ai.file.analyzed"
    )
    assert analyzed_event.payload["auto_submitted"] is False
    assert analyzed_event.payload["auto_submit_blocked_reason"] == "critical_sensitive_content"


async def test_high_risk_analysis_auto_submits_with_sensitive_event() -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.document.models import File

    uploader_id = await _create_user()
    file_id = await _create_file(uploader_id=uploader_id, submit_after_upload=True)

    await _run_analysis(
        file_id,
        FakeAiStorage(content=b"api key should be reviewed before publication"),
    )

    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        assert file is not None
        assert file.status == "pending_review"
        assert file.submitted_at is not None
        assert file.review_due_at is not None
        events_result = await session.execute(
            select(EventOutbox)
            .where(EventOutbox.aggregate_id == str(file_id))
            .order_by(EventOutbox.id)
        )
        outbox_events = list(events_result.scalars())

    assert [event.event_type for event in outbox_events] == [
        "ai.text.extracted",
        "ai.file.analyzed",
        "ai.sensitive.detected",
        "review.file.submitted",
    ]
    sensitive_event = outbox_events[2]
    assert sensitive_event.payload["sensitive_risk_level"] == "high"


async def test_analysis_failure_auto_submission_obeys_feature_gate() -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.document.models import File

    uploader_id = await _create_user()
    file_id = await _create_file(uploader_id=uploader_id, submit_after_upload=True)
    await _set_ai_feature(feature_name="allow_sync_when_analysis_failed", enabled=False)

    await _run_analysis(file_id, FakeAiStorage(content=b"", fail=True))

    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        assert file is not None
        assert file.status == "analysis_failed"
        events_result = await session.execute(
            select(EventOutbox).where(EventOutbox.aggregate_id == str(file_id))
        )
        outbox_events = list(events_result.scalars())
    assert "review.file.submitted" not in {event.event_type for event in outbox_events}


async def test_analysis_failure_auto_submits_when_feature_allows() -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.document.models import File

    uploader_id = await _create_user()
    file_id = await _create_file(uploader_id=uploader_id, submit_after_upload=True)
    await _set_ai_feature(feature_name="allow_sync_when_analysis_failed", enabled=True)

    await _run_analysis(file_id, FakeAiStorage(content=b"", fail=True))

    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        assert file is not None
        assert file.status == "pending_review"
        assert file.submitted_at is not None
        assert file.review_due_at is not None
        assert file.review_due_at > file.submitted_at
        events_result = await session.execute(
            select(EventOutbox).where(EventOutbox.aggregate_id == str(file_id))
        )
        outbox_events = list(events_result.scalars())

    submitted_event = next(
        event for event in outbox_events if event.event_type == "review.file.submitted"
    )
    assert submitted_event.payload["analysis_failed"] is True
    assert submitted_event.payload["auto_submitted"] is True


async def test_ai_analysis_stores_extracted_tables_and_markdown() -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ai.models import DocumentAnalysis

    uploader_id = await _create_user()
    file_id = await _create_file(
        uploader_id=uploader_id,
        hash_value="d" * 64,
        original_name="finance.xlsx",
        stored_name="file-finance.xlsx",
        extension="xlsx",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    await _set_ai_feature(feature_name="table_extraction", enabled=True)

    await _run_analysis(
        file_id,
        FakeAiStorage(content=_build_xlsx_bytes("财务", [["合同编号", "金额"], ["KU-001", "100"]])),
    )

    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(DocumentAnalysis).where(DocumentAnalysis.file_id == file_id)
        )
        analysis = result.scalar_one()
        assert analysis.table_count == 1
        assert analysis.tables_json[0]["headers"] == ["合同编号", "金额"]
        assert "| 合同编号 | 金额 |" in (analysis.extracted_text or "")


async def test_ai_analysis_stores_quality_score_when_enabled() -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ai.models import DocumentAnalysis

    uploader_id = await _create_user()
    file_id = await _create_file(uploader_id=uploader_id, hash_value="e" * 64)
    await _set_ai_feature(feature_name="quality_score", enabled=True)

    await _run_analysis(
        file_id,
        FakeAiStorage(
            content=(
                "# 员工手册\n"
                "1. 入职流程\n"
                "员工需要完成账号开通、权限申请和安全培训后才能进入项目环境。\n"
                "2. 审核要求\n"
                "所有知识库文档需要经过管理员审核后拦截重复或低质量内容进入系统。"
            ).encode(),
        ),
    )

    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(DocumentAnalysis).where(DocumentAnalysis.file_id == file_id)
        )
        analysis = result.scalar_one()
        assert analysis.quality_score is not None
        assert 0 <= analysis.quality_score <= 100
        assert analysis.quality_detail["level"] in {"较差", "一般", "良好", "优秀"}


async def test_ai_analysis_stores_simhash_and_similar_file_ids() -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ai.models import DocumentAnalysis
    from app.modules.document.models import File

    uploader_id = await _create_user()
    first_file_id = await _create_file(uploader_id=uploader_id, hash_value="f" * 64)
    second_file_id = await _create_file(uploader_id=uploader_id, hash_value="a" * 64)
    await _set_ai_feature(feature_name="similarity_detection", enabled=True)
    content = b"knowledge uploader handbook review workflow ragflow sync quality gate " * 30

    await _run_analysis(first_file_id, FakeAiStorage(content=content))
    await _run_analysis(second_file_id, FakeAiStorage(content=content + b"minor appendix"))

    async with AsyncSessionFactory() as session:
        first_file = await session.get(File, first_file_id)
        second_file = await session.get(File, second_file_id)
        assert first_file is not None
        assert second_file is not None
        assert first_file.simhash is not None
        assert second_file.simhash is not None
        assert second_file.simhash_band_0 is not None

        result = await session.execute(
            select(DocumentAnalysis).where(DocumentAnalysis.file_id == second_file_id)
        )
        analysis = result.scalar_one()
        assert str(first_file_id) in analysis.similar_file_ids


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


async def test_sensitive_flag_action_records_hit_without_review() -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ai.models import DocumentAnalysis, SensitiveRule
    from app.modules.document.models import File

    uploader_id = await _create_user()
    await _create_category()
    file_id = await _create_file(uploader_id=uploader_id)
    async with AsyncSessionFactory() as session:
        session.add(
            SensitiveRule(
                name="仅标记词",
                rule_type="keyword",
                keywords=["公开标记词"],
                risk_level="high",
                action="flag",
                enabled=True,
            )
        )
        await session.commit()

    await _run_analysis(
        file_id,
        FakeAiStorage(content="handbook 包含公开标记词".encode()),
    )

    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        assert file is not None
        assert file.status == "analyzed"
        result = await session.execute(
            select(DocumentAnalysis).where(DocumentAnalysis.file_id == file_id)
        )
        analysis = result.scalar_one()
        assert analysis.sensitive_risk_level == "high"
        assert analysis.sensitive_hits[0]["action"] == "flag"


async def test_ai_failure_marks_analysis_failed_without_deleting_file() -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.ai import events
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
        event_result = await session.execute(
            select(EventOutbox).where(EventOutbox.event_type == events.AI_FILE_ANALYSIS_FAILED)
        )
        failure_event = event_result.scalar_one()

    assert failure_event.aggregate_id == str(file_id)
    assert failure_event.payload == {
        "file_id": str(file_id),
        "status": "analysis_failed",
        "analysis_id": str(analysis.id),
        "analysis_status": "failed",
        "error_code": "internal",
    }


async def test_analysis_failure_event_is_allowlisted_and_once_per_attempt() -> None:
    from app.core.config import Settings
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.ai import events
    from app.modules.ai.repository import AiRepository  # noqa: TID251 - direct service test
    from app.modules.ai.service import AiAnalysisService  # noqa: TID251 - direct service test

    uploader_id = await _create_user()
    file_id = await _create_file(uploader_id=uploader_id, status_value="analyzing")
    analysis_id = await _create_document_analysis(file_id=file_id, status="running")
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
        assert await service.mark_analysis_failed(
            file_id=file_id,
            error_message="private document text must not enter the event",
            error_code="private document text must not enter the event",
        )
        assert await service.mark_analysis_failed(
            file_id=file_id,
            error_message="duplicate terminal delivery",
            error_code=events.AiAnalysisFailureCode.TIMEOUT,
        )

    async with AsyncSessionFactory() as session:
        event_result = await session.execute(
            select(EventOutbox).where(EventOutbox.event_type == events.AI_FILE_ANALYSIS_FAILED)
        )
        failure_events = list(event_result.scalars())
    assert len(failure_events) == 1
    assert failure_events[0].payload == {
        "file_id": str(file_id),
        "status": "analysis_failed",
        "analysis_id": str(analysis_id),
        "analysis_status": "failed",
        "error_code": "internal",
    }
    assert "private document text" not in str(failure_events[0].payload)


async def test_analysis_failure_event_rolls_back_with_domain_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import Settings
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.ai import events
    from app.modules.ai.models import DocumentAnalysis
    from app.modules.ai.repository import AiRepository  # noqa: TID251 - direct service test
    from app.modules.ai.service import AiAnalysisService  # noqa: TID251 - direct service test
    from app.modules.document.models import File

    uploader_id = await _create_user()
    file_id = await _create_file(uploader_id=uploader_id, status_value="analyzing")
    analysis_id = await _create_document_analysis(file_id=file_id, status="running")
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

        async def _fail_commit() -> None:
            await session.flush()
            raise RuntimeError("commit unavailable")

        monkeypatch.setattr(session, "commit", _fail_commit)
        with pytest.raises(RuntimeError, match="commit unavailable"):
            await service.mark_analysis_failed(
                file_id=file_id,
                error_message="analysis timed out",
                error_code=events.AiAnalysisFailureCode.TIMEOUT,
            )
        await session.rollback()

    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        analysis = await session.get(DocumentAnalysis, analysis_id)
        event_result = await session.execute(
            select(EventOutbox).where(EventOutbox.event_type == events.AI_FILE_ANALYSIS_FAILED)
        )
        assert event_result.scalar_one_or_none() is None
    assert file is not None
    assert file.status == "analyzing"
    assert analysis is not None
    assert analysis.status == "running"


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
async def test_running_analysis_repeated_delivery_requests_retry(status_value: str) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ai.models import DocumentAnalysis
    from app.modules.document.models import File

    uploader_id = await _create_user()
    file_id = await _create_file(uploader_id=uploader_id, status_value=status_value)
    analysis_id = await _create_document_analysis(file_id=file_id, status="running")
    storage = FakeAiStorage(content=b"running content should not be fetched")

    from app.modules.ai.exceptions import AiAnalysisTransientError

    with pytest.raises(AiAnalysisTransientError, match="already running"):
        await _run_analysis_for_id(file_id, storage)

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


async def test_stale_running_analysis_lease_is_recovered() -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ai.models import DocumentAnalysis
    from app.modules.ai.service import AiAnalysisService  # noqa: TID251 - same-module test
    from app.modules.document.models import File

    uploader_id = await _create_user()
    file_id = await _create_file(uploader_id=uploader_id, status_value="analyzing")
    analysis_id = await _create_document_analysis(
        file_id=file_id,
        status="running",
        started_at=datetime.now(UTC)
        - timedelta(seconds=AiAnalysisService.ANALYSIS_LEASE_SECONDS + 1),
    )
    storage = FakeAiStorage(content=b"recovered stale analysis")

    returned_id = await _run_analysis_for_id(file_id, storage)

    assert returned_id == analysis_id
    assert storage.calls == 1
    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        analysis = await session.get(DocumentAnalysis, analysis_id)
    assert file is not None
    assert file.status == "analyzed"
    assert analysis is not None
    assert analysis.status == "succeeded"
    assert analysis.summary == "recovered stale analysis"


async def test_transient_storage_retry_wait_is_owned_and_resumable() -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ai.exceptions import (
        AiAnalysisAlreadyRunningError,
        AiAnalysisTransientError,
    )
    from app.modules.ai.models import DocumentAnalysis
    from app.modules.document.models import File

    uploader_id = await _create_user()
    file_id = await _create_file(uploader_id=uploader_id)
    storage = TransientOnceAiStorage(content=b"resumed after transient storage outage")
    owner_token = "celery-delivery-owner"

    with pytest.raises(AiAnalysisTransientError, match="object storage unavailable"):
        await _run_analysis_for_id(
            file_id,
            storage,
            delivery_token=owner_token,
        )

    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        result = await session.execute(
            select(DocumentAnalysis).where(DocumentAnalysis.file_id == file_id)
        )
        analysis = result.scalar_one()
    assert file is not None
    assert file.status == "extracting_text"
    assert analysis.status == "running"
    assert analysis.started_at is None
    assert analysis.lease_token == owner_token

    with pytest.raises(AiAnalysisAlreadyRunningError, match="already running"):
        await _run_analysis_for_id(
            file_id,
            storage,
            delivery_token="competing-delivery",
        )

    returned_id = await _run_analysis_for_id(
        file_id,
        storage,
        delivery_token=owner_token,
    )

    assert returned_id == analysis.id
    assert storage.calls == 2
    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        completed = await session.get(DocumentAnalysis, analysis.id)
    assert file is not None
    assert file.status == "analyzed"
    assert completed is not None
    assert completed.status == "succeeded"
    assert completed.lease_token is None


async def test_old_analysis_failure_cannot_overwrite_newer_lease() -> None:
    from app.core.config import Settings
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.ai import events
    from app.modules.ai.exceptions import AiAnalysisTransientError
    from app.modules.ai.models import DocumentAnalysis
    from app.modules.ai.repository import AiRepository  # noqa: TID251 - direct service test
    from app.modules.ai.service import AiAnalysisService  # noqa: TID251 - direct service test
    from app.modules.document.models import File

    uploader_id = await _create_user()
    file_id = await _create_file(uploader_id=uploader_id)
    storage = TransientOnceAiStorage(content=b"unused")
    old_token = "old-delivery"
    with pytest.raises(AiAnalysisTransientError):
        await _run_analysis_for_id(file_id, storage, delivery_token=old_token)

    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(DocumentAnalysis).where(DocumentAnalysis.file_id == file_id)
        )
        analysis = result.scalar_one()
        analysis.lease_token = "new-delivery"
        analysis.started_at = datetime.now(UTC)
        await session.commit()

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
        changed = await service.mark_analysis_failed(
            file_id=file_id,
            error_message="late old-worker failure",
            expected_delivery_token=old_token,
            require_retry_wait=True,
        )
    assert changed is False

    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        result = await session.execute(
            select(DocumentAnalysis).where(DocumentAnalysis.file_id == file_id)
        )
        current = result.scalar_one()
        event_result = await session.execute(
            select(EventOutbox).where(EventOutbox.event_type == events.AI_FILE_ANALYSIS_FAILED)
        )
        failure_event = event_result.scalar_one_or_none()
    assert file is not None
    assert file.status == "extracting_text"
    assert current.status == "running"
    assert current.lease_token == "new-delivery"
    assert current.error_message is None
    assert failure_event is None


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
    from app.modules.ai.models import DocumentAnalysis
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

    storage = FakeAiStorage(content=b"handbook")
    async with AsyncSessionFactory() as session:
        service = AiAnalysisService(
            session=session,
            repository=AiRepository(session),
            settings=settings,
        )
        with pytest.raises(AiAnalysisPreconditionError):
            await service.run_file_analysis(
                file_id,
                storage=storage,
            )

    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        assert file is not None
        assert file.status == "uploaded"
        result = await session.execute(
            select(DocumentAnalysis).where(DocumentAnalysis.file_id == file_id)
        )
        assert result.scalar_one_or_none() is None
    assert storage.calls == 0


async def test_queued_snapshot_survives_database_hot_disable() -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.ai.models import DocumentAnalysis
    from app.modules.document.models import File

    uploader_id = await _create_user()
    file_id = await _create_file(uploader_id=uploader_id, ai_enabled=True)
    await _set_ai_feature(feature_name="ai_analysis", enabled=False)
    storage = FakeAiStorage(content=b"queued snapshot remains authoritative")

    await _run_analysis(file_id, storage)

    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        analysis = (
            await session.execute(
                select(DocumentAnalysis).where(DocumentAnalysis.file_id == file_id)
            )
        ).scalar_one()
    assert file is not None
    assert file.status == "analyzed"
    assert analysis.status == "succeeded"
    assert storage.calls == 1


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


async def test_ai_env_disabled_auto_submit_skips_all_ai_states() -> None:
    from app.core.config import Settings
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.ai.exceptions import AiAnalysisPreconditionError
    from app.modules.ai.models import DocumentAnalysis
    from app.modules.ai.repository import AiRepository  # noqa: TID251 - direct service test
    from app.modules.ai.service import AiAnalysisService  # noqa: TID251 - direct service test
    from app.modules.document.models import File

    uploader_id = await _create_user()
    file_id = await _create_file(
        uploader_id=uploader_id,
        ai_enabled=True,
        submit_after_upload=True,
    )
    settings = Settings(
        allowed_email_domains="company.com",
        jwt_secret="test-jwt-secret-with-more-than-32-bytes",
        cache_redis_url=os.environ["CACHE_REDIS_URL"],
        require_email_verification=False,
        ai_analysis_enabled=False,
        llm_provider="mock",
    )
    storage = FakeAiStorage(content=b"must never be read")
    for _ in range(2):
        async with AsyncSessionFactory() as session:
            service = AiAnalysisService(
                session=session,
                repository=AiRepository(session),
                settings=settings,
            )
            with pytest.raises(AiAnalysisPreconditionError):
                await service.run_file_analysis(file_id, storage=storage)

    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        analysis = (
            await session.execute(
                select(DocumentAnalysis).where(DocumentAnalysis.file_id == file_id)
            )
        ).scalar_one_or_none()
        submitted_events = list(
            (
                await session.execute(
                    select(EventOutbox).where(
                        EventOutbox.aggregate_id == str(file_id),
                        EventOutbox.event_type == "review.file.submitted",
                    )
                )
            ).scalars()
        )
    assert file is not None
    assert file.status == "pending_review"
    assert file.review_version == 1
    assert file.submitted_at is not None
    assert file.review_due_at is not None
    assert analysis is None
    assert storage.calls == 0
    assert len(submitted_events) == 1
    assert submitted_events[0].payload["analysis_skipped_reason"] == "environment_disabled"


@pytest.mark.parametrize(
    "status_value",
    ["extracting_text", "analysis_queued", "analyzing"],
)
@pytest.mark.parametrize("submit_after_upload", [False, True])
async def test_ai_env_hard_off_recovers_intermediate_states_idempotently(
    status_value: str,
    submit_after_upload: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import Settings
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.ai import tasks
    from app.modules.ai.models import DocumentAnalysis
    from app.modules.document.models import File

    uploader_id = await _create_user()
    file_id = await _create_file(
        uploader_id=uploader_id,
        status_value=status_value,
        ai_enabled=True,
        submit_after_upload=submit_after_upload,
    )
    await _create_document_analysis(file_id=file_id, status="running")
    settings = Settings(
        allowed_email_domains="company.com",
        jwt_secret="test-jwt-secret-with-more-than-32-bytes",
        cache_redis_url=os.environ["CACHE_REDIS_URL"],
        require_email_verification=False,
        ai_analysis_enabled=False,
        llm_provider="mock",
    )
    storage = FakeAiStorage(content=b"must never be read")
    monkeypatch.setattr(tasks, "get_settings", lambda: settings)
    monkeypatch.setattr(tasks, "build_ai_storage", lambda _settings: storage)

    await tasks.run_ai_analyze_file_task_async(str(file_id), delivery_token="hard-off-1")
    await tasks.run_ai_analyze_file_task_async(str(file_id), delivery_token="hard-off-2")

    async with AsyncSessionFactory() as session:
        file = await session.get(File, file_id)
        analysis = (
            await session.execute(
                select(DocumentAnalysis).where(DocumentAnalysis.file_id == file_id)
            )
        ).scalar_one()
        submitted_events = list(
            (
                await session.execute(
                    select(EventOutbox).where(
                        EventOutbox.aggregate_id == str(file_id),
                        EventOutbox.event_type == "review.file.submitted",
                    )
                )
            ).scalars()
        )
    assert file is not None
    assert file.status == ("pending_review" if submit_after_upload else "uploaded")
    assert file.review_version == (1 if submit_after_upload else 0)
    assert (file.submitted_at is not None) is submit_after_upload
    assert (file.review_due_at is not None) is submit_after_upload
    assert analysis.status == "failed"
    assert analysis.lease_token is None
    assert analysis.error_message == "AI analysis disabled by environment"
    assert storage.calls == 0
    assert len(submitted_events) == (1 if submit_after_upload else 0)
    if submitted_events:
        assert (
            submitted_events[0].payload["analysis_skipped_reason"]
            == "environment_disabled_recovery"
        )


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
