from __future__ import annotations

import base64
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy import CheckConstraint, Table
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.llm.base import LLMCompletion, LLMUsage
from app.core import metrics
from app.core.config import Settings
from app.core.minio_capacity_telemetry import (
    MinioCapacityCollectionError,
    MinioCapacityMeasurement,
    _metrics_headers,
    _metrics_url,
    _read_limited_metrics_body,
    _should_persist_snapshot,
    _single_cluster_value,
)
from app.core.ragflow_call_telemetry import _validate_completion
from app.core.ragflow_metrics_contract import (
    RAGFLOW_FAILURE_CATEGORIES,
    RAGFLOW_OPERATIONS,
    RAGFLOW_PERSISTED_RESULTS,
)
from app.modules.ai.cost_governance import (
    CostObservation,
    aggregate_cost_observation,
    merge_cost_status,
    observe_llm_cost,
    pricing_confirmation_basis,
    pricing_confirmation_is_effective,
    resolve_create_pricing_configured,
    resolve_update_pricing_configured,
)
from app.modules.ai.llm_analysis import (
    MAX_POSTGRES_BIGINT,
    LLMInputProvenance,
)
from app.modules.ai.schemas import AiProviderUpdateRequest
from app.modules.ai.service import (  # noqa: TID251 - direct service regression
    AiAnalysisService,
    LLMAnalysisConfigurationError,
    _pricing_fields_submitted,
    _provider_update_audit_fields,
)
from app.modules.document.models import File
from app.modules.governance_metrics import exceptions
from app.modules.governance_metrics import service as governance_service_module
from app.modules.governance_metrics.repository import (
    _PROCESSING_STAGE_STATUSES,
    _RETAINED_INACTIVE_STATUSES,
    CapacityAggregate,
    GovernanceMetricsRepository,
    LlmUsageAggregate,
    MetricsRange,
    PhysicalSnapshotRow,
    RagflowUsageAggregate,
)
from app.modules.governance_metrics.service import (
    _CAPACITY_DIMENSION_LABELS,
    _RAGFLOW_DIMENSION_LABELS,
    GovernanceMetricsService,
    MetricsQuery,
    _build_llm_rows,
    _capacity_row,
    _physical_capacity,
    _ragflow_row,
)
from app.modules.user.schemas import AuthUserRecord


class _EmptyMappingsResult:
    def mappings(self) -> list[dict[str, object]]:
        return []

    def scalar_one(self) -> int:
        return 0


class _CapturingSession:
    def __init__(self) -> None:
        self.statement: object | None = None

    async def execute(self, statement: object) -> _EmptyMappingsResult:
        self.statement = statement
        return _EmptyMappingsResult()


class _MetricCounter:
    def __init__(self) -> None:
        self.labels_seen: list[dict[str, str]] = []

    def labels(self, **labels: str) -> _MetricCounter:
        self.labels_seen.append(labels)
        return self

    def inc(self) -> None:
        return None


class _UsageRepository:
    def __init__(self) -> None:
        self.logs: list[Any] = []

    async def next_usage_call_sequence(
        self,
        *,
        analysis_id: uuid.UUID,
        analysis_attempt: int,
    ) -> int:
        assert analysis_id
        assert analysis_attempt == 1
        return 1

    async def add_usage_log(self, log: Any) -> None:
        self.logs.append(log)


def _user(role: str) -> AuthUserRecord:
    department_id = uuid.uuid4()
    return AuthUserRecord(
        id=uuid.uuid4(),
        name="tester",
        email="tester@example.com",
        role=role,
        status="active",
        email_verified=True,
        department_id=department_id,
        department_name="测试部",
        department_code="test",
        department="测试部",
        phone=None,
        email_domain="example.com",
        password_hash="x",
        failed_login_count=0,
        locked_until=None,
        session_version=1,
    )


def test_pricing_confirmation_compatibility_is_explicit_and_fail_safe() -> None:
    assert resolve_create_pricing_configured(
        explicit=None,
        input_price_microunits_per_million_tokens=5,
        output_price_microunits_per_million_tokens=0,
    )
    assert not resolve_create_pricing_configured(
        explicit=None,
        input_price_microunits_per_million_tokens=0,
        output_price_microunits_per_million_tokens=0,
    )
    assert resolve_create_pricing_configured(
        explicit=True,
        input_price_microunits_per_million_tokens=0,
        output_price_microunits_per_million_tokens=0,
    )
    assert not resolve_create_pricing_configured(
        explicit=False,
        input_price_microunits_per_million_tokens=5,
        output_price_microunits_per_million_tokens=5,
    )

    assert resolve_update_pricing_configured(
        explicit=None,
        previous=False,
        pricing_fields_submitted=True,
        input_price_microunits_per_million_tokens=1,
        output_price_microunits_per_million_tokens=0,
    )
    assert resolve_update_pricing_configured(
        explicit=None,
        previous=True,
        pricing_fields_submitted=True,
        input_price_microunits_per_million_tokens=0,
        output_price_microunits_per_million_tokens=0,
    )
    assert not resolve_update_pricing_configured(
        explicit=False,
        previous=True,
        pricing_fields_submitted=True,
        input_price_microunits_per_million_tokens=100,
        output_price_microunits_per_million_tokens=100,
    )


