from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import ClassVar
from uuid import UUID

from pydantic import BaseModel


class DomainEvent(BaseModel):
    ROUTING_KEY: ClassVar[str]


class FileUploaded(DomainEvent):
    ROUTING_KEY = "document.file.uploaded"

    file_id: UUID
    uploader_id: UUID
    sha256: str
    size: int
    extension: str


EventHandler = Callable[[DomainEvent], Awaitable[None]]
EVENT_HANDLERS: dict[str, list[EventHandler]] = {}


def event_handler(event_cls: type[DomainEvent]) -> Callable[[EventHandler], EventHandler]:
    def decorator(handler: EventHandler) -> EventHandler:
        EVENT_HANDLERS.setdefault(event_cls.ROUTING_KEY, []).append(handler)
        return handler

    return decorator
