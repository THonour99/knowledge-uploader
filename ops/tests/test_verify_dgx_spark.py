from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest


def _load_verifier() -> ModuleType:
    verifier_path = Path(__file__).parents[2] / "scripts/verify_dgx_spark.py"
    spec = importlib.util.spec_from_file_location("verify_dgx_spark", verifier_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load DGX Spark verifier")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _evidence(*, generated_at: datetime | None = None) -> dict[str, object]:
    return {
        "status": "passed",
        "generated_at": (generated_at or datetime.now(UTC)).isoformat(),
        "git_sha": "abcdef123456",
        "environment": "staging",
        "architecture": "aarch64",
        "full_compose_e2e": "passed",
        "results": {
            "compose_up": "passed",
            "alembic_head": "passed",
            "ready": "passed",
            "workers": "passed",
            "rabbitmq_topology": "passed",
            "upload_review_ragflow": "passed",
            "dlq_protocol": "passed",
        },
    }


def _write_evidence(path: Path, evidence: dict[str, object]) -> None:
    path.write_text(json.dumps(evidence), encoding="utf-8")


def test_verifier_rejects_unknown_git_identity_before_host_checks(tmp_path: Path) -> None:
    verifier = _load_verifier()

    with pytest.raises(RuntimeError, match="git SHA"):
        verifier.verify(
            backend_image="backend:test",
            frontend_image="frontend:test",
            git_sha="unknown",
            environment="staging",
            compose_e2e_evidence=tmp_path / "missing.json",
        )


def test_compose_e2e_evidence_accepts_matching_complete_proof(tmp_path: Path) -> None:
    verifier = _load_verifier()
    evidence_path = tmp_path / "infrastructure-e2e.json"
    _write_evidence(evidence_path, _evidence())

    loaded = verifier._load_compose_e2e_evidence(
        evidence_path,
        git_sha="abcdef123456",
        environment="staging",
        architecture="aarch64",
    )

    assert loaded["full_compose_e2e"] == "passed"


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("git_sha", "wrong"),
        ("environment", "production"),
        ("architecture", "amd64"),
        ("full_compose_e2e", "skipped"),
    ),
)
def test_compose_e2e_evidence_rejects_identity_or_scope_mismatch(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    verifier = _load_verifier()
    evidence_path = tmp_path / "infrastructure-e2e.json"
    evidence = _evidence()
    evidence[field] = value
    _write_evidence(evidence_path, evidence)

    with pytest.raises(RuntimeError, match=field):
        verifier._load_compose_e2e_evidence(
            evidence_path,
            git_sha="abcdef123456",
            environment="staging",
            architecture="aarch64",
        )


def test_compose_e2e_evidence_rejects_missing_protocol_result(tmp_path: Path) -> None:
    verifier = _load_verifier()
    evidence_path = tmp_path / "infrastructure-e2e.json"
    evidence = _evidence()
    results = evidence["results"]
    assert isinstance(results, dict)
    results["dlq_protocol"] = "skipped"
    _write_evidence(evidence_path, evidence)

    with pytest.raises(RuntimeError, match="required passed results"):
        verifier._load_compose_e2e_evidence(
            evidence_path,
            git_sha="abcdef123456",
            environment="staging",
            architecture="aarch64",
        )


def test_compose_e2e_evidence_rejects_stale_proof(tmp_path: Path) -> None:
    verifier = _load_verifier()
    evidence_path = tmp_path / "infrastructure-e2e.json"
    _write_evidence(
        evidence_path,
        _evidence(generated_at=datetime.now(UTC) - timedelta(hours=3)),
    )

    with pytest.raises(RuntimeError, match="stale"):
        verifier._load_compose_e2e_evidence(
            evidence_path,
            git_sha="abcdef123456",
            environment="staging",
            architecture="aarch64",
        )
