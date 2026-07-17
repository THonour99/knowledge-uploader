from __future__ import annotations

import io
import traceback as traceback_module
from collections.abc import Callable, Iterable
from typing import cast

import pytest
from kombu import Connection as KombuConnection

from app.workers import rabbitmq_topology as topology


class _FakeConnection:
    def __init__(
        self,
        *,
        retry_error: BaseException | None = None,
        terminal_error: BaseException | None = None,
    ) -> None:
        self.retry_error = retry_error
        self.terminal_error = terminal_error
        self.ensure_kwargs: dict[str, object] | None = None
        self.channel_calls = 0
        self.exit_calls = 0
        self.channel_value = object()

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(
        self,
        exc_type: object,
        exc: object,
        traceback: object,
    ) -> None:
        self.exit_calls += 1

    def ensure_connection(self, **kwargs: object) -> None:
        self.ensure_kwargs = dict(kwargs)
        if self.retry_error is not None:
            errback = cast(
                Callable[[BaseException, float], None],
                kwargs["errback"],
            )
            errback(self.retry_error, 1.0)
        if self.terminal_error is not None:
            raise self.terminal_error

    def channel(self) -> object:
        self.channel_calls += 1
        return self.channel_value


def _install_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    connection: _FakeConnection,
) -> tuple[
    list[tuple[str, float]],
    list[tuple[object, tuple[object, ...]]],
    list[tuple[str, dict[str, object]]],
]:
    connection_arguments: list[tuple[str, float]] = []
    declarations: list[tuple[object, tuple[object, ...]]] = []
    warnings: list[tuple[str, dict[str, object]]] = []

    def connection_factory(
        broker_url: str,
        *,
        connect_timeout: float,
    ) -> _FakeConnection:
        connection_arguments.append((broker_url, connect_timeout))
        return connection

    def capture_declarations(
        channel: object,
        entities: Iterable[object],
    ) -> None:
        declarations.append((channel, tuple(entities)))

    def capture_warning(event: str, **kwargs: object) -> None:
        warnings.append((event, kwargs))

    monkeypatch.setattr(topology, "Connection", connection_factory)
    monkeypatch.setattr(topology, "_declare_entities", capture_declarations)
    monkeypatch.setattr(topology.logger, "warning", capture_warning)
    return connection_arguments, declarations, warnings


def test_declare_topology_retries_with_bounded_policy_and_sanitized_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _FakeConnection(
        retry_error=ConnectionRefusedError("amqp://knowledge:do-not-log@rabbitmq:5672//")
    )
    connection_arguments, declarations, warnings = _install_fakes(
        monkeypatch,
        connection=connection,
    )

    broker_url = "amqp://knowledge:secret@rabbitmq:5672//"
    topology.declare_topology(broker_url)

    assert connection_arguments == [(broker_url, topology.TOPOLOGY_CONNECT_ATTEMPT_TIMEOUT_SECONDS)]
    assert connection.ensure_kwargs is not None
    assert connection.ensure_kwargs["errback"] is topology._log_connection_retry
    assert connection.ensure_kwargs["max_retries"] == topology.TOPOLOGY_CONNECT_MAX_RETRIES
    assert (
        connection.ensure_kwargs["interval_start"]
        == topology.TOPOLOGY_CONNECT_INTERVAL_START_SECONDS
    )
    assert (
        connection.ensure_kwargs["interval_step"] == topology.TOPOLOGY_CONNECT_INTERVAL_STEP_SECONDS
    )
    assert (
        connection.ensure_kwargs["interval_max"] == topology.TOPOLOGY_CONNECT_INTERVAL_MAX_SECONDS
    )
    assert connection.ensure_kwargs["timeout"] == topology.TOPOLOGY_CONNECT_TOTAL_TIMEOUT_SECONDS
    assert connection.channel_calls == 1
    assert connection.exit_calls == 1
    assert len(declarations) == 2
    assert all(channel is connection.channel_value for channel, _entities in declarations)
    assert warnings == [
        (
            "rabbitmq_topology_connection_retry",
            {
                "error_type": "ConnectionRefusedError",
                "retry_in_seconds": 1.0,
            },
        )
    ]
    assert "do-not-log" not in repr(warnings)


def test_declare_topology_sanitizes_exhaustion_without_declaring_queues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminal_error = OSError("amqp://knowledge:do-not-log@rabbitmq:5672//")
    connection = _FakeConnection(
        retry_error=terminal_error,
        terminal_error=terminal_error,
    )
    _connection_arguments, declarations, warnings = _install_fakes(
        monkeypatch,
        connection=connection,
    )
    errors: list[tuple[str, dict[str, object]]] = []

    def capture_error(event: str, **kwargs: object) -> None:
        errors.append((event, kwargs))

    monkeypatch.setattr(topology.logger, "error", capture_error)
    broker_url = "amqp://knowledge:secret@rabbitmq:5672//"

    with pytest.raises(topology.RabbitmqTopologyError) as raised:
        topology.declare_topology(broker_url)

    assert connection.ensure_kwargs is not None
    assert connection.ensure_kwargs["max_retries"] == topology.TOPOLOGY_CONNECT_MAX_RETRIES
    assert connection.ensure_kwargs["timeout"] == topology.TOPOLOGY_CONNECT_TOTAL_TIMEOUT_SECONDS
    assert connection.channel_calls == 0
    assert connection.exit_calls == 1
    assert declarations == []
    assert errors == [("rabbitmq_topology_declaration_failed", {"error_type": "OSError"})]
    rendered_logs = repr((warnings, errors))
    assert "do-not-log" not in rendered_logs
    assert "secret" not in rendered_logs
    buffer = io.StringIO()
    traceback_module.print_exception(
        type(raised.value), raised.value, raised.value.__traceback__, file=buffer
    )
    rendered_traceback = buffer.getvalue()
    assert "do-not-log" not in rendered_traceback
    assert "secret" not in rendered_traceback
    assert raised.value.__context__ is None


