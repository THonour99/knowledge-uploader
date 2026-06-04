from __future__ import annotations

from typing import Protocol


class StorageAdapter(Protocol):
    async def exists(self, bucket: str, object_key: str) -> bool: ...
