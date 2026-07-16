from __future__ import annotations

from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.requests import Request
from starlette.responses import Response

from app.core import email_delivery_metrics, metrics
from app.workers import outbox_dispatcher


async def test_metrics_endpoint_uses_prometheus_content_type() -> None:
    from app.main import app

    transport = ASGITransport(app=app)  # type: ignore[arg-type]
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"] == CONTENT_TYPE_LATEST
    assert "knowledge_uploader_http_requests_total" in response.text


def test_email_metric_result_labels_share_one_bounded_contract() -> None:
    assert metrics._EMAIL_DELIVERY_RESULTS == email_delivery_metrics.EMAIL_DELIVERY_RESULTS
    assert "publish_failure" in metrics._EMAIL_DELIVERY_RESULTS


async def test_http_metrics_use_route_template_not_raw_path() -> None:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/files/private-file-id-123",
            "headers": [],
        }
    )

    async def call_next(current_request: Request) -> Response:
        current_request.scope["route"] = SimpleNamespace(path="/api/files/{file_id}")
        return Response(status_code=201)

    response = await metrics.http_metrics_middleware(request, call_next)
    output = generate_latest().decode("utf-8")

    assert response.status_code == 201
    assert 'route="/api/files/{file_id}"' in output
    assert "private-file-id-123" not in output


def test_metric_labels_collapse_untrusted_values() -> None:
    metrics.observe_outbox_publish("tenant-secret.injected", "unbounded-result")
    metrics.observe_task_result("user-supplied-task", "unbounded-result")
    metrics.observe_external_request("https://secret.example", "unbounded-result")
    metrics.observe_config_invariant_violation("tenant-controlled-key")
    output = generate_latest().decode("utf-8")

    assert "tenant-secret" not in output
    assert "user-supplied-task" not in output
    assert "secret.example" not in output
    assert "tenant-controlled-key" not in output
    assert 'event_family="other",result="failure"' in output
    assert 'task_family="other"' in output
    assert 'service="other"' in output
    assert 'config_key="other"' in output


def test_storage_and_ragflow_window_metrics_have_truthful_units_and_names() -> None:
    metrics.update_operational_snapshot(
        review_overdue=0,
        ragflow_success=3,
        ragflow_failure=1,
        ragflow_canceled=2,
        minio_bytes=123,
        postgres_bytes=456,
        email_delivery_totals={},
        email_delivery_last_timestamps={},
        collected_at_timestamp=1000.0,
    )
    output = generate_latest().decode("utf-8")

    assert 'knowledge_uploader_logical_document_references_bytes{backend="minio"} 123.0' in output
    assert "knowledge_uploader_postgres_database_size_bytes 456.0" in output
    assert "knowledge_uploader_referenced_storage_bytes" not in output
    assert "knowledge_uploader_logical_document_storage_bytes" not in output
    assert 'knowledge_uploader_ragflow_sync_outcomes_window{result="success"} 3.0' in output
    assert "knowledge_uploader_ragflow_sync_window_total" not in output


def test_outbox_metrics_port_is_strictly_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OUTBOX_METRICS_PORT", "9201")
    assert outbox_dispatcher._metrics_port() == 9201

    monkeypatch.setenv("OUTBOX_METRICS_PORT", "not-a-number")
    with pytest.raises(RuntimeError, match="must be an integer"):
        outbox_dispatcher._metrics_port()

    monkeypatch.setenv("OUTBOX_METRICS_PORT", "70000")
    with pytest.raises(RuntimeError, match="must be between"):
        outbox_dispatcher._metrics_port()


async def test_email_delivery_metrics_are_read_from_persistent_redis_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeRedis:
        async def hgetall(self, _key: str) -> dict[str, str]:
            return {
                "success_total": "4",
                "failure_total": "2",
                "last_failure_timestamp_seconds": "123.5",
            }

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        email_delivery_metrics,
        "from_url",
        lambda *_args, **_kwargs: FakeRedis(),
    )

    snapshot = await email_delivery_metrics.read_email_delivery_metrics(
        redis_url="redis://cache.test/0"
    )

    assert snapshot.totals["success"] == 4
    assert snapshot.totals["failure"] == 2
    assert snapshot.totals["expired"] == 0
    assert snapshot.totals["publish_failure"] == 0
    assert snapshot.last_timestamps["failure"] == 123.5


async def test_email_delivery_metrics_reject_corrupt_persistent_counter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeRedis:
        async def hgetall(self, _key: str) -> dict[str, str]:
            return {"failure_total": "-1"}

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        email_delivery_metrics,
        "from_url",
        lambda *_args, **_kwargs: FakeRedis(),
    )

    with pytest.raises(RuntimeError, match="counter is invalid"):
        await email_delivery_metrics.read_email_delivery_metrics(redis_url="redis://cache.test/0")


@pytest.mark.parametrize("invalid_timestamp", ["nan", "inf", "-inf", "-1"])
async def test_email_delivery_metrics_reject_nonfinite_or_negative_timestamp(
    monkeypatch: pytest.MonkeyPatch,
    invalid_timestamp: str,
) -> None:
    class FakeRedis:
        async def hgetall(self, _key: str) -> dict[str, str]:
            return {"last_failure_timestamp_seconds": invalid_timestamp}

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        email_delivery_metrics,
        "from_url",
        lambda *_args, **_kwargs: FakeRedis(),
    )

    with pytest.raises(RuntimeError, match="timestamp is invalid"):
        await email_delivery_metrics.read_email_delivery_metrics(redis_url="redis://cache.test/0")


def test_operational_metrics_identify_their_own_database_pool() -> None:
    metrics.update_db_pool(size=5, checked_out=2, overflow=1)
    output = generate_latest().decode("utf-8")

    assert "knowledge_uploader_operational_collector_db_pool_connections" in output
    assert "knowledge_uploader_db_pool_connections" not in output


def test_operational_collector_component_labels_are_bounded() -> None:
    metrics.observe_operational_collector_component(
        "database",
        succeeded=True,
        timestamp=1234.0,
    )
    metrics.observe_operational_collector_component("email_redis", succeeded=False)
    metrics.observe_operational_collector_component("redis://secret-host/0", succeeded=False)
    output = generate_latest().decode("utf-8")

    assert "secret-host" not in output
    assert (
        "knowledge_uploader_operational_collector_component_last_success_"
        'timestamp_seconds{component="database"} 1234.0'
    ) in output
    assert (
        'knowledge_uploader_operational_collector_component_errors_total{component="email_redis"}'
    ) in output
    assert (
        'knowledge_uploader_operational_collector_component_errors_total{component="other"}'
    ) in output