@pytest.mark.parametrize(
    ("payload", "expected_submission", "expected_audit_fields"),
    [
        (
            {
                "input_price_microunits_per_million_tokens": None,
                "output_price_microunits_per_million_tokens": None,
                "pricing_currency": None,
            },
            False,
            [],
        ),
        ({"pricing_currency": None, "priority": 78}, False, ["priority"]),
        (
            {"pricing_currency": None, "pricing_configured": True},
            False,
            ["pricing_configured"],
        ),
        ({"pricing_currency": "CNY"}, True, ["pricing_currency"]),
        (
            {"input_price_microunits_per_million_tokens": 0},
            True,
            ["input_price_microunits_per_million_tokens"],
        ),
        (
            {
                "base_url": None,
                "chat_model": None,
                "max_input_tokens": None,
                "max_output_tokens": None,
                "top_p": None,
            },
            False,
            [
                "base_url",
                "chat_model",
                "max_input_tokens",
                "max_output_tokens",
                "top_p",
            ],
        ),
        ({"api_key": "sk-secret"}, False, ["api_key_rotated"]),
        ({"clear_api_key": True}, False, ["api_key_cleared"]),
        ({"api_key": "sk-secret", "clear_api_key": True}, False, ["api_key_cleared"]),
    ],
)
def test_null_pricing_patch_fields_are_noops_for_confirmation_and_audit(
    payload: dict[str, object],
    expected_submission: bool,
    expected_audit_fields: list[str],
) -> None:
    request = AiProviderUpdateRequest.model_validate(payload)
    assert _pricing_fields_submitted(request) is expected_submission
    assert _provider_update_audit_fields(request) == expected_audit_fields


@pytest.mark.parametrize(
    ("declared", "confirmed_input", "confirmed_output", "confirmed_currency", "expected"),
    [
        (True, 0, 0, "USD", True),
        (True, 10, 20, "USD", True),
        (False, 10, 20, "USD", False),
        (True, None, 20, "USD", False),
        (True, 10, None, "USD", False),
        (True, 10, 20, None, False),
        (True, 11, 20, "USD", False),
        (True, 10, 21, "USD", False),
        (True, 10, 20, "CNY", False),
    ],
)
def test_pricing_confirmation_requires_an_exact_complete_snapshot(
    declared: bool,
    confirmed_input: int | None,
    confirmed_output: int | None,
    confirmed_currency: str | None,
    expected: bool,
) -> None:
    assert (
        pricing_confirmation_is_effective(
            declared=declared,
            input_price_microunits_per_million_tokens=10 if confirmed_input != 0 else 0,
            output_price_microunits_per_million_tokens=20 if confirmed_output != 0 else 0,
            pricing_currency="USD",
            confirmed_input_microunits_per_million=confirmed_input,
            confirmed_output_microunits_per_million=confirmed_output,
            confirmed_currency=confirmed_currency,
        )
        is expected
    )


def test_pricing_confirmation_basis_supports_explicit_zero_and_clear() -> None:
    assert pricing_confirmation_basis(
        configured=True,
        input_price_microunits_per_million_tokens=0,
        output_price_microunits_per_million_tokens=0,
        pricing_currency="CNY",
    ) == (0, 0, "CNY")
    assert pricing_confirmation_basis(
        configured=False,
        input_price_microunits_per_million_tokens=10,
        output_price_microunits_per_million_tokens=20,
        pricing_currency="USD",
    ) == (None, None, None)


def test_cost_observation_distinguishes_explicit_free_unconfigured_and_missing_usage() -> None:
    explicit_free = observe_llm_cost(
        pricing_configured=True,
        prompt_tokens=10,
        completion_tokens=20,
        input_price_microunits_per_million_tokens=0,
        output_price_microunits_per_million_tokens=0,
    )
    unconfigured = observe_llm_cost(
        pricing_configured=False,
        prompt_tokens=10,
        completion_tokens=20,
        input_price_microunits_per_million_tokens=0,
        output_price_microunits_per_million_tokens=0,
    )
    unconfirmed_nonzero = observe_llm_cost(
        pricing_configured=False,
        prompt_tokens=10,
        completion_tokens=20,
        input_price_microunits_per_million_tokens=100,
        output_price_microunits_per_million_tokens=200,
    )
    missing_usage = observe_llm_cost(
        pricing_configured=True,
        prompt_tokens=None,
        completion_tokens=20,
        input_price_microunits_per_million_tokens=100,
        output_price_microunits_per_million_tokens=100,
    )

    assert explicit_free.status == "known"
    assert explicit_free.estimated_cost_microunits == 0
    assert unconfigured.status == "unknown_pricing"
    assert unconfigured.estimated_cost_microunits is None
    assert unconfirmed_nonzero == CostObservation(
        status="unknown_pricing",
        estimated_cost_microunits=None,
    )
    assert missing_usage.status == "unknown_usage"
    assert missing_usage.estimated_cost_microunits is None


