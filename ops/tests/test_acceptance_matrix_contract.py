from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
MATRIX = ROOT / "docs" / "product" / "ACCEPTANCE_MATRIX.md"
README = ROOT / "README.md"
PHASE_PLAN = ROOT / "需求文档" / "08_TASK_BREAKDOWN_开发任务拆解.md"
PROTECTED_RELEASE_RUNBOOK = ROOT / "ops" / "runbooks" / "protected-release.md"
DEPLOYMENT = ROOT / "docs" / "deployment.md"
CONFIG_CONTRACT = ROOT / "docs" / "product" / "CONFIG_CONTRACT.md"
DEPLOYMENT_ENV = ROOT / "需求文档" / "07_DEPLOYMENT_ENV_部署与环境配置.md"
EXTERNAL_ACCEPTANCE_IDS = (
    "EXT-SMTP-001",
    "EXT-WEBHOOK-001",
    "EXT-LLM-001",
    "EXT-RAGFLOW-001",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _matrix_row(matrix: str, acceptance_id: str) -> str:
    matches = re.findall(rf"^\|\s*{re.escape(acceptance_id)}\s*\|.*$", matrix, re.MULTILINE)
    assert len(matches) == 1, f"{acceptance_id} must have exactly one matrix row"
    return matches[0]


def test_external_acceptance_ids_are_unique_and_traceable() -> None:
    matrix = _read(MATRIX)
    readme = _read(README)
    phase_plan = _read(PHASE_PLAN)
    runbook = _read(PROTECTED_RELEASE_RUNBOOK)

    for acceptance_id in EXTERNAL_ACCEPTANCE_IDS:
        _matrix_row(matrix, acceptance_id)
        assert acceptance_id in readme
        assert acceptance_id in phase_plan
        assert acceptance_id in runbook


def test_ai_001_is_protocol_only_and_external_contracts_remain_explicit() -> None:
    matrix = _read(MATRIX)
    ai_row = _matrix_row(matrix, "AI-001")
    llm_row = _matrix_row(matrix, "EXT-LLM-001")
    ragflow_row = _matrix_row(matrix, "EXT-RAGFLOW-001")

    assert "LLM 协议与失败分类" in ai_row
    assert "真 LLM" not in ai_row
    assert "内部非计费" in llm_row
    assert "COST-002" in llm_row
    expected_status = f"未完成{chr(0xFF08)}发布阻断"
    for external_row in (llm_row, ragflow_row):
        assert expected_status in external_row
    for requirement in ("独立受保护 workflow", "HTTPS/SPKI", "环境所有者签名"):
        assert requirement in ragflow_row


def test_minimum_release_decision_names_all_non_waivable_external_gates() -> None:
    matrix = _read(MATRIX)
    minimum_release = matrix.split("## 最低发布判定", maxsplit=1)[1]

    for acceptance_id in (*EXTERNAL_ACCEPTANCE_IDS, "REL-001"):
        assert acceptance_id in minimum_release


def test_cost_002_protected_runtime_gate_is_consistent() -> None:
    contracts = (_read(DEPLOYMENT), _read(CONFIG_CONTRACT), _read(DEPLOYMENT_ENV))

    for contract in contracts:
        for required in (
            "COST-002",
            "staging",
            "production",
            "ALLOW_EXTERNAL_LLM=false",
            "ALLOW_EXTERNAL_LLM=true",
            "development",
            "受控开发",
            "内部非计费",
        ):
            assert required in contract
        assert "拒绝" in contract or "启动失败" in contract

    assert "系统管理员确认后开启" not in _read(DEPLOYMENT)
