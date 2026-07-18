from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]
COMPOSE = ROOT / "docker-compose.yml"
ENV_EXAMPLE = ROOT / ".env.example"
DEPLOYMENT = ROOT / "docs" / "deployment.md"
CONFIG_CONTRACT = ROOT / "docs" / "product" / "CONFIG_CONTRACT.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _env_block_after(source: str, marker: str) -> str:
    tail = source.split(marker, maxsplit=1)[1]
    match = re.search(r"```env\n(?P<body>.*?)\n```", tail, re.DOTALL)
    assert match is not None
    return match.group("body")


def _assignments(block: str) -> dict[str, str]:
    return {
        key: value
        for key, value in (
            line.split("=", maxsplit=1)
            for line in block.splitlines()
            if line and not line.startswith("#") and "=" in line
        )
    }


def test_spki_pin_variables_are_forwarded_and_documented_as_json_mappings() -> None:
    compose = yaml.safe_load(_read(COMPOSE))
    shared_environment = compose["x-app-environment"]
    assert shared_environment["RAGFLOW_TLS_SPKI_PINS"] == "${RAGFLOW_TLS_SPKI_PINS:-}"
    assert shared_environment["LLM_TLS_SPKI_PINS"] == "${LLM_TLS_SPKI_PINS:-}"
    for service_name in ("backend-api", "worker-ai", "worker-ragflow"):
        service_environment = compose["services"][service_name]["environment"]
        for variable in ("RAGFLOW_TLS_SPKI_PINS", "LLM_TLS_SPKI_PINS"):
            assert service_environment[variable] == shared_environment[variable]

    env_example = _read(ENV_EXAMPLE)
    assert env_example.count("RAGFLOW_TLS_SPKI_PINS=") == 1
    assert env_example.count("LLM_TLS_SPKI_PINS=") == 1
    assert '{"https://ragflow.example.invalid/api":["sha256/<base64-spki-sha256>"]}' in env_example
    assert '{"https://llm.example.invalid/v1":["sha256/<base64-spki-sha256>"]}' in env_example


def test_protected_examples_use_exact_https_endpoint_pin_mappings() -> None:
    deployment = _read(DEPLOYMENT)
    fullwidth_colon = chr(0xFF1A)
    for marker, base_key, allowed_key, pins_key in (
        (
            f"Protected RAGFlow 配置示例{fullwidth_colon}",
            "RAGFLOW_BASE_URL",
            "RAGFLOW_ALLOWED_BASE_URLS",
            "RAGFLOW_TLS_SPKI_PINS",
        ),
        (
            f"Protected 内部非计费 LLM 配置示例{fullwidth_colon}",
            "LLM_BASE_URL",
            "LLM_ALLOWED_BASE_URLS",
            "LLM_TLS_SPKI_PINS",
        ),
    ):
        block = _env_block_after(deployment, marker)
        values = _assignments(block)
        assert "http://" not in block
        assert values[base_key].startswith("https://")
        assert values[allowed_key] == values[base_key]
        mapping = json.loads(values[pins_key])
        assert list(mapping) == [values[base_key]]
        assert mapping[values[base_key]] == ["sha256/<base64-spki-sha256>"]

    for requirement in (
        "仅限 `development` 本地联调",
        "缺 pin",
        "fail closed",
        "同一 pin 禁止跨 hostname 复用",
    ):
        assert requirement in deployment


def test_database_base_urls_cannot_expand_environment_allowlist_or_pin_boundaries() -> None:
    contract = _read(CONFIG_CONTRACT)
    for requirement in (
        "`ragflow.base_url`",
        "`RAGFLOW_ALLOWED_BASE_URLS`",
        "`RAGFLOW_TLS_SPKI_PINS`",
        "`ai_providers.base_url`",
        "`LLM_ALLOWED_BASE_URLS`",
        "`LLM_TLS_SPKI_PINS`",
        "不能扩大 allowlist 或 pin 边界",
        "不能新增受信 endpoint",
        "不同 hostname",
    ):
        assert requirement in contract
