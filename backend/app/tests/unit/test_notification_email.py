from __future__ import annotations

from datetime import UTC, datetime, timedelta
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
    monkeypatch.setattr(tasks, "_record_email_delivery_result_best_effort", lambda _result: None)

    encrypted_envelope = tasks._encrypt_email_envelope(
        recipient="user@company.test",
        subject="subject",
        body="body",
    )
    assert tasks.send_email_task.run(encrypted_envelope) == "sent"
    assert sent == [("user@company.test", "subject", "body")]


def test_enqueue_email_uses_confirmed_persistent_encrypted_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.notification import tasks

    captured: dict[str, object] = {}

    class FakeSender:
        def send_task(
            self,
            name: str,
            args: list[str],
            queue: str,
            delivery_mode: int,
            task_id: str,
            retry: bool,
            retry_policy: object,
            expires: datetime | None,
            argsrepr: str,
        ) -> object:
            captured.update(
                {
                    "name": name,
                    "args": args,
                    "queue": queue,
                    "delivery_mode": delivery_mode,
                    "task_id": task_id,
                    "retry": retry,
                    "retry_policy": retry_policy,
                    "expires": expires,
                    "argsrepr": argsrepr,
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

    assert captured["name"] == "notification.send_email"
    assert captured["queue"] == "notification_queue"
    assert captured["delivery_mode"] == 2
    assert captured["retry"] is True
    assert captured["retry_policy"] == tasks.EMAIL_PUBLISH_RETRY_POLICY
    assert captured["expires"] is None
    assert captured["argsrepr"] == "(<encrypted-email-envelope>,)"
    assert str(captured["task_id"]).startswith("email-")
    args = captured["args"]
    assert isinstance(args, list)
    assert len(args) == 1
    encrypted_envelope = args[0]
    assert isinstance(encrypted_envelope, str)
    assert encrypted_envelope not in str(captured["argsrepr"])
    assert "user@company.test" not in encrypted_envelope
    assert "subject" not in encrypted_envelope
    assert "body" not in encrypted_envelope
    assert tasks._decrypt_email_envelope(encrypted_envelope) == (
        "user@company.test",
        "subject",
        "body",
        None,
    )
    assert tasks.celery_app.conf.broker_transport_options["confirm_publish"] is True

    message = tasks.celery_app.amqp.as_task_v2(
        task_id="email-argsrepr-contract",
        name="notification.send_email",
        args=[encrypted_envelope],
        argsrepr=str(captured["argsrepr"]),
    )
    assert message.headers["argsrepr"] == "(<encrypted-email-envelope>,)"
    assert encrypted_envelope not in message.headers["argsrepr"]


def test_send_email_task_uses_early_ack_without_ambiguous_smtp_retry() -> None:
    from app.modules.notification import tasks

    assert tasks.send_email_task.acks_late is False
    assert tasks.send_email_task.acks_on_failure_or_timeout is True
    assert tasks.send_email_task.reject_on_worker_lost is False
    assert not hasattr(tasks.send_email_task, "autoretry_for")


def test_enqueue_email_publisher_failure_is_visible(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.notification import tasks

    class FailingSender:
        def send_task(self, *_args: object, **_kwargs: object) -> object:
            raise RuntimeError("publisher confirm failed")

    monkeypatch.setenv("APP_ENV", "development")
    with pytest.raises(RuntimeError, match="publisher confirm failed"):
        tasks.enqueue_email(
            recipient="user@company.test",
            subject="subject",
            body="body",
            sender=FailingSender(),
        )


def test_send_email_task_rejects_plaintext_payload_without_logging_token() -> None:
    from app.modules.notification import tasks

    raw_token = "secret-verification-token"
    with pytest.raises(tasks.EmailEnvelopeError, match="envelope is invalid") as captured:
        tasks.send_email_task.run(raw_token)

    assert raw_token not in str(captured.value)


def test_auth_email_expiry_is_applied_to_broker_and_checked_by_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.notification import tasks

    captured: dict[str, object] = {}

    class FakeSender:
        def send_task(self, _name: str, **kwargs: object) -> object:
            captured.update(kwargs)
            return object()

    expires_at = datetime.now(UTC) + timedelta(minutes=10)
    monkeypatch.setenv("APP_ENV", "development")
    tasks.enqueue_email(
        recipient="user@company.test",
        subject="verify",
        body="token-link",
        expires_at=expires_at,
        sender=FakeSender(),
    )

    assert captured["expires"] == expires_at
    args = captured["args"]
    assert isinstance(args, list)
    assert tasks._decrypt_email_envelope(str(args[0]))[3] == expires_at

    results: list[str] = []
    monkeypatch.setattr(
        tasks,
        "_record_email_delivery_result_best_effort",
        lambda result: results.append(result),
    )
    expired = tasks._encrypt_email_envelope(
        recipient="user@company.test",
        subject="verify",
        body="token-link",
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    assert tasks.send_email_task.run(expired) == "expired"
    assert results == ["expired"]
