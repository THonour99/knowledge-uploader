from __future__ import annotations

from typing import Protocol


class EmailAdapter(Protocol):
    async def send(self, recipient: str, subject: str, body: str) -> None: ...