def test_cost_observation_converts_invalid_or_unrepresentable_inputs_to_unknown() -> None:
    invalid_usage = observe_llm_cost(
        pricing_configured=True,
        prompt_tokens=-1,
        completion_tokens=1,
        input_price_microunits_per_million_tokens=1,
        output_price_microunits_per_million_tokens=1,
    )
    invalid_pricing = observe_llm_cost(
        pricing_configured=True,
        prompt_tokens=1,
        completion_tokens=1,
        input_price_microunits_per_million_tokens=-1,
        output_price_microunits_per_million_tokens=1,
    )
    unrepresentable_cost = observe_llm_cost(
        pricing_configured=True,
        prompt_tokens=MAX_POSTGRES_BIGINT * 10,
        completion_tokens=1,
        input_price_microunits_per_million_tokens=1_000_000_000_000,
        output_price_microunits_per_million_tokens=1_000_000_000_000,
    )

    assert invalid_usage == CostObservation(
        status="unknown_usage",
        estimated_cost_microunits=None,
    )
    assert invalid_pricing == CostObservation(
        status="unknown_pricing",
        estimated_cost_microunits=None,
    )
    assert unrepresentable_cost == CostObservation(
        status="unknown_usage",
        estimated_cost_microunits=None,
    )


def test_cost_status_merge_never_washes_unknown_back_to_known() -> None:
    assert merge_cost_status("unknown_pricing", "known") == "unknown_pricing"
    assert merge_cost_status("known", "unknown_pricing") == "unknown_pricing"
    assert merge_cost_status("unknown_usage", "unknown_pricing") == "unknown_usage"
    assert merge_cost_status("legacy_unverifiable", "known") == "legacy_unverifiable"


def test_aggregate_cost_never_adds_different_currencies() -> None:
    call = CostObservation(status="known", estimated_cost_microunits=123)

    assert (
        aggregate_cost_observation(
            call_observation=call, aggregate_currency="USD", call_currency="USD"
        )
        == call
    )
    mixed = aggregate_cost_observation(
        call_observation=call, aggregate_currency="USD", call_currency="CNY"
    )
    assert mixed.status == "unknown_pricing"
    assert mixed.estimated_cost_microunits is None


@pytest.mark.asyncio
async def test_usage_overflow_is_persisted_as_unknown_without_writing_oversized_counters() -> None:
    repository = _UsageRepository()
    service = AiAnalysisService(
        session=cast(AsyncSession, SimpleNamespace()),
        repository=cast(Any, repository),
        settings=cast(Settings, SimpleNamespace()),
    )
    file = SimpleNamespace(id=uuid.uuid4())
    analysis = SimpleNamespace(
        id=uuid.uuid4(),
        attempt_number=1,
        cost_currency="USD",
        prompt_tokens=0,
        completion_tokens=0,
        latency_ms=0,
        cost_status="known",
        estimated_cost_microunits=0,
        failure_category=None,
    )
    provider = SimpleNamespace(
        id=uuid.uuid4(),
        name="overflow-safe-provider",
        chat_model="model",
        pricing_configured=True,
        pricing_confirmed_input_microunits_per_million=1_000_000_000_000,
        pricing_confirmed_output_microunits_per_million=1_000_000_000_000,
        pricing_confirmed_currency="USD",
        input_price_microunits_per_million_tokens=1_000_000_000_000,
        output_price_microunits_per_million_tokens=1_000_000_000_000,
        pricing_currency="USD",
    )
    prompt_template = SimpleNamespace(
        id=uuid.uuid4(),
        template_key="analysis",
        version=1,
    )
    completion = LLMCompletion(
        content="{}",
        model="model",
        usage=LLMUsage(
            prompt_tokens=MAX_POSTGRES_BIGINT * 10,
            completion_tokens=1,
        ),
        latency_ms=5,
    )

    with pytest.raises(LLMAnalysisConfigurationError, match="usage aggregate overflow"):
        await service._record_llm_usage(
            file=cast(Any, file),
            analysis=cast(Any, analysis),
            provider=cast(Any, provider),
            prompt_template=cast(Any, prompt_template),
            completion=completion,
            input_provenance=LLMInputProvenance(2, "a" * 64, 0, False),
            latency_ms=completion.latency_ms,
            status="success",
            failure_category=None,
        )

    assert len(repository.logs) == 1
    usage_log = repository.logs[0]
    assert usage_log.prompt_tokens is None
    assert usage_log.completion_tokens == 1
    assert usage_log.latency_ms == 5
    assert usage_log.estimated_cost_microunits == 0
    assert usage_log.cost_status == "unknown_usage"
    assert usage_log.status == "failed"
    assert usage_log.failure_category == "usage_overflow"
    assert analysis.estimated_cost_microunits == 0
    assert analysis.cost_status == "unknown_usage"
    assert analysis.failure_category == "usage_overflow"


