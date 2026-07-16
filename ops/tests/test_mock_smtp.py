from __future__ import annotations

import asyncio
import importlib.util
import smtplib
from email.message import EmailMessage
from pathlib import Path
from types import ModuleType

import pytest


def _load_mock_smtp() -> ModuleType:
    path = Path(__file__).parents[2] / "ops" / "e2e" / "mock_smtp.py"
    spec = importlib.util.spec_from_file_location("mock_smtp", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load mock SMTP module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_mock_smtp_extracts_verification_token_without_logging_message_body() -> None:
    mock_smtp = _load_mock_smtp()
    message = EmailMessage()
    message["From"] = "noreply@e2e.invalid"
    message["To"] = "employee@e2e.invalid"
    message["Subject"] = "Verify account"
    message.set_content(
        "Open https://knowledge.e2e.invalid/verify-email?token=verification-token-123"
    )
    state = mock_smtp.MailState()

    state.record(message.as_bytes(), ["employee@e2e.invalid"])

    assert state.snapshot() == {
        "messages": [
            {
                "recipient": "employee@e2e.invalid",
                "verification_token": "verification-token-123",
                "subject": "Verify account",
            }
        ]
    }


def test_mock_smtp_ignores_non_verification_message_content() -> None:
    mock_smtp = _load_mock_smtp()
    message = EmailMessage()
    message["To"] = "employee@e2e.invalid"
    message.set_content("a body that must not be returned by the state endpoint")
    state = mock_smtp.MailState()

    state.record(message.as_bytes(), [])

    snapshot = state.snapshot()
    assert "must not be returned" not in repr(snapshot)
    assert snapshot["messages"][0]["verification_token"] == ""


@pytest.mark.asyncio
async def test_mock_smtp_accepts_a_real_smtp_client_delivery() -> None:
    mock_smtp = _load_mock_smtp()
    mock_smtp.STATE = mock_smtp.MailState()
    server = await asyncio.start_server(mock_smtp.handle_smtp, "127.0.0.1", 0)
    socket = server.sockets[0]
    port = int(socket.getsockname()[1])
    message = EmailMessage()
    message["From"] = "noreply@e2e.invalid"
    message["To"] = "reviewer@e2e.invalid"
    message["Subject"] = "Verify account"
    message.set_content("https://example.invalid/verify-email?token=real-smtp-token")

    def deliver() -> None:
        with smtplib.SMTP("127.0.0.1", port, timeout=5) as client:
            client.send_message(message)

    try:
        await asyncio.to_thread(deliver)
    finally:
        server.close()
        await server.wait_closed()

    assert mock_smtp.STATE.snapshot()["messages"] == [
        {
            "recipient": "reviewer@e2e.invalid",
            "verification_token": "real-smtp-token",
            "subject": "Verify account",
        }
    ]
