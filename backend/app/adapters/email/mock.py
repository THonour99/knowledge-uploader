from __future__ import annotations


class MockEmailAdapter:
    async def send(self, recipient: str, subject: str, body: str) -> None:
        _ = (recipient, subject, body)
