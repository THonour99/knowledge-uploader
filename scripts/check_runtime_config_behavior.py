"""Run the executable behavior evidence registered for every active runtime config key."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "ops/runtime_config_behavior_registry.json"


def registered_nodeids() -> tuple[int, list[str]]:
    payload = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("runtime config behavior registry must be an object")
    active_key_count = payload.get("active_key_count")
    raw_entries = payload.get("keys")
    if active_key_count != 26 or not isinstance(raw_entries, list) or len(raw_entries) != 26:
        raise RuntimeError("runtime config behavior registry must contain exactly 26 active keys")

    nodeids: set[str] = set()
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, dict):
            raise RuntimeError("runtime config behavior registry entry must be an object")
        raw_nodes = raw_entry.get("pytest_nodes")
        if not isinstance(raw_nodes, list) or not raw_nodes:
            raise RuntimeError("runtime config behavior registry entry has no pytest nodes")
        for raw_node in raw_nodes:
            if not isinstance(raw_node, str) or "::" not in raw_node:
                raise RuntimeError(
                    "runtime config behavior registry contains an invalid pytest node"
                )
            nodeids.add(raw_node)
    return cast(int, active_key_count), sorted(nodeids)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run all registered runtime-config behavior tests.",
    )
    parser.add_argument(
        "--collect-only",
        action="store_true",
        help="Only prove that every registered pytest node is collectable.",
    )
    args = parser.parse_args()

    active_key_count, nodeids = registered_nodeids()
    command = [sys.executable, "-m", "pytest", "-q"]
    if args.collect_only:
        command.append("--collect-only")
    command.extend(nodeids)
    completed = subprocess.run(command, cwd=ROOT, check=False)
    if completed.returncode != 0:
        return completed.returncode
    mode = "collection" if args.collect_only else "execution"
    sys.stdout.write(
        f"runtime config behavior {mode} ok: {active_key_count} active keys, "
        f"{len(nodeids)} registered pytest nodes\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
