from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from importlib import import_module
from typing import ClassVar, Protocol

from pydantic import BaseModel


class DomainEvent(BaseModel):
    ROUTING_KEY: ClassVar[str]


class EventEnvelope(Protocol):
    event_id: int
    event_type: str
    payload: dict[str, object]


class TaskSender(Protocol):
    def send_task(
        self,
        name: str,
        args: list[str],
        queue: str,
        *,
        countdown: int | None = None,
    ) -> object:
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
    ordered_module_names = tuple(module_names)
    for module_name in ordered_module_names:
        if module_name in _LOADED_HANDLER_MODULES:
            continue
        import_module(module_name)
        _LOADED_HANDLER_MODULES.add(module_name)
    # Handler modules can be imported during process bootstrap or test collection
    # before the outbox worker loads its declared subscriber list. Always restore
    # that declaration order so an early import cannot change side-effect ordering.
    module_order = {module_name: index for index, module_name in enumerate(ordered_module_names)}
    unknown_module_order = len(module_order)
    for handlers in EVENT_HANDLERS.values():
        handlers.sort(
            key=lambda handler: module_order.get(handler.__module__, unknown_module_order)
        )


def dispatch_event(event: EventEnvelope, context: EventDispatchContext) -> int:
    handlers = tuple(EVENT_HANDLERS.get(event.event_type, ()))
    for handler in handlers:
        handler(event, context)
    return len(handlers)
