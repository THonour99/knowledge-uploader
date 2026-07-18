# ruff: noqa: RUF001 -- contract strings intentionally mirror Chinese authority documents.

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

import pytest
import scripts.check_baseline_contract as baseline_contract
from scripts.check_baseline_contract import (
    BaselineContractError,
    audit_api_contract,
    audit_state_claims,
    verify_candidate_identity,
)

ROOT = Path(__file__).parents[2]
AGENTS = ROOT / "AGENTS.md"
README = ROOT / "README.md"
REQUIREMENTS = ROOT / "需求文档"
PRODUCT_DOCS = ROOT / "docs" / "product"
PRD = REQUIREMENTS / "01_PRD_产品需求文档.md"
ARCHITECTURE = REQUIREMENTS / "02_ARCHITECTURE_最终架构设计.md"
BACKEND_SPEC = REQUIREMENTS / "03_BACKEND_SPEC_后端开发规范.md"
FRONTEND_SPEC = REQUIREMENTS / "04_FRONTEND_SPEC_前端开发规范.md"
STATE_API_SPEC = REQUIREMENTS / "05_DATABASE_API_SPEC_数据库与API规范.md"
AI_RAGFLOW_SPEC = REQUIREMENTS / "06_AI_RAGFLOW_SPEC_AI与RAGFlow集成规范.md"
DEPLOYMENT_SPEC = REQUIREMENTS / "07_DEPLOYMENT_ENV_部署与环境配置.md"
PHASE_PLAN = REQUIREMENTS / "08_TASK_BREAKDOWN_开发任务拆解.md"
REQUIREMENTS_INDEX = REQUIREMENTS / "README.md"
DESIGN = ROOT / "docs" / "design" / "design.md"
SPARK_SUPPLEMENT = ROOT / "docs" / "spark" / "2026-06-04-p0-implementation-supplement.md"
IA = PRODUCT_DOCS / "IA_ROLE_WORKBENCH.md"
CONFIG = PRODUCT_DOCS / "CONFIG_CONTRACT.md"
MATRIX = PRODUCT_DOCS / "ACCEPTANCE_MATRIX.md"
BASELINE_EVIDENCE = PRODUCT_DOCS / "BASELINE_TRACEABILITY.md"

AGENT_REQUIRED_PATHS = (
    PRD,
    ARCHITECTURE,
    BACKEND_SPEC,
    STATE_API_SPEC,
    DEPLOYMENT_SPEC,
    PHASE_PLAN,
    DESIGN,
    SPARK_SUPPLEMENT,
)
INDEX_PRIORITY_TARGETS = (
    "./01_PRD_产品需求文档.md",
    "./02_ARCHITECTURE_最终架构设计.md",
    "./03_BACKEND_SPEC_后端开发规范.md",
    "./05_DATABASE_API_SPEC_数据库与API规范.md",
    "./07_DEPLOYMENT_ENV_部署与环境配置.md",
    "./08_TASK_BREAKDOWN_开发任务拆解.md",
    "../docs/design/design.md",
    "../docs/spark/2026-06-04-p0-implementation-supplement.md",
)
AUTHORITY_DOCUMENTS = (
    AGENTS,
    README,
    REQUIREMENTS_INDEX,
    PRD,
    ARCHITECTURE,
    BACKEND_SPEC,
    FRONTEND_SPEC,
    STATE_API_SPEC,
    AI_RAGFLOW_SPEC,
    DEPLOYMENT_SPEC,
    PHASE_PLAN,
    DESIGN,
    SPARK_SUPPLEMENT,
    IA,
    CONFIG,
    MATRIX,
    BASELINE_EVIDENCE,
)
EXPECTED_STATES = {
    "uploaded",
    "extracting_text",
    "analysis_queued",
    "analyzing",
    "analyzed",
    "analysis_failed",
    "sensitive_review_required",
    "pending_review",
    "approved",
    "rejected",
    "queued",
    "syncing",
    "uploaded_to_ragflow",
    "parsing",
    "parsed",
    "failed",
    "disabled",
    "deleted",
    "ragflow_cleanup_failed",
}
EXPECTED_PRODUCT_ROUTES = {
    "/login",
    "/register",
    "/forgot-password",
    "/verify-email",
    "/reset-password/:token",
    "/dashboard",
    "/upload",
    "/my-files",
    "/files",
    "/files/:id",
    "/task-logs",
    "/categories",
    "/tags",
    "/datasets",
    "/ai-config",
    "/statistics",
    "/audit-logs",
    "/users",
    "/departments",
    "/settings",
    "/profile",
}
MATRIX_STATUSES = (
    "待执行",
    "进行中",
    "通过",
    "失败",
    "豁免",
    "未完成（发布阻断）",
)
TRANSITION_REFERENCE_DOCUMENTS = (
    PRD,
    ARCHITECTURE,
    BACKEND_SPEC,
    FRONTEND_SPEC,
    AI_RAGFLOW_SPEC,
    PHASE_PLAN,
    DESIGN,
    SPARK_SUPPLEMENT,
    IA,
)
TRANSITION_EXPRESSION = re.compile(
    r"[a-z_]+(?:\|[a-z_]+)*(?:\s*(?:<->|->)\s*[a-z_]+(?:\|[a-z_]+)*)+"
)
TRANSITION_NEGATION_MARKERS = ("不得", "禁止", "不能", "非法")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _section(text: str, start: str, end: str) -> str:
    return text.split(start, maxsplit=1)[1].split(end, maxsplit=1)[0]