@pytest.mark.asyncio
@pytest.mark.parametrize(("current_input", "current_output"), [(101, 200), (100, 201)])
async def test_usage_worker_fails_closed_when_legacy_writer_drifts_a_confirmed_price(
    current_input: int,
    current_output: int,
) -> None:
    repository = _UsageRepository()
    service = AiAnalysisService(
        session=cast(AsyncSession, SimpleNamespace()),
        repository=cast(Any, repository),
        settings=cast(Settings, SimpleNamespace()),
    )
    file = SimpleNamespace(id=uuid.uuid4())
    analysis = SimpleNamespace(
        id=uuid.uuid4(),
        attempt_number=1,
        cost_currency="USD",
        prompt_tokens=0,
        completion_tokens=0,
        latency_ms=0,
        cost_status="known",
        estimated_cost_microunits=0,
        failure_category=None,
    )
    provider = SimpleNamespace(
        id=uuid.uuid4(),
        name="drifted-provider",
        chat_model="model",
        pricing_configured=True,
        pricing_confirmed_input_microunits_per_million=100,
        pricing_confirmed_output_microunits_per_million=200,
        pricing_confirmed_currency="USD",
        input_price_microunits_per_million_tokens=current_input,
        output_price_microunits_per_million_tokens=current_output,
        pricing_currency="USD",
    )
    prompt_template = SimpleNamespace(
        id=uuid.uuid4(),
        template_key="analysis",
        version=1,
    )
    completion = LLMCompletion(
        content="{}",
        model="model",
        usage=LLMUsage(prompt_tokens=10, completion_tokens=20),
        latency_ms=5,
    )

    await service._record_llm_usage(
        file=cast(Any, file),
        analysis=cast(Any, analysis),
        provider=cast(Any, provider),
        prompt_template=cast(Any, prompt_template),
        completion=completion,
        input_provenance=LLMInputProvenance(2, "a" * 64, 0, False),
        latency_ms=completion.latency_ms,
        status="success",
        failure_category=None,
    )

    assert len(repository.logs) == 1
    usage_log = repository.logs[0]
    assert usage_log.cost_status == "unknown_pricing"
    assert usage_log.estimated_cost_microunits == 0
    assert analysis.cost_status == "unknown_pricing"
    assert analysis.estimated_cost_microunits == 0


def test_llm_aggregation_keeps_known_currency_and_unknown_buckets_separate() -> None:
    rows = _build_llm_rows(
        [
            LlmUsageAggregate("all", "全部", "known", "USD", 2, 10, 20, 0, 123),
            LlmUsageAggregate("all", "全部", "known", "CNY", 1, 3, 4, 0, 0),
            LlmUsageAggregate("all", "全部", "unknown_pricing", "USD", 5, 50, 60, 0, None),
            LlmUsageAggregate("all", "全部", "unknown_usage", "USD", 7, 8, 9, 4, None),
        ]
    )

    assert len(rows) == 1
    row = rows[0]
    assert row.total_calls == "15"
    assert [(item.currency, item.estimated_cost_microunits) for item in row.known_costs] == [
        ("CNY", "0"),
        ("USD", "123"),
    ]
    assert [(item.status, item.calls) for item in row.unknown_costs] == [
        ("unknown_pricing", "5"),
        ("unknown_usage", "7"),
    ]
    assert row.unknown_costs[1].calls_with_unknown_tokens == "4"


def test_capacity_response_uses_decimal_strings_without_fake_physical_allocation() -> None:
    value = 9_223_372_036_854_775_000
    row = _capacity_row(
        CapacityAggregate(
            dimension_key="all",
            dimension_label="全部",
            file_count=4,
            active_logical_bytes=value,
            retained_inactive_bytes=321,
            total_referenced_bytes=value + 321,
        )
    )

    assert row.file_count == "4"
    assert row.active_logical_bytes == str(value)
    assert row.retained_inactive_bytes == "321"
    assert row.total_referenced_bytes == str(value + 321)
    assert "idx_files_uploaded_at" in {index.name for index in cast(Table, File.__table__).indexes}


def test_governance_machine_dimensions_have_complete_localized_labels() -> None:
    capacity = _capacity_row(
        CapacityAggregate(
            dimension_key="draft",
            dimension_label="draft",
            file_count=1,
            active_logical_bytes=10,
            retained_inactive_bytes=0,
            total_referenced_bytes=10,
        ),
        group_by="processing_stage",
    )
    ragflow = _ragflow_row(
        RagflowUsageAggregate(
            dimension_key="failure",
            dimension_label="failure",
            calls=1,
            completed_calls=1,
            failure_calls=1,
            in_progress_calls=0,
            total_latency_ms=50,
        ),
        group_by="result",
    )

    assert capacity.dimension_label == "草稿"
    assert ragflow.dimension_label == "失败"
    assert set(_CAPACITY_DIMENSION_LABELS["processing_stage"]) == set(
        _PROCESSING_STAGE_STATUSES
    ) | {"unknown"}
    assert set(_RAGFLOW_DIMENSION_LABELS["operation"]) == set(RAGFLOW_OPERATIONS) | {"other"}
    assert set(_RAGFLOW_DIMENSION_LABELS["result"]) == set(RAGFLOW_PERSISTED_RESULTS)
    assert set(_RAGFLOW_DIMENSION_LABELS["failure_category"]) == set(RAGFLOW_FAILURE_CATEGORIES) | {
        "none"
    }