def _install_real_kombu_retry(
    monkeypatch: pytest.MonkeyPatch,
    *,
    connect: Callable[[], object],
    max_retries: int,
) -> tuple[
    object,
    list[tuple[object, tuple[object, ...]]],
    list[tuple[str, dict[str, object]]],
    list[tuple[str, dict[str, object]]],
]:
    connection = KombuConnection("memory://")
    channel = object()
    declarations: list[tuple[object, tuple[object, ...]]] = []
    warnings: list[tuple[str, dict[str, object]]] = []
    errors: list[tuple[str, dict[str, object]]] = []

    def connection_factory(
        _broker_url: str,
        *,
        connect_timeout: float,
    ) -> KombuConnection:
        assert connect_timeout == topology.TOPOLOGY_CONNECT_ATTEMPT_TIMEOUT_SECONDS
        return connection

    def capture_declarations(
        candidate_channel: object,
        entities: Iterable[object],
    ) -> None:
        declarations.append((candidate_channel, tuple(entities)))

    def capture_warning(event: str, **kwargs: object) -> None:
        warnings.append((event, kwargs))

    def capture_error(event: str, **kwargs: object) -> None:
        errors.append((event, kwargs))

    monkeypatch.setattr(connection, "_connection_factory", connect)
    monkeypatch.setattr(connection, "recoverable_connection_errors", (OSError,))
    monkeypatch.setattr(connection, "channel", lambda: channel)
    monkeypatch.setattr(topology, "Connection", connection_factory)
    monkeypatch.setattr(topology, "_declare_entities", capture_declarations)
    monkeypatch.setattr(topology.logger, "warning", capture_warning)
    monkeypatch.setattr(topology.logger, "error", capture_error)
    monkeypatch.setattr(topology, "TOPOLOGY_CONNECT_MAX_RETRIES", max_retries)
    monkeypatch.setattr(topology, "TOPOLOGY_CONNECT_INTERVAL_START_SECONDS", 0)
    monkeypatch.setattr(topology, "TOPOLOGY_CONNECT_INTERVAL_STEP_SECONDS", 0)
    monkeypatch.setattr(topology, "TOPOLOGY_CONNECT_INTERVAL_MAX_SECONDS", 0)
    monkeypatch.setattr(topology, "TOPOLOGY_CONNECT_TOTAL_TIMEOUT_SECONDS", 1)
    return channel, declarations, warnings, errors


def test_real_kombu_retry_eventually_succeeds_before_declaring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def flaky_connect() -> object:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise OSError("amqp://knowledge:do-not-log@rabbitmq:5672//")
        return object()

    channel, declarations, warnings, errors = _install_real_kombu_retry(
        monkeypatch,
        connect=flaky_connect,
        max_retries=4,
    )

    topology.declare_topology("amqp://knowledge:secret@rabbitmq:5672//")

    assert attempts == 3
    assert len(warnings) == 2
    assert errors == []
    assert len(declarations) == 2
    assert all(declared_channel is channel for declared_channel, _entities in declarations)
    assert "do-not-log" not in repr(warnings)
    assert "secret" not in repr(warnings)


def test_real_kombu_retry_exhaustion_is_bounded_and_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def always_fail() -> object:
        nonlocal attempts
        attempts += 1
        raise OSError("amqp://knowledge:do-not-log@rabbitmq:5672//")

    _channel, declarations, warnings, errors = _install_real_kombu_retry(
        monkeypatch,
        connect=always_fail,
        max_retries=2,
    )
    broker_url = "amqp://knowledge:secret@rabbitmq:5672//"

    with pytest.raises(topology.RabbitmqTopologyError) as raised:
        topology.declare_topology(broker_url)

    assert attempts == 3
    assert declarations == []
    assert len(warnings) == 2
    assert len(errors) == 1
    rendered_logs = repr((warnings, errors))
    assert "do-not-log" not in rendered_logs
    assert "secret" not in rendered_logs
    buffer = io.StringIO()
    traceback_module.print_exception(
        type(raised.value), raised.value, raised.value.__traceback__, file=buffer
    )
    rendered_traceback = buffer.getvalue()
    assert "do-not-log" not in rendered_traceback
    assert "secret" not in rendered_traceback
    assert raised.value.__context__ is None
