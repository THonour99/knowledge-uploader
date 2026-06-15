from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from importlib import import_module
from typing import ClassVar, Protocol

from pydantic import BaseModel


class DomainEvent(BaseModel):
    ROUTING_KEY: ClassVar[str]


class EventEnvelope(Protocol):
    event_type: str
    payload: dict[str, object]


class TaskSender(Protocol):
    def send_task(self, name: str, args: list[str], queue: str) -> object:
        pass


@dataclass(frozen=True, slots=True)
class EventDispatchContext:
    sender: TaskSender


EventHandler = Callable[[EventEnvelope, EventDispatchContext], None]
EVENT_HANDLERS: dict[str, list[EventHandler]] = {}
_LOADED_HANDLER_MODULES: set[str] = set()


def _handler_identity(handler: EventHandler) -> tuple[str, str]:
    return (handler.__module__, handler.__qualname__)


def event_handler(event_cls: type[DomainEvent]) -> Callable[[EventHandler], EventHandler]:
    routing_key = event_cls.ROUTING_KEY

    def decorator(handler: EventHandler) -> EventHandler:
        handlers = EVENT_HANDLERS.setdefault(routing_key, [])
        identity = _handler_identity(handler)
        if all(_handler_identity(existing) != identity for existing in handlers):
            handlers.append(handler)
        return handler

    return decorator


def load_event_handlers(module_names: Iterable[str]) -> None:
    for module_name in module_names:
        if module_name in _LOADED_HANDLER_MODULES:
            continue
        import_module(module_name)
        _LOADED_HANDLER_MODULES.add(module_name)


def dispatch_event(event: EventEnvelope, context: EventDispatchContext) -> int:
    handlers = tuple(EVENT_HANDLERS.get(event.event_type, ()))
    for handler in handlers:
        handler(event, context)
    return len(handlers)
