# ruff: noqa: RUF001 -- contract strings intentionally mirror Chinese authority documents.

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote
from uuid import uuid4

import pytest

import scripts.acceptance_launcher as acceptance_launcher
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
    tmp_path: Path,
) -> None:
    with pytest.raises(BaselineContractError, match="exactly 40 hexadecimal"):
        verify_candidate_identity("deadbeef")

    expected_sha = "a" * 40

    def reject_dirty_candidate(*_args: object, **_kwargs: object) -> None:
        raise baseline_contract.AcceptanceGitError(
            "tracked and non-ignored untracked worktree must be clean"
        )

    monkeypatch.setattr(baseline_contract, "verify_git_snapshot", reject_dirty_candidate)
    with pytest.raises(BaselineContractError, match="non-ignored untracked"):
        baseline_contract.verify_candidate_identity(expected_sha)

    with pytest.raises(BaselineContractError, match="outside the repository"):
        baseline_contract._prepare_output_dir(ROOT / "artifacts" / "baseline-evidence")

    expected_nodes = baseline_contract.BASELINE_TEST_NODES
    assert len(expected_nodes) == 13

    def write_junit(
        path: Path,
        *,
        skipped_type: str | None = None,
        skip_all: bool = False,
        drop_last: bool = False,
        duplicate_first: bool = False,
    ) -> None:
        names = [nodeid.rsplit("::", maxsplit=1)[1] for nodeid in expected_nodes]
        if drop_last:
            names.pop()
        if duplicate_first:
            names[0] = names[1]
        skipped_indexes = (
            set(range(len(names))) if skip_all else ({0} if skipped_type is not None else set())
        )
        testcases = []
        for index, name in enumerate(names):
            outcome = f'<skipped type="{skipped_type}" />' if index in skipped_indexes else ""
            testcases.append(
                '<testcase classname="ops.tests.test_baseline_document_contract" '
                f'name="{name}">{outcome}</testcase>'
            )
        payload = (
            '<?xml version="1.0" encoding="utf-8"?>'
            "<testsuites>"
            '<testsuite name="pytest" errors="0" failures="0" '
            f'skipped="{len(skipped_indexes)}" tests="{len(expected_nodes)}">'
            + "".join(testcases)
            + "</testsuite></testsuites>"
        )
        path.write_text(payload, encoding="utf-8")

    valid_junit = tmp_path / "valid.xml"
    write_junit(valid_junit)
    passed_count, observed_nodes = baseline_contract._validate_baseline_junit(valid_junit)
    assert passed_count == 13
    assert set(observed_nodes) == set(expected_nodes)

    malformed_junit = tmp_path / "malformed.xml"
    malformed_junit.write_text("<testsuites>", encoding="utf-8")
    with pytest.raises(BaselineContractError, match="missing or invalid"):
        baseline_contract._validate_baseline_junit(malformed_junit)

    for skipped_type in ("pytest.skip", "pytest.xfail"):
        skipped_junit = tmp_path / f"{skipped_type}.xml"
        write_junit(skipped_junit, skipped_type=skipped_type)
        with pytest.raises(BaselineContractError, match="exactly 13 expected nodes"):
            baseline_contract._validate_baseline_junit(skipped_junit)

    missing_junit = tmp_path / "missing.xml"
    write_junit(missing_junit, drop_last=True)
    with pytest.raises(BaselineContractError, match="testcase count"):
        baseline_contract._validate_baseline_junit(missing_junit)

    duplicate_junit = tmp_path / "duplicate.xml"
    write_junit(duplicate_junit, duplicate_first=True)
    with pytest.raises(BaselineContractError, match="node identity mismatch"):
        baseline_contract._validate_baseline_junit(duplicate_junit)

    temporary_junit_paths: list[Path] = []
    simulate_all_skipped = False

    def fake_pytest_run(
        command: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        assert command[1:10] == [
            "-I",
            "-S",
            "-X",
            "utf8",
            "-X",
            command[6],
            "-c",
            baseline_contract.PYTEST_BOOTSTRAP,
            "-q",
        ]
        assert command[6].startswith("pycache_prefix=")
        assert "-c" in command
        assert command[command.index("--rootdir") + 1] == str(ROOT)
        assert "--runxfail" in command
        assert "-p" not in command
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        normalized_environment = {str(key).upper(): value for key, value in environment.items()}
        assert "PYTEST_ADDOPTS" not in normalized_environment
        assert "PYTEST_PLUGINS" not in normalized_environment
        assert normalized_environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
        assert normalized_environment["KNOWLEDGE_UPLOADER_ACCEPTANCE_TOKEN"]
        assert Path(normalized_environment["KNOWLEDGE_UPLOADER_ACCEPTANCE_MARKER"]).is_file()
        assert command[-len(expected_nodes) :] == list(expected_nodes)
        junit_path = Path(command[command.index("--junitxml") + 1])
        temporary_junit_paths.append(junit_path)
        write_junit(
            junit_path,
            skipped_type="pytest.skip" if simulate_all_skipped else None,
            skip_all=simulate_all_skipped,
        )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="SENSITIVE_STDOUT_MUST_NOT_BE_PERSISTED",
            stderr="SENSITIVE_STDERR_MUST_NOT_BE_PERSISTED",
        )

    monkeypatch.setattr(baseline_contract.subprocess, "run", fake_pytest_run)
    log, exit_code, passed_count, observed_nodes = baseline_contract._run_baseline_tests()
    assert exit_code == 0
    assert passed_count == 13
    assert set(observed_nodes) == set(expected_nodes)
    assert "SENSITIVE_" not in log
    assert not temporary_junit_paths[-1].parent.exists()

    simulate_all_skipped = True
    with pytest.raises(BaselineContractError, match="exactly 13 expected nodes"):
        baseline_contract._run_baseline_tests()
    assert not temporary_junit_paths[-1].parent.exists()

    runner = _read(ROOT / "scripts" / "check_baseline_contract.py")
    evidence = _read(BASELINE_EVIDENCE)
    assert 'parser.add_argument("--expected-git-sha", required=True)' in runner
    assert 'parser.add_argument("--output-dir", type=Path, required=True)' in runner
    assert '"external_release_status": "not_evaluated"' in runner
    assert "verify_git_snapshot" in runner
    assert "spec_from_file_location" in runner
    assert "from scripts.acceptance_git import" not in runner
    provenance = _read(ROOT / "scripts" / "acceptance_git.py")
    assert '"--untracked-files=all"' in provenance
    assert "--no-replace-objects" in provenance
    assert "core.hooksPath" in provenance
    assert "Git index hiding flags are forbidden" in provenance
    assert "executed sources do not match candidate commit blobs" in provenance
    assert baseline_contract.PYTEST_CONFIG in baseline_contract.SOURCE_FILES
    assert baseline_contract.ACCEPTANCE_ENTRY_SCRIPT in baseline_contract.SOURCE_FILES
    assert baseline_contract.ACCEPTANCE_LAUNCHER_SCRIPT in baseline_contract.SOURCE_FILES
    assert "untracked or ignored Python execution inputs are forbidden" in provenance
    assert "Git 2.36 or newer is required" in provenance
    assert "最终整合后的 SHA 尚未绑定" in evidence
    assert "python -I -S -X utf8 scripts/acceptance_launcher.py baseline" in evidence
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


