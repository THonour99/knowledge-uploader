from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from importlib import import_module
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from redis.asyncio import from_url
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


def _changed_fields(metadata: dict[str, object]) -> set[str]:
    raw_fields = metadata.get("changed_fields")
    assert isinstance(raw_fields, list)
    fields: set[str] = set()
    for field in raw_fields:
        assert isinstance(field, str)
        fields.add(field)
    return fields


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


@pytest.fixture
async def ai_client() -> AsyncGenerator[AsyncClient, None]:
    from app.core.config import Settings
    from app.core.database import AsyncSessionFactory, get_session
    from app.core.deps import get_app_settings
    from app.main import app

    settings = Settings(
        allowed_email_domains="company.com",
        jwt_secret="test-jwt-secret-with-more-than-32-bytes",
        cache_redis_url=os.environ["CACHE_REDIS_URL"],
        require_email_verification=False,
        llm_provider="mock",
        llm_api_key="sk-test-secret",
        llm_model="test-model",
        allow_external_llm=True,
        llm_allowed_base_urls="https://llm.example.test/v1,https://8.8.8.8/v1",
    )

    async def override_session() -> AsyncGenerator[AsyncSession, None]:
        async with AsyncSessionFactory() as session:
            yield session

    app.dependency_overrides[get_app_settings] = lambda: settings
    app.dependency_overrides[get_session] = override_session
    transport = ASGITransport(app=app)  # type: ignore[arg-type]
    async with AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client
    app.dependency_overrides.clear()


async def _create_user(*, email: str, password: str, role: str = "employee") -> UUID:
    from app.core.database import AsyncSessionFactory
    from app.core.security import hash_password
    from app.modules.user.models import User

    normalized_email = email.lower()
    user = User(
        name=email.split("@", 1)[0],
        email=normalized_email,
        email_domain=normalized_email.rsplit("@", 1)[1],
        password_hash=hash_password(password),
        role=role,
        status="active",
        email_verified=True,
    )
    async with AsyncSessionFactory() as session:
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user.id


async def _login(client: AsyncClient, *, email: str, password: str) -> str:
    response = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    return str(response.json()["data"]["access_token"])


