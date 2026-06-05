from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
APP_ROOT = BACKEND_ROOT / "app"
MODULES_ROOT = APP_ROOT / "modules"
CORE_ROOT = APP_ROOT / "core"

BANNED_CROSS_MODULE_LAYERS = {"models", "repository", "service"}
BANNED_CORE_LAYERS = {"models", "repository", "service"}


@dataclass(frozen=True)
class Violation:
    path: Path
    line: int
    import_name: str
    message: str


def _python_module_name(path: Path) -> str:
    relative = path.relative_to(BACKEND_ROOT).with_suffix("")
    return ".".join(relative.parts)


def _package_name(path: Path) -> str:
    module_name = _python_module_name(path)
    return module_name.rsplit(".", 1)[0]


def _resolve_import_from(path: Path, node: ast.ImportFrom) -> list[str]:
    if node.level == 0:
        if node.module is None:
            return []
        base_module = node.module
        base_parts = base_module.split(".")
        if len(base_parts) == 3 and base_parts[:2] == ["app", "modules"]:
            return [f"{base_module}.{alias.name}" for alias in node.names if alias.name != "*"]
        return [base_module]

    package_parts = _package_name(path).split(".")
    base_parts = package_parts[: len(package_parts) - node.level + 1]
    module_parts = node.module.split(".") if node.module else []
    base_module = ".".join([*base_parts, *module_parts])
    base_module_parts = base_module.split(".")
    if len(base_module_parts) == 3 and base_module_parts[:2] == ["app", "modules"]:
        return [f"{base_module}.{alias.name}" for alias in node.names if alias.name != "*"]
    if node.module:
        return [base_module]
    return [f"{base_module}.{alias.name}" for alias in node.names if alias.name != "*"]


def _module_layer(import_name: str) -> tuple[str, str] | None:
    parts = import_name.split(".")
    if len(parts) < 4 or parts[0] != "app" or parts[1] != "modules":
        return None
    return parts[2], parts[3]


def _current_module(path: Path) -> str | None:
    try:
        relative = path.relative_to(MODULES_ROOT)
    except ValueError:
        return None
    return relative.parts[0] if relative.parts else None


def _is_core_file(path: Path) -> bool:
    try:
        path.relative_to(CORE_ROOT)
    except ValueError:
        return False
    return True


def _check_import(path: Path, import_name: str, line: int) -> Violation | None:
    module_layer = _module_layer(import_name)
    if module_layer is None:
        return None

    target_module, target_layer = module_layer
    current_module = _current_module(path)
    if current_module is not None:
        if (
            target_module != current_module
            and target_layer in BANNED_CROSS_MODULE_LAYERS
        ):
            return Violation(
                path=path,
                line=line,
                import_name=import_name,
                message=(
                    "cross-module models/repository/service imports are forbidden; "
                    "use shared schemas, events, or tasks"
                ),
            )
        return None

    if _is_core_file(path) and target_layer in BANNED_CORE_LAYERS:
        return Violation(
            path=path,
            line=line,
            import_name=import_name,
            message="core must not import module models/repository/service layers",
        )
    return None


def _check_file(path: Path) -> list[Violation]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                violation = _check_import(path, alias.name, node.lineno)
                if violation is not None:
                    violations.append(violation)
        elif isinstance(node, ast.ImportFrom):
            for import_name in _resolve_import_from(path, node):
                violation = _check_import(path, import_name, node.lineno)
                if violation is not None:
                    violations.append(violation)
    return violations


def main() -> int:
    paths = [
        *MODULES_ROOT.rglob("*.py"),
        *CORE_ROOT.rglob("*.py"),
    ]
    violations = [violation for path in paths for violation in _check_file(path)]
    if violations:
        print("Module boundary violations found:", file=sys.stderr)
        for violation in violations:
            relative = violation.path.relative_to(PROJECT_ROOT)
            print(
                f"{relative}:{violation.line}: {violation.import_name}: {violation.message}",
                file=sys.stderr,
            )
        return 1
    print("Module boundary check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
