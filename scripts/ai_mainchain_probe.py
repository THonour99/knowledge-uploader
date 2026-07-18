from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.engine import make_url

API_BASE_URL = "http://backend-api:8000"
MOCK_LLM_STATE_URL = "http://mock-llm:8081/__probe/state"
PROBE_USER_AGENT = "knowledge-uploader-ai-mainchain-probe/1"
WAIT_TIMEOUT_SECONDS = 120.0
ORDINARY_EVENT_SEQUENCE = (
    "document.file.uploaded",
    "ai.text.extracted",
    "ai.file.analyzed",
    "review.file.submitted",
)
CRITICAL_EVENT_SEQUENCE = (
    "document.file.uploaded",
    "ai.text.extracted",
    "ai.file.analyzed",
    "ai.sensitive.detected",
)


class ProbeFailure(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ProbeFailure(message)


def mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProbeFailure(f"{label} is not an object")
    return value


def uuid_value(value: object, label: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ProbeFailure(f"{label} is not a UUID") from exc


async def api_data(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    token: str | None = None,
    expected_status: int = 200,
    json_body: Mapping[str, object] | None = None,
    files: Mapping[str, tuple[str, bytes, str]] | None = None,
    form: Mapping[str, str] | None = None,
) -> object:
    headers = {"User-Agent": PROBE_USER_AGENT}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    response = await client.request(
        method,
        path,
        headers=headers,
        json=json_body,
        files=files,
        data=form,
    )
    if response.status_code != expected_status:
        raise ProbeFailure(f"{method} {path} returned HTTP {response.status_code}")
    try:
        envelope: object = response.json()
    except json.JSONDecodeError as exc:
        raise ProbeFailure(f"{method} {path} returned invalid JSON") from exc
    root = mapping(envelope, f"{method} {path} envelope")
    require(root.get("success") is True, f"{method} {path} did not return success")
    return root.get("data")


async def login(client: httpx.AsyncClient, email: str, password: str) -> str:
    data = mapping(
        await api_data(
            client,
            "POST",
            "/api/auth/login",
            json_body={"email": email, "password": password},
        ),
        "login data",
    )
    token = data.get("access_token")
    require(isinstance(token, str) and bool(token), "login token is missing")
    return str(token)


async def wait_for_file_status(
    client: httpx.AsyncClient,
    *,
    token: str,
    file_id: uuid.UUID,
    expected_status: str,
) -> dict[str, Any]:
    deadline = time.monotonic() + WAIT_TIMEOUT_SECONDS
    last_status = "unknown"
    while time.monotonic() < deadline:
        file_data = mapping(
            await api_data(
                client,
                "GET",
                f"/api/files/{file_id}",
                token=token,
            ),
            "file detail",
        )
        last_status = str(file_data.get("status", "unknown"))
        if last_status == expected_status:
            return file_data
        if last_status in {"analysis_failed", "failed", "deleted", "disabled"}:
            raise ProbeFailure(f"file entered unexpected terminal status {last_status}")
        await asyncio.sleep(0.25)
    raise ProbeFailure(
        f"file did not reach {expected_status}; last observed status was {last_status}"
    )


async def run_public_api_flow() -> tuple[uuid.UUID, uuid.UUID, dict[str, object]]:
    run_id = os.environ["AI_PROBE_RUN_ID"]
    admin_email = os.environ["AI_PROBE_ADMIN_EMAIL"]
    admin_password = os.environ["AI_PROBE_ADMIN_PASSWORD"]
    employee_password = os.environ["AI_PROBE_EMPLOYEE_PASSWORD"]
    email_domain = admin_email.rpartition("@")[2]
    require(bool(email_domain), "admin email domain is missing")

    async with httpx.AsyncClient(base_url=API_BASE_URL, timeout=20.0) as client:
        admin_token = await login(client, admin_email, admin_password)
        department = mapping(
            await api_data(
                client,
                "POST",
                "/api/admin/departments",
                token=admin_token,
                expected_status=201,
                json_body={
                    "name": f"AI Mainchain Probe {run_id}",
                    "code": f"ai-probe-{run_id}",
                },
            ),
            "department",
        )
        department_id = uuid_value(department.get("id"), "department id")
        employee_email = f"ai-probe-{run_id}@{email_domain}"
        registration = mapping(
            await api_data(
                client,
                "POST",
                "/api/auth/register",
                expected_status=201,
                json_body={
                    "name": "AI Mainchain Probe Employee",
                    "email": employee_email,
                    "password": employee_password,
                    "department_id": str(department_id),
                },
            ),
            "registration",
        )
        require(registration.get("accepted") is True, "registration was not accepted")
        employee_token = await login(client, employee_email, employee_password)

        ordinary = mapping(
            await api_data(
                client,
                "POST",
                "/api/files/upload",
                token=employee_token,
                expected_status=201,
                files={
                    "file": (
                        f"ordinary-{run_id}.txt",
                        b"ordinary policy handbook for the AI mainchain integration probe",
                        "text/plain",
                    )
                },
                form={
                    "submit_after_upload": "true",
                    "ai_analysis_enabled": "true",
                    "visibility": "private",
                    "description": "DOC-002 isolated integration probe",
                },
            ),
            "ordinary upload",
        )
        ordinary_id = uuid_value(ordinary.get("id"), "ordinary file id")
        require(
            ordinary.get("status") == "uploaded",
            "AI-enabled auto-submit upload skipped the uploaded draft state",
        )
        require(
            ordinary.get("ai_analysis_enabled_at_upload") is True,
            "ordinary upload lost its AI-enabled snapshot",
        )
        ordinary_final = await wait_for_file_status(
            client,
            token=employee_token,
            file_id=ordinary_id,
            expected_status="pending_review",
        )
        require(
            ordinary_final.get("submitted_at") is not None,
            "ordinary auto-submit did not persist submitted_at",
        )
        require(
            ordinary_final.get("review_due_at") is not None,
            "ordinary auto-submit did not persist review_due_at",
        )

        critical = mapping(
            await api_data(
                client,
                "POST",
                "/api/files/upload",
                token=employee_token,
                expected_status=201,
                files={
                    "file": (
                        f"critical-{run_id}.txt",
                        b"production password must never leave the security boundary",
                        "text/plain",
                    )
                },
                form={
                    "submit_after_upload": "true",
                    "ai_analysis_enabled": "true",
                    "visibility": "private",
                    "description": "DOC-003 isolated integration probe",
                },
            ),
            "critical upload",
        )
        critical_id = uuid_value(critical.get("id"), "critical file id")
        require(
            critical.get("status") == "uploaded",
            "critical AI-enabled upload skipped the uploaded draft state",
        )
        critical_final = await wait_for_file_status(
            client,
            token=employee_token,
            file_id=critical_id,
            expected_status="sensitive_review_required",
        )
        require(
            critical_final.get("submitted_at") is None,
            "critical analysis persisted an automatic submission timestamp",
        )
        await asyncio.sleep(1.0)
        critical_stable = mapping(
            await api_data(
                client,
                "GET",
                f"/api/files/{critical_id}",
                token=employee_token,
            ),
            "critical stable detail",
        )
        require(
            critical_stable.get("status") == "sensitive_review_required",
            "critical file did not remain at the sensitive-review gate",
        )

    mock_state = await read_mock_llm_state()
    return ordinary_id, critical_id, mock_state


async def read_mock_llm_state() -> dict[str, object]:
    headers = {
        "User-Agent": PROBE_USER_AGENT,
        "X-AI-Probe-Token": os.environ["AI_PROBE_STATE_TOKEN"],
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(MOCK_LLM_STATE_URL, headers=headers)
    require(response.status_code == 200, "mock LLM state endpoint was unavailable")
    try:
        state: object = response.json()
    except json.JSONDecodeError as exc:
        raise ProbeFailure("mock LLM state was invalid JSON") from exc
    result = mapping(state, "mock LLM state")
    require(result.get("request_count") == 2, "mock LLM did not receive exactly two calls")
    require(result.get("authorization_failures") == 0, "mock LLM saw an auth failure")
    require(result.get("protocol_failures") == 0, "mock LLM saw a protocol failure")
    require(
        result.get("last_model") == os.environ["AI_PROBE_LLM_MODEL"],
        "mock LLM model identity changed",
    )
    return {
        "request_count": 2,
        "authorization_failures": 0,
        "protocol_failures": 0,
        "last_model": str(result["last_model"]),
    }


def event_sequence(events: Sequence[object]) -> tuple[str, ...]:
    return tuple(str(event.event_type) for event in events)


def isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


async def collect_database_evidence(
    ordinary_id: uuid.UUID,
    critical_id: uuid.UUID,
    mock_state: dict[str, object],
) -> dict[str, object]:
    from app.core.database import AsyncSessionFactory, engine
    from app.core.outbox import EventOutbox
    from app.modules.ai.models import AiUsageLog, DocumentAnalysis
    from app.modules.audit.models import AuditLog
    from app.modules.document.models import File
    from app.modules.ragflow.models import SyncTask

    expected_database = os.environ["AI_PROBE_DATABASE_NAME"]
    database_name = make_url(os.environ["DATABASE_URL"]).database
    require(database_name == expected_database, "probe connected to an unexpected database")
    require(
        isinstance(database_name, str) and database_name.endswith("_test"),
        "probe database name must end with _test",
    )

    deadline = time.monotonic() + WAIT_TIMEOUT_SECONDS
    evidence: dict[str, object] | None = None
    while time.monotonic() < deadline:
        async with AsyncSessionFactory() as session:
            ordinary = await session.get(File, ordinary_id)
            critical = await session.get(File, critical_id)
            analyses = list(
                (
                    await session.execute(
                        select(DocumentAnalysis).where(
                            DocumentAnalysis.file_id.in_([ordinary_id, critical_id])
                        )
                    )
                ).scalars()
            )
            events = list(
                (
                    await session.execute(
                        select(EventOutbox)
                        .where(EventOutbox.aggregate_id.in_([str(ordinary_id), str(critical_id)]))
                        .order_by(EventOutbox.id)
                    )
                ).scalars()
            )
            audits = list(
                (
                    await session.execute(
                        select(AuditLog).where(
                            AuditLog.target_id.in_([ordinary_id, critical_id]),
                            AuditLog.action == "file.upload",
                        )
                    )
                ).scalars()
            )
            usage_logs = list(
                (
                    await session.execute(
                        select(AiUsageLog)
                        .where(AiUsageLog.file_id.in_([ordinary_id, critical_id]))
                        .order_by(AiUsageLog.id)
                    )
                ).scalars()
            )
            critical_sync_count = int(
                (
                    await session.execute(
                        select(func.count(SyncTask.id)).where(SyncTask.file_id == critical_id)
                    )
                ).scalar_one()
            )

        events_by_file = {
            file_id: [event for event in events if event.aggregate_id == str(file_id)]
            for file_id in (ordinary_id, critical_id)
        }
        if (
            ordinary is not None
            and critical is not None
            and len(analyses) == 2
            and len(audits) == 2
            and len(usage_logs) == 2
            and event_sequence(events_by_file[ordinary_id]) == ORDINARY_EVENT_SEQUENCE
            and event_sequence(events_by_file[critical_id]) == CRITICAL_EVENT_SEQUENCE
            and all(event.published_at is not None for event in events)
        ):
            evidence = validate_persisted_chain(
                ordinary=ordinary,
                critical=critical,
                analyses=analyses,
                events_by_file=events_by_file,
                audits=audits,
                usage_logs=usage_logs,
                critical_sync_count=critical_sync_count,
                database_name=database_name,
                mock_state=mock_state,
            )
            break
        await asyncio.sleep(0.25)

    await engine.dispose()
    if evidence is None:
        raise ProbeFailure("persisted AI mainchain evidence did not converge")
    return evidence


def validate_persisted_chain(
    *,
    ordinary: object,
    critical: object,
    analyses: Sequence[object],
    events_by_file: Mapping[uuid.UUID, Sequence[object]],
    audits: Sequence[object],
    usage_logs: Sequence[object],
    critical_sync_count: int,
    database_name: str,
    mock_state: dict[str, object],
) -> dict[str, object]:
    ordinary_id = uuid_value(ordinary.id, "ordinary persisted id")
    critical_id = uuid_value(critical.id, "critical persisted id")
    analysis_by_file = {
        uuid_value(analysis.file_id, "analysis file id"): analysis for analysis in analyses
    }
    audit_by_file = {uuid_value(audit.target_id, "audit target id"): audit for audit in audits}
    usage_by_file = {uuid_value(usage.file_id, "usage file id"): usage for usage in usage_logs}
    ordinary_analysis = analysis_by_file[ordinary_id]
    critical_analysis = analysis_by_file[critical_id]
    ordinary_events = list(events_by_file[ordinary_id])
    critical_events = list(events_by_file[critical_id])
    ordinary_audit = audit_by_file[ordinary_id]
    critical_audit = audit_by_file[critical_id]

    require(ordinary.status == "pending_review", "ordinary status is not pending")
    require(ordinary.review_version == 1, "ordinary review version is not one")
    require(ordinary.submitted_at is not None, "ordinary submitted_at is missing")
    require(ordinary.review_due_at is not None, "ordinary review_due_at is missing")
    require(
        ordinary_analysis.status == "succeeded",
        "ordinary analysis did not succeed",
    )
    require(
        ordinary_analysis.engine_type == "hybrid",
        "ordinary analysis did not use the LLM plus rule engine",
    )
    require(
        ordinary_analysis.finished_at <= ordinary.submitted_at,
        "ordinary submission timestamp preceded analysis completion",
    )
    ordinary_analyzed_payload = mapping(
        ordinary_events[2].payload, "ordinary analyzed event payload"
    )
    ordinary_submitted_payload = mapping(
        ordinary_events[3].payload, "ordinary submitted event payload"
    )
    require(
        ordinary_analyzed_payload.get("analysis_status") == "succeeded",
        "ordinary analyzed event was not terminal-success evidence",
    )
    require(
        ordinary_analyzed_payload.get("auto_submitted") is True,
        "ordinary analyzed event did not record automatic submission",
    )
    require(
        ordinary_submitted_payload.get("previous_status") == "analyzed",
        "ordinary review event did not continue from analyzed",
    )
    require(
        ordinary_submitted_payload.get("analysis_failed") is False,
        "ordinary review event incorrectly recorded analysis failure",
    )

    require(
        critical.status == "sensitive_review_required",
        "critical file escaped the sensitive-review gate",
    )
    require(critical.review_version == 0, "critical review version changed")
    require(critical.submitted_at is None, "critical submitted_at was populated")
    require(critical.review_due_at is None, "critical review_due_at was populated")
    require(
        critical_analysis.status == "succeeded",
        "critical analysis did not complete",
    )
    require(
        critical_analysis.sensitive_risk_level == "critical",
        "critical deterministic rule was not preserved",
    )
    critical_analyzed_payload = mapping(
        critical_events[2].payload, "critical analyzed event payload"
    )
    require(
        critical_analyzed_payload.get("auto_submitted") is False,
        "critical analyzed event claimed automatic submission",
    )
    require(
        critical_analyzed_payload.get("auto_submit_blocked_reason") == "critical_sensitive_content",
        "critical analyzed event lost the blocking reason",
    )
    require(critical_sync_count == 0, "critical file created a RAGFlow sync task")

    expected_model = os.environ["AI_PROBE_LLM_MODEL"]
    for file_id, analysis in analysis_by_file.items():
        usage = usage_by_file[file_id]
        require(analysis.model_name == expected_model, "analysis model changed")
        require(usage.status == "success", "LLM usage did not record success")
        require(usage.model_name == expected_model, "usage model changed")
        require(usage.prompt_tokens == 37, "prompt token evidence changed")
        require(usage.completion_tokens == 13, "completion token evidence changed")
        require(usage.call_sequence == 1, "unexpected LLM repair call occurred")

    for file_id, audit in audit_by_file.items():
        metadata = mapping(audit.metadata_json, "upload audit metadata")
        require(metadata.get("submit_after_upload") is True, "audit lost auto-submit intent")
        require(
            metadata.get("ai_analysis_enabled_at_upload") is True,
            "audit lost the AI-enabled snapshot",
        )
        first_event = next(iter(events_by_file[file_id]))
        require(
            audit.created_at <= first_event.occurred_at,
            "upload audit was persisted after its upload event",
        )

    return {
        "schema_version": 1,
        "status": "passed",
        "database_name": database_name,
        "infrastructure_scope": {
            "postgresql": "real_container",
            "rabbitmq": "real_container",
            "redis": "real_container",
            "minio": "real_container",
            "outbox_dispatcher": "real_process",
            "celery_worker_ai": "real_process",
            "llm_provider": "openai_compatible_protocol_mock",
        },
        "doc_002": {
            "file_id": str(ordinary_id),
            "initial_state": "uploaded",
            "terminal_state": "pending_review",
            "analysis_status": "succeeded",
            "analysis_engine_type": "hybrid",
            "analysis_finished_at": isoformat(ordinary_analysis.finished_at),
            "submitted_at": isoformat(ordinary.submitted_at),
            "event_sequence": list(ORDINARY_EVENT_SEQUENCE),
            "event_ids": [int(event.id) for event in ordinary_events],
            "upload_audit_id": str(ordinary_audit.id),
            "auto_submit_intent_audited": True,
        },
        "doc_003": {
            "file_id": str(critical_id),
            "terminal_state": "sensitive_review_required",
            "analysis_status": "succeeded",
            "sensitive_risk_level": "critical",
            "event_sequence": list(CRITICAL_EVENT_SEQUENCE),
            "event_ids": [int(event.id) for event in critical_events],
            "upload_audit_id": str(critical_audit.id),
            "review_event_absent": True,
            "sync_task_count": 0,
        },
        "mock_llm": mock_state,
        "provider_boundary": {
            "external_provider_verified": False,
            "statement": (
                "This evidence verifies the OpenAI-compatible protocol and local orchestration "
                "only; it does not satisfy EXT-LLM or a real provider acceptance gate."
            ),
        },
    }


async def async_main() -> int:
    ordinary_id, critical_id, mock_state = await run_public_api_flow()
    evidence = await collect_database_evidence(ordinary_id, critical_id, mock_state)
    sys.stdout.write(
        "AI_MAINCHAIN_EVIDENCE="
        + json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