def _table_first_column(section: str) -> list[str]:
    return re.findall(r"^\|\s*`([^`]+)`\s*\|", section, re.MULTILINE)


def _backtick_routes(text: str) -> set[str]:
    return set(re.findall(r"`(/[^`?]+)(?:\?token=)?`", text))


def _plain_routes(text: str) -> set[str]:
    pattern = r"(?:^|[ \t])(/(?:[a-z][a-z0-9-]*)(?:/:?[a-z][a-z0-9-]*)?)"
    return set(re.findall(pattern, text, re.MULTILINE))


def _transition_edges(expression: str, states: set[str]) -> set[tuple[str, str]]:
    parts = re.split(r"\s*(<->|->)\s*", expression.strip())
    nodes = [set(part.split("|")) for part in parts[::2]]
    operators = parts[1::2]
    assert nodes and len(nodes) == len(operators) + 1
    unknown = set().union(*nodes) - states
    assert not unknown, f"unknown states in transition expression {expression!r}: {sorted(unknown)}"

    edges: set[tuple[str, str]] = set()
    for index, operator in enumerate(operators):
        left = nodes[index]
        right = nodes[index + 1]
        edges.update((source, target) for source in left for target in right)
        if operator == "<->":
            edges.update((target, source) for source in left for target in right)
    return edges


def test_authority_documents_and_local_links_are_traceable() -> None:
    agents = _read(AGENTS)
    requirements_index = _read(REQUIREMENTS_INDEX)
    root = ROOT.resolve()

    agent_positions: list[int] = []
    for required in AGENT_REQUIRED_PATHS:
        assert required.is_file(), f"missing authority document: {required.relative_to(ROOT)}"
        marker = required.relative_to(ROOT).as_posix()
        assert marker in agents
        agent_positions.append(agents.index(marker))
    assert agent_positions == sorted(agent_positions)

    index_positions = [requirements_index.index(target) for target in INDEX_PRIORITY_TARGETS]
    assert index_positions == sorted(index_positions)
    assert "补充 spec §5" in agents
    assert "补充 spec §9" not in agents

    link_pattern = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
    for document in AUTHORITY_DOCUMENTS:
        assert document.is_file(), f"missing baseline document: {document.relative_to(ROOT)}"
        for raw_target in link_pattern.findall(_read(document)):
            target = raw_target.strip()
            if target.startswith(("#", "http://", "https://", "mailto:")):
                continue
            relative_target = unquote(target.split("#", maxsplit=1)[0])
            assert relative_target, f"empty local link in {document.relative_to(ROOT)}"
            resolved = (document.parent / relative_target).resolve()
            assert resolved.is_relative_to(
                root
            ), f"local link escapes repository: {document.relative_to(ROOT)} -> {target}"
            assert resolved.exists(), f"broken local link: {document.relative_to(ROOT)} -> {target}"