def _fresh_external_prefix(label: str) -> Path:
    base = (
        Path(os.environ.get("SYSTEMDRIVE", "C:") + os.sep) / "tmp"
        if os.name == "nt"
        else Path("/tmp")
    )
    return base / f"ku-{label}-pycache-{uuid4().hex}"


def test_baseline_entry_requires_trusted_launcher_and_cleans_runtime() -> None:
    script = ROOT / "scripts" / "check_baseline_contract.py"
    launcher = ROOT / "scripts" / "acceptance_launcher.py"
    unsafe = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=ROOT,
        env=dict(os.environ),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert unsafe.returncode != 0
    assert "isolated mode (-I) is required" in unsafe.stderr

    direct_prefix = _fresh_external_prefix("baseline-direct")
    direct_command = [
        sys.executable,
        "-I",
        "-S",
        "-X",
        "utf8",
        "-X",
        f"pycache_prefix={direct_prefix}",
        str(script),
        "--help",
    ]
    direct = subprocess.run(
        direct_command,
        cwd=ROOT,
        env=dict(os.environ),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert direct.returncode != 0
    assert "trusted acceptance launcher is required" in direct.stderr

    preseeded_prefix = _fresh_external_prefix("baseline-preseeded")
    preseeded_prefix.mkdir(parents=True)
    (preseeded_prefix / "payload.pyc").write_bytes(b"untrusted")
    preseeded_command = direct_command.copy()
    preseeded_command[6] = f"pycache_prefix={preseeded_prefix}"
    preseeded = subprocess.run(
        preseeded_command,
        cwd=ROOT,
        env=dict(os.environ),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert preseeded.returncode != 0
    assert "trusted acceptance launcher is required" in preseeded.stderr

    runtime_parent = _fresh_external_prefix("baseline-launcher-parent")
    runtime_parent.mkdir(parents=True)
    environment = dict(os.environ)
    environment.update(
        {
            "TEMP": str(runtime_parent),
            "TMP": str(runtime_parent),
            "TMPDIR": str(runtime_parent),
        }
    )
    launched = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-X",
            "utf8",
            str(launcher),
            "baseline",
            "--help",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert launched.returncode == 0, launched.stderr
    assert "--expected-git-sha" in launched.stdout
    assert list(runtime_parent.iterdir()) == []
    runtime_parent.rmdir()


def test_baseline_pytest_environment_is_minimal_and_drops_secret_sentinels(
    tmp_path: Path,
) -> None:
    environment = baseline_contract._pytest_environment(
        {
            "PATH": "trusted-path",
            "PATHEXT": ".EXE",
            "PYTEST_ADDOPTS": "-p hostile",
            "PYTEST_PLUGINS": "hostile_plugin",
            "PYTHONPATH": "hostile-import-root",
            "APP_ENV": "production",
            "DOCKER_CONFIG": "secret-docker-config",
            "AWS_SECRET_ACCESS_KEY": "secret-sentinel",
            "NPM_TOKEN": "secret-sentinel",
        },
        runtime_dir=tmp_path,
    )

    assert environment["PATH"] == "trusted-path"
    assert environment["PATHEXT"] == ".EXE"
    assert environment["APP_ENV"] == "test"
    assert environment["PYTHONUTF8"] == "1"
    assert environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
    assert environment["TEMP"] == str(tmp_path.resolve())
    for forbidden in (
        "PYTEST_ADDOPTS",
        "PYTEST_PLUGINS",
        "PYTHONPATH",
        "DOCKER_CONFIG",
        "AWS_SECRET_ACCESS_KEY",
        "NPM_TOKEN",
    ):
        assert forbidden not in environment


def test_launcher_child_environment_is_minimal_and_drops_secret_sentinels(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "launcher-runtime"
    runtime.mkdir()
    environment = acceptance_launcher._child_environment(
        {
            "PATH": "trusted-path",
            "PATHEXT": ".EXE",
            "AWS_SECRET_ACCESS_KEY": "secret-sentinel",
            "NPM_TOKEN": "secret-sentinel",
            "DOCKER_HOST": "tcp://hostile.invalid",
            "PYTHONPATH": "hostile-import-root",
        },
        token="a" * 64,
        marker=str(runtime / acceptance_launcher.CLAIM_FILENAME),
        runtime=str(runtime),
    )
    assert environment["PATH"] == "trusted-path"
    assert environment["COMPOSE_DISABLE_ENV_FILE"] == "1"
    assert environment["DOCKER_CONFIG"] == str(runtime / "docker")
    for forbidden in (
        "AWS_SECRET_ACCESS_KEY",
        "NPM_TOKEN",
        "DOCKER_HOST",
        "PYTHONPATH",
    ):
        assert forbidden not in environment


def test_baseline_internal_entry_requires_consumed_launcher_claim() -> None:
    with pytest.raises(BaselineContractError, match="consumed launcher claim"):
        baseline_contract._require_isolated_runtime()
