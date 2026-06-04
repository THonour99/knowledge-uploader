from __future__ import annotations


class MockStorageAdapter:
    async def exists(self, bucket: str, object_key: str) -> bool:
        return bool(bucket and object_key)
