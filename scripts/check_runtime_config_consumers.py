"""Fail CI when runtime configuration is dead, undeclared, or resurrected."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS_PATH = ROOT / "backend/app/modules/config/defaults.py"
RUNTIME_PATH = ROOT / "backend/app/core/runtime_config.py"
BACKEND_ROOT = ROOT / "backend/app"

CONSUMER_EXCLUDED_PARTS = {
    "tests",
    "migrations",
    "config",
}
INVARIANT_ONLY_KEYS = frozenset({"security.block_critical_sensitive_sync"})
FORWARDED_CONSUMER_HELPERS: dict[str, frozenset[str]] = {
    "core/review_policy.py": frozenset({"_bounded_int_config"}),
}
DELETED_CONFIG_KEYS = frozenset(
    {
        "upload.enable_duplicate_check",
        "processing.auto_parse_on_upload",
        "processing.auto_sync_after_parse",
        "processing.sync_after_ai_analysis",
        "processing.task_timeout_seconds",
        "processing.task_max_retries",
        "security.require_review_before_sync",
        "basic.system_name",
        "basic.system_logo_url",
        "basic.default_language",
        "basic.default_timezone",
        "basic.notification_channels",
        "basic.admin_contact_email",
        "ragflow.default_dataset_id",
        "ragflow.auto_sync_enabled",
    }
)


def _literal_string_set(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def _assignment_dict_keys(path: Path, assignment_name: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
            continue
        if node.target.id != assignment_name or not isinstance(node.value, ast.Dict):
            continue
        keys: set[str] = set()
        for key in node.value.keys:
            if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                raise RuntimeError(f"{assignment_name} must use literal string keys")
            keys.add(key.value)
        return keys
    raise RuntimeError(f"could not find {assignment_name} in {path}")


def _definition_keys() -> set[str]:
    tree = ast.parse(DEFAULTS_PATH.read_text(encoding="utf-8"), filename=str(DEFAULTS_PATH))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function_name = node.func.id if isinstance(node.func, ast.Name) else None
        if function_name != "ConfigDefinition":
            continue
        for keyword in node.keywords:
            if (
                keyword.arg == "key"
                and isinstance(keyword.value, ast.Constant)
                and isinstance(keyword.value.value, str)
            ):
                keys.add(keyword.value.value)
    if not keys:
        raise RuntimeError("CONFIG_DEFINITIONS contains no literal ConfigDefinition keys")
    return keys


def _production_python_files() -> list[Path]:
    files: list[Path] = []
    for path in BACKEND_ROOT.rglob("*.py"):
        relative_parts = set(path.relative_to(BACKEND_ROOT).parts)
        if relative_parts & CONSUMER_EXCLUDED_PARTS:
            continue
        if path == RUNTIME_PATH:
            continue
        files.append(path)
    return files


def _runtime_config_consumers(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    direct_aliases: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or node.module != "app.core.runtime_config":
            continue
        for imported in node.names:
            if imported.name == "get_config":
                direct_aliases.add(imported.asname or imported.name)

    relative = path.relative_to(BACKEND_ROOT).as_posix()
    forwarding_helpers = FORWARDED_CONSUMER_HELPERS.get(relative, frozenset())
    _validate_forwarding_helpers(
        tree=tree,
        path=path,
        helper_names=forwarding_helpers,
        direct_aliases=direct_aliases,
    )
    consumers: set[str] = set()
    for walked_node in ast.walk(tree):
        if not isinstance(walked_node, ast.Call) or not walked_node.args:
            continue
        function_name = (
            walked_node.func.id if isinstance(walked_node.func, ast.Name) else None
        )
        if function_name not in direct_aliases | forwarding_helpers:
            continue
        first_argument = walked_node.args[0]
        if isinstance(first_argument, ast.Constant) and isinstance(first_argument.value, str):
            consumers.add(first_argument.value)
    return consumers


def _validate_forwarding_helpers(
    *,
    tree: ast.Module,
    path: Path,
    helper_names: frozenset[str],
    direct_aliases: set[str],
) -> None:
    for helper_name in helper_names:
        helper = next(
            (
                node
                for node in tree.body
                if isinstance(node, ast.AsyncFunctionDef) and node.name == helper_name
            ),
            None,
        )
        if helper is None or not helper.args.args:
            raise RuntimeError(f"forwarding config helper missing: {path}:{helper_name}")
        key_parameter = helper.args.args[0].arg
        forwards_key = any(
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id in direct_aliases
            and bool(call.args)
            and isinstance(call.args[0], ast.Name)
            and call.args[0].id == key_parameter
            for call in ast.walk(helper)
        )
        if not forwards_key:
            raise RuntimeError(
                f"forwarding config helper does not call get_config(key): {path}:{helper_name}"
            )


def main() -> int:
    errors: list[str] = []
    definition_keys = _definition_keys()
    fallback_keys = _assignment_dict_keys(RUNTIME_PATH, "FALLBACKS")
    if definition_keys != fallback_keys:
        errors.append(
            "CONFIG_DEFINITIONS/FALLBACKS mismatch: "
            f"definitions_only={sorted(definition_keys - fallback_keys)}, "
            f"fallbacks_only={sorted(fallback_keys - definition_keys)}"
        )
    fail_closed_keys = _assignment_dict_keys(RUNTIME_PATH, "FAIL_CLOSED_DEFAULTS")
    if definition_keys != fail_closed_keys:
        errors.append(
            "CONFIG_DEFINITIONS/FAIL_CLOSED_DEFAULTS mismatch: "
            f"definitions_only={sorted(definition_keys - fail_closed_keys)}, "
            f"fail_closed_only={sorted(fail_closed_keys - definition_keys)}"
        )

    production_files = _production_python_files()
    consumed = set().union(*(_runtime_config_consumers(path) for path in production_files))
    dead_keys = definition_keys - consumed - INVARIANT_ONLY_KEYS
    if dead_keys:
        errors.append(f"runtime config keys without production consumers: {sorted(dead_keys)}")

    production_literals = set().union(*(
        _literal_string_set(path)
        for path in BACKEND_ROOT.rglob("*.py")
        if "tests" not in path.parts and "migrations" not in path.parts
    ))
    resurrected = sorted(key for key in DELETED_CONFIG_KEYS if key in production_literals)
    if resurrected:
        errors.append(f"deleted runtime config keys found in production code: {resurrected}")

    deployment_surfaces = (ROOT / "docker-compose.yml", ROOT / ".env.example")
    has_deleted_environment = any(
        "DEFAULT_DATASET_ID" in path.read_text(encoding="utf-8")
        for path in deployment_surfaces
    )
    if has_deleted_environment:
        errors.append("deleted DEFAULT_DATASET_ID remains on a deployment surface")
    for doc_path in (ROOT / "docs/deployment.md", ROOT / "docs/faq.md"):
        text = doc_path.read_text(encoding="utf-8")
        if "`DEFAULT_DATASET_ID` 已删除" not in text:
            errors.append(f"{doc_path.relative_to(ROOT)} must document DEFAULT_DATASET_ID deletion")

    if errors:
        sys.stderr.write("\n".join(f"ERROR: {error}" for error in errors) + "\n")
        return 1
    sys.stdout.write(
        f"runtime config contract ok: {len(definition_keys)} active keys, "
        f"{len(DELETED_CONFIG_KEYS)} deleted keys guarded\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
