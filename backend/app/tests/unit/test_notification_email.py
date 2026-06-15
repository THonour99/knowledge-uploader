from __future__ import annotations

from email.message import EmailMessage
from typing import ClassVar

import pytest

from app.adapters.email import SmtpEmailAdapter, SmtpEmailConfig


class FakeSmtp:
    instances: ClassVar[list[FakeSmtp]] = []

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.started_tls = False
        self.login_args: tuple[str, str] | None = None
        self.message: EmailMessage | None = None
        FakeSmtp.instances.append(self)

    def __enter__(self) -> FakeSmtp:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        return None

    def starttls(self, *, context: object) -> None:
        self.started_tls = context is not None

    def login(self, username: str, password: str) -> None:
        self.login_args = (username, password)

    def send_message(self, message: EmailMessage) -> None:
        self.message = message


@pytest.mark.asyncio
async def test_smtp_adapter_sends_message_with_starttls(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSmtp.instances = []
    monkeypatch.setattr("app.adapters.email.smtp.smtplib.SMTP", FakeSmtp)

    adapter = SmtpEmailAdapter(
        SmtpEmailConfig(
            host="smtp.company.test",
            port=587,
            username="mailer",
            password="secret",
            sender="noreply@company.test",
            use_tls=True,
            timeout_seconds=3,
        )
    )

    await adapter.send("user@company.test", "subject", "body")

    assert len(FakeSmtp.instances) == 1
    smtp = FakeSmtp.instances[0]
    assert smtp.host == "smtp.company.test"
    assert smtp.port == 587
    assert smtp.timeout == 3
    assert smtp.started_tls is True
    assert smtp.login_args == ("mailer", "secret")
    message = smtp.message
    assert message is not None
    assert message["From"] == "noreply@company.test"
    assert message["To"] == "user@company.test"
    assert message["Subject"] == "subject"
    assert message.get_content().strip() == "body"


def test_send_email_task_uses_mocked_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.notification import tasks

    sent: list[tuple[str, str, str]] = []

    class FakeAdapter:
        async def send(self, recipient: str, subject: str, body: str) -> None:
            sent.append((recipient, subject, body))

    monkeypatch.setattr(tasks, "build_email_adapter_from_env", lambda: FakeAdapter())

    assert tasks.send_email_task.run("user@company.test", "subject", "body") == "sent"
    assert sent == [("user@company.test", "subject", "body")]


def test_enqueue_email_uses_transient_notification_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.notification import tasks

    captured: dict[str, object] = {}

    class FakeSender:
        def send_task(
            self,
            name: str,
            args: list[str],
            queue: str,
            delivery_mode: int,
        ) -> object:
            captured.update(
                {
                    "name": name,
                    "args": args,
                    "queue": queue,
                    "delivery_mode": delivery_mode,
                }
            )
            return object()

    monkeypatch.setenv("APP_ENV", "development")
    tasks.enqueue_email(
        recipient="user@company.test",
        subject="subject",
        body="body",
        sender=FakeSender(),
    )

    assert captured == {
        "name": "notification.send_email",
        "args": ["user@company.test", "subject", "body"],
        "queue": "notification_queue",
        "delivery_mode": 1,
    }
