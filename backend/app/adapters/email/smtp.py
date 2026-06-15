from __future__ import annotations

import asyncio
import os
import smtplib
import ssl
from collections.abc import Mapping
from dataclasses import dataclass
from email.message import EmailMessage as StdlibEmailMessage

from .base import EmailAdapter, EmailConfigurationError, EmailDeliveryError

DEFAULT_SMTP_PORT = 587
DEFAULT_SMTP_TIMEOUT_SECONDS = 10.0
TRUTHY_VALUES = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class SmtpEmailConfig:
    host: str
    port: int
    username: str
    password: str
    sender: str
    use_tls: bool = True
    timeout_seconds: float = DEFAULT_SMTP_TIMEOUT_SECONDS

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> SmtpEmailConfig:
        source = os.environ if environ is None else environ
        username = source.get("SMTP_USER", "").strip()
        return cls(
            host=source.get("SMTP_HOST", "").strip(),
            port=_int_from_env(source.get("SMTP_PORT"), DEFAULT_SMTP_PORT),
            username=username,
            password=source.get("SMTP_PASSWORD", ""),
            sender=source.get("SMTP_FROM", "").strip() or username,
            use_tls=_bool_from_env(source.get("SMTP_TLS"), default=True),
            timeout_seconds=_float_from_env(
                source.get("SMTP_TIMEOUT_SECONDS"),
                DEFAULT_SMTP_TIMEOUT_SECONDS,
            ),
        )

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

        context = ssl.create_default_context()
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
    return raw_value.strip().lower() in TRUTHY_VALUES


def _int_from_env(raw_value: str | None, default: int) -> int:
    if raw_value is None or not raw_value.strip():
        return default
    try:
        return int(raw_value)
    except ValueError:
        return default


def _float_from_env(raw_value: str | None, default: float) -> float:
    if raw_value is None or not raw_value.strip():
        return default
    try:
        return float(raw_value)
    except ValueError:
        return default