@pytest.mark.parametrize(
    ("group_by", "dimension_key", "dimension_label"),
    [
        ("none", "all", "全部"),
        ("operation", "ping", "ping"),
        ("result", "success", "success"),
        ("failure_category", "none", "none"),
        ("department", str(uuid.uuid4()), "治理部"),
        ("day", "2026-07-17", "2026-07-17"),
    ],
)
def test_ragflow_aggregate_conservation_fails_closed_for_every_grouping(
    group_by: Any,
    dimension_key: str,
    dimension_label: str,
) -> None:
    raw_secret = "tenant-secret-dimension"
    row = RagflowUsageAggregate(
        dimension_key=dimension_key,
        dimension_label=f"{dimension_label}-{raw_secret}",
        calls=3,
        completed_calls=1,
        failure_calls=0,
        in_progress_calls=1,
        total_latency_ms=10,
    )

    with pytest.raises(exceptions.GovernanceMetricsError) as raised:
        _ragflow_row(row, group_by=group_by)

    assert raised.value.status_code == 500
    assert raised.value.error_code == "INTERNAL_ERROR"
    assert raised.value.message == "governance metrics aggregate invariant violation"
    assert raw_secret not in str(raised.value)


@pytest.mark.parametrize(
    "invalid_field",
    [
        "calls",
        "completed_calls",
        "failure_calls",
        "in_progress_calls",
        "total_latency_ms",
    ],
)
def test_ragflow_aggregate_rejects_negative_values(invalid_field: str) -> None:
    values = {
        "dimension_key": "all",
        "dimension_label": "全部",
        "calls": 2,
        "completed_calls": 1,
        "failure_calls": 0,
        "in_progress_calls": 1,
        "total_latency_ms": 10,
    }
    values[invalid_field] = -1

    with pytest.raises(exceptions.GovernanceMetricsError, match="aggregate invariant"):
        _ragflow_row(RagflowUsageAggregate(**cast(Any, values)))


def test_ragflow_aggregate_rejects_failure_count_above_completed_count() -> None:
    with pytest.raises(exceptions.GovernanceMetricsError, match="aggregate invariant"):
        _ragflow_row(
            RagflowUsageAggregate(
                dimension_key="all",
                dimension_label="全部",
                calls=2,
                completed_calls=1,
                failure_calls=2,
                in_progress_calls=1,
                total_latency_ms=10,
            )
        )


@pytest.mark.parametrize("dimension_key", ["unsupported", None])
def test_ragflow_result_group_rejects_unknown_and_null_buckets(dimension_key: object) -> None:
    with pytest.raises(exceptions.GovernanceMetricsError, match="aggregate invariant"):
        _ragflow_row(
            RagflowUsageAggregate(
                dimension_key=cast(Any, dimension_key),
                dimension_label="untrusted-result",
                calls=1,
                completed_calls=1,
                failure_calls=0,
                in_progress_calls=0,
                total_latency_ms=1,
            ),
            group_by="result",
        )


@pytest.mark.parametrize(
    ("result", "completed", "failed", "in_progress"),
    [
        ("started", 0, 0, 2),
        ("success", 2, 0, 0),
        ("failure", 2, 2, 0),
    ],
)
def test_ragflow_result_group_accepts_only_coherent_persisted_buckets(
    result: str,
    completed: int,
    failed: int,
    in_progress: int,
) -> None:
    row = _ragflow_row(
        RagflowUsageAggregate(
            dimension_key=result,
            dimension_label=result,
            calls=2,
            completed_calls=completed,
            failure_calls=failed,
            in_progress_calls=in_progress,
            total_latency_ms=10,
        ),
        group_by="result",
    )

    assert row.calls == "2"


def test_ragflow_aggregate_preserves_large_integer_strings() -> None:
    large = 10**30
    row = _ragflow_row(
        RagflowUsageAggregate(
            dimension_key="all",
            dimension_label="全部",
            calls=large,
            completed_calls=large,
            failure_calls=large,
            in_progress_calls=0,
            total_latency_ms=large,
        )
    )

    assert row.calls == str(large)
    assert row.failure_calls == str(large)
    assert row.total_latency_ms == str(large)