async def test_system_admin_reads_ai_config_without_secret_echo(ai_client: AsyncClient) -> None:
    await _create_user(email="root@company.com", password="password123", role="system_admin")
    token = await _login(ai_client, email="root@company.com", password="password123")

    response = await ai_client.get(
        "/api/admin/ai/config",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["global"]["ai_analysis_enabled"] is True
    assert data["global"]["ai_analysis_environment_enabled"] is True
    assert data["global"]["ai_analysis_db_enabled"] is True
    assert data["global"]["allow_external_llm"] is True
    assert data["global"]["allow_external_llm_environment_enabled"] is True
    assert data["global"]["allow_external_llm_db_enabled"] is True
    assert {feature["key"] for feature in data["features"]} >= {
        "summary",
        "auto_category",
        "tag_generation",
        "sensitive_detection",
        "table_extraction",
        "quality_score",
        "similarity_detection",
    }
    assert all(feature["key"] != "ocr" for feature in data["features"])
    assert data["providers"][0]["has_api_key"] is True
    assert data["providers"][0]["api_key_masked"] == "sk-****cret"
    assert data["prompt_templates"][0]["prompt_text"]
    assert isinstance(data["prompt_templates"][0]["variables"], list)
    assert "pattern" in data["sensitive_rules"][0]
    assert "keywords" in data["sensitive_rules"][0]
    assert "sk-test-secret" not in response.text


async def test_employee_cannot_read_ai_config(ai_client: AsyncClient) -> None:
    await _create_user(email="employee@company.com", password="password123", role="employee")
    token = await _login(ai_client, email="employee@company.com", password="password123")

    response = await ai_client.get(
        "/api/admin/ai/config",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403


async def test_dept_admin_cannot_manage_ai_config(ai_client: AsyncClient) -> None:
    await _create_user(email="dept-ai@company.com", password="password123", role="dept_admin")
    token = await _login(ai_client, email="dept-ai@company.com", password="password123")

    read_response = await ai_client.get(
        "/api/admin/ai/config",
        headers={"Authorization": f"Bearer {token}"},
    )
    create_response = await ai_client.post(
        "/api/admin/ai/prompt-templates",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "template_key": "dept_forbidden",
            "name": "部门无权模板",
            "prompt_text": "forbidden {text}",
            "variables": ["text"],
        },
    )

    assert read_response.status_code == 403
    assert create_response.status_code == 403


async def test_prompt_template_crud_masks_audit_content(ai_client: AsyncClient) -> None:
    from app.core.database import AsyncSessionFactory
    from app.core.outbox import EventOutbox
    from app.modules.audit.models import AuditLog

    await _create_user(email="prompt@company.com", password="password123", role="system_admin")
    token = await _login(ai_client, email="prompt@company.com", password="password123")
    secret_prompt = "请严格摘要内部高密提示 {text}"
    updated_prompt = "请输出可审核摘要 {text}"

    create_response = await ai_client.post(
        "/api/admin/ai/prompt-templates",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "template_key": "custom_summary",
            "name": "自定义摘要",
            "description": "用于审核摘要",
            "prompt_text": secret_prompt,
            "variables": ["text", "text"],
            "enabled": True,
        },
    )
    assert create_response.status_code == 201
    created = create_response.json()["data"]
    assert created["template_key"] == "custom_summary"
    assert created["prompt_text"] == secret_prompt
    assert created["variables"] == ["text"]
    assert created["version"] == 1

    update_response = await ai_client.patch(
        f"/api/admin/ai/prompt-templates/{created['id']}",
        headers={"Authorization": f"Bearer {token}"},
        json={"prompt_text": updated_prompt, "enabled": False},
    )
    assert update_response.status_code == 200
    updated = update_response.json()["data"]
    assert updated["prompt_text"] == updated_prompt
    assert updated["version"] == 2
    assert updated["enabled"] is False

    config_response = await ai_client.get(
        "/api/admin/ai/config",
        headers={"Authorization": f"Bearer {token}"},
    )
    default_template_id = next(
        template["id"]
        for template in config_response.json()["data"]["prompt_templates"]
        if template["template_key"] == "summary"
    )
    restore_response = await ai_client.post(
        f"/api/admin/ai/prompt-templates/{default_template_id}/restore-default",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert restore_response.status_code == 200
    assert restore_response.json()["data"]["template_key"] == "summary"
    assert restore_response.json()["data"]["is_default"] is True

    delete_response = await ai_client.delete(
        f"/api/admin/ai/prompt-templates/{created['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert delete_response.status_code == 200

    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(AuditLog).where(AuditLog.action.in_(["ai.prompt.create", "ai.prompt.update"]))
        )
        audit_text = str([log.metadata_json for log in result.scalars()])
        assert secret_prompt not in audit_text
        assert updated_prompt not in audit_text
        assert "prompt_text" in audit_text

        outbox_result = await session.execute(
            select(EventOutbox.event_type).where(EventOutbox.event_type == "ai.config.changed")
        )
        assert outbox_result.scalars().first() == "ai.config.changed"


async def test_sensitive_rule_crud_validation_and_test_masks_audit(
    ai_client: AsyncClient,
) -> None:
    from app.core.database import AsyncSessionFactory
    from app.modules.audit.models import AuditLog

    await _create_user(email="rules@company.com", password="password123", role="system_admin")
    token = await _login(ai_client, email="rules@company.com", password="password123")

    create_response = await ai_client.post(
        "/api/admin/ai/sensitive-rules",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "客户机密编号",
            "rule_type": "keyword",
            "keywords": ["客户机密编号"],
            "risk_level": "critical",
            "action": "block_sync",
            "enabled": True,
        },
    )
    assert create_response.status_code == 201
    created = create_response.json()["data"]
    assert created["keywords"] == ["客户机密编号"]
    assert created["pattern"] is None
    assert created["action"] == "block_sync"

    test_response = await ai_client.post(
        "/api/admin/ai/sensitive-rules/test",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": "这份资料包含客户机密编号, 不应同步。"},
    )
    assert test_response.status_code == 200
    hit = test_response.json()["data"]["hits"][0]
    assert hit["rule_id"] == created["id"]
    assert hit["action"] == "block_sync"

    invalid_regex_response = await ai_client.patch(
        f"/api/admin/ai/sensitive-rules/{created['id']}",
        headers={"Authorization": f"Bearer {token}"},
        json={"rule_type": "regex", "pattern": "["},
    )
    assert invalid_regex_response.status_code == 400

    update_response = await ai_client.patch(
        f"/api/admin/ai/sensitive-rules/{created['id']}",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "rule_type": "regex",
            "pattern": r"客户\d{4}",
            "keywords": [],
            "risk_level": "high",
            "action": "require_review",
        },
    )
    assert update_response.status_code == 200
    updated = update_response.json()["data"]
    assert updated["pattern"] == r"客户\d{4}"
    assert updated["keywords"] == []
    assert updated["action"] == "require_review"

    delete_response = await ai_client.delete(
        f"/api/admin/ai/sensitive-rules/{created['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert delete_response.status_code == 200

    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(AuditLog).where(
                AuditLog.action.in_(["ai.sensitive_rule.create", "ai.sensitive_rule.update"])
            )
        )
        audit_text = str([log.metadata_json for log in result.scalars()])
        assert "客户机密编号" not in audit_text
        assert r"客户\d{4}" not in audit_text
        assert "pattern" in audit_text
        assert "keywords" in audit_text


