from __future__ import annotations

from typing import ClassVar

from app.core.events import DomainEvent

USER_PASSWORD_RESET_REQUESTED = "user.password_reset.requested"


class UserPasswordResetRequested(DomainEvent):
    ROUTING_KEY: ClassVar[str] = USER_PASSWORD_RESET_REQUESTED