@pytest.mark.asyncio
async def test_capacity_query_excludes_every_retained_inactive_status_from_active() -> None:
    assert _RETAINED_INACTIVE_STATUSES == (
        "disabled",
        "deleted",
        "ragflow_cleanup_failed",
    )
    session = _CapturingSession()
    repository = GovernanceMetricsRepository(cast(AsyncSession, session))
    now = datetime(2026, 7, 17, tzinfo=UTC)
    await repository.capacity(
        metrics_range=MetricsRange(start_at=now - timedelta(days=1), end_before=now),
        group_by="none",
        page=1,
        page_size=20,
    )

    assert session.statement is not None
    sql = str(
        cast(Any, session.statement).compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "files.status NOT IN ('disabled', 'deleted', 'ragflow_cleanup_failed')" in sql
    assert "retained_inactive_bytes" in sql


@pytest.mark.asyncio
async def test_governance_group_pagination_is_applied_in_postgresql() -> None:
    session = _CapturingSession()
    repository = GovernanceMetricsRepository(cast(AsyncSession, session))
    end_before = datetime(2026, 7, 17, tzinfo=UTC)
    metrics_range = MetricsRange(
        start_at=end_before - timedelta(days=30),
        end_before=end_before,
    )
    statements: list[object] = []

    await repository.capacity(
        metrics_range=metrics_range,
        group_by="processing_stage",
        page=3,
        page_size=7,
    )
    statements.append(session.statement)
    await repository.llm_usage(
        metrics_range=metrics_range,
        group_by="provider",
        page=3,
        page_size=7,
    )
    statements.append(session.statement)
    await repository.ragflow_usage(
        metrics_range=metrics_range,
        group_by="result",
        page=3,
        page_size=7,
    )
    statements.append(session.statement)

    for statement in statements:
        assert statement is not None
        sql = str(
            cast(Any, statement).compile(
                dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
                compile_kwargs={"literal_binds": True},
            )
        )
        assert "LIMIT 7 OFFSET 14" in sql


def test_capacity_processing_stage_mapping_covers_every_persisted_file_status() -> None:
    status_constraint = next(
        constraint
        for constraint in cast(Table, File.__table__).constraints
        if isinstance(constraint, CheckConstraint) and constraint.name == "ck_files_status"
    )
    expected_statuses = set(re.findall(r"'([^']+)'", str(status_constraint.sqltext)))
    mapped_statuses = [
        status for statuses in _PROCESSING_STAGE_STATUSES.values() for status in statuses
    ]

    assert set(mapped_statuses) == expected_statuses
    assert len(mapped_statuses) == len(expected_statuses)
    assert _PROCESSING_STAGE_STATUSES["review"] == (
        "sensitive_review_required",
        "pending_review",
        "approved",
        "rejected",
    )


@pytest.mark.asyncio
async def test_day_grouping_is_explicitly_utc_for_every_governance_metric() -> None:
    session = _CapturingSession()
    repository = GovernanceMetricsRepository(cast(AsyncSession, session))
    end_before = datetime(2026, 7, 17, tzinfo=UTC)
    metrics_range = MetricsRange(
        start_at=end_before - timedelta(days=1),
        end_before=end_before,
    )
    statements: list[object] = []
    await repository.capacity(
        metrics_range=metrics_range,
        group_by="day",
        page=1,
        page_size=20,
    )
    statements.append(session.statement)
    await repository.llm_usage(
        metrics_range=metrics_range,
        group_by="day",
        page=1,
        page_size=20,
    )
    statements.append(session.statement)
    await repository.ragflow_usage(
        metrics_range=metrics_range,
        group_by="day",
        page=1,
        page_size=20,
    )
    statements.append(session.statement)

    for statement in statements:
        assert statement is not None
        sql = str(
            cast(Any, statement).compile(
                dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
                compile_kwargs={"literal_binds": True},
            )
        )
        assert "date_trunc('day', timezone('UTC'," in sql


def test_physical_capacity_reports_fresh_stale_future_and_unsupported_truthfully() -> None:
    now = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)
    fresh = PhysicalSnapshotRow(
        source_kind="minio_cluster_metrics",
        total_bytes=100,
        used_bytes=60,
        free_bytes=40,
        captured_at=now - timedelta(minutes=14),
        collected_at=now - timedelta(minutes=13),
    )
    stale = PhysicalSnapshotRow(
        source_kind="minio_cluster_metrics",
        total_bytes=100,
        used_bytes=60,
        free_bytes=40,
        captured_at=now - timedelta(minutes=16),
        collected_at=now - timedelta(minutes=15),
    )
    future = PhysicalSnapshotRow(
        source_kind="minio_cluster_metrics",
        total_bytes=100,
        used_bytes=60,
        free_bytes=40,
        captured_at=now + timedelta(minutes=2),
        collected_at=now + timedelta(minutes=2),
    )

    fresh_response = _physical_capacity(fresh, physical_dimension="cluster", now=now)
    assert fresh_response.status == "available"
    assert fresh_response.requested_dimension == "cluster"
    assert fresh_response.measurement_basis == "minio_raw_cluster_capacity"
    assert _physical_capacity(stale, physical_dimension="cluster", now=now).status == "stale"
    future_response = _physical_capacity(future, physical_dimension="cluster", now=now)
    assert future_response.status == "unavailable"
    assert future_response.total_bytes is None
    assert future_response.measurement_basis is None
    unsupported = _physical_capacity(fresh, physical_dimension="department", now=now)
    assert unsupported.status == "unsupported_dimension"
    assert unsupported.requested_dimension == "department"
    assert unsupported.total_bytes is None


def test_minio_parser_handles_whitespace_timestamp_and_rejects_inconsistent_reporters() -> None:
    metric = "minio_cluster_capacity_raw_total_bytes"
    valid = f'{metric}{{server="a"}}    1000 123\n{metric}{{server="b"}}\t1000\t456\n'
    assert _single_cluster_value(valid, metric) == 1000

    with pytest.raises(MinioCapacityCollectionError):
        _single_cluster_value(f'{metric}{{server="a"}} 1000\n{metric}{{server="b"}} 999\n', metric)
    with pytest.raises(MinioCapacityCollectionError):
        _single_cluster_value(f"{metric} NaN\n", metric)


class _StreamingMetricsResponse:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def aiter_bytes(self) -> Any:
        for chunk in self._chunks:
            yield chunk


@pytest.mark.asyncio
async def test_minio_metrics_body_is_bounded_while_streaming(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.core.minio_capacity_telemetry._MAX_METRICS_RESPONSE_BYTES",
        5,
    )
    response = cast(Any, _StreamingMetricsResponse([b"abc", b"def"]))

    with pytest.raises(MinioCapacityCollectionError, match="response size"):
        await _read_limited_metrics_body(response)

    bounded = cast(Any, _StreamingMetricsResponse([b"ab", b"cd"]))
    assert await _read_limited_metrics_body(bounded) == b"abcd"


