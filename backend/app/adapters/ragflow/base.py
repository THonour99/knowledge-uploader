from __future__ import annotations

from typing import Protocol


class RagflowClient(Protocol):
    async def ping(self) -> bool: ...
