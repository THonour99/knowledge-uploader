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


def test_contract_and_release_readiness_require_all_four_authorization_bindings(
    capsys: pytest.CaptureFixture[str],
) -> None:
    gate = _load_gate()

    assert gate.contract_errors() == []
    assert gate.readiness_errors() == []
    assert gate.main(["--contract-only"]) == 0
    assert gate.main(["--require-ready"]) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == (
        "external release evidence binding contract is valid\n"
        "external release evidence binding contract is valid\n"
    )


def test_contract_entrypoint_does_not_import_runtime_or_crypto_dependencies() -> None:
    root = Path(__file__).parents[2]
    code = """
import builtins
import runpy
import sys

original_import = builtins.__import__

def guarded_import(name, *args, **kwargs):
    if name.split('.', 1)[0] in {'app', 'cryptography'}:
        raise AssertionError(f'pure contract imported forbidden dependency: {name}')
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
sys.argv = ['check_external_release_readiness', '--contract-only']
runpy.run_module('scripts.check_external_release_readiness', run_name='__main__')
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert result.stdout == "external release evidence binding contract is valid\n"


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
    module = "scripts.check_external_release_readiness"
    contract_only = subprocess.run(
        [sys.executable, "-m", module, "--contract-only"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    blocked = subprocess.run(
        [sys.executable, "-m", module, "--require-ready"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert contract_only.returncode == 0
    assert contract_only.stdout == "external release evidence binding contract is valid\n"
    assert contract_only.stderr == ""
    assert blocked.returncode == 0
    assert blocked.stdout == "external release evidence binding contract is valid\n"
    assert blocked.stderr == ""