def test_05_is_the_single_state_and_api_authority() -> None:
    assert "文件状态与 HTTP 契约的唯一权威源" in _read(STATE_API_SPEC)

    references = {
        AGENTS: "05_DATABASE_API_SPEC_数据库与API规范.md",
        README: "唯一状态机/API",
        REQUIREMENTS_INDEX: "唯一状态机与 HTTP 目标契约",
        PRD: "05_DATABASE_API_SPEC_数据库与API规范.md",
        ARCHITECTURE: "05_DATABASE_API_SPEC_数据库与API规范.md",
        BACKEND_SPEC: "05_DATABASE_API_SPEC_数据库与API规范.md",
        FRONTEND_SPEC: "05_DATABASE_API_SPEC_数据库与API规范.md",
        AI_RAGFLOW_SPEC: "05_DATABASE_API_SPEC_数据库与API规范.md",
        PHASE_PLAN: "05 文档成为唯一状态/API 权威源",
        DESIGN: "05_DATABASE_API_SPEC_数据库与API规范.md",
        SPARK_SUPPLEMENT: "05 状态/API 契约",
        IA: "05_DATABASE_API_SPEC_数据库与API规范.md",
    }
    for document, marker in references.items():
        assert marker in _read(
            document
        ), f"missing 05 authority marker: {document.relative_to(ROOT)}"


def test_cross_document_state_transitions_are_allowed_by_05() -> None:
    state_section = _section(_read(STATE_API_SPEC), "### 2.1 状态定义", "### 2.2 正常路径")
    canonical_states = set(_table_first_column(state_section))
    allowed_section = _section(_read(STATE_API_SPEC), "### 2.3 允许转换", "### 2.4 自动提交")
    allowed_edges: set[tuple[str, str]] = set()
    for expression in re.findall(r"`([^`]*(?:<->|->)[^`]*)`", allowed_section):
        normalized = expression.strip()
        assert TRANSITION_EXPRESSION.fullmatch(normalized), normalized
        allowed_edges.update(_transition_edges(normalized, canonical_states))
    assert allowed_edges

    for document in TRANSITION_REFERENCE_DOCUMENTS:
        for line_number, line in enumerate(_read(document).splitlines(), start=1):
            if any(marker in line for marker in TRANSITION_NEGATION_MARKERS):
                continue
            for expression in re.findall(r"`([^`]*(?:<->|->)[^`]*)`", line):
                normalized = expression.strip()
                tokens = set(re.findall(r"[a-z_]+", normalized))
                if not tokens.intersection(canonical_states):
                    continue
                assert TRANSITION_EXPRESSION.fullmatch(normalized), (
                    f"unparseable state transition in {document.relative_to(ROOT)}:"
                    f"{line_number}: {normalized}"
                )
                referenced_edges = _transition_edges(normalized, canonical_states)
                unexpected = referenced_edges - allowed_edges
                assert not unexpected, (
                    f"state transition conflicts with 05 in {document.relative_to(ROOT)}:"
                    f"{line_number}: {sorted(unexpected)}"
                )


def test_nonstructured_state_claims_are_exhaustively_classified() -> None:
    report = audit_state_claims(ROOT)

    assert report["canonical_state_count"] == 19
    assert report["canonical_allowed_edge_count"] == 48
    assert report["candidate_line_count"] == 33
    assert report["automatic_transition_line_count"] == 14
    assert report["manual_transition_line_count"] == 11
    assert report["state_set_line_count"] == 8
    assert report["referenced_allowed_edge_count"] == 48
    assert report["referenced_forbidden_edge_count"] == 3


