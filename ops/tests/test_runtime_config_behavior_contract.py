from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import cast

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "ops/runtime_config_behavior_registry.json"
GUARD_PATH = ROOT / "scripts/check_runtime_config_consumers.py"
ALLOWED_ASSERTION_TYPES = frozenset(
    {"fail_closed", "immutable", "secret_redaction", "snapshot", "value_ab"}
)
REQUIRED_ENTRY_FIELDS = frozenset(
    {"key", "consumer", "effect_boundary", "assertion_type", "pytest_nodes"}
)


def _load_guard() -> ModuleType:
    spec = importlib.util.spec_from_file_location("runtime_config_consumer_guard", GUARD_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load runtime config consumer guard")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_registry() -> dict[str, object]:
    payload = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("runtime config behavior registry must be an object")
    return cast(dict[str, object], payload)


def _entries(payload: dict[str, object]) -> list[dict[str, object]]:
    raw_entries = payload.get("keys")
    if not isinstance(raw_entries, list) or not all(
        isinstance(entry, dict) for entry in raw_entries
    ):
        raise TypeError("runtime config behavior registry keys must be an object list")
    return cast(list[dict[str, object]], raw_entries)


def _registered_nodeids() -> list[str]:
    nodeids: set[str] = set()
    for entry in _entries(_load_registry()):
        raw_nodes = entry["pytest_nodes"]
        assert isinstance(raw_nodes, list)
        nodeids.update(cast(list[str], raw_nodes))
    return sorted(nodeids)


def _test_function_has_assertion(nodeid: str) -> bool:
    relative_path, function_name = nodeid.split("::", maxsplit=1)
    tree = ast.parse(
        (ROOT / relative_path).read_text(encoding="utf-8"),
        filename=relative_path,
    )
    function = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == function_name
        ),
        None,
    )
    return function is not None and any(isinstance(node, ast.Assert) for node in ast.walk(function))


def test_registry_covers_exact_active_contract_and_real_consumers() -> None:
    guard = _load_guard()
    payload = _load_registry()
    entries = _entries(payload)
    keys = [cast(str, entry["key"]) for entry in entries]

    assert payload["schema_version"] == 1
    assert payload["active_key_count"] == 26
    assert payload["deleted_key_count"] == 15
    assert len(entries) == 26
    assert len(keys) == len(set(keys))
    assert set(keys) == guard._definition_keys()
    assert set(keys).isdisjoint(guard.DELETED_CONFIG_KEYS)
    assert len(guard.DELETED_CONFIG_KEYS) == 15

    for entry in entries:
        assert set(entry) == REQUIRED_ENTRY_FIELDS
        key = entry["key"]
        consumer = entry["consumer"]
        assertion_type = entry["assertion_type"]
        effect_boundary = entry["effect_boundary"]
        nodeids = entry["pytest_nodes"]

        assert isinstance(key, str) and key
        assert isinstance(consumer, dict)
        assert set(consumer) == {"path", "symbol"}
        consumer_path = consumer["path"]
        consumer_symbol = consumer["symbol"]
        assert isinstance(consumer_path, str) and consumer_path.startswith("backend/app/")
        assert isinstance(consumer_symbol, str) and consumer_symbol
        absolute_consumer_path = ROOT / consumer_path
        assert absolute_consumer_path.is_file()

        consumer_tree = ast.parse(
            absolute_consumer_path.read_text(encoding="utf-8"),
            filename=consumer_path,
        )
        symbols = {
            node.name
            for node in ast.walk(consumer_tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert consumer_symbol in symbols
        if assertion_type != "immutable":
            assert key in guard._runtime_config_consumers(absolute_consumer_path)

        assert assertion_type in ALLOWED_ASSERTION_TYPES
        assert isinstance(effect_boundary, str) and effect_boundary
        assert isinstance(nodeids, list) and nodeids
        for nodeid in nodeids:
            assert isinstance(nodeid, str)
            assert nodeid.startswith("backend/app/tests/")
            assert nodeid.count("::") == 1
            assert _test_function_has_assertion(nodeid)


def test_registered_pytest_nodes_are_collectable() -> None:
    nodeids = _registered_nodeids()
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", *nodeids],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    normalized_stdout = completed.stdout.replace("\\", "/")
    for nodeid in nodeids:
        collected_nodeid = nodeid.removeprefix("backend/")
        assert nodeid in normalized_stdout or collected_nodeid in normalized_stdout
