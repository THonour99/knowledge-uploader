from __future__ import annotations

import importlib.util
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.exc import OperationalError

from app.core import runtime_config
from app.core.config import Settings
from app.core.security import encrypt_secret

TEST_JWT_SECRET = "test-jwt-secret-with-more-than-32-bytes"

MIGRATION_FILENAME = "e5b8c0d1f2a3_add_system_configs.py"


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
    runtime_config.invalidate()
    yield
    runtime_config.invalidate()


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


async def test_get_config_falls_back_to_env_default_on_db_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = FakeSessionFactory(
        error=OperationalError("select", {}, Exception("connection refused"))
    )
    _use_factory(monkeypatch, factory)
    settings = Settings(jwt_secret=TEST_JWT_SECRET, login_lock_minutes=42)
    _use_settings(monkeypatch, settings)

    assert await runtime_config.get_config("security.login_lock_minutes") == 42


async def test_repeated_reads_within_ttl_do_not_query_db(
    monkeypatch: pytest.MonkeyPatch, clock: FakeClock
) -> None:
    factory = FakeSessionFactory(rows={"upload.max_file_size_mb": (80, False)})
    _use_factory(monkeypatch, factory)

    assert await runtime_config.get_config("upload.max_file_size_mb") == 80
    clock.advance(59.0)
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
    clock.advance(29.0)
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
            "basic.system_name": ("kb", False),
        }
    )
    _use_factory(monkeypatch, factory)

    assert await runtime_config.get_config("upload.max_file_size_mb") == 80
    assert await runtime_config.get_config("basic.system_name") == "kb"
    assert factory.execute_count == 2

    runtime_config.invalidate()

    assert await runtime_config.get_config("upload.max_file_size_mb") == 80
    assert await runtime_config.get_config("basic.system_name") == "kb"
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


async def test_secret_decrypt_failure_falls_back_to_env(
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

    assert await runtime_config.get_config("ragflow.api_key") == "env-fallback-key"


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


async def test_unknown_key_without_fallback_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = FakeSessionFactory(rows={})
    _use_factory(monkeypatch, factory)

    assert await runtime_config.get_config("upload.not_a_real_key") is None


def _load_migration_module() -> Any:
    migration_path = (
        Path(__file__).resolve().parents[2] / "db" / "migrations" / "versions" / MIGRATION_FILENAME
    )
    spec = importlib.util.spec_from_file_location("_system_configs_migration", migration_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fallback_keys_match_migration_seed_keys() -> None:
    migration = _load_migration_module()
    seed_keys = {str(row[0]) for row in migration.SEED_CONFIGS}
    fallback_keys = set(runtime_config.FALLBACKS)

    assert fallback_keys - seed_keys == set()
    assert seed_keys - fallback_keys == set()
    assert len(fallback_keys) == 35