def test_unclassified_natural_language_state_claim_fails_closed() -> None:
    tick = chr(96)
    mutated_prd = _read(PRD) + f"\n状态由 {tick}parsed{tick} 回退到 {tick}queued{tick}。\n"

    with pytest.raises(BaselineContractError, match="unclassified nonstructured"):
        audit_state_claims(
            ROOT,
            text_overrides={PRD.relative_to(ROOT): mutated_prd},
        )


def test_05_api_methods_paths_and_critical_fields_match_runtime_openapi() -> None:
    report = audit_api_contract(ROOT)
    raw_endpoints = report["endpoints"]
    assert isinstance(raw_endpoints, list)
    endpoints = {(item["method"], item["path"]) for item in raw_endpoints if isinstance(item, dict)}

    assert report["spec_endpoint_count"] == 105
    assert report["runtime_operation_count"] == 105
    assert report["runtime_path_count"] == 88
    assert report["body_contract_count"] == 13
    assert report["query_contract_count"] == 11
    assert ("GET", "/api/admin/configs") in endpoints
    assert ("GET", "/api/admin/audit-logs") in endpoints
    assert ("POST", "/api/notifications/{notification_id}/read") in endpoints
    assert ("GET", "/api/saved-views") in endpoints
    assert ("PATCH", "/api/saved-views/{saved_view_id}") in endpoints

    raw_exclusions = report["runtime_route_exclusions"]
    assert isinstance(raw_exclusions, list)
    exclusions = {
        (item["method"], item["path"]) for item in raw_exclusions if isinstance(item, dict)
    }
    assert len(exclusions) == 9
    assert ("GET", "/metrics") in exclusions
    assert ("HEAD", "/docs") in exclusions


def test_candidate_evidence_runner_rejects_ambiguous_identity_and_stays_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(BaselineContractError, match="exactly 40 hexadecimal"):
        verify_candidate_identity("deadbeef")

    expected_sha = "a" * 40

    def fake_run_git(arguments: list[str]) -> str:
        if arguments == ["rev-parse", "HEAD"]:
            return expected_sha
        if arguments == ["rev-parse", "HEAD^{tree}"]:
            return "b" * 40
        if arguments == ["status", "--porcelain=v1", "--untracked-files=all"]:
            return "?? unexpected.py"
        raise AssertionError(f"unexpected Git command: {arguments}")

    monkeypatch.setattr(baseline_contract, "_run_git", fake_run_git)
    with pytest.raises(BaselineContractError, match="non-ignored untracked"):
        baseline_contract.verify_candidate_identity(expected_sha)

    runner = _read(ROOT / "scripts" / "check_baseline_contract.py")
    evidence = _read(BASELINE_EVIDENCE)
    assert 'parser.add_argument("--expected-git-sha", required=True)' in runner
    assert 'parser.add_argument("--output-dir", type=Path, required=True)' in runner
    assert '"external_release_status": "not_evaluated"' in runner
    assert '"--untracked-files=all"' in runner
    assert "tracked and non-ignored untracked worktree must be clean" in runner
    assert "最终整合后的 SHA 尚未绑定" in evidence
    assert "external_release_status=not_evaluated" in evidence


def test_state_catalog_and_visual_mapping_are_identical() -> None:
    state_section = _section(_read(STATE_API_SPEC), "### 2.1 状态定义", "### 2.2 正常路径")
    canonical_states = set(_table_first_column(state_section))
    assert canonical_states == EXPECTED_STATES
    assert "draft" not in canonical_states
    assert "不再引入另一个含义重叠的 `draft` 状态" in _read(PRD)

    visual_section = _section(_read(DESIGN), "## 7. 状态映射", "## 8. 移动端")
    visual_states = {
        state
        for grouped_states in _table_first_column(visual_section)
        for state in grouped_states.split("/")
    }
    assert visual_states == canonical_states