async def test_provider_key_is_encrypted_and_masked(ai_client: AsyncClient) -> None:
    from sqlalchemy import select

    from app.core.database import AsyncSessionFactory
    from app.core.security import decrypt_api_key
    from app.modules.ai.models import AiProvider
    from app.modules.audit.models import AuditLog

    await _create_user(email="provider@company.com", password="password123", role="system_admin")
    token = await _login(ai_client, email="provider@company.com", password="password123")

    response = await ai_client.post(
        "/api/admin/ai/providers",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "测试供应商",
            "provider_type": "mock",
            "api_key": "sk-live-secret",
            "chat_model": "mock-chat",
            "input_price_microunits_per_million_tokens": 5_000_000,
            "output_price_microunits_per_million_tokens": 10_000_000,
            "pricing_currency": "CNY",
        },
    )

    assert response.status_code == 201
    provider_data = response.json()["data"]
    assert provider_data["has_api_key"] is True
    assert provider_data["api_key_masked"] == "sk-****cret"
    assert "sk-live-secret" not in response.text
    assert provider_data["input_price_microunits_per_million_tokens"] == 5_000_000
    assert provider_data["output_price_microunits_per_million_tokens"] == 10_000_000
    assert provider_data["pricing_currency"] == "CNY"
    assert provider_data["pricing_configured"] is True
    async with AsyncSessionFactory() as session:
        stored_provider = await session.get(AiProvider, UUID(provider_data["id"]))
        assert stored_provider is not None
        assert stored_provider.api_key_encrypted != "sk-live-secret"
        assert stored_provider.pricing_confirmed_input_microunits_per_million == 5_000_000
        assert stored_provider.pricing_confirmed_output_microunits_per_million == 10_000_000
        assert stored_provider.pricing_confirmed_currency == "CNY"
        assert (
            decrypt_api_key(
                stored_provider.api_key_encrypted or "",
                "RZ1Sw_27VrN9c5Cfsq01qiwViwT6y7jDCuXYn7tgGJY=",
            )
            == "sk-live-secret"
        )

    rotated_secret = "sk-rotated-secret"
    safe_base_url = "https://llm.example.test/v1"
    update_response = await ai_client.patch(
        f"/api/admin/ai/providers/{provider_data['id']}",
        headers={"Authorization": f"Bearer {token}"},
        json={"base_url": safe_base_url, "api_key": rotated_secret},
    )
    assert update_response.status_code == 200
    clear_response = await ai_client.patch(
        f"/api/admin/ai/providers/{provider_data['id']}",
        headers={"Authorization": f"Bearer {token}"},
        json={"clear_api_key": True},
    )
    assert clear_response.status_code == 200

    async with AsyncSessionFactory() as session:
        provider = await session.get(AiProvider, UUID(provider_data["id"]))
        assert provider is not None
        assert provider.api_key_encrypted is None
        result = await session.execute(
            select(AuditLog).where(AuditLog.action == "ai.provider.create")
        )
        audit_log = result.scalar_one()
        audit_metadata = str(audit_log.metadata_json)
        assert "sk-live-secret" not in audit_metadata
        assert "api_key_encrypted" not in audit_metadata

        assert "'pricing_currency': 'CNY'" in audit_metadata
        assert "'input_price_microunits_per_million_tokens': 5000000" in audit_metadata
        assert "'output_price_microunits_per_million_tokens': 10000000" in audit_metadata
        create_changed_fields = _changed_fields(audit_log.metadata_json)
        assert "pricing_currency" in create_changed_fields
        assert "pricing_configured" in create_changed_fields
        assert "input_price_microunits_per_million_tokens" in create_changed_fields
        assert "output_price_microunits_per_million_tokens" in create_changed_fields
        assert "api_key_rotated" in create_changed_fields
        update_result = await session.execute(
            select(AuditLog).where(AuditLog.action == "ai.provider.update")
        )
        update_metadata = [log.metadata_json for log in update_result.scalars()]
        changed_field_sets = [_changed_fields(metadata) for metadata in update_metadata]
        assert any({"base_url", "api_key_rotated"} <= fields for fields in changed_field_sets)
        assert any("api_key_cleared" in fields for fields in changed_field_sets)
        update_audit_text = str(update_metadata)
        assert rotated_secret not in update_audit_text
        assert safe_base_url not in update_audit_text


