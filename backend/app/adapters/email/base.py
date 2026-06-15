from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class EmailConfigurationError(RuntimeError):
    """Raised when email delivery is requested without enough SMTP configuration."""


class EmailDeliveryError(RuntimeError):
    """Raised when an email adapter cannot deliver a configured message."""


@dataclass(frozen=True)
class EmailMessage:
    recipient: str
    subject: str
    body: str


class EmailAdapter(Protocol):
    async def send(self, recipient: str, subject: str, body: str) -> None: ...
