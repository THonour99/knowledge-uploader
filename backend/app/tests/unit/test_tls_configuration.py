from __future__ import annotations

import importlib
import ssl
from pathlib import Path
from typing import Any, cast

import certifi
import pytest

from app.adapters.email import EmailConfigurationError, SmtpEmailAdapter, SmtpEmailConfig
from app.adapters.minio_client import (
    MINIO_HTTP_POOL_SIZE,
    MINIO_HTTP_RETRY_BACKOFF_FACTOR,
    MINIO_HTTP_RETRY_COUNT,
    MINIO_HTTP_RETRY_STATUSES,
    MINIO_HTTP_TIMEOUT_SECONDS,
    MinioDocumentStorage,
)
from app.core.config import Settings


def test_protected_smtp_rejects_plaintext_delivery() -> None:
    with pytest.raises(EmailConfigurationError, match="SMTP_TLS"):
        SmtpEmailConfig.from_env(
            {
                "APP_ENV": "staging",
                "SMTP_HOST": "mail.internal",
                "SMTP_FROM": "noreply@example.invalid",
                "SMTP_TLS": "false",
            }
        )


def test_smtp_ca_file_is_loaded_from_environment() -> None:
    config = SmtpEmailConfig.from_env(
        {
            "APP_ENV": "staging",
            "SMTP_HOST": "mail.internal",
            "SMTP_FROM": "noreply@example.invalid",
            "SMTP_TLS": "true",
            "SMTP_CA_CERT_FILE": "/run/secrets/company-ca.pem",
        }
    )

    assert config.ca_cert_file == "/run/secrets/company-ca.pem"
    assert config.use_tls is True


@pytest.mark.parametrize(
    ("environment", "message"),
    (
        ({"SMTP_HOST": "mail.internal"}, "SMTP_HOST"),
        ({"SMTP_FROM": "noreply@example.invalid"}, "SMTP_HOST"),
        ({"SMTP_USER": "mailer"}, "SMTP_USER"),
        ({"SMTP_PASSWORD": "mail-secret"}, "SMTP_USER"),
        ({"SMTP_CA_CERT_FILE": "/run/secrets/mail-ca.pem"}, "SMTP_HOST"),
    ),
)
def test_smtp_adapter_rejects_partial_environment_configuration(
    environment: dict[str, str],
    message: str,
) -> None:
    with pytest.raises(EmailConfigurationError, match=message):
        SmtpEmailConfig.from_env(environment)


@pytest.mark.parametrize("port", ["0", "65536", "not-a-port"])
def test_smtp_adapter_rejects_invalid_port(port: str) -> None:
    with pytest.raises(EmailConfigurationError, match="SMTP_PORT"):
        SmtpEmailConfig.from_env({"SMTP_PORT": port})


@pytest.mark.parametrize("timeout", ["0", "-1", "300.1", "nan", "not-a-timeout"])
def test_smtp_adapter_rejects_invalid_timeout(timeout: str) -> None:
    with pytest.raises(EmailConfigurationError, match="SMTP_TIMEOUT_SECONDS"):
        SmtpEmailConfig.from_env({"SMTP_TIMEOUT_SECONDS": timeout})


def test_smtp_adapter_rejects_invalid_tls_boolean() -> None:
    with pytest.raises(EmailConfigurationError, match="SMTP_TLS"):
        SmtpEmailConfig.from_env({"SMTP_TLS": "sometimes"})


def test_smtp_adapter_accepts_anonymous_relay() -> None:
    config = SmtpEmailConfig.from_env(
        {
            "SMTP_HOST": "mail.internal",
            "SMTP_FROM": "noreply@example.invalid",
        }
    )

    assert config.is_configured is True
    assert config.username == ""
    assert config.password == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("ca_contents", [None, "not a certificate"])