async def test_provider_pricing_confirmation_fails_closed_on_drift_and_can_be_reconfirmed(
    ai_client: AsyncClient,
) -> None:
    from sqlalchemy import select

    from app.core.database import AsyncSessionFactory
    from app.modules.ai.models import AiProvider
    from app.modules.audit.models import AuditLog

    await _create_user(email="pricing@company.com", password="password123", role="system_admin")
    token = await _login(ai_client, email="pricing@company.com", password="password123")
    headers = {"Authorization": f"Bearer {token}"}

    created_response = await ai_client.post(
        "/api/admin/ai/providers",
        headers=headers,
        json={
            "name": "价格确认供应商",
            "provider_type": "mock",
            "chat_model": "mock-chat",
            "input_price_microunits_per_million_tokens": 10,
            "output_price_microunits_per_million_tokens": 20,
            "pricing_currency": "USD",
        },
    )
    assert created_response.status_code == 201
    provider_id = UUID(created_response.json()["data"]["id"])
    assert created_response.json()["data"]["pricing_configured"] is True

    async with AsyncSessionFactory() as session:
        provider = await session.get(AiProvider, provider_id)
        assert provider is not None
        # Simulate a rolling old writer that changes pricing but cannot update the
        # confirmation basis.
        provider.pricing_currency = "CNY"
        await session.commit()

    config_response = await ai_client.get("/api/admin/ai/config", headers=headers)
    assert config_response.status_code == 200
    provider_data = next(
        item
        for item in config_response.json()["data"]["providers"]
        if item["id"] == str(provider_id)
    )
    assert provider_data["pricing_configured"] is False

    unrelated_response = await ai_client.patch(
        f"/api/admin/ai/providers/{provider_id}",
        headers=headers,
        json={"priority": 77},
    )
    assert unrelated_response.status_code == 200
    assert unrelated_response.json()["data"]["pricing_configured"] is False

    all_null_response = await ai_client.patch(
        f"/api/admin/ai/providers/{provider_id}",
        headers=headers,
        json={
            "input_price_microunits_per_million_tokens": None,
            "output_price_microunits_per_million_tokens": None,
            "pricing_currency": None,
        },
    )
    assert all_null_response.status_code == 200
    assert all_null_response.json()["data"]["pricing_configured"] is False

    single_null_response = await ai_client.patch(
        f"/api/admin/ai/providers/{provider_id}",
        headers=headers,
        json={"pricing_currency": None, "priority": 78},
    )
    assert single_null_response.status_code == 200
    assert single_null_response.json()["data"]["pricing_configured"] is False

    async with AsyncSessionFactory() as session:
        update_audits = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.action == "ai.provider.update",
                        AuditLog.target_id == provider_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        null_patch_audit = next(
            log.metadata_json for log in update_audits if log.metadata_json["changed_fields"] == []
        )
        assert null_patch_audit["pricing_configured"] is False
        assert "pricing_configured" not in _changed_fields(null_patch_audit)

    reconfirmed_response = await ai_client.patch(
        f"/api/admin/ai/providers/{provider_id}",
        headers=headers,
        json={"pricing_configured": True, "pricing_currency": None},
    )
    assert reconfirmed_response.status_code == 200
    assert reconfirmed_response.json()["data"]["pricing_configured"] is True

    async with AsyncSessionFactory() as session:
        provider = await session.get(AiProvider, provider_id)
        assert provider is not None
        assert provider.pricing_confirmed_input_microunits_per_million == 10
        assert provider.pricing_confirmed_output_microunits_per_million == 20
        assert provider.pricing_confirmed_currency == "CNY"
        provider.pricing_currency = "EUR"
        await session.commit()

    currency_only_response = await ai_client.patch(
        f"/api/admin/ai/providers/{provider_id}",
        headers=headers,
        json={"pricing_currency": "USD"},
    )
    assert currency_only_response.status_code == 200
    assert currency_only_response.json()["data"]["pricing_configured"] is True

    async with AsyncSessionFactory() as session:
        provider = await session.get(AiProvider, provider_id)
        assert provider is not None
        provider.input_price_microunits_per_million_tokens = 11
        await session.commit()

    price_only_response = await ai_client.patch(
        f"/api/admin/ai/providers/{provider_id}",
        headers=headers,
        json={"output_price_microunits_per_million_tokens": 21},
    )
    assert price_only_response.status_code == 200
    assert price_only_response.json()["data"]["pricing_configured"] is True

    async with AsyncSessionFactory() as session:
        provider = await session.get(AiProvider, provider_id)
        assert provider is not None
        assert provider.pricing_confirmed_input_microunits_per_million == 11
        assert provider.pricing_confirmed_output_microunits_per_million == 21
        assert provider.pricing_confirmed_currency == "USD"

    explicit_free_response = await ai_client.patch(
        f"/api/admin/ai/providers/{provider_id}",
        headers=headers,
        json={
            "input_price_microunits_per_million_tokens": 0,
            "output_price_microunits_per_million_tokens": 0,
            "pricing_configured": True,
        },
    )
    assert explicit_free_response.status_code == 200
    assert explicit_free_response.json()["data"]["pricing_configured"] is True

    async with AsyncSessionFactory() as session:
        provider = (
            await session.execute(select(AiProvider).where(AiProvider.id == provider_id))
        ).scalar_one()
        assert provider.pricing_confirmed_input_microunits_per_million == 0
        assert provider.pricing_confirmed_output_microunits_per_million == 0
        assert provider.pricing_confirmed_currency == "USD"

    cancelled_response = await ai_client.patch(
        f"/api/admin/ai/providers/{provider_id}",
        headers=headers,
        json={
            "input_price_microunits_per_million_tokens": 5,
            "pricing_configured": False,
        },
    )
    assert cancelled_response.status_code == 200
    assert cancelled_response.json()["data"]["pricing_configured"] is False

    async with AsyncSessionFactory() as session:
        provider = await session.get(AiProvider, provider_id)
        assert provider is not None
        assert provider.pricing_configured is False
        assert provider.pricing_confirmed_input_microunits_per_million is None
        assert provider.pricing_confirmed_output_microunits_per_million is None
        assert provider.pricing_confirmed_currency is None
        assert provider.input_price_microunits_per_million_tokens == 5


