from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _load_guard() -> ModuleType:
    guard_path = Path(__file__).parents[2] / "scripts/check_runtime_config_consumers.py"
    spec = importlib.util.spec_from_file_location("check_runtime_config_consumers", guard_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load runtime config consumer guard")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_literal_or_comment_does_not_count_as_runtime_consumer(tmp_path: Path) -> None:
    guard = _load_guard()
    guard.BACKEND_ROOT = tmp_path
    source = tmp_path / "consumer.py"
    source.write_text(
        "from app.core.runtime_config import get_config\n"
        "# get_config('upload.enabled')\n"
        "DOCUMENTATION = 'upload.enabled'\n",
        encoding="utf-8",
    )

    assert guard._runtime_config_consumers(source) == set()


def test_aliased_get_config_call_counts_as_runtime_consumer(tmp_path: Path) -> None:
    guard = _load_guard()
    guard.BACKEND_ROOT = tmp_path
    source = tmp_path / "consumer.py"
    source.write_text(
        "from app.core.runtime_config import get_config as runtime_value\n"
        "\nasync def resolve():\n"
        "    return await runtime_value('upload.enabled')\n",
        encoding="utf-8",
    )

    assert guard._runtime_config_consumers(source) == {"upload.enabled"}


def test_unrelated_get_config_method_does_not_count(tmp_path: Path) -> None:
    guard = _load_guard()
    guard.BACKEND_ROOT = tmp_path
    source = tmp_path / "consumer.py"
    source.write_text(
        "async def resolve(service):\n"
        "    return await service.get_config('upload.enabled')\n",
        encoding="utf-8",
    )

    assert guard._runtime_config_consumers(source) == set()
