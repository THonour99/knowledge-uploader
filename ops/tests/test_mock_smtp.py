from __future__ import annotations

import asyncio
import importlib.util
import smtplib
import ssl
from collections.abc import Callable
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


def _load_certificate_generator() -> ModuleType:
    path = Path(__file__).parents[2] / "backend" / "scripts" / "generate_e2e_certificates.py"
    spec = importlib.util.spec_from_file_location("generate_e2e_certificates", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load E2E certificate generator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _smtp_tls_contexts(tmp_path: Path) -> tuple[ssl.SSLContext, ssl.SSLContext]:
    certificate_generator = _load_certificate_generator()
    certificate_dir = tmp_path / "certificates"
    certificate_generator.generate_certificates(certificate_dir)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(
        certificate_dir / "smtp.crt",
        certificate_dir / "smtp.key",
    )
    client_context = ssl.create_default_context(cafile=certificate_dir / "ca.crt")
    return server_context, client_context


def _tracked_smtp_callback(
    mock_smtp: ModuleType,
    *,
    tls_context: ssl.SSLContext,
    handler_tasks: list[asyncio.Task[None]],
) -> Callable[[asyncio.StreamReader, asyncio.StreamWriter], None]:
    def start_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        handler_tasks.append(
            asyncio.create_task(
                mock_smtp.handle_smtp(
                    reader,
                    writer,
                    tls_context=tls_context,
                )
            )
        )

    return start_handler


async def _finish_mock_smtp_server(
    server: asyncio.Server,
    handler_tasks: list[asyncio.Task[None]],
) -> None:
    try:
        if handler_tasks:
            await asyncio.wait_for(asyncio.gather(*handler_tasks), timeout=5)
    finally:
        for task in handler_tasks:
            if not task.done():
                task.cancel()
        if handler_tasks:
            await asyncio.gather(*handler_tasks, return_exceptions=True)
        server.close()
        await server.wait_closed()


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
async def test_mock_smtp_accepts_a_verified_starttls_delivery(tmp_path: Path) -> None:
    mock_smtp = _load_mock_smtp()
    mock_smtp.STATE = mock_smtp.MailState()
    server_context, client_context = _smtp_tls_contexts(tmp_path)
    handler_tasks: list[asyncio.Task[None]] = []
    server = await asyncio.start_server(
        _tracked_smtp_callback(
            mock_smtp,
            tls_context=server_context,
            handler_tasks=handler_tasks,
        ),
        "127.0.0.1",
        0,
    )
    socket = server.sockets[0]
    port = int(socket.getsockname()[1])
    message = EmailMessage()
    message["From"] = "noreply@e2e.invalid"
    message["To"] = "reviewer@e2e.invalid"
    message["Subject"] = "Verify account"
    message.set_content("https://example.invalid/verify-email?token=real-smtp-token")

    def deliver() -> None:
        with smtplib.SMTP("127.0.0.1", port, timeout=5) as client:
            client.starttls(context=client_context)
            client.send_message(message)

    try:
        await asyncio.to_thread(deliver)
    finally:
        await _finish_mock_smtp_server(server, handler_tasks)

    assert mock_smtp.STATE.snapshot()["messages"] == [
        {
            "recipient": "reviewer@e2e.invalid",
            "verification_token": "real-smtp-token",
            "subject": "Verify account",
        }
    ]


@pytest.mark.asyncio
async def test_mock_smtp_rejects_plaintext_mail_when_starttls_is_available(
    tmp_path: Path,
) -> None:
    mock_smtp = _load_mock_smtp()
    server_context, _client_context = _smtp_tls_contexts(tmp_path)
    handler_tasks: list[asyncio.Task[None]] = []
    server = await asyncio.start_server(
        _tracked_smtp_callback(
            mock_smtp,
            tls_context=server_context,
            handler_tasks=handler_tasks,
        ),
        "127.0.0.1",
        0,
    )
    socket = server.sockets[0]
    port = int(socket.getsockname()[1])
    message = EmailMessage()
    message["From"] = "noreply@e2e.invalid"
    message["To"] = "reviewer@e2e.invalid"
    message.set_content("plaintext must be rejected")

    def deliver_without_tls() -> None:
        with smtplib.SMTP("127.0.0.1", port, timeout=5) as client:
            with pytest.raises(smtplib.SMTPResponseException) as raised:
                client.send_message(message)
            assert raised.value.smtp_code == 530

    try:
        await asyncio.to_thread(deliver_without_tls)
    finally:
        await _finish_mock_smtp_server(server, handler_tasks)