async def test_short_provider_key_is_never_returned_or_audited(
    ai_client: AsyncClient,
) -> None:
    from sqlalchemy import select

    from app.core.database import AsyncSessionFactory
    from app.modules.audit.models import AuditLog

    await _create_user(email="short-key@company.com", password="password123", role="system_admin")
    token = await _login(ai_client, email="short-key@company.com", password="password123")
    raw_secret = "abcde"

    response = await ai_client.post(
        "/api/admin/ai/providers",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "短密钥供应商",
            "provider_type": "mock",
            "api_key": raw_secret,
            "chat_model": "mock-chat",
        },
    )

    assert response.status_code == 201
    provider_data = response.json()["data"]
    assert provider_data["api_key_masked"] == "****"
    assert raw_secret not in response.text

    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(AuditLog).where(
                AuditLog.action == "ai.provider.create",
                AuditLog.target_id == UUID(provider_data["id"]),
            )
        )
        audit_log = result.scalar_one()
        assert raw_secret not in str(audit_log.metadata_json)
        assert "api_key_rotated" in _changed_fields(audit_log.metadata_json)


async def test_update_feature_writes_audit_log(ai_client: AsyncClient) -> None:
    from app.core.audit import AUDIT_LOGS
    from app.core.database import AsyncSessionFactory

    await _create_user(email="audit@company.com", password="password123", role="system_admin")
    token = await _login(ai_client, email="audit@company.com", password="password123")
    seed = await ai_client.get(
        "/api/admin/ai/config",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert seed.status_code == 200

    response = await ai_client.patch(
        "/api/admin/ai/features/sensitive_detection",
        headers={"Authorization": f"Bearer {token}"},
        json={"enabled": False},
    )

    assert response.status_code == 200
    assert response.json()["data"]["enabled"] is False
    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(AUDIT_LOGS.c.action).where(AUDIT_LOGS.c.action == "ai.feature.update")
        )
        assert result.scalar_one() == "ai.feature.update"


