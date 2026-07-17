"""Minimal isolated SMTP sink with a probe-token protected verification state API."""

from __future__ import annotations

import asyncio
import json
import os
import re
import ssl
import threading
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import getaddresses
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

MAX_MESSAGE_BYTES = 2 * 1024 * 1024
TOKEN_PATTERN = re.compile(r"/verify-email\?token=([A-Za-z0-9._~-]+)")


def _text_content(message: Message) -> str:
    payload = message.get_payload(decode=True)
    if isinstance(payload, bytes):
        charset = message.get_content_charset() or "utf-8"
        try:
            return payload.decode(charset, errors="replace")
        except LookupError:
            return payload.decode("utf-8", errors="replace")
    if isinstance(payload, str):
        return payload
    return ""


class MailState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._messages: list[dict[str, str]] = []

    def record(self, raw_message: bytes, envelope_recipients: list[str]) -> None:
        message = BytesParser(policy=policy.default).parsebytes(raw_message)
        recipients = {
            address.lower() for _name, address in getaddresses(message.get_all("to", [])) if address
        }
        recipients.update(address.lower() for address in envelope_recipients if address)
        body_parts: list[str] = []
        if message.is_multipart():
            for part in message.walk():
                if part.get_content_type() not in {"text/plain", "text/html"}:
                    continue
                body_parts.append(_text_content(part))
        else:
            body_parts.append(_text_content(message))
        token_match = TOKEN_PATTERN.search("\n".join(body_parts))
        token = token_match.group(1) if token_match is not None else ""
        subject = str(message.get("subject", ""))[:160]
        records = [
            {"recipient": recipient, "verification_token": token, "subject": subject}
            for recipient in sorted(recipients)
        ]
        with self._lock:
            self._messages.extend(records)

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {"messages": [dict(message) for message in self._messages]}


STATE = MailState()


async def _write_line(writer: asyncio.StreamWriter, line: str) -> None:
    writer.write(f"{line}\r\n".encode("ascii"))
    await writer.drain()


def _smtp_path(command: str) -> str:
    _prefix, _separator, value = command.partition(":")
    address = value.strip().split(" ", 1)[0].strip("<>")
    return address


async def handle_smtp(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    tls_context: ssl.SSLContext | None = None,
) -> None:
    recipients: list[str] = []
    tls_active = False
    await _write_line(writer, "220 mock-smtp ESMTP ready")
    try:
        while True:
            raw_line = await reader.readline()
            if not raw_line:
                return
            if len(raw_line) > 4096:
                await _write_line(writer, "500 line too long")
                return
            command = raw_line.decode("utf-8", errors="replace").strip()
            verb = command.split(" ", 1)[0].split(":", 1)[0].upper()
            if verb in {"EHLO", "HELO"}:
                if tls_context is not None and not tls_active:
                    writer.write(b"250-mock-smtp\r\n" b"250-STARTTLS\r\n" b"250 SIZE 2097152\r\n")
                else:
                    writer.write(b"250-mock-smtp\r\n250 SIZE 2097152\r\n")
                await writer.drain()
            elif verb == "STARTTLS":
                if tls_context is None or tls_active:
                    await _write_line(writer, "454 TLS not available")
                    continue
                await _write_line(writer, "220 ready to start TLS")
                await writer.start_tls(tls_context)
                tls_active = True
                recipients = []
            elif tls_context is not None and not tls_active and verb in {"MAIL", "RCPT", "DATA"}:
                await _write_line(writer, "530 must issue STARTTLS first")
            elif verb == "MAIL":
                recipients = []
                await _write_line(writer, "250 sender accepted")
            elif verb == "RCPT":
                recipient = _smtp_path(command)
                if not recipient:
                    await _write_line(writer, "501 recipient required")
                    continue
                recipients.append(recipient)
                await _write_line(writer, "250 recipient accepted")
            elif verb == "DATA":
                await _write_line(writer, "354 end data with <CR><LF>.<CR><LF>")
                chunks: list[bytes] = []
                size = 0
                while True:
                    data_line = await reader.readline()
                    if not data_line or data_line in {b".\r\n", b".\n"}:
                        break
                    if data_line.startswith(b".."):
                        data_line = data_line[1:]
                    size += len(data_line)
                    if size > MAX_MESSAGE_BYTES:
                        chunks = []
                        break
                    chunks.append(data_line)
                if chunks:
                    STATE.record(b"".join(chunks), recipients)
                    await _write_line(writer, "250 message accepted")
                else:
                    await _write_line(writer, "552 message too large or empty")
            elif verb == "RSET":
                recipients = []
                await _write_line(writer, "250 reset")
            elif verb == "NOOP":
                await _write_line(writer, "250 ok")
            elif verb == "QUIT":
                await _write_line(writer, "221 bye")
                return
            else:
                await _write_line(writer, "502 command not implemented")
    finally:
        writer.close()
        await writer.wait_closed()


class StateHandler(BaseHTTPRequestHandler):
    state: ClassVar[MailState]
    probe_token: ClassVar[str]

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(HTTPStatus.OK, {"status": "ok"})
            return
        if self.path != "/__e2e/state":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        if self.headers.get("X-E2E-Probe-Token") != self.probe_token:
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "forbidden"})
            return
        self._send_json(HTTPStatus.OK, self.state.snapshot())

    def _send_json(self, status: HTTPStatus, payload: object) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def run_http_server(
    probe_token: str,
    *,
    tls_context: ssl.SSLContext | None = None,
) -> ThreadingHTTPServer:
    handler = type(
        "BoundStateHandler",
        (StateHandler,),
        {"state": STATE, "probe_token": probe_token},
    )
    server = ThreadingHTTPServer(("0.0.0.0", 8080), handler)
    if tls_context is not None:
        server.socket = tls_context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _server_tls_context_from_env() -> ssl.SSLContext:
    certificate = os.environ.get("E2E_TLS_CERT_FILE", "").strip()
    private_key = os.environ.get("E2E_TLS_KEY_FILE", "").strip()
    if not certificate or not private_key:
        raise RuntimeError("mock SMTP TLS certificate and key are required")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certificate, private_key)
    return context


async def main() -> None:
    probe_token = os.environ.get("E2E_PROBE_TOKEN", "").strip()
    if not probe_token:
        raise RuntimeError("E2E_PROBE_TOKEN is required")
    tls_context = _server_tls_context_from_env()
    http_server = run_http_server(probe_token, tls_context=tls_context)
    try:
        smtp_server = await asyncio.start_server(
            lambda reader, writer: handle_smtp(
                reader,
                writer,
                tls_context=tls_context,
            ),
            "0.0.0.0",
            1025,
        )
        async with smtp_server:
            await smtp_server.serve_forever()
    finally:
        http_server.shutdown()
        http_server.server_close()


if __name__ == "__main__":
    asyncio.run(main())
