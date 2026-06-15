"""Email adapter package."""

from .base import EmailAdapter, EmailConfigurationError, EmailDeliveryError, EmailMessage
from .mock import MockEmailAdapter, SentEmail
from .smtp import SmtpEmailAdapter, SmtpEmailConfig, build_email_adapter_from_env

__all__ = [
    "EmailAdapter",
    "EmailConfigurationError",
    "EmailDeliveryError",
    "EmailMessage",
    "MockEmailAdapter",
    "SentEmail",
    "SmtpEmailAdapter",
    "SmtpEmailConfig",
    "build_email_adapter_from_env",
]
