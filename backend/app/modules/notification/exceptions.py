from __future__ import annotations


class NotificationSourceEventNotFoundError(RuntimeError):
    """The stable source event ID no longer resolves to the canonical outbox row."""


class UnsupportedNotificationSourceEventError(RuntimeError):
    """A task referenced an event type that is not a notification source."""


class NotificationEmailNotFoundError(RuntimeError):
    """The stable notification ID is not a deliverable email notification."""
