from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml


def _load_gate() -> ModuleType:
    path = Path(__file__).parents[2] / "scripts" / "check_protected_release.py"
    spec = importlib.util.spec_from_file_location("prometheus_contract_gate", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load protected release gate")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _protected_config() -> dict[str, object]:
    path = (
        Path(__file__).parents[2]
        / "ops"
        / "observability"
        / "prometheus.protected.yml"
    )
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _minio_job(config: dict[str, object]) -> dict[str, object]:
    scrape_configs = config["scrape_configs"]
    assert isinstance(scrape_configs, list)
    job = next(
        item
        for item in scrape_configs
        if isinstance(item, dict) and item.get("job_name") == "minio"
    )
    return job


def test_protected_minio_scrape_contract_accepts_verified_https() -> None:
    gate = _load_gate()

    assert gate._protected_minio_scrape_errors(_protected_config()) == []


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("scheme", "http"),
        ("ca_file", ""),
        ("server_name", "127.0.0.1"),
        ("insecure_skip_verify", True),
    ),
)
def test_protected_minio_scrape_contract_rejects_tls_downgrade(
    field: str,
    value: object,
) -> None:
    gate = _load_gate()
    config = copy.deepcopy(_protected_config())
    minio = _minio_job(config)
    if field == "scheme":
        minio[field] = value
    else:
        tls_config = minio["tls_config"]
        assert isinstance(tls_config, dict)
        tls_config[field] = value

    assert gate._protected_minio_scrape_errors(config)
