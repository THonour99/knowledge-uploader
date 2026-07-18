from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    start_http_server,
)
from starlette.requests import Request
from starlette.responses import Response

from app.core.email_delivery_metrics import EMAIL_DELIVERY_RESULTS
from app.core.ragflow_metrics_contract import (
    RAGFLOW_COMPLETED_RESULTS,
    RAGFLOW_FAILURE_CATEGORIES,
    RAGFLOW_OPERATIONS,
)

HTTP_REQUESTS = Counter(
    "knowledge_uploader_http_requests_total",
    "HTTP requests grouped only by method, route template and status class.",
    ("method", "route", "status_class"),
)
HTTP_DURATION = Histogram(
    "knowledge_uploader_http_request_duration_seconds",
    "HTTP latency grouped only by method and route template.",
    ("method", "route"),
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
OUTBOX_PENDING = Gauge(
    "knowledge_uploader_outbox_pending",
    "Number of outbox events still eligible for delivery.",
)
OUTBOX_OLDEST_SECONDS = Gauge(
    "knowledge_uploader_outbox_oldest_seconds",
    "Age in seconds of the oldest event still eligible for delivery.",
)
OUTBOX_DEAD_LETTERS = Gauge(
    "knowledge_uploader_outbox_dead_letters",
    "Dead-letter rows grouped by their bounded lifecycle status.",
    ("status",),
)
OUTBOX_PUBLISH = Counter(
    "knowledge_uploader_outbox_publish_total",
    "Outbox publish attempts grouped by bounded event family and result.",
    ("event_family", "result"),
)
TASK_RESULTS = Counter(
    "knowledge_uploader_task_results_total",
    "Background task results grouped by bounded task family.",
    ("task_family", "result"),
)
EXTERNAL_REQUESTS = Counter(
    "knowledge_uploader_external_requests_total",
    "External dependency calls grouped by bounded service and result.",
    ("service", "result"),
)
LOGICAL_DOCUMENT_REFERENCES_BYTES = Gauge(
    "knowledge_uploader_logical_document_references_bytes",
    "Sum of active file-row byte references; archived objects remain separately retained.",
    ("backend",),
)
POSTGRES_DATABASE_SIZE_BYTES = Gauge(
    "knowledge_uploader_postgres_database_size_bytes",
    "Physical size in bytes reported by pg_database_size for the application database.",
)
OPERATIONAL_COLLECTOR_DB_POOL_CONNECTIONS = Gauge(
    "knowledge_uploader_operational_collector_db_pool_connections",
    "Operational collector SQLAlchemy connection pool values grouped by a bounded state.",
    ("state",),
)
CONFIG_INVARIANT_VIOLATIONS = Counter(
    "knowledge_uploader_config_invariant_violations_total",
    "Runtime config invariant violations grouped by a bounded key.",
    ("config_key",),
)
SYSTEM_READY = Gauge(
    "knowledge_uploader_system_ready",
    "Whether the API readiness endpoint reports every dependency healthy.",
)
SYSTEM_READY_CONSECUTIVE_FAILURES = Gauge(
    "knowledge_uploader_system_ready_consecutive_failures",
    "Consecutive failed readiness probes from the operational collector.",
)
REVIEW_OVERDUE = Gauge(
    "knowledge_uploader_review_overdue",
    "Number of submitted files whose persisted review deadline has passed.",
)
RAGFLOW_SYNC_OUTCOMES_WINDOW = Gauge(
    "knowledge_uploader_ragflow_sync_outcomes_window",
    (
        "Latest mutually exclusive RAGFlow document outcomes in the collector window; "
        "file sync wins timestamp ties and null outcome timestamps are ignored."
    ),
    ("result",),
)
RAGFLOW_API_CALLS = Counter(
    "knowledge_uploader_ragflow_api_calls_total",
    "RAGFlow API calls grouped only by bounded operation, result and failure category.",
    ("operation", "result", "failure_category"),
)
RAGFLOW_API_STALE_STARTED = Gauge(
    "knowledge_uploader_ragflow_api_stale_started",
    "Persisted RAGFlow API calls still in started state after the bounded stale threshold.",
)
RAGFLOW_API_STALE_RECOVERED = Counter(
    "knowledge_uploader_ragflow_api_stale_recovered_total",
    "Persisted stale RAGFlow API calls recovered to a terminal unknown-failure state.",
)
OPERATIONAL_COLLECTOR_LAST_SUCCESS = Gauge(
    "knowledge_uploader_operational_collector_last_success_timestamp_seconds",
    "Unix timestamp of the last successful operational database collection.",
)
OPERATIONAL_COLLECTOR_INTERVAL_SECONDS = Gauge(
    "knowledge_uploader_operational_collector_interval_seconds",
    "Configured operational collection cadence, bounded to the supported range.",
)
OPERATIONAL_COLLECTOR_COMPONENT_ERRORS = Counter(
    "knowledge_uploader_operational_collector_component_errors_total",
    "Operational collection errors grouped only by a bounded component name.",
    ("component",),
)
OPERATIONAL_COLLECTOR_COMPONENT_LAST_SUCCESS = Gauge(
    "knowledge_uploader_operational_collector_component_last_success_timestamp_seconds",
    "Unix timestamp of the last successful collection by bounded component name.",
    ("component",),
)
EMAIL_DELIVERY_PERSISTED_TOTAL = Gauge(
    "knowledge_uploader_email_delivery_persisted_total",
    "Redis-persisted email publication and delivery results grouped by a bounded result.",
    ("result",),
)
EMAIL_DELIVERY_LAST_RESULT = Gauge(
    "knowledge_uploader_email_delivery_last_result_timestamp_seconds",
    "Unix timestamp of the last email publication or delivery result by bounded result.",
    ("result",),
)

_EVENT_FAMILIES = frozenset(
    {
        "ai",
        "auth",
        "config",
        "document",
        "notification",
        "ragflow",
        "review",
        "statistics",
        "user",
    }
)
_TASK_FAMILIES = frozenset({"outbox"})
_EXTERNAL_SERVICES = frozenset({"rabbitmq"})
_RESULTS = frozenset({"success", "failure", "timeout"})
_CONFIG_INVARIANTS = frozenset({"security.block_critical_sensitive_sync"})
_EMAIL_DELIVERY_RESULTS = EMAIL_DELIVERY_RESULTS
_OPERATIONAL_COLLECTOR_COMPONENTS = frozenset(
    {"database", "email_redis", "minio_capacity", "ragflow_call_telemetry"}
)


async def http_metrics_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    started_at = time.perf_counter()
    method = _bounded_method(request.method)
    try:
        response = await call_next(request)
    except Exception:
        route = _route_template(request)
        HTTP_REQUESTS.labels(method=method, route=route, status_class="5xx").inc()
        HTTP_DURATION.labels(method=method, route=route).observe(time.perf_counter() - started_at)
        raise
    route = _route_template(request)
    HTTP_REQUESTS.labels(
        method=method,
        route=route,
        status_class=_status_class(response.status_code),
    ).inc()
    HTTP_DURATION.labels(method=method, route=route).observe(time.perf_counter() - started_at)
    return response


def metrics_response() -> Response:
    return Response(
        content=generate_latest(),
        headers={"Content-Type": CONTENT_TYPE_LATEST},
    )


def start_metrics_server(port: int) -> None:
    if port < 1 or port > 65535:
        raise ValueError("metrics port must be between 1 and 65535")
    start_http_server(port)


def observe_outbox_publish(event_type: str, result: str) -> None:
    family = event_type.partition(".")[0]
    bounded_family = family if family in _EVENT_FAMILIES else "other"
    OUTBOX_PUBLISH.labels(
        event_family=bounded_family,
        result=_bounded_result(result),
    ).inc()


def update_outbox_health(
    *,
    pending: int,
    oldest_seconds: float,
    dead_letter_pending: int,
    dead_letter_requeued: int,
    dead_letter_resolved: int,
) -> None:
    OUTBOX_PENDING.set(max(pending, 0))
    OUTBOX_OLDEST_SECONDS.set(max(oldest_seconds, 0.0))
    OUTBOX_DEAD_LETTERS.labels(status="pending").set(max(dead_letter_pending, 0))
    OUTBOX_DEAD_LETTERS.labels(status="requeued").set(max(dead_letter_requeued, 0))
    OUTBOX_DEAD_LETTERS.labels(status="resolved").set(max(dead_letter_resolved, 0))


def observe_task_result(task_family: str, result: str) -> None:
    bounded_family = task_family if task_family in _TASK_FAMILIES else "other"
    TASK_RESULTS.labels(task_family=bounded_family, result=_bounded_result(result)).inc()


def observe_external_request(service: str, result: str) -> None:
    bounded_service = service if service in _EXTERNAL_SERVICES else "other"
    EXTERNAL_REQUESTS.labels(service=bounded_service, result=_bounded_result(result)).inc()


def observe_ragflow_api_call(
    *,
    operation: str,
    result: str,
    failure_category: str | None,
) -> None:
    bounded_operation = operation if operation in RAGFLOW_OPERATIONS else "other"
    bounded_result = result if result in RAGFLOW_COMPLETED_RESULTS else "failure"
    if bounded_result == "success":
        bounded_failure = "none"
    else:
        bounded_failure = (
            failure_category if failure_category in RAGFLOW_FAILURE_CATEGORIES else "unknown"
        )
    RAGFLOW_API_CALLS.labels(
        operation=bounded_operation,
        result=bounded_result,
        failure_category=bounded_failure,
    ).inc()


def update_ragflow_call_telemetry_health(*, stale_started: int, recovered: int) -> None:
    RAGFLOW_API_STALE_STARTED.set(max(stale_started, 0))
    recovered_count = max(recovered, 0)
    if recovered_count:
        RAGFLOW_API_STALE_RECOVERED.inc(recovered_count)


def set_logical_document_references_bytes(value: int) -> None:
    LOGICAL_DOCUMENT_REFERENCES_BYTES.labels(backend="minio").set(max(value, 0))


def set_postgres_database_size_bytes(value: int) -> None:
    POSTGRES_DATABASE_SIZE_BYTES.set(max(value, 0))


def update_db_pool(*, size: int, checked_out: int, overflow: int) -> None:
    OPERATIONAL_COLLECTOR_DB_POOL_CONNECTIONS.labels(state="size").set(max(size, 0))
    OPERATIONAL_COLLECTOR_DB_POOL_CONNECTIONS.labels(state="checked_out").set(max(checked_out, 0))
    OPERATIONAL_COLLECTOR_DB_POOL_CONNECTIONS.labels(state="overflow").set(max(overflow, 0))


def observe_config_invariant_violation(config_key: str) -> None:
    bounded_key = config_key if config_key in _CONFIG_INVARIANTS else "other"
    CONFIG_INVARIANT_VIOLATIONS.labels(config_key=bounded_key).inc()


def update_ready_probe(*, ready: bool, consecutive_failures: int) -> None:
    SYSTEM_READY.set(1 if ready else 0)
    SYSTEM_READY_CONSECUTIVE_FAILURES.set(max(consecutive_failures, 0))


def update_operational_snapshot(
    *,
    review_overdue: int,
    ragflow_success: int,
    ragflow_failure: int,
    ragflow_canceled: int,
    minio_bytes: int,
    postgres_bytes: int,
    email_delivery_totals: Mapping[str, int],
    email_delivery_last_timestamps: Mapping[str, float],
    collected_at_timestamp: float,
) -> None:
    update_operational_database_snapshot(
        review_overdue=review_overdue,
        ragflow_success=ragflow_success,
        ragflow_failure=ragflow_failure,
        ragflow_canceled=ragflow_canceled,
        minio_bytes=minio_bytes,
        postgres_bytes=postgres_bytes,
        collected_at_timestamp=collected_at_timestamp,
    )
    update_email_delivery_snapshot(
        totals=email_delivery_totals,
        last_timestamps=email_delivery_last_timestamps,
    )


def update_operational_database_snapshot(
    *,
    review_overdue: int,
    ragflow_success: int,
    ragflow_failure: int,
    ragflow_canceled: int,
    minio_bytes: int,
    postgres_bytes: int,
    collected_at_timestamp: float,
) -> None:
    REVIEW_OVERDUE.set(max(review_overdue, 0))
    RAGFLOW_SYNC_OUTCOMES_WINDOW.labels(result="success").set(max(ragflow_success, 0))
    RAGFLOW_SYNC_OUTCOMES_WINDOW.labels(result="failure").set(max(ragflow_failure, 0))
    RAGFLOW_SYNC_OUTCOMES_WINDOW.labels(result="canceled").set(max(ragflow_canceled, 0))
    set_logical_document_references_bytes(minio_bytes)
    set_postgres_database_size_bytes(postgres_bytes)
    OPERATIONAL_COLLECTOR_LAST_SUCCESS.set(max(collected_at_timestamp, 0.0))


def set_operational_collector_interval(interval_seconds: int) -> None:
    OPERATIONAL_COLLECTOR_INTERVAL_SECONDS.set(min(max(interval_seconds, 5), 3600))


def update_email_delivery_snapshot(
    *,
    totals: Mapping[str, int],
    last_timestamps: Mapping[str, float],
) -> None:
    for result in _EMAIL_DELIVERY_RESULTS:
        EMAIL_DELIVERY_PERSISTED_TOTAL.labels(result=result).set(max(totals.get(result, 0), 0))
        EMAIL_DELIVERY_LAST_RESULT.labels(result=result).set(
            max(last_timestamps.get(result, 0.0), 0.0)
        )


def observe_operational_collector_component(
    component: str,
    *,
    succeeded: bool,
    timestamp: float | None = None,
) -> None:
    bounded_component = component if component in _OPERATIONAL_COLLECTOR_COMPONENTS else "other"
    if succeeded:
        observed_at = time.time() if timestamp is None else timestamp
        OPERATIONAL_COLLECTOR_COMPONENT_LAST_SUCCESS.labels(component=bounded_component).set(
            max(observed_at, 0.0)
        )
        return
    OPERATIONAL_COLLECTOR_COMPONENT_ERRORS.labels(component=bounded_component).inc()


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    route_path = getattr(route, "path", None)
    return route_path if isinstance(route_path, str) and route_path.startswith("/") else "unmatched"


def _bounded_method(method: str) -> str:
    normalized = method.upper()
    if normalized in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
        return normalized
    return "OTHER"


def _status_class(status_code: int) -> str:
    if 100 <= status_code <= 599:
        return f"{status_code // 100}xx"
    return "other"


def _bounded_result(result: str) -> str:
    return result if result in _RESULTS else "failure"