def test_readme_frontend_spec_and_ia_share_one_product_route_set() -> None:
    readme_section = _section(_read(README), "### 5. 访问前端", "## 本地开发模式")
    readme_routes = set(_table_first_column(readme_section))
    frontend_routes = _backtick_routes(
        _section(_read(FRONTEND_SPEC), "## 2. 路由契约", "## 3. 页面状态")
    )
    ia_routes = _plain_routes(_section(_read(IA), "## 1. 导航原则", "## 2. 员工工作台"))

    assert readme_routes == EXPECTED_PRODUCT_ROUTES
    assert frontend_routes == EXPECTED_PRODUCT_ROUTES
    assert ia_routes == EXPECTED_PRODUCT_ROUTES


def test_config_contract_declared_counts_match_unique_table_keys() -> None:
    config = _read(CONFIG)
    active_section = _section(config, "## 2. Active 配置（26）", "## 3. Deleted 配置（15）")
    deleted_section = _section(
        config,
        "## 3. Deleted 配置（15）",
        "## 4. 启动与基础设施配置",
    )
    active_keys = set(re.findall(r"^\|\s*([a-z][a-z0-9_.]+)\s*\|", active_section, re.MULTILINE))
    deleted_keys = set(re.findall(r"^\|\s*([a-z][a-z0-9_.]+)\s*\|", deleted_section, re.MULTILINE))

    assert len(active_keys) == 26
    assert len(deleted_keys) == 15
    assert active_keys.isdisjoint(deleted_keys)
    assert "恰好包含 26 个 active key 和 15 个 deleted key" in config


def test_stage_nine_is_consistently_declared_incomplete() -> None:
    required_markers = {
        README: "阶段 9 尚未完成",
        PRD: "不是阶段 9 已完成",
        REQUIREMENTS_INDEX: "阶段 9（联调、上线与文档）尚未完成",
        PHASE_PLAN: "**验收整改进行中，未完成**",
        CONFIG: "不得声明阶段 9 完成",
    }
    for document, marker in required_markers.items():
        assert marker in _read(document)

    negative_context = (
        "尚未完成",
        "未完成",
        "不是",
        "不等于",
        "不得",
        "才能",
        "待补",
        "进行中",
        "阻断",
        "去除",
    )
    for document in AUTHORITY_DOCUMENTS:
        for line in _read(document).splitlines():
            if re.search(r"阶段\s*9", line) and "完成" in line:
                assert any(
                    marker in line for marker in negative_context
                ), f"positive stage-9 completion claim: {document.relative_to(ROOT)}: {line}"


def test_acceptance_matrix_ids_and_statuses_are_well_formed() -> None:
    rows = re.findall(
        r"^\|\s*([A-Z]+(?:-[A-Z0-9]+)+)\s*\|(.+)$",
        _read(MATRIX),
        re.MULTILINE,
    )
    ids = [acceptance_id for acceptance_id, _ in rows]
    assert ids
    assert len(ids) == len(set(ids))

    for acceptance_id, remainder in rows:
        current = remainder.rsplit("|", maxsplit=2)[-2].strip()
        assert current.startswith(MATRIX_STATUSES), f"invalid status for {acceptance_id}: {current}"


def test_base_001_records_progress_without_self_certifying_completion() -> None:
    matrix = _read(MATRIX)
    rows = re.findall(r"^\|\s*BASE-001\s*\|.*$", matrix, re.MULTILINE)
    assert len(rows) == 1
    assert "进行中（[本地基线验证器](./BASELINE_TRACEABILITY.md)已实现" in rows[0]
    assert "最终整合后的 clean HEAD JSON/原始日志仍须由根 Agent 重跑归档" in rows[0]

    evidence = _read(BASELINE_EVIDENCE)
    assert "python -m pytest ops/tests/test_baseline_document_contract.py -q" in evidence
    assert "当前判定：`进行中`" in evidence
    assert "`13 passed`" in evidence
    assert "不得提前改成“通过”" in evidence
    assert "不替代受保护 CI、真实基础设施、外部服务、DGX ARM64、告警或灾备证据" in evidence