async def test_smtp_rejects_missing_or_invalid_ca_without_disclosing_path(
    tmp_path: Path,
    ca_contents: str | None,
) -> None:
    ca_file = tmp_path / "sensitive-mail-ca.pem"
    if ca_contents is not None:
        ca_file.write_text(ca_contents, encoding="utf-8")
    adapter = SmtpEmailAdapter(
        SmtpEmailConfig(
            host="mail.internal",
            port=587,
            username="",
            password="",
            sender="noreply@example.invalid",
            use_tls=True,
            ca_cert_file=str(ca_file),
        )
    )

    with pytest.raises(
        EmailConfigurationError,
        match="SMTP CA certificate is unavailable or invalid",
    ) as raised:
        await adapter.send("employee@example.invalid", "Subject", "Body")

    assert str(ca_file) not in str(raised.value)
    assert raised.value.__cause__ is None


def test_secure_minio_client_requires_ca_verification(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ca_file = tmp_path / "minio-ca.pem"
    ca_file.write_text("", encoding="utf-8")
    validated_ca_files: list[str | None] = []

    def validate_context(*, cafile: str | None = None) -> ssl.SSLContext:
        validated_ca_files.append(cafile)
        return cast(ssl.SSLContext, object())

    monkeypatch.setattr(ssl, "create_default_context", validate_context)
    storage = MinioDocumentStorage(
        Settings(
            minio_secure=True,
            minio_ca_cert_file=str(ca_file),
        )
    )

    client = cast(Any, storage)._client
    pool = client._http
    options: dict[str, Any] = pool.connection_pool_kw
    assert options["cert_reqs"] == "CERT_REQUIRED"
    assert options["ca_certs"] == str(ca_file)
    assert options["maxsize"] == MINIO_HTTP_POOL_SIZE
    timeout = options["timeout"]
    assert timeout.connect_timeout == MINIO_HTTP_TIMEOUT_SECONDS
    assert timeout.read_timeout == MINIO_HTTP_TIMEOUT_SECONDS
    retry = options["retries"]
    assert retry.total == MINIO_HTTP_RETRY_COUNT
    assert retry.backoff_factor == MINIO_HTTP_RETRY_BACKOFF_FACTOR
    assert tuple(retry.status_forcelist) == MINIO_HTTP_RETRY_STATUSES
    assert validated_ca_files == [str(ca_file)]


def test_secure_minio_client_preserves_sdk_ca_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ca_file = tmp_path / "certifi-ca.pem"
    ca_file.write_text("", encoding="utf-8")
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.setattr(certifi, "where", lambda: str(ca_file))
    monkeypatch.setattr(
        ssl,
        "create_default_context",
        lambda *, cafile=None: cast(ssl.SSLContext, object()),
    )

    storage = MinioDocumentStorage(Settings(minio_secure=True, minio_ca_cert_file=""))

    pool = cast(Any, storage)._client._http
    assert pool.connection_pool_kw["ca_certs"] == str(ca_file)


@pytest.mark.parametrize("ca_contents", [None, "not a certificate"])
def test_secure_minio_client_rejects_missing_or_invalid_ca_without_disclosing_path(
    tmp_path: Path,
    ca_contents: str | None,
) -> None:
    missing = tmp_path / "sensitive-tenant-ca.pem"
    if ca_contents is not None:
        missing.write_text(ca_contents, encoding="utf-8")

    with pytest.raises(
        ValueError,
        match="MinIO CA certificate file is unavailable or invalid",
    ) as raised:
        MinioDocumentStorage(
            Settings(
                minio_secure=True,
                minio_ca_cert_file=str(missing),
            )
        )

    assert str(missing) not in str(raised.value)
    assert raised.value.__cause__ is None


@pytest.mark.asyncio
@pytest.mark.parametrize("ca_contents", [None, "not a certificate"])
async def test_minio_readiness_fails_closed_without_disclosing_ca_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    ca_contents: str | None,
) -> None:
    main_module = importlib.import_module("app.main")
    ca_file = tmp_path / "sensitive-readiness-ca.pem"
    if ca_contents is not None:
        ca_file.write_text(ca_contents, encoding="utf-8")
    settings = Settings(
        minio_endpoint="minio.invalid:9000",
        minio_secure=True,
        minio_ca_cert_file=str(ca_file),
    )
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)

    result = await main_module._run_dependency_check(main_module._check_minio)

    assert result["status"] == "error"
    assert result["detail"] in {"FileNotFoundError", "SSLError"}
    assert str(ca_file) not in repr(result)
