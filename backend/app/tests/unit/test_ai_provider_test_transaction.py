from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.llm.openai_compatible import LLMTestResult
from app.core.config import Settings
from app.modules.ai.models import AiFeatureConfig, AiProvider
from app.modules.ai.repository import AiRepository  # noqa: TID251
from app.modules.ai.service import (  # noqa: TID251
    AiConfigService,
    ProviderTestSnapshot,
    RequestContext,
)
from app.modules.user.schemas import AuthUserRecord

pytestmark = pytest.mark.asyncio


class FakeSession:
    def __init__(self) -> None:
        self.transaction_open = True
        self.commit_count = 0

    def in_transaction(self) -> bool:
        return self.transaction_open

    async def commit(self) -> None:
        self.transaction_open = False
        self.commit_count += 1

    async def rollback(self) -> None:
        self.transaction_open = False


class FakeRepository:
    def __init__(self, *, session: FakeSession, current: AiProvider | None) -> None:
        self.session = session
        self.current = current

    async def get_provider_for_update(self, provider_id: UUID) -> AiProvider | None:
        self.session.transaction_open = True
        if self.current is not None:
            assert self.current.id == provider_id
        return self.current

    async def list_providers(self) -> list[AiProvider]:
        return []

    async def list_prompt_templates(self) -> list[Any]:
        return []

    async def list_sensitive_rules(self) -> list[Any]:
        return []


def _provider(*, updated_at: datetime) -> AiProvider:
    return AiProvider(
        id=uuid4(),
        name="provider",
        provider_type="openai_compatible",
        base_url="https://llm.example/v1",
        chat_model="analysis-model",
        is_internal=False,
        enabled=True,
        priority=1,
        timeout_seconds=60,
        updated_at=updated_at,
    )


def _admin() -> AuthUserRecord:
    department_id = uuid4()
    return AuthUserRecord(
        id=uuid4(),
        name="admin",
        email="admin@company.com",
        email_domain="company.com",
        password_hash="hash",
        role="system_admin",
        status="active",
        email_verified=True,
        department_id=department_id,
        department_name="平台",
        department_code="platform",
        department="平台",
        phone=None,
        failed_login_count=0,
        locked_until=None,
        session_version=0,
    )


def _feature() -> AiFeatureConfig:
    return AiFeatureConfig(
        id=uuid4(),
        feature_name="allow_external_llm",
        enabled=True,
        config_json={},
    )


def _settings() -> Settings:
    return Settings(
        allow_external_llm=True,
        llm_allowed_base_urls="https://llm.example/v1",
    )


def _service_harness(
    monkeypatch: pytest.MonkeyPatch,
    *,
    initial: AiProvider,
    current: AiProvider,
) -> tuple[
    AiConfigService,
    FakeSession,
    FakeRepository,
    list[dict[str, object] | None],
]:
    session = FakeSession()
    repository = FakeRepository(session=session, current=current)
    service = AiConfigService(
        session=cast(AsyncSession, session),
        repository=cast(AiRepository, repository),
        settings=_settings(),
    )
    audits: list[dict[str, object] | None] = []

    async def get_provider(provider_id: UUID) -> AiProvider:
        assert provider_id == initial.id
        return initial

    async def feature_map() -> dict[str, AiFeatureConfig]:
        return {"allow_external_llm": _feature()}

    async def record_audit(**kwargs: Any) -> None:
        audits.append(cast(dict[str, object] | None, kwargs.get("metadata_json")))

    monkeypatch.setattr(service, "_get_provider_or_raise", get_provider)
    monkeypatch.setattr(service, "_feature_map", feature_map)
    monkeypatch.setattr(service, "_record_admin_audit", record_audit)
    return service, session, repository, audits


async def test_provider_connectivity_uses_unified_deployed_environment_spki_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_pins = '{"https://llm.example/v1":["sha256/AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE="]}'
    settings = Settings(
        llm_allowed_base_urls="https://llm.example/v1",
        llm_tls_spki_pins=raw_pins,
    ).model_copy(update={"app_base_url": "https://knowledge.example.com"})
    session = FakeSession()
    session.transaction_open = False
    service = AiConfigService(
        session=cast(AsyncSession, session),
        repository=cast(AiRepository, object()),
        settings=settings,
    )
    captured: dict[str, object] = {}

    class RecordingProvider:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        async def test_connection(self) -> LLMTestResult:
            return LLMTestResult(status="success", latency_ms=1, message="ok")

    monkeypatch.setattr(
        "app.modules.ai.service.OpenAICompatibleProvider",
        RecordingProvider,
    )
    result = await service._test_provider_connectivity(
        ProviderTestSnapshot(
            provider_id=uuid4(),
            updated_at=datetime.now(UTC),
            fingerprint="fingerprint",
            provider_type="openai_compatible",
            base_url="https://llm.example/v1",
            api_key=None,
            chat_model="analysis-model",
            is_internal=True,
            timeout_seconds=60,
            effective_allow_external=False,
        )
    )

    assert result.status == "success"
    assert captured["require_tls_spki_pin"] is True
    assert captured["raw_tls_spki_pins"] == raw_pins


