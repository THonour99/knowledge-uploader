from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest


def _load_boundaries_module() -> Any:
    for parent in Path(__file__).resolve().parents:
        script_path = parent / "scripts" / "check_module_boundaries.py"
        if script_path.exists():
            spec = importlib.util.spec_from_file_location("check_module_boundaries", script_path)
            if spec is None or spec.loader is None:
                break
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            return module
    pytest.skip(
        "check_module_boundaries.py is outside the backend Docker build context",
        allow_module_level=True,
    )


boundaries = _load_boundaries_module()


def test_import_from_package_expands_banned_aliases() -> None:
    path = boundaries.MODULES_ROOT / "auth" / "api.py"
    node = ast.parse("from app.modules.user import service, schemas").body[0]
    assert isinstance(node, ast.ImportFrom)

    imports = boundaries._resolve_import_from(path, node)

    assert "app.modules.user.service" in imports
    assert "app.modules.user.schemas" in imports


def test_relative_import_from_package_expands_banned_aliases() -> None:
    path = boundaries.MODULES_ROOT / "auth" / "api.py"
    node = ast.parse("from ..user import repository").body[0]
    assert isinstance(node, ast.ImportFrom)

    imports = boundaries._resolve_import_from(path, node)

    assert imports == ["app.modules.user.repository"]


def test_core_cannot_import_module_models() -> None:
    violation = boundaries._check_import(
        boundaries.CORE_ROOT / "deps.py",
        "app.modules.user.models",
        1,
    )

    assert violation is not None