def test_minio_snapshot_sampling_skips_identical_30_second_poll_but_records_change_or_5m() -> None:
    captured = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)
    measurement = MinioCapacityMeasurement(100, 60, 40, "a" * 64, captured)
    latest: dict[str, object] = {
        "total_bytes": 100,
        "used_bytes": 60,
        "free_bytes": 40,
        "collected_at": captured,
    }

    assert not _should_persist_snapshot(measurement, latest, captured + timedelta(seconds=30))
    assert _should_persist_snapshot(measurement, latest, captured + timedelta(minutes=5))
    changed = MinioCapacityMeasurement(100, 61, 39, "b" * 64, captured)
    assert _should_persist_snapshot(changed, latest, captured + timedelta(seconds=30))
    future = dict(latest)
    future["collected_at"] = captured + timedelta(minutes=2)
    with pytest.raises(MinioCapacityCollectionError):
        _should_persist_snapshot(measurement, future, captured)


def test_minio_metrics_url_rejects_credentials_or_paths() -> None:
    valid = cast(
        Settings,
        SimpleNamespace(minio_endpoint="minio.internal:9000", minio_secure=True),
    )
    assert _metrics_url(valid) == "https://minio.internal:9000/minio/v2/metrics/cluster"

    for endpoint in ("user:pass@minio:9000", "minio:9000/private", "minio:9000?token=x"):
        invalid = cast(
            Settings,
            SimpleNamespace(minio_endpoint=endpoint, minio_secure=False),
        )
        with pytest.raises(MinioCapacityCollectionError):
            _metrics_url(invalid)


@pytest.mark.asyncio
async def test_minio_metrics_bearer_file_is_required_and_strict(tmp_path: Path) -> None:
    missing = cast(
        Settings,
        SimpleNamespace(minio_metrics_bearer_token_file=str(tmp_path / "missing-token")),
    )
    with pytest.raises(MinioCapacityCollectionError, match="unavailable"):
        await _metrics_headers(missing)

    now_seconds = int(datetime.now(UTC).timestamp())

    def base64url(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    def synthetic_jwt(
        *,
        claims: dict[str, object],
        algorithm: str = "HS256",
        signature: bytes = b"synthetic-signature",
    ) -> str:
        header = json.dumps(
            {"alg": algorithm, "typ": "JWT"},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        payload = json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return f"{base64url(header)}.{base64url(payload)}.{base64url(signature)}"

    valid_claims: dict[str, object] = {
        "sub": "minio-metrics",
        "iat": now_seconds - 1,
        "exp": now_seconds + 3600,
    }
    token = synthetic_jwt(claims=valid_claims)
    token_file = tmp_path / "minio-metrics-token"
    token_file.write_bytes(f"{token}\n".encode("ascii"))
    configured = cast(
        Settings,
        SimpleNamespace(minio_metrics_bearer_token_file=str(token_file)),
    )
    headers = await _metrics_headers(configured)
    assert headers == {
        "Accept": "text/plain",
        "Authorization": f"Bearer {token}",
    }

    missing_exp = synthetic_jwt(
        claims={
            "sub": "minio-metrics",
            "iat": now_seconds - 1,
        }
    )
    expired = synthetic_jwt(
        claims={
            "sub": "minio-metrics",
            "iat": now_seconds - 3600,
            "exp": now_seconds - 1,
        }
    )
    future_iat = synthetic_jwt(
        claims={
            "sub": "minio-metrics",
            "iat": now_seconds + 3600,
            "exp": now_seconds + 7200,
        }
    )
    invalid_payloads = (
        b"not-a-jwt\n",
        token.encode("ascii"),
        f"{token}\r\n".encode(),
        f"{token}\nextra".encode(),
        f"{token}\n\x00".encode(),
        f"{token}!\n".encode(),
        b"a" * 16_384 + b"\n",
        b"Zm9v.YmFy.YmF6\n",
        f"{synthetic_jwt(claims=valid_claims, algorithm='none')}\n".encode("ascii"),
        f"{synthetic_jwt(claims={})}\n".encode("ascii"),
        f"{missing_exp}\n".encode("ascii"),
        f"{expired}\n".encode("ascii"),
        f"{future_iat}\n".encode("ascii"),
    )
    for invalid_payload in invalid_payloads:
        token_file.write_bytes(invalid_payload)
        with pytest.raises(MinioCapacityCollectionError, match="invalid"):
            await _metrics_headers(configured)

    unconfigured = cast(
        Settings,
        SimpleNamespace(minio_metrics_bearer_token_file=""),
    )
    with pytest.raises(MinioCapacityCollectionError, match="not configured"):
        await _metrics_headers(unconfigured)


class _GovernanceAuditSession:
    def __init__(self) -> None:
        self.commit_count = 0
        self.rollback_count = 0

    async def commit(self) -> None:
        self.commit_count += 1

    async def rollback(self) -> None:
        self.rollback_count += 1


class _GovernanceAuditRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def capacity(self, **values: object) -> tuple[list[object], int]:
        self.calls.append(("capacity", values))
        return [], 0

    async def latest_physical_snapshot(self) -> None:
        return None

    async def llm_usage(self, **values: object) -> tuple[list[object], int]:
        self.calls.append(("llm_usage", values))
        return [], 0

    async def ragflow_usage(self, **values: object) -> tuple[list[object], int]:
        self.calls.append(("ragflow_usage", values))
        return [], 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "action"),
    [
        ("capacity", "statistics.capacity.view"),
        ("llm_usage", "statistics.llm_usage.view"),
        ("ragflow_usage", "statistics.ragflow_usage.view"),
    ],
)
async def test_governance_statistics_audit_is_bounded_and_committed(
    monkeypatch: pytest.MonkeyPatch,
    method_name: str,
    action: str,
) -> None:
    now = datetime(2026, 7, 17, 12, tzinfo=UTC)
    session = _GovernanceAuditSession()
    repository = _GovernanceAuditRepository()
    captured: dict[str, object] = {}

    async def fake_record_admin_audit_log(
        _session: object,
        **values: object,
    ) -> uuid.UUID:
        captured.update(values)
        return uuid.uuid4()

    monkeypatch.setattr(
        governance_service_module,
        "record_admin_audit_log",
        fake_record_admin_audit_log,
    )
    service = GovernanceMetricsService(
        session=cast(AsyncSession, session),
        repository=cast(GovernanceMetricsRepository, repository),
        now_provider=lambda: now,
    )
    call_values: dict[str, object] = {
        "current_user": _user("system_admin"),
        "query": MetricsQuery(
            start_at=now - timedelta(days=2),
            end_before=now,
            page=2,
            page_size=5,
        ),
        "group_by": "day",
        "context": governance_service_module.RequestContext(
            ip_address="127.0.0.1",
            user_agent="governance-test",
        ),
    }
    if method_name == "capacity":
        call_values["physical_dimension"] = "cluster"

    response = await getattr(service, method_name)(**call_values)

    assert response.pagination.total == 0
    assert captured["action"] == action
    assert captured["target_type"] == "statistics"
    metadata = cast(dict[str, object], captured["metadata_json"])
    assert metadata["group_by"] == "day"
    assert metadata["page"] == 2
    assert metadata["page_size"] == 5
    assert set(metadata) <= {
        "start_at",
        "end_before",
        "group_by",
        "page",
        "page_size",
        "physical_dimension",
    }
    assert session.commit_count == 1
    assert session.rollback_count == 0
    assert repository.calls[0][1]["page"] == 2
    assert repository.calls[0][1]["page_size"] == 5