async def test_provider_test_discards_result_when_snapshot_becomes_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started_at = datetime.now(UTC)
    initial = _provider(updated_at=started_at)
    current = _provider(updated_at=started_at)
    current.id = initial.id
    service, session, _repository, audits = _service_harness(
        monkeypatch,
        initial=initial,
        current=current,
    )

    async def test_connectivity(snapshot: ProviderTestSnapshot) -> LLMTestResult:
        assert session.in_transaction() is False
        assert snapshot.chat_model == "analysis-model"
        current.chat_model = "new-model"
        current.updated_at = started_at + timedelta(seconds=1)
        return LLMTestResult(status="success", latency_ms=12, message="ok")

    monkeypatch.setattr(service, "_test_provider_connectivity", test_connectivity)
    result = await service.test_provider(
        current_user=_admin(),
        provider_id=initial.id,
        context=RequestContext(ip_address="127.0.0.1", user_agent="test"),
    )

    assert result.status == "failed"
    assert result.message == "provider configuration changed during test"
    assert current.last_test_status is None
    assert session.commit_count == 2
    assert session.in_transaction() is False
    assert len(audits) == 1
    audit = audits[0]
    assert audit is not None
    assert audit == {
        "status": "discarded",
        "observed_status": "success",
        "latency_ms": 12,
        "stale_config": True,
        "config_fingerprint": audit["config_fingerprint"],
    }


async def test_provider_test_network_failure_is_audited_after_transaction_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started_at = datetime.now(UTC)
    initial = _provider(updated_at=started_at)
    current = _provider(updated_at=started_at)
    current.id = initial.id
    service, session, _repository, audits = _service_harness(
        monkeypatch,
        initial=initial,
        current=current,
    )

    async def test_connectivity(_snapshot: ProviderTestSnapshot) -> LLMTestResult:
        assert session.in_transaction() is False
        raise RuntimeError("Authorization: Bearer sk-network-secret")

    monkeypatch.setattr(service, "_test_provider_connectivity", test_connectivity)
    result = await service.test_provider(
        current_user=_admin(),
        provider_id=initial.id,
        context=RequestContext(ip_address="127.0.0.1", user_agent="test"),
    )

    assert result.status == "failed"
    assert result.message == "connection_error"
    assert current.last_test_status == "failed"
    assert current.last_test_latency_ms is None
    assert session.commit_count == 2
    assert session.in_transaction() is False
    assert audits[0] is not None
    assert audits[0]["status"] == "failed"
    assert "sk-network-secret" not in repr(audits)


async def test_ai_global_config_reports_environment_db_and_effective_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession()
    repository = FakeRepository(session=session, current=None)
    service = AiConfigService(
        session=cast(AsyncSession, session),
        repository=cast(AiRepository, repository),
        settings=Settings(ai_analysis_enabled=False, allow_external_llm=False),
    )
    features = {
        "ai_analysis": AiFeatureConfig(
            id=uuid4(),
            feature_name="ai_analysis",
            enabled=True,
            config_json={},
        ),
        "allow_external_llm": AiFeatureConfig(
            id=uuid4(),
            feature_name="allow_external_llm",
            enabled=True,
            config_json={},
        ),
        "allow_sync_when_analysis_failed": AiFeatureConfig(
            id=uuid4(),
            feature_name="allow_sync_when_analysis_failed",
            enabled=False,
            config_json={},
        ),
    }

    async def ensure_defaults() -> None:
        return None

    async def feature_map() -> dict[str, AiFeatureConfig]:
        return features

    async def record_audit(**_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(service, "_ensure_defaults", ensure_defaults)
    monkeypatch.setattr(service, "_feature_map", feature_map)
    monkeypatch.setattr(service, "_record_admin_audit", record_audit)

    response = await service.get_config(
        current_user=_admin(),
        context=RequestContext(ip_address="127.0.0.1", user_agent="test"),
    )

    assert response.global_config.ai_analysis_environment_enabled is False
    assert response.global_config.ai_analysis_db_enabled is True
    assert response.global_config.ai_analysis_enabled is False
    assert response.global_config.allow_external_llm_environment_enabled is False
    assert response.global_config.allow_external_llm_db_enabled is True
    assert response.global_config.allow_external_llm is False
