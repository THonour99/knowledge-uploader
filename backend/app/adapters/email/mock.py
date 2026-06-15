from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SentEmail:
    recipient: str
    subject: str
    body: str


class MockEmailAdapter:
    def __init__(self) -> None:
        self.sent: list[SentEmail] = []

    async def send(self, recipient: str, subject: str, body: str) -> None:
        self.sent.append(SentEmail(recipient=recipient, subject=subject, body=body))
