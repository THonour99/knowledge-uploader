from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.core import ragflow_runtime
from app.core.config import Settings, is_protected_environment
from app.core.ragflow_endpoint import (
    MAX_RAGFLOW_TLS_SPKI_PINS_LENGTH,
    normalize_ragflow_base_url,
    normalized_ragflow_tls_spki_pins,
)

PIN = "sha256/AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE="
OTHER_PIN = "sha256/AgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgI="
PRODUCTION_FERNET_KEY = "x6TF85ulMkiMF3GSpxCRgYn5v_t7q8D2r5LJw8ZvcVY="


def _production_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "app_env": "production",
        "jwt_secret": "this-is-a-production-secret-with-32-bytes",
        "encryption_key": PRODUCTION_FERNET_KEY,
        "database_url": (
            "postgresql+asyncpg://knowledge:strong-db-secret@postgres:5432/knowledge_uploader"
        ),
        "alembic_database_url": (
            "postgresql+psycopg://knowledge:strong-db-secret@postgres:5432/knowledge_uploader"
        ),
        "celery_broker_url": "amqp://knowledge:strong-rabbit-secret@rabbitmq:5672//",
        "cache_redis_url": "redis://:strong-redis-secret@redis:6379/1",
        "minio_access_key": "knowledge-prod",
        "minio_secret_key": "strong-minio-secret",
        "minio_secure": True,
        "minio_ca_cert_file": "/etc/ssl/certs/ca-certificates.crt",
        "smtp_host": "mail.internal",
        "smtp_from": "noreply@example.com",
        "smtp_tls": True,
    }
    values.update(overrides)
    return Settings(**values)


@pytest.mark.parametrize(
    "base_url",
    [
        "https://ragflow.internal/api\\escape",
        "https://ragflow.internal/api%2fescape",
        "https://ragflow.internal/api%5cescape",
        "https://ragflow.internal/api/%2e%2e/admin",
        "https://999.999.999.999/api",
        "https://ragflow.internal/line\nbreak",
        "https://" + "a" * 501,
    ],
)
def test_ragflow_endpoint_rejects_ambiguous_or_oversized_urls(base_url: str) -> None:
    with pytest.raises(ValueError):
        normalize_ragflow_base_url(base_url)


@pytest.mark.parametrize(
    "mapping",
    [
        '{"https://ragflow.internal/api":["' + PIN + '"],'
        '"https://ragflow.internal/api":["' + OTHER_PIN + '"]}',
        '{"https://RAGFLOW.internal:443/api/":["' + PIN + '"],'
        '"https://ragflow.internal/api":["' + OTHER_PIN + '"]}',
        '{"https://one.internal/api":["' + PIN + '"],"https://two.internal/api":["' + PIN + '"]}',
        '{"https://ragflow.internal/api":[]}',
        '{"https://ragflow.internal/api":["' + PIN + '","' + PIN + '"]}',
        '{"https://ragflow.internal/api":{"pin":"' + PIN + '"}}',
        "{" + '"x":' * 80 + "[]}" * 80,
        " " * (MAX_RAGFLOW_TLS_SPKI_PINS_LENGTH + 1) + "{}",
    ],
)
def test_ragflow_pin_mapping_rejects_duplicate_cross_host_or_unbounded_input(
    mapping: str,
) -> None:
    with pytest.raises(ValueError, match="RAGFLOW_TLS_SPKI_PINS"):
        normalized_ragflow_tls_spki_pins(mapping)


def test_ragflow_pin_mapping_allows_same_certificate_for_same_host_paths() -> None:
    mapping = (
        '{"https://ragflow.internal/api":["'
        + PIN
        + '"],"https://ragflow.internal/other":["'
        + PIN
        + '"]}'
    )

    parsed = normalized_ragflow_tls_spki_pins(mapping)

    assert len(parsed) == 2
    assert {identity[1] for identity in parsed} == {"ragflow.internal"}


def test_settings_rejects_pin_endpoint_outside_approved_urls() -> None:
    with pytest.raises(ValidationError, match="approved RAGFlow URLs"):
        Settings(
            ragflow_base_url="https://ragflow.internal/api",
            ragflow_tls_spki_pins='{"https://other.internal/api":["' + PIN + '"]}',
        )


@pytest.mark.parametrize(
    "app_base_url",
    [
        "https://knowledge.example.com",
        "knowledge.example.com",
        "//knowledge.example.com",
        "https://user@localhost",
        "http://localhost:invalid",
    ],
)
def test_external_or_malformed_app_url_fails_closed_as_protected(
    app_base_url: str,
) -> None:
    assert is_protected_environment("development", app_base_url) is True


@pytest.mark.parametrize(
    "app_base_url",
    ["http://localhost", "https://localhost.", "http://127.0.0.1", "http://[::1]"],
)
def test_explicit_local_app_url_preserves_development_mode(app_base_url: str) -> None:
    assert is_protected_environment("development", app_base_url) is False


async def test_runtime_database_key_keeps_only_exact_pinned_https_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = "https://ragflow.internal/api"
    settings = _production_settings(
        ragflow_base_url=endpoint,
        ragflow_tls_spki_pins='{"' + endpoint + '":["' + PIN + '"]}',
        ragflow_allowed_dataset_ids="dataset-1",
    )

    async def get_config(key: str) -> object:
        return {
            "ragflow.base_url": endpoint,
            "ragflow.api_key": "runtime-secret",
            "ragflow.sync_timeout_seconds": 45,
        }[key]

    monkeypatch.setattr(ragflow_runtime, "get_config", get_config)

    resolved = await ragflow_runtime.resolve_ragflow_runtime_settings(settings)

    assert resolved.integration_enabled is True
    assert resolved.protected_environment is True
    assert resolved.base_url == endpoint
    assert resolved.tls_spki_pins == frozenset({bytes([1]) * 32})


@pytest.mark.parametrize(
    ("endpoint", "pin_mapping"),
    [
        ("http://ragflow.internal:9380", ""),
        ("https://ragflow.internal/api", ""),
    ],
)
async def test_runtime_database_key_fails_closed_without_protected_https_and_pin(
    endpoint: str,
    pin_mapping: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _production_settings(
        ragflow_base_url=endpoint,
        ragflow_tls_spki_pins=pin_mapping,
        ragflow_allowed_dataset_ids="dataset-1",
    )

    async def get_config(key: str) -> object:
        return {
            "ragflow.base_url": endpoint,
            "ragflow.api_key": "runtime-secret",
            "ragflow.sync_timeout_seconds": 45,
        }[key]

    monkeypatch.setattr(ragflow_runtime, "get_config", get_config)

    resolved = await ragflow_runtime.resolve_ragflow_runtime_settings(settings)

    assert resolved.integration_enabled is False
    assert resolved.api_key == ""
    assert resolved.base_url == ""
    assert resolved.tls_spki_pins == frozenset()