async def test_mock_provider_connection_test(ai_client: AsyncClient) -> None:
    await _create_user(email="test@company.com", password="password123", role="system_admin")
    token = await _login(ai_client, email="test@company.com", password="password123")
    config = await ai_client.get(
        "/api/admin/ai/config",
        headers={"Authorization": f"Bearer {token}"},
    )
    provider_id = config.json()["data"]["providers"][0]["id"]

    response = await ai_client.post(
        f"/api/admin/ai/providers/{provider_id}/test",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "provider_id": provider_id,
        "status": "success",
        "latency_ms": 0,
        "message": "ok",
    }


async def test_provider_connection_blocks_external_url_when_feature_disabled(
    ai_client: AsyncClient,
) -> None:
    await _create_user(email="external@company.com", password="password123", role="system_admin")
    token = await _login(ai_client, email="external@company.com", password="password123")
    seed = await ai_client.get(
        "/api/admin/ai/config",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert seed.status_code == 200
    feature_response = await ai_client.patch(
        "/api/admin/ai/features/allow_external_llm",
        headers={"Authorization": f"Bearer {token}"},
        json={"enabled": False},
    )
    assert feature_response.status_code == 200

    provider_response = await ai_client.post(
        "/api/admin/ai/providers",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "疑似本地域名绕过",
            "provider_type": "openai_compatible",
            "base_url": "https://8.8.8.8/v1",
            "chat_model": "gpt-test",
            "enabled": True,
        },
    )
    assert provider_response.status_code == 201
    provider_id = provider_response.json()["data"]["id"]

    response = await ai_client.post(
        f"/api/admin/ai/providers/{provider_id}/test",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "failed"
    assert response.json()["data"]["message"] == "request_rejected"


async def test_document_analysis_prompt_contract_fails_fast(ai_client: AsyncClient) -> None:
    await _create_user(
        email="prompt-contract@company.com", password="password123", role="system_admin"
    )
    token = await _login(
        ai_client,
        email="prompt-contract@company.com",
        password="password123",
    )
    headers = {"Authorization": f"Bearer {token}"}
    config_response = await ai_client.get("/api/admin/ai/config", headers=headers)
    template = next(
        item
        for item in config_response.json()["data"]["prompt_templates"]
        if item["template_key"] == "document_analysis"
    )

    variables_response = await ai_client.patch(
        f"/api/admin/ai/prompt-templates/{template['id']}",
        headers=headers,
        json={"variables": ["text"]},
    )
    interpolation_response = await ai_client.patch(
        f"/api/admin/ai/prompt-templates/{template['id']}",
        headers=headers,
        json={"prompt_text": "分析 {text}", "variables": []},
    )
    valid_response = await ai_client.patch(
        f"/api/admin/ai/prompt-templates/{template['id']}",
        headers=headers,
        json={"prompt_text": "按严格 JSON 契约分析输入。", "variables": []},
    )

    assert variables_response.status_code == 400
    assert interpolation_response.status_code == 400
    assert valid_response.status_code == 200
    assert valid_response.json()["data"]["variables"] == []


async def test_provider_base_url_rejects_credentials_and_unsafe_components(
    ai_client: AsyncClient,
) -> None:
    await _create_user(email="url-admin@company.com", password="password123", role="system_admin")
    token = await _login(ai_client, email="url-admin@company.com", password="password123")
    headers = {"Authorization": f"Bearer {token}"}
    invalid_urls = (
        "https://user:pass@llm.example.test/v1",
        "https://llm.example.test/v1?api_key=secret-query-token",
        "https://llm.example.test/v1#secret-fragment",
        "ftp://llm.example.test/v1",
        "https:///missing-host",
    )
    for index, base_url in enumerate(invalid_urls):
        response = await ai_client.post(
            "/api/admin/ai/providers",
            headers=headers,
            json={
                "name": f"invalid-url-{index}",
                "provider_type": "openai_compatible",
                "base_url": base_url,
                "chat_model": "test-model",
            },
        )
        assert response.status_code == 400
        assert "user:pass" not in response.text
        assert "secret-query-token" not in response.text

    safe_url = "https://llm.example.test/v1"
    created = await ai_client.post(
        "/api/admin/ai/providers",
        headers=headers,
        json={
            "name": "safe-url",
            "provider_type": "mock",
            "base_url": safe_url,
            "chat_model": "mock-analysis-v1",
        },
    )
    assert created.status_code == 201
    provider_id = created.json()["data"]["id"]
    bad_update = await ai_client.patch(
        f"/api/admin/ai/providers/{provider_id}",
        headers=headers,
        json={"base_url": "https://user:pass@llm.example.test/v1"},
    )
    assert bad_update.status_code == 400
    assert "user:pass" not in bad_update.text


async def test_provider_runtime_limits_reject_dead_configuration(ai_client: AsyncClient) -> None:
    await _create_user(
        email="limits-admin@company.com", password="password123", role="system_admin"
    )
    token = await _login(ai_client, email="limits-admin@company.com", password="password123")
    headers = {"Authorization": f"Bearer {token}"}
    for field_name, value in (
        ("timeout_seconds", 241),
        ("max_retry_count", 11),
        ("max_output_tokens", 4_097),
    ):
        response = await ai_client.post(
            "/api/admin/ai/providers",
            headers=headers,
            json={
                "name": f"invalid-{field_name}",
                "provider_type": "mock",
                field_name: value,
            },
        )
        assert response.status_code == 422

    created = await ai_client.post(
        "/api/admin/ai/providers",
        headers=headers,
        json={
            "name": "valid-runtime-limits",
            "provider_type": "mock",
            "chat_model": "mock-analysis-v1",
            "max_retry_count": 2,
            "max_output_tokens": 4_096,
        },
    )
    assert created.status_code == 201
    provider_id = created.json()["data"]["id"]
    for field_name, value in (
        ("timeout_seconds", 241),
        ("max_retry_count", 11),
        ("max_output_tokens", 4_097),
    ):
        response = await ai_client.patch(
            f"/api/admin/ai/providers/{provider_id}", headers=headers, json={field_name: value}
        )
    config = await ai_client.get("/api/admin/ai/config", headers=headers)
    provider = next(
        item for item in config.json()["data"]["providers"] if item["id"] == provider_id
    )
    assert provider["max_retry_count"] == 2
    assert provider["max_output_tokens"] == 4_096
    assert provider["timeout_seconds"] == 60


async def test_enabled_provider_requires_chat_model(ai_client: AsyncClient) -> None:
    await _create_user(
        email="model-required@company.com",
        password="password123",
        role="system_admin",
    )
    token = await _login(
        ai_client,
        email="model-required@company.com",
        password="password123",
    )
    headers = {"Authorization": f"Bearer {token}"}

    rejected_create = await ai_client.post(
        "/api/admin/ai/providers",
        headers=headers,
        json={"name": "missing-model", "provider_type": "mock", "enabled": True},
    )
    assert rejected_create.status_code == 400

    disabled_create = await ai_client.post(
        "/api/admin/ai/providers",
        headers=headers,
        json={"name": "disabled-without-model", "provider_type": "mock", "enabled": False},
    )
    assert disabled_create.status_code == 201
    rejected_enable = await ai_client.patch(
        f"/api/admin/ai/providers/{disabled_create.json()['data']['id']}",
        headers=headers,
        json={"enabled": True},
    )
    assert rejected_enable.status_code == 400
