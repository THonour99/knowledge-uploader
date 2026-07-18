"""Focused runtime assertions that complete the AI-001 local evidence contract.

The broader governance acceptance runner also executes the existing protocol,
version-switch, expiry and notification suites.  This file only adds assertions
that were previously implicit in production code: prompt-template version
persistence, unknown-pricing treatment and the absence of prompt/original/key
material from usage and event evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import import_module
from typing import cast
from uuid import UUID

import pytest
from redis.asyncio import Redis, from_url
from sqlalchemy import select

from app.adapters.llm.base import LLMCompletion, LLMUsage
from app.tests.safety import require_safe_test_database_reset, require_safe_test_redis_reset

pytestmark = pytest.mark.asyncio

_redis_from_url = cast(Callable[..., Redis], from_url)


@dataclass
class _MemoryStorage:
    content: bytes

    async def get_object(self, *, bucket: str, object_key: str) -> bytes:
        del bucket, object_key
        return self.content


@dataclass
class _ProtocolResult:
    content: str
    input_char_count: int | None = None
    input_sha256: str | None = None

    async def complete(
        self,
        prompt: str,
        *,
        model: str | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        max_output_tokens: int | None = None,
        system_prompt: str | None = None,
        json_mode: bool = False,
    ) -> LLMCompletion:
        assert prompt
        assert model == "governance-protocol-model"
        assert temperature is not None
        assert max_output_tokens is not None
        assert system_prompt is not None
        assert json_mode is True
        del top_p
        canonical_messages = json.dumps(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        self.input_char_count = len(system_prompt) + len(prompt)
        self.input_sha256 = hashlib.sha256(canonical_messages.encode("utf-8")).hexdigest()
        return LLMCompletion(
            content=self.content,
            model="governance-protocol-model",
            usage=LLMUsage(prompt_tokens=17, completion_tokens=11),
            latency_ms=13,
        )


async def _reset_database() -> None:
    require_safe_test_database_reset()
    require_safe_test_redis_reset()
    import_module("app.db.models")

    from app.core.database import engine
    from app.db.base import Base

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()

    redis_client = _redis_from_url(
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


async def _seed_file() -> UUID:
    from app.core.database import AsyncSessionFactory
    from app.modules.department.models import Department
    from app.modules.document.models import File
    from app.modules.user.models import User

    department = Department(name="治理验收部", code="governance-acceptance", status="active")
    async with AsyncSessionFactory() as session:
        session.add(department)
        await session.flush()
        user = User(
            name="governance-uploader",
            email="governance-uploader@company.com",
            email_domain="company.com",
            password_hash="not-used-by-direct-service-test",
            department_id=department.id,
            department=department.name,
            role="employee",
            status="active",
            email_verified=True,
        )
        session.add(user)
        await session.flush()
        file = File(
            original_name="governance-evidence.txt",
            title="governance-evidence.txt",
            stored_name="governance-evidence.txt",
            extension="txt",
            mime_type="text/plain",
            size=128,
            hash="d" * 64,
            storage_type="minio",
            bucket="governance-acceptance",
            object_key=f"governance/{user.id}/evidence.txt",
            uploader_id=user.id,
            owner_id=user.id,
            department_id=department.id,
            department=department.name,
            visibility="private",
            status="uploaded",
            review_status="pending",
            ai_analysis_enabled_at_upload=True,
            ai_config_snapshot={"submit_after_upload": False},
        )
        session.add(file)
        await session.commit()
        return file.id


async def test_ai_001_persists_prompt_version_and_unknown_pricing_safely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import Settings
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.ai.models import AiProvider, AiUsageLog, DocumentAnalysis
    from app.modules.ai.repository import AiRepository  # noqa: TID251 - acceptance probe
    from app.modules.ai.service import AiAnalysisService  # noqa: TID251 - acceptance probe

    file_id = await _seed_file()
    protocol = _ProtocolResult(
        content=json.dumps(
            {
                "summary": "治理验收结构化摘要",
                "category_id": None,
                "tags": ["治理", "验收"],
                "sensitive_risk_level": "none",
            },
            ensure_ascii=False,
        )
    )

    def build_protocol_substitute(
        _self: AiAnalysisService,
        _provider: AiProvider,
        *,
        allow_external_llm: bool,
    ) -> _ProtocolResult:
        assert allow_external_llm is False
        return protocol

    monkeypatch.setattr(AiAnalysisService, "_build_llm_provider", build_protocol_substitute)
    async with AsyncSessionFactory() as session:
        session.add(
            AiProvider(
                name="governance protocol substitute",
                provider_type="local_openai_compatible",
                base_url="http://governance-protocol.invalid/v1",
                api_key_encrypted="opaque-test-ciphertext",
                chat_model="governance-protocol-model",
                is_internal=True,
                enabled=True,
                priority=1,
                max_output_tokens=512,
                pricing_configured=False,
                input_price_microunits_per_million_tokens=123,
                output_price_microunits_per_million_tokens=456,
                pricing_currency="USD",
            )
        )
        await session.commit()

    settings = Settings(
        allowed_email_domains="company.com",
        jwt_secret="governance-acceptance-secret-over-32-bytes",
        cache_redis_url=os.environ["CACHE_REDIS_URL"],
        require_email_verification=False,
        ai_analysis_enabled=True,
        allow_external_llm=False,
        llm_provider="disabled",
    )
    original_text = "governance acceptance original content"
    async with AsyncSessionFactory() as session:
        service = AiAnalysisService(
            session=session,
            repository=AiRepository(session),
            settings=settings,
        )
        analysis_id = await service.run_file_analysis(
            file_id,
            storage=_MemoryStorage(original_text.encode("utf-8")),
            delivery_token="governance-ai-001",
        )

    async with AsyncSessionFactory() as session:
        analysis = await session.get(DocumentAnalysis, analysis_id)
        usage = (
            await session.execute(select(AiUsageLog).where(AiUsageLog.analysis_id == analysis_id))
        ).scalar_one()
        events = list(
            (
                await session.execute(
                    select(EventOutbox)
                    .where(EventOutbox.aggregate_id == str(file_id))
                    .order_by(EventOutbox.id)
                )
            ).scalars()
        )

    assert analysis is not None
    assert analysis.status == "succeeded"
    assert analysis.engine_type == "hybrid"
    assert analysis.summary == "治理验收结构化摘要"
    assert analysis.suggested_tags == ["治理", "验收"]
    assert analysis.model_name == "governance-protocol-model"
    assert analysis.prompt_template_key == "document_analysis"
    assert analysis.prompt_version == 1
    assert analysis.prompt_tokens == 17
    assert analysis.completion_tokens == 11
    assert analysis.cost_status == "unknown_pricing"
    assert analysis.estimated_cost_microunits == 0
    assert analysis.input_char_count == protocol.input_char_count
    assert analysis.input_sha256 == protocol.input_sha256

    assert usage.model_name == "governance-protocol-model"
    assert usage.prompt_template_key == "document_analysis"
    assert usage.prompt_version == 1
    assert usage.prompt_tokens == 17
    assert usage.completion_tokens == 11
    assert usage.cost_status == "unknown_pricing"
    assert usage.estimated_cost_microunits == 0
    assert usage.input_char_count == protocol.input_char_count
    assert usage.input_sha256 == protocol.input_sha256
    assert usage.error_message is None
    assert not hasattr(usage, "prompt")
    assert not hasattr(usage, "input_text")
    assert not hasattr(usage, "api_key")

    event_evidence = json.dumps([event.payload for event in events], ensure_ascii=False)
    assert events
    assert original_text not in event_evidence
    assert "governance-evidence.txt" not in event_evidence
    assert "opaque-test-ciphertext" not in event_evidence
    assert "governance-acceptance-secret-over-32-bytes" not in event_evidence
    assert all(event.event_type.startswith("ai.") for event in events)
    assert analysis.finished_at is not None
    assert analysis.finished_at <= datetime.now(UTC)
