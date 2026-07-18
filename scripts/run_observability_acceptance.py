#!/usr/bin/env python3
# ruff: noqa: E402, PTH118, PTH120 -- isolation precedes imports.
"""Run candidate-bound local OBS-001 Prometheus acceptance.

Alertmanager is intentionally absent. This command cannot verify, send, or
emulate the protected EXT-WEBHOOK-001 receipt.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from typing import Protocol, cast


class _AcceptanceEntry(Protocol):
    def consume_launcher_claim(self, repo_root: str) -> None: ...


def _load_acceptance_entry() -> _AcceptanceEntry:
    module_path = os.path.join(os.path.dirname(__file__), "acceptance_entry.py")
    spec = importlib.util.spec_from_file_location(
        "knowledge_uploader_observability_wrapper_entry",
        module_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("acceptance entry helper loader unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return cast(_AcceptanceEntry, module)


_acceptance_entry = _load_acceptance_entry()
_repository = os.path.realpath(os.path.join(os.path.dirname(__file__), os.pardir))
try:
    _acceptance_entry.consume_launcher_claim(_repository)
except RuntimeError as _claim_error:
    raise SystemExit(f"observability acceptance refused: {_claim_error}") from _claim_error

import site

site.main()

if sys.argv[1:] == ["--isolation-probe"]:
    sys.stdout.write("observability Python isolation verified\n")
    raise SystemExit(0)

from collections.abc import Callable
from pathlib import Path


class _ObservabilityModule(Protocol):
    _LAUNCHER_CLAIM_CONSUMED: bool
    main: Callable[[], int]


MODULE_PATH = Path(__file__).with_name("observability_acceptance.py").resolve()
SPEC = importlib.util.spec_from_file_location(
    "knowledge_uploader_observability_acceptance", MODULE_PATH
)
if SPEC is None or SPEC.loader is None:
    raise SystemExit("observability acceptance refused: module loader unavailable")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
_observability_module = cast(_ObservabilityModule, MODULE)
_observability_module._LAUNCHER_CLAIM_CONSUMED = True
main = _observability_module.main

if __name__ == "__main__":
    raise SystemExit(main())
