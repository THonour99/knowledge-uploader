from __future__ import annotations

from app.core.events import EventDispatchContext, EventEnvelope, event_handler

from .events import UserPasswordResetRequested


@event_handler(UserPasswordResetRequested)
def trigger_password_reset(event: EventEnvelope, context: EventDispatchContext) -> None:
    user_id = event.payload.get("user_id")
    if not isinstance(user_id, str) or not user_id:
        msg = "password reset event missing user_id"
        raise RuntimeError(msg)
    context.sender.send_task(
        "auth.trigger_password_reset",
        args=[user_id],
        queue="notification_queue",
    )
