from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


def _load_probe() -> ModuleType:
    path = Path(__file__).parents[2] / "scripts" / "infrastructure_e2e_probe.py"
    spec = importlib.util.spec_from_file_location("infrastructure_e2e_probe_contract", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load infrastructure E2E probe")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_draft_contract_uses_main_state_and_submission_facts() -> None:
    probe = _load_probe()
    draft = {
        "status": "uploaded",
        "review_status": "pending",
        "submitted_at": None,
        "review_due_at": None,
        "claimed_by": None,
        "claimed_at": None,
        "claim_expires_at": None,
    }

    probe._require_explicit_draft(draft)

    for field in probe.DRAFT_REVIEW_FACT_FIELDS:
        invalid = dict(draft)
        invalid[field] = "persisted-review-fact"
        with pytest.raises(probe.InfrastructureProbeError, match="review submission facts"):
            probe._require_explicit_draft(invalid)


def test_draft_must_not_appear_in_department_review_queue() -> None:
    probe_module = _load_probe()
    file_id = uuid.uuid4()

    class Client:
        def __init__(self) -> None:
            self.items: list[dict[str, str]] = []

        def json(self, _method: str, _path: str, *, token: str) -> object:
            assert token == "reviewer-token"
            return {"items": list(self.items)}

    probe = probe_module.InfrastructureBusinessProbe.__new__(
        probe_module.InfrastructureBusinessProbe
    )
    client = Client()
    probe._client = client
    actor = SimpleNamespace(token="reviewer-token")

    probe._require_not_in_review_queue(actor, file_id=file_id, search="draft.txt")

    client.items = [{"id": str(file_id)}]
    with pytest.raises(probe_module.InfrastructureProbeError, match="before submission"):
        probe._require_not_in_review_queue(actor, file_id=file_id, search="draft.txt")


def test_review_notification_uses_public_structured_metadata_contract() -> None:
    probe = _load_probe()
    file_id = uuid.uuid4()
    notification = {
        "type": "review_approved",
        "metadata": {
            "resource_type": "file",
            "resource_id": str(file_id),
            "status": "approved",
        },
    }

    assert probe._is_review_approval_notification(notification, file_id=file_id) is True

    legacy_only = {
        "type": "review_approved",
        "metadata": {"file_id": str(file_id)},
    }
    assert probe._is_review_approval_notification(legacy_only, file_id=file_id) is False
    wrong_type = dict(notification, type="ragflow_sync_succeeded")
    assert probe._is_review_approval_notification(wrong_type, file_id=file_id) is False


def test_remote_identity_uses_persisted_document_id_not_display_name() -> None:
    probe = _load_probe()
    document_id = str(uuid.uuid4())
    documents: list[object] = [
        {"id": document_id, "name": "server-generated-stable-name.txt"},
        {"id": str(uuid.uuid4()), "name": "uploaded-original-name.txt"},
    ]

    matching = probe._remote_documents_with_id(documents, document_id)

    assert matching == [documents[0]]


def _status_probe(
    probe_module: ModuleType,
    statuses: list[str],
) -> tuple[object, object]:
    class Client:
        def __init__(self) -> None:
            self.statuses = list(statuses)
            self.calls = 0

        def json(self, _method: str, _path: str, *, token: str) -> object:
            assert token == "employee-token"
            self.calls += 1
            return {"status": self.statuses.pop(0)}

    probe = probe_module.InfrastructureBusinessProbe.__new__(
        probe_module.InfrastructureBusinessProbe
    )
    client = Client()
    probe._client = client
    probe._timeout_seconds = 1
    return probe, client


def test_wait_for_file_allows_initial_failed_state_during_manual_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe_module = _load_probe()
    probe, client = _status_probe(probe_module, ["failed", "syncing", "parsed"])
    monkeypatch.setattr(probe_module.time, "sleep", lambda _seconds: None)

    result = probe._wait_for_file(
        SimpleNamespace(token="employee-token"),
        uuid.uuid4(),
        expected_status="parsed",
        allow_initial_failed=True,
    )

    assert result["status"] == "parsed"
    assert client.calls == 3


def test_wait_for_file_still_fails_fast_without_retry_context() -> None:
    probe_module = _load_probe()
    probe, client = _status_probe(probe_module, ["failed", "parsed"])

    with pytest.raises(
        probe_module.InfrastructureProbeError, match="last bounded state was failed"
    ):
        probe._wait_for_file(
            SimpleNamespace(token="employee-token"),
            uuid.uuid4(),
            expected_status="parsed",
        )

    assert client.calls == 1


def test_wait_for_file_rejects_failure_after_recovery_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe_module = _load_probe()
    probe, client = _status_probe(
        probe_module,
        ["failed", "syncing", "failed", "parsed"],
    )
    monkeypatch.setattr(probe_module.time, "sleep", lambda _seconds: None)

    with pytest.raises(
        probe_module.InfrastructureProbeError, match="last bounded state was failed"
    ):
        probe._wait_for_file(
            SimpleNamespace(token="employee-token"),
            uuid.uuid4(),
            expected_status="parsed",
            allow_initial_failed=True,
        )

    assert client.calls == 3
