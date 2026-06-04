from __future__ import annotations


class MockRagflowClient:
    async def ping(self) -> bool:
        return True