@pytest.mark.asyncio
async def test_governance_statistics_fails_closed_when_audit_write_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 7, 17, 12, tzinfo=UTC)
    session = _GovernanceAuditSession()
    repository = _GovernanceAuditRepository()

    async def fail_audit(_session: object, **_values: object) -> uuid.UUID:
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(
        governance_service_module,
        "record_admin_audit_log",
        fail_audit,
    )
    service = GovernanceMetricsService(
        session=cast(AsyncSession, session),
        repository=cast(GovernanceMetricsRepository, repository),
        now_provider=lambda: now,
    )

    with pytest.raises(RuntimeError, match="audit unavailable"):
        await service.capacity(
            current_user=_user("system_admin"),
            query=MetricsQuery(start_at=now - timedelta(days=1), end_before=now),
            group_by="none",
            physical_dimension="cluster",
            context=governance_service_module.RequestContext(
                ip_address="127.0.0.1",
                user_agent="governance-test",
            ),
        )

    assert session.commit_count == 0
    assert session.rollback_count == 1


def test_statistics_service_rejects_non_system_admin_and_unbounded_windows() -> None:
    now = datetime(2026, 7, 17, tzinfo=UTC)
    service = GovernanceMetricsService(
        session=cast(AsyncSession, SimpleNamespace()),
        repository=cast(GovernanceMetricsRepository, SimpleNamespace()),
        now_provider=lambda: now,
    )

    with pytest.raises(exceptions.GovernanceMetricsError) as denied:
        service._require_system_admin(_user("dept_admin"))
    assert denied.value.status_code == 403
    service._require_system_admin(_user("system_admin"))
    with pytest.raises(exceptions.GovernanceMetricsError) as invalid:
        service._validated_range(MetricsQuery(start_at=now - timedelta(days=367), end_before=now))
    assert invalid.value.status_code == 422


def test_ragflow_labels_are_bounded_and_completion_contract_is_fixed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counter = _MetricCounter()
    monkeypatch.setattr(metrics, "RAGFLOW_API_CALLS", counter)
    metrics.observe_ragflow_api_call(
        operation="https://secret.invalid/path",
        result="payload-value",
        failure_category="raw exception text",
    )
    assert counter.labels_seen == [
        {"operation": "other", "result": "failure", "failure_category": "unknown"}
    ]

    _validate_completion(result="success", failure_category=None)
    _validate_completion(result="failure", failure_category="timeout")
    with pytest.raises(ValueError):
        _validate_completion(result="success", failure_category="timeout")
    with pytest.raises(ValueError):
        _validate_completion(result="started", failure_category=None)
