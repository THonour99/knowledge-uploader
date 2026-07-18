from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _load_gate() -> ModuleType:
    gate_path = Path(__file__).parents[2] / "scripts/check_external_release_readiness.py"
    spec = importlib.util.spec_from_file_location("check_external_release_readiness", gate_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load external release readiness gate")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_contract_only_accepts_explicitly_unbound_external_gates() -> None:
    gate = _load_gate()

    assert gate.contract_errors() == []
    assert gate.main(["--contract-only"]) == 0


def test_release_readiness_fails_closed_for_llm_and_ragflow(
    capsys: pytest.CaptureFixture[str],
) -> None:
    gate = _load_gate()

    assert gate.main(["--require-ready"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "external release readiness failed: unbound external release evidence gates: "
        "EXT-LLM-001, EXT-RAGFLOW-001\n"
    )


def test_bound_gate_must_reuse_authorization_evidence_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = _load_gate()
    contracts = dict(gate.release_oci.EXTERNAL_EVIDENCE_CONTRACTS)
    contracts.pop("email-delivery.json")
    monkeypatch.setattr(gate.release_oci, "EXTERNAL_EVIDENCE_CONTRACTS", contracts)

    errors = gate.contract_errors()

    assert errors == ["EXT-SMTP-001 evidence contract is not authorization-bound"]


def test_script_entrypoint_enforces_external_release_readiness() -> None:
    root = Path(__file__).parents[2]
    script = root / "scripts/check_external_release_readiness.py"
    contract_only = subprocess.run(
        [sys.executable, str(script), "--contract-only"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    blocked = subprocess.run(
        [sys.executable, str(script), "--require-ready"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert contract_only.returncode == 0
    assert contract_only.stdout == "external release evidence binding contract is valid\n"
    assert contract_only.stderr == ""
    assert blocked.returncode == 1
    assert blocked.stdout == ""
    assert blocked.stderr == (
        "external release readiness failed: unbound external release evidence gates: "
        "EXT-LLM-001, EXT-RAGFLOW-001\n"
    )
