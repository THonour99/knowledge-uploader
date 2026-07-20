from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.exc import OperationalError

from app.core import runtime_config
from app.core.config import Settings
from app.core.security import encrypt_secret

TEST_JWT_SECRET = "test-jwt-secret-with-more-than-32-bytes"


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeResult:
    def __init__(self, row: tuple[object, bool] | None) -> None:
        self._row = row

    def first(self) -> tuple[object, bool] | None:
        return self._row


class FakeSession:
    def __init__(self, factory: FakeSessionFactory) -> None:
        self._factory = factory

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def execute(self, statement: Any) -> FakeResult:
        if self._factory.error is not None:
            raise self._factory.error
        self._factory.execute_count += 1
        params = statement.compile().params
        key = str(next(iter(params.values())))
        self._factory.queried_keys.append(key)
        return FakeResult(self._factory.rows.get(key))


class FakeSessionFactory:
    def __init__(
        self,
        rows: dict[str, tuple[object, bool]] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.rows: dict[str, tuple[object, bool]] = rows or {}
        self.error = error
        self.execute_count = 0
        self.queried_keys: list[str] = []

    def __call__(self) -> FakeSession:
        return FakeSession(self)


@pytest.fixture(autouse=True)
def clear_runtime_cache() -> Generator[None, None, None]:
    runtime_config.invalidate(forget_last_known_good=True)
    yield
    runtime_config.invalidate(forget_last_known_good=True)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    fake_clock = FakeClock()
    monkeypatch.setattr(runtime_config, "_monotonic", fake_clock)
    return fake_clock


def _use_factory(monkeypatch: pytest.MonkeyPatch, factory: FakeSessionFactory) -> None:
    monkeypatch.setattr(runtime_config, "AsyncSessionFactory", factory)


def _use_settings(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    monkeypatch.setattr(runtime_config, "get_settings", lambda: settings)


async def test_get_config_returns_db_value(monkeypatch: pytest.MonkeyPatch) -> None:
    factory = FakeSessionFactory(rows={"upload.max_file_size_mb": (120, False)})
    _use_factory(monkeypatch, factory)

    value = await runtime_config.get_config("upload.max_file_size_mb")

    assert value == 120
    assert factory.execute_count == 1
    assert factory.queried_keys == ["upload.max_file_size_mb"]


async def test_get_config_falls_back_to_env_default_when_db_has_no_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = FakeSessionFactory(rows={})
    _use_factory(monkeypatch, factory)
    settings = Settings(
        jwt_secret=TEST_JWT_SECRET,
        upload_max_file_size_bytes=10 * 1024 * 1024,
        allowed_email_domains="a.com, b.com",
    )
    _use_settings(monkeypatch, settings)

    assert await runtime_config.get_config("upload.max_file_size_mb") == 10
    assert await runtime_config.get_config("security.allowed_email_domains") == ["a.com", "b.com"]
    assert factory.execute_count == 2


async def test_security_config_uses_fail_closed_value_on_db_error_without_lkg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = FakeSessionFactory(
        error=OperationalError("select", {}, Exception("connection refused"))
    )
    _use_factory(monkeypatch, factory)
    settings = Settings(jwt_secret=TEST_JWT_SECRET, login_lock_minutes=42)
    _use_settings(monkeypatch, settings)

    assert await runtime_config.get_config("security.login_lock_minutes") == 1440
    assert await runtime_config.get_config("security.require_email_verification") is True


async def test_control_config_uses_fail_closed_value_not_permissive_env_on_db_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = FakeSessionFactory(
        error=OperationalError("select", {}, Exception("connection refused"))
    )
    _use_factory(monkeypatch, factory)
    settings = Settings(jwt_secret=TEST_JWT_SECRET, require_email_verification=False)
    _use_settings(monkeypatch, settings)

    assert await runtime_config.get_config("upload.enabled") is False
    assert await runtime_config.get_config("ragflow.api_key") == ""
    assert await runtime_config.get_config("ragflow.allow_high_risk_sync") is False
    assert await runtime_config.get_config("review.claim_timeout_minutes") == 5
    assert await runtime_config.get_config("processing.parse_max_chars") == 1


async def test_fail_closed_claim_timeout_survives_consumer_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core import review_policy

    factory = FakeSessionFactory(
        error=OperationalError("select", {}, Exception("connection refused"))
    )
    _use_factory(monkeypatch, factory)

    assert await review_policy.resolve_claim_timeout_minutes() == 5


async def test_database_outage_uses_last_known_good_and_retries_quickly(
    monkeypatch: pytest.MonkeyPatch,
    clock: FakeClock,
) -> None:
    factory = FakeSessionFactory(rows={"upload.enabled": (False, False)})
    _use_factory(monkeypatch, factory)

    assert await runtime_config.get_config("upload.enabled") is False
    clock.advance(16)
    factory.error = OperationalError("select", {}, Exception("connection refused"))
    factory.rows["upload.enabled"] = (True, False)
    assert await runtime_config.get_config("upload.enabled") is False

    clock.advance(1.1)
    factory.error = None
    assert await runtime_config.get_config("upload.enabled") is True


async def test_committed_local_update_replaces_last_known_good_before_outage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = FakeSessionFactory(
        error=OperationalError("select", {}, Exception("connection refused"))
    )
    _use_factory(monkeypatch, factory)
    runtime_config.record_trusted_value("upload.max_file_size_mb", 7)
    runtime_config.invalidate("upload.max_file_size_mb")

    assert await runtime_config.get_config("upload.max_file_size_mb") == 7


async def test_committed_null_secret_resolves_env_fallback_for_cache_and_lkg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fallback_secret = "sk-env-fallback-abcd"
    settings = Settings(
        jwt_secret=TEST_JWT_SECRET,
        ragflow_api_key=fallback_secret,
        ragflow_allowed_dataset_ids="runtime-config-test",
    )
    _use_settings(monkeypatch, settings)
    factory = FakeSessionFactory(
        error=OperationalError("select", {}, Exception("connection refused"))
    )
    _use_factory(monkeypatch, factory)

    runtime_config.record_trusted_value("ragflow.api_key", None)

    assert await runtime_config.get_config("ragflow.api_key") == fallback_secret
    runtime_config.invalidate("ragflow.api_key")
    assert await runtime_config.get_config("ragflow.api_key") == fallback_secret


async def test_repeated_reads_within_ttl_do_not_query_db(
    monkeypatch: pytest.MonkeyPatch, clock: FakeClock
) -> None:
    factory = FakeSessionFactory(rows={"upload.max_file_size_mb": (80, False)})
    _use_factory(monkeypatch, factory)

    assert await runtime_config.get_config("upload.max_file_size_mb") == 80
    clock.advance(14.0)
    assert await runtime_config.get_config("upload.max_file_size_mb") == 80
    assert factory.execute_count == 1

    clock.advance(2.0)
    factory.rows["upload.max_file_size_mb"] = (90, False)
    assert await runtime_config.get_config("upload.max_file_size_mb") == 90
    assert factory.execute_count == 2


async def test_security_group_uses_shorter_ttl(
    monkeypatch: pytest.MonkeyPatch, clock: FakeClock
) -> None:
    factory = FakeSessionFactory(rows={"security.login_max_failed_attempts": (7, False)})
    _use_factory(monkeypatch, factory)

    assert await runtime_config.get_config("security.login_max_failed_attempts") == 7
    clock.advance(4.0)
    assert await runtime_config.get_config("security.login_max_failed_attempts") == 7
    assert factory.execute_count == 1

    clock.advance(2.0)
    assert await runtime_config.get_config("security.login_max_failed_attempts") == 7
    assert factory.execute_count == 2


async def test_invalidate_key_forces_reload(monkeypatch: pytest.MonkeyPatch) -> None:
    factory = FakeSessionFactory(rows={"upload.max_file_size_mb": (80, False)})
    _use_factory(monkeypatch, factory)

    assert await runtime_config.get_config("upload.max_file_size_mb") == 80
    factory.rows["upload.max_file_size_mb"] = (200, False)
    assert await runtime_config.get_config("upload.max_file_size_mb") == 80
    assert factory.execute_count == 1

    runtime_config.invalidate("upload.max_file_size_mb")

    assert await runtime_config.get_config("upload.max_file_size_mb") == 200
    assert factory.execute_count == 2


async def test_invalidate_all_clears_every_key(monkeypatch: pytest.MonkeyPatch) -> None:
    factory = FakeSessionFactory(
        rows={
            "upload.max_file_size_mb": (80, False),
            "review.sla_hours": (12, False),
        }
    )
    _use_factory(monkeypatch, factory)

    assert await runtime_config.get_config("upload.max_file_size_mb") == 80
    assert await runtime_config.get_config("review.sla_hours") == 12
    assert factory.execute_count == 2

    runtime_config.invalidate()

    assert await runtime_config.get_config("upload.max_file_size_mb") == 80
    assert await runtime_config.get_config("review.sla_hours") == 12
    assert factory.execute_count == 4


async def test_secret_value_is_decrypted_for_internal_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    encryption_key = Fernet.generate_key().decode("utf-8")
    plaintext = "sk-runtime-secret-abcd"
    encrypted = encrypt_secret(plaintext, encryption_key)
    factory = FakeSessionFactory(rows={"ragflow.api_key": (encrypted, True)})
    _use_factory(monkeypatch, factory)
    settings = Settings(jwt_secret=TEST_JWT_SECRET, encryption_key=encryption_key)
    _use_settings(monkeypatch, settings)

    assert await runtime_config.get_config("ragflow.api_key") == plaintext


async def test_secret_decrypt_failure_is_alertable_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = FakeSessionFactory(rows={"ragflow.api_key": ("not-a-fernet-token", True)})
    _use_factory(monkeypatch, factory)
    settings = Settings(
        jwt_secret=TEST_JWT_SECRET,
        encryption_key=Fernet.generate_key().decode("utf-8"),
        ragflow_api_key="env-fallback-key",
        ragflow_allowed_dataset_ids="ds-1",
    )
    _use_settings(monkeypatch, settings)

    with pytest.raises(
        runtime_config.RuntimeConfigSecretError,
        match=r"cannot decrypt runtime secret: ragflow\.api_key",
    ):
        await runtime_config.get_config("ragflow.api_key")

    assert "not-a-fernet-token" not in repr(runtime_config._cache)


async def test_get_config_group_merges_db_values_and_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = FakeSessionFactory(rows={"upload.max_file_size_mb": (200, False)})
    _use_factory(monkeypatch, factory)
    settings = Settings(jwt_secret=TEST_JWT_SECRET, upload_allowed_extensions="pdf,md")
    _use_settings(monkeypatch, settings)

    group = await runtime_config.get_config_group("upload")

    expected_keys = {key for key in runtime_config.FALLBACKS if key.startswith("upload.")}
    assert set(group) == expected_keys
    assert group["upload.max_file_size_mb"] == 200
    assert group["upload.allowed_extensions"] == ["pdf", "md"]
    assert group["upload.allow_multi_file"] is True
    assert group["upload.enabled"] is True


async def test_unknown_key_without_fallback_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = FakeSessionFactory(rows={})
    _use_factory(monkeypatch, factory)

    assert await runtime_config.get_config("upload.not_a_real_key") is None


def test_fallback_keys_match_active_config_definitions() -> None:
    from app.modules.config.defaults import DEFINITIONS_BY_KEY

    fallback_keys = set(runtime_config.FALLBACKS)
    definition_keys = set(DEFINITIONS_BY_KEY)

    assert fallback_keys == definition_keys
    assert len(fallback_keys) == 27
    assert set(runtime_config.FAIL_CLOSED_DEFAULTS) == fallback_keys
    assert runtime_config.FAIL_CLOSED_DEFAULTS["review.claim_timeout_minutes"] == 5


async def test_critical_sensitive_sync_invariant_cannot_be_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = FakeSessionFactory(rows={"security.block_critical_sensitive_sync": (False, False)})
    _use_factory(monkeypatch, factory)

    assert await runtime_config.get_config("security.block_critical_sensitive_sync") is True


async def test_email_verification_environment_floor_overrides_false_database_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = FakeSessionFactory(rows={"security.require_email_verification": (False, False)})
    _use_factory(monkeypatch, factory)
    _use_settings(
        monkeypatch,
        Settings(jwt_secret=TEST_JWT_SECRET, require_email_verification=True),
    )

    assert await runtime_config.get_config("security.require_email_verification") is True
    assert runtime_config._cache["security.require_email_verification"][0] is True
    assert runtime_config._last_known_good["security.require_email_verification"] is True


async def test_email_verification_floor_tightens_cached_and_last_known_good_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = FakeSessionFactory(rows={"security.require_email_verification": (False, False)})
    _use_factory(monkeypatch, factory)
    settings = {
        "value": Settings(
            jwt_secret=TEST_JWT_SECRET,
            require_email_verification=False,
        )
    }
    monkeypatch.setattr(runtime_config, "get_settings", lambda: settings["value"])

    assert await runtime_config.get_config("security.require_email_verification") is False
    settings["value"] = Settings(
        jwt_secret=TEST_JWT_SECRET,
        require_email_verification=True,
    )
    assert await runtime_config.get_config("security.require_email_verification") is True

    runtime_config.invalidate("security.require_email_verification")
    factory.error = OperationalError("select", {}, Exception("connection refused"))
    assert await runtime_config.get_config("security.require_email_verification") is True
    assert runtime_config._last_known_good["security.require_email_verification"] is True


def test_email_verification_environment_floor_applies_to_committed_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_settings(
        monkeypatch,
        Settings(jwt_secret=TEST_JWT_SECRET, require_email_verification=True),
    )

    runtime_config.record_trusted_value("security.require_email_verification", False)

    assert runtime_config._cache["security.require_email_verification"][0] is True
    assert runtime_config._last_known_good["security.require_email_verification"] is True


async def test_unapproved_database_ragflow_endpoint_clears_existing_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core import ragflow_runtime

    values: dict[str, object] = {
        "ragflow.base_url": "https://attacker.invalid/capture",
        "ragflow.api_key": "sk-existing-secret-must-not-leave",
        "ragflow.sync_timeout_seconds": 30,
        "ragflow.allowed_dataset_ids": ["database-dataset"],
    }

    async def get_config(key: str) -> object:
        return values[key]

    monkeypatch.setattr(ragflow_runtime, "get_config", get_config)
    settings = Settings(
        jwt_secret=TEST_JWT_SECRET,
        ragflow_base_url="http://ragflow:9380",
        ragflow_allowed_dataset_ids="dataset-1",
    )

    resolved = await ragflow_runtime.resolve_ragflow_runtime_settings(settings)

    assert resolved.base_url == ""
    assert resolved.api_key == ""
    assert resolved.integration_enabled is False
    assert resolved.allowed_dataset_ids == frozenset({"database-dataset"})
