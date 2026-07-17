from __future__ import annotations

import asyncio
import math
import os
import smtplib
import ssl
from collections.abc import Mapping
from dataclasses import dataclass
from email.message import EmailMessage as StdlibEmailMessage

from app.core.config import (
    DEFAULT_SMTP_PORT,
    DEFAULT_SMTP_TIMEOUT_SECONDS,
    MAX_SMTP_TIMEOUT_SECONDS,
)

from .base import EmailAdapter, EmailConfigurationError, EmailDeliveryError

TRUTHY_VALUES = {"1", "true", "yes", "on"}
FALSY_VALUES = {"0", "false", "no", "off"}
PROTECTED_SMTP_ENVS = {"production", "prod", "staging"}


@dataclass(frozen=True)
class SmtpEmailConfig:
    host: str
    port: int
    username: str
    password: str
    sender: str
    use_tls: bool = True
    ca_cert_file: str = ""
    timeout_seconds: float = DEFAULT_SMTP_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        if (
            isinstance(self.port, bool)
            or not isinstance(self.port, int)
            or not 1 <= self.port <= 65535
        ):
            msg = "SMTP_PORT must be between 1 and 65535"
            raise EmailConfigurationError(msg)
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, int | float)
            or not math.isfinite(self.timeout_seconds)
            or not 0 < self.timeout_seconds <= MAX_SMTP_TIMEOUT_SECONDS
        ):
            msg = "SMTP_TIMEOUT_SECONDS must be greater than 0 and at most 300"
            raise EmailConfigurationError(msg)
        requested = bool(
            self.host
            or self.username
            or self.password
            or self.sender
            or self.ca_cert_file
            or self.port != DEFAULT_SMTP_PORT
            or self.timeout_seconds != DEFAULT_SMTP_TIMEOUT_SECONDS
            or not self.use_tls
        )
        if not requested:
            return
        if bool(self.username) != bool(self.password):
            msg = "SMTP_USER and SMTP_PASSWORD must be configured together"
            raise EmailConfigurationError(msg)
        if not self.host or not self.sender:
            msg = "SMTP_HOST and SMTP_FROM or SMTP_USER must be configured together"
            raise EmailConfigurationError(msg)
        if self.ca_cert_file and not self.use_tls:
            msg = "SMTP_CA_CERT_FILE requires SMTP_TLS=true"
            raise EmailConfigurationError(msg)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> SmtpEmailConfig:
        source = os.environ if environ is None else environ
        username = source.get("SMTP_USER", "").strip()
        config = cls(
            host=source.get("SMTP_HOST", "").strip(),
            port=_int_from_env(source.get("SMTP_PORT"), DEFAULT_SMTP_PORT),
            username=username,
            password=source.get("SMTP_PASSWORD", ""),
            sender=source.get("SMTP_FROM", "").strip() or username,
            use_tls=_bool_from_env(source.get("SMTP_TLS"), default=True),
            ca_cert_file=source.get("SMTP_CA_CERT_FILE", "").strip(),
            timeout_seconds=_float_from_env(
                source.get("SMTP_TIMEOUT_SECONDS"),
                DEFAULT_SMTP_TIMEOUT_SECONDS,
            ),
        )
        if (
            config.is_configured
            and source.get("APP_ENV", "").strip().lower() in PROTECTED_SMTP_ENVS
            and not config.use_tls
        ):
            msg = "SMTP_TLS must be enabled in protected environments"
            raise EmailConfigurationError(msg)
        return config

    @property
    def is_configured(self) -> bool:
        return bool(self.host and self.sender)


class SmtpEmailAdapter(EmailAdapter):
    def __init__(self, config: SmtpEmailConfig) -> None:
        self._config = config

    async def send(self, recipient: str, subject: str, body: str) -> None:
        if not self._config.is_configured:
            msg = "SMTP_HOST and SMTP_FROM or SMTP_USER must be configured"
            raise EmailConfigurationError(msg)
        await asyncio.to_thread(self._send_sync, recipient, subject, body)

    def _send_sync(self, recipient: str, subject: str, body: str) -> None:
        message = StdlibEmailMessage()
        message["From"] = self._config.sender
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(body)

        context: ssl.SSLContext | None = None
        if self._config.use_tls:
            try:
                context = ssl.create_default_context(cafile=self._config.ca_cert_file or None)
            except (OSError, ssl.SSLError):
                msg = "SMTP CA certificate is unavailable or invalid"
                raise EmailConfigurationError(msg) from None
        try:
            if self._config.use_tls and self._config.port == 465:
                with smtplib.SMTP_SSL(
                    self._config.host,
                    self._config.port,
                    timeout=self._config.timeout_seconds,
                    context=context,
                ) as smtp:
                    self._login_if_configured(smtp)
                    smtp.send_message(message)
                return

            with smtplib.SMTP(
                self._config.host,
                self._config.port,
                timeout=self._config.timeout_seconds,
            ) as smtp:
                if self._config.use_tls:
                    smtp.starttls(context=context)
                self._login_if_configured(smtp)
                smtp.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            msg = "SMTP delivery failed"
            raise EmailDeliveryError(msg) from exc

    def _login_if_configured(self, smtp: smtplib.SMTP) -> None:
        if self._config.username:
            smtp.login(self._config.username, self._config.password)


def build_email_adapter_from_env() -> EmailAdapter:
    return SmtpEmailAdapter(SmtpEmailConfig.from_env())


def _bool_from_env(raw_value: str | None, *, default: bool) -> bool:
    if raw_value is None or not raw_value.strip():
        return default
    normalized = raw_value.strip().lower()
    if normalized in TRUTHY_VALUES:
        return True
    if normalized in FALSY_VALUES:
        return False
    msg = "SMTP_TLS must be a boolean value"
    raise EmailConfigurationError(msg)


def _int_from_env(raw_value: str | None, default: int) -> int:
    if raw_value is None or not raw_value.strip():
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        msg = "SMTP_PORT must be between 1 and 65535"
        raise EmailConfigurationError(msg) from exc
    if not 1 <= value <= 65535:
        msg = "SMTP_PORT must be between 1 and 65535"
        raise EmailConfigurationError(msg)
    return value


def _float_from_env(raw_value: str | None, default: float) -> float:
    if raw_value is None or not raw_value.strip():
        return default
    try:
        value = float(raw_value)
    except ValueError as exc:
        msg = "SMTP_TIMEOUT_SECONDS must be greater than 0 and at most 300"
        raise EmailConfigurationError(msg) from exc
    if not math.isfinite(value) or not 0 < value <= MAX_SMTP_TIMEOUT_SECONDS:
        msg = "SMTP_TIMEOUT_SECONDS must be greater than 0 and at most 300"
        raise EmailConfigurationError(msg)
    return value
