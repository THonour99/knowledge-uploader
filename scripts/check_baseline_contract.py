# ruff: noqa: E402, PTH118, PTH120, RUF001 -- isolation precedes imports.

"""Verify BASE-001 locally and bind its evidence to an exact clean Git commit."""

from __future__ import annotations

import importlib.util
import os
import sys
from typing import Protocol, cast


class _AcceptanceEntry(Protocol):
    CLAIM_FILENAME: str
    CLAIM_MARKER_ENV: str
    CLAIM_RUNTIME_ENV: str
    CLAIM_TOKEN_ENV: str

    def consume_launcher_claim(self, repo_root: str) -> None: ...

    def runtime_isolation_error(self, repo_root: str) -> str | None: ...


def _load_acceptance_entry() -> _AcceptanceEntry:
    module_path = os.path.join(os.path.dirname(__file__), "acceptance_entry.py")
    spec = importlib.util.spec_from_file_location(
        "knowledge_uploader_baseline_acceptance_entry",
        module_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("acceptance entry helper loader unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return cast(_AcceptanceEntry, module)


_acceptance_entry = _load_acceptance_entry()
_LAUNCHER_CLAIM_CONSUMED = False

if __name__ == "__main__":
    _repository = os.path.realpath(os.path.join(os.path.dirname(__file__), os.pardir))
    try:
        _acceptance_entry.consume_launcher_claim(_repository)
        _LAUNCHER_CLAIM_CONSUMED = True
    except RuntimeError as _claim_error:
        raise SystemExit(f"baseline contract refused: {_claim_error}") from _claim_error
    import site

    site.main()


import argparse
import hashlib
import json
import re
import secrets
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType
from typing import Final
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]


class _GitSnapshot(Protocol):
    head: str
    tree: str
    status: str


class _VerifyGitSnapshot(Protocol):
    def __call__(
        self,
        repo_root: Path,
        *,
        expected_sha: str,
        relative_paths: tuple[Path, ...],
        source_sha256: dict[str, str],
    ) -> _GitSnapshot: ...


def _load_acceptance_git() -> ModuleType:
    module_path = ROOT / "scripts" / "acceptance_git.py"
    spec = importlib.util.spec_from_file_location(
        "knowledge_uploader_baseline_acceptance_git",
        module_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("acceptance Git helper loader unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_acceptance_git = _load_acceptance_git()
AcceptanceGitError = cast(type[RuntimeError], _acceptance_git.AcceptanceGitError)
verify_git_snapshot = cast(_VerifyGitSnapshot, _acceptance_git.verify_git_snapshot)

STATE_API_SPEC = Path("需求文档/05_DATABASE_API_SPEC_数据库与API规范.md")
BASELINE_TEST = Path("ops/tests/test_baseline_document_contract.py")
BASELINE_TEST_NAMES: Final[tuple[str, ...]] = (
    "test_authority_documents_and_local_links_are_traceable",
    "test_05_is_the_single_state_and_api_authority",
    "test_cross_document_state_transitions_are_allowed_by_05",
    "test_nonstructured_state_claims_are_exhaustively_classified",
    "test_unclassified_natural_language_state_claim_fails_closed",
    "test_05_api_methods_paths_and_critical_fields_match_runtime_openapi",
    "test_candidate_evidence_runner_rejects_ambiguous_identity_and_stays_local",
    "test_state_catalog_and_visual_mapping_are_identical",
    "test_readme_frontend_spec_and_ia_share_one_product_route_set",
    "test_config_contract_declared_counts_match_unique_table_keys",
    "test_stage_nine_is_consistently_declared_incomplete",
    "test_acceptance_matrix_ids_and_statuses_are_well_formed",
    "test_base_001_records_progress_without_self_certifying_completion",
)
BASELINE_TEST_NODES: Final[tuple[str, ...]] = tuple(
    f"{BASELINE_TEST.as_posix()}::{name}" for name in BASELINE_TEST_NAMES
)
THIS_SCRIPT = Path("scripts/check_baseline_contract.py")
ACCEPTANCE_GIT_SCRIPT = Path("scripts/acceptance_git.py")
ACCEPTANCE_ENTRY_SCRIPT = Path("scripts/acceptance_entry.py")
ACCEPTANCE_LAUNCHER_SCRIPT = Path("scripts/acceptance_launcher.py")
PYTEST_CONFIG = Path("pyproject.toml")
PYTEST_BOOTSTRAP: Final = """
import importlib.util
import os
import runpy
import site
import sys

entry_path = os.path.join(os.getcwd(), "scripts", "acceptance_entry.py")
entry_spec = importlib.util.spec_from_file_location(
    "knowledge_uploader_baseline_pytest_entry",
    entry_path,
)
if entry_spec is None or entry_spec.loader is None:
    raise SystemExit("baseline pytest entry helper loader unavailable")
entry_module = importlib.util.module_from_spec(entry_spec)
sys.modules[entry_spec.name] = entry_module
entry_spec.loader.exec_module(entry_module)
try:
    entry_module.consume_launcher_claim(os.getcwd())
except RuntimeError as error:
    raise SystemExit(f"baseline pytest refused: {error}") from error
site.main()
runpy.run_module("pytest", run_name="__main__")
"""
TICK = chr(96)

AUTHORITY_DOCUMENTS: Final = (
    Path("AGENTS.md"),
    Path("README.md"),
    Path("需求文档/README.md"),
    Path("需求文档/01_PRD_产品需求文档.md"),
    Path("需求文档/02_ARCHITECTURE_最终架构设计.md"),
    Path("需求文档/03_BACKEND_SPEC_后端开发规范.md"),
    Path("需求文档/04_FRONTEND_SPEC_前端开发规范.md"),
    STATE_API_SPEC,
    Path("需求文档/06_AI_RAGFLOW_SPEC_AI与RAGFlow集成规范.md"),
    Path("需求文档/07_DEPLOYMENT_ENV_部署与环境配置.md"),
    Path("需求文档/08_TASK_BREAKDOWN_开发任务拆解.md"),
    Path("docs/design/design.md"),
    Path("docs/spark/2026-06-04-p0-implementation-supplement.md"),
    Path("docs/product/IA_ROLE_WORKBENCH.md"),
    Path("docs/product/CONFIG_CONTRACT.md"),
    Path("docs/product/ACCEPTANCE_MATRIX.md"),
    Path("docs/product/BASELINE_TRACEABILITY.md"),
)
SOURCE_FILES: Final = tuple(
    dict.fromkeys(
        (
            *AUTHORITY_DOCUMENTS,
            PYTEST_CONFIG,
            THIS_SCRIPT,
            ACCEPTANCE_GIT_SCRIPT,
            ACCEPTANCE_ENTRY_SCRIPT,
            ACCEPTANCE_LAUNCHER_SCRIPT,
            BASELINE_TEST,
        )
    )
)
GIT_SHA_PATTERN: Final = re.compile(r"[0-9a-f]{40}")
TRANSITION_EXPRESSION: Final = re.compile(
    r"[a-z_]+(?:\|[a-z_]+)*(?:\s*(?:<->|->)\s*[a-z_]+(?:\|[a-z_]+)*)+"
)
BACKTICK_EXPRESSION: Final = re.compile(re.escape(TICK) + r"([^\r\n]*?)" + re.escape(TICK))
BACKTICK_FRAGMENT: Final = re.compile(re.escape(TICK) + r"[^\r\n]*?" + re.escape(TICK))
ARROWISH: Final = re.compile(r"(?:<->|->|[─━-]+>|→|↔)")
NEGATION_MARKERS: Final = ("不得", "禁止", "不能", "非法")
TABLE_ENDPOINT: Final = re.compile(
    r"^\|\s*"
    + re.escape(TICK)
    + r"(GET|POST|PUT|PATCH|DELETE)"
    + re.escape(TICK)
    + r"\s*\|\s*"
    + re.escape(TICK)
    + r"(/api/[A-Za-z0-9_{}./-]+)"
    + re.escape(TICK),
    re.MULTILINE,
)
INLINE_ENDPOINT: Final = re.compile(
    re.escape(TICK)
    + r"(GET|POST|PUT|PATCH|DELETE) "
    + r"(/api/[A-Za-z0-9_{}./-]+)"
    + re.escape(TICK)
)


class BaselineContractError(RuntimeError):
    """A fail-closed baseline verification error."""


def _require_isolated_runtime() -> None:
    if not _LAUNCHER_CLAIM_CONSUMED:
        raise BaselineContractError("a consumed launcher claim is required")
    error = _acceptance_entry.runtime_isolation_error(str(ROOT))
    if error is not None:
        raise BaselineContractError(error)


MINIMAL_HOST_ENVIRONMENT_KEYS: Final = frozenset(
    {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "SYSTEMDRIVE"}
)


def _pytest_environment(source: dict[str, str], *, runtime_dir: Path) -> dict[str, str]:
    normalized: dict[str, tuple[str, str]] = {}
    for key, value in source.items():
        upper = key.upper()
        if upper not in MINIMAL_HOST_ENVIRONMENT_KEYS:
            continue
        if upper in normalized:
            raise BaselineContractError("ambiguous host environment key")
        normalized[upper] = (key, value)
    if "PATH" not in normalized:
        raise BaselineContractError("host PATH is required for baseline pytest")
    environment = {original: value for original, value in normalized.values()}
    external_runtime = str(runtime_dir.resolve())
    environment.update(
        {
            "TEMP": external_runtime,
            "TMP": external_runtime,
            "TMPDIR": external_runtime,
            "PYTHONUTF8": "1",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "APP_ENV": "test",
        }
    )
    return environment


class _PytestItem(Protocol):
    def get_closest_marker(self, name: str) -> object | None: ...


class _PytestSession(Protocol):
    items: list[_PytestItem]


def pytest_collection_finish(session: _PytestSession) -> None:
    """Reject xfail markers so an explicit strict=False XPASS cannot self-certify."""
    if any(item.get_closest_marker("xfail") is not None for item in session.items):
        raise BaselineContractError("baseline pytest nodes must not carry xfail markers")


def pytest_runtest_logreport(report: object) -> None:
    """Reject dynamically applied xfail/XPASS outcomes that appear after collection."""
    if getattr(report, "wasxfail", None) is not None:
        raise BaselineContractError("baseline pytest nodes must not produce xfail or XPASS")


Edge = tuple[str, str]


@dataclass(frozen=True)
class ManualStateClaim:
    document: str
    statement: str
    kind: str = "allowed_edges"
    edges: tuple[Edge, ...] = ()


@dataclass(frozen=True)
class ApiBodyContract:
    method: str
    path: str
    media_type: str
    required: frozenset[str]
    optional: frozenset[str]
    markers: tuple[str, ...]
    enums: tuple[tuple[str, frozenset[str]], ...] = ()


@dataclass(frozen=True)
class ApiQueryContract:
    method: str
    path: str
    parameters: frozenset[str]
    required: frozenset[str] = frozenset()
    enums: tuple[tuple[str, frozenset[str]], ...] = ()
    standard_pagination: bool = False
    max_lengths: tuple[tuple[str, int], ...] = ()


def _fields(value: str) -> frozenset[str]:
    return frozenset(value.split())


MANUAL_STATE_CLAIMS: Final = (
    ManualStateClaim(
        "AGENTS.md",
        f"- AI 关闭：跳过 {TICK}extracting_text{TICK} / {TICK}analysis_queued{TICK} / "
        f"{TICK}analyzing{TICK} / {TICK}analysis_failed{TICK} / {TICK}analyzed{TICK}",
        "state_set",
    ),
    ManualStateClaim(
        "需求文档/01_PRD_产品需求文档.md",
        f"- AI 开启：{TICK}uploaded -> extracting_text -> analysis_queued -> analyzing{TICK}，"
        f"成功后进入 {TICK}analyzed{TICK}，发现需人工确认的敏感内容进入 "
        f"{TICK}sensitive_review_required{TICK}，失败进入 {TICK}analysis_failed{TICK}。",
        edges=(
            ("analyzing", "analyzed"),
            ("analyzing", "sensitive_review_required"),
            ("analyzing", "analysis_failed"),
        ),
    ),
    ManualStateClaim(
        str(STATE_API_SPEC),
        "uploaded ──手工/自动提交──> pending_review",
        edges=(("uploaded", "pending_review"),),
    ),
    ManualStateClaim(
        str(STATE_API_SPEC),
        "uploaded -> extracting_text -> analysis_queued -> analyzing",
        edges=(
            ("uploaded", "extracting_text"),
            ("extracting_text", "analysis_queued"),
            ("analysis_queued", "analyzing"),
        ),
    ),
    ManualStateClaim(
        str(STATE_API_SPEC),
        "├-> analyzed ──提交──> pending_review",
        edges=(("analyzing", "analyzed"), ("analyzed", "pending_review")),
    ),
    ManualStateClaim(
        str(STATE_API_SPEC),
        "├-> sensitive_review_required ──人工确认──> pending_review",
        edges=(
            ("analyzing", "sensitive_review_required"),
            ("sensitive_review_required", "pending_review"),
        ),
    ),
    ManualStateClaim(
        str(STATE_API_SPEC),
        "└-> analysis_failed ──策略允许提交──> pending_review",
        edges=(("analyzing", "analysis_failed"), ("analysis_failed", "pending_review")),
    ),
    ManualStateClaim(
        str(STATE_API_SPEC),
        "pending_review ──驳回──> rejected ──重提──> pending_review",
        edges=(("pending_review", "rejected"), ("rejected", "pending_review")),
    ),
    ManualStateClaim(
        str(STATE_API_SPEC),
        "pending_review ──批准且 approve_only──> approved",
        edges=(("pending_review", "approved"),),
    ),
    ManualStateClaim(
        str(STATE_API_SPEC),
        "pending_review ──批准且 sync──> approved -> queued -> syncing",
        edges=(
            ("pending_review", "approved"),
            ("approved", "queued"),
            ("queued", "syncing"),
        ),
    ),
    ManualStateClaim(
        str(STATE_API_SPEC),
        "-> uploaded_to_ragflow -> parsing -> parsed",
        edges=(
            ("syncing", "uploaded_to_ragflow"),
            ("uploaded_to_ragflow", "parsing"),
            ("parsing", "parsed"),
        ),
    ),
    ManualStateClaim(
        str(STATE_API_SPEC),
        f"任何未列边非法并返回 409/422；{TICK}queued/syncing/parsing{TICK} "
        "等运行态不能直接删除。状态变更必须同时留下审计或领域事件证据。",
        "state_set",
    ),
    ManualStateClaim(
        str(STATE_API_SPEC),
        f"- {TICK}submit_after_upload=false{TICK}：AI 关停在 {TICK}uploaded{TICK}；"
        f"AI 开停在 {TICK}analyzed{TICK}/{TICK}analysis_failed{TICK}/"
        f"{TICK}sensitive_review_required{TICK}。",
        "state_set",
    ),
    ManualStateClaim(
        "需求文档/06_AI_RAGFLOW_SPEC_AI与RAGFlow集成规范.md",
        f"- {TICK}critical{TICK} 永远停在 {TICK}sensitive_review_required{TICK}；"
        f"其他风险在策略允许时可进入 {TICK}pending_review{TICK}。",
        "state_set",
    ),
    ManualStateClaim(
        "需求文档/06_AI_RAGFLOW_SPEC_AI与RAGFlow集成规范.md",
        "-> parsed / failed",
        edges=(("parsing", "parsed"), ("parsing", "failed")),
    ),
    ManualStateClaim(
        "docs/design/design.md",
        f"| {TICK}extracting_text/analysis_queued/analyzing{TICK} | AI 处理中 | info |",
        "state_set",
    ),
    ManualStateClaim(
        "docs/design/design.md",
        f"| {TICK}queued/syncing/uploaded_to_ragflow/parsing{TICK} | 入库处理中 | info |",
        "state_set",
    ),
    ManualStateClaim(
        "docs/design/design.md",
        f"| {TICK}failed/ragflow_cleanup_failed{TICK} | 处理失败 | danger |",
        "state_set",
    ),
    ManualStateClaim(
        "docs/design/design.md",
        f"| {TICK}disabled/deleted{TICK} | 已归档/已删除 | neutral |",
        "state_set",
    ),
)

BODY_CONTRACTS: Final = (
    ApiBodyContract(
        "POST",
        "/api/auth/register",
        "application/json",
        _fields("name email password"),
        _fields("department_id phone"),
        (
            f"JSON 必填 {TICK}name,email,password{TICK}",
            f"{TICK}department_id{TICK}",
            f"{TICK}phone?{TICK}",
        ),
    ),
    ApiBodyContract(
        "POST",
        "/api/auth/login",
        "application/json",
        _fields("email password"),
        frozenset(),
        (f"JSON 必填 {TICK}email,password{TICK}",),
    ),
    ApiBodyContract(
        "POST",
        "/api/auth/verify-email",
        "application/json",
        _fields("token"),
        frozenset(),
        (f"JSON 必填 {TICK}token{TICK}",),
    ),
    ApiBodyContract(
        "POST",
        "/api/auth/resend-verification",
        "application/json",
        _fields("email"),
        frozenset(),
        (f"JSON 必填 {TICK}email{TICK}",),
    ),
    ApiBodyContract(
        "POST",
        "/api/auth/forgot-password",
        "application/json",
        _fields("email"),
        frozenset(),
        (f"JSON 必填 {TICK}email{TICK}",),
    ),
    ApiBodyContract(
        "POST",
        "/api/auth/reset-password",
        "application/json",
        _fields("token new_password"),
        frozenset(),
        (f"JSON 必填 {TICK}token,new_password{TICK}",),
    ),
    ApiBodyContract(
        "POST",
        "/api/files/upload",
        "multipart/form-data",
        _fields("file submit_after_upload"),
        _fields("description visibility ai_analysis_enabled replaces_file_id"),
        (
            f"multipart 必填 {TICK}file,submit_after_upload{TICK}",
            f"{TICK}description?,visibility?=private,ai_analysis_enabled?,replaces_file_id?{TICK}",
        ),
    ),
    ApiBodyContract(
        "POST",
        "/api/files/{file_id}/approve",
        "application/json",
        _fields("sync_decision"),
        _fields("dataset_mapping_id category_id reason"),
        (
            f"JSON 必填 {TICK}sync_decision{TICK}",
            f"{TICK}dataset_mapping_id?,category_id?,reason?{TICK}",
        ),
        (("sync_decision", _fields("sync approve_only")),),
    ),
    ApiBodyContract(
        "POST",
        "/api/files/{file_id}/reject",
        "application/json",
        _fields("reason"),
        frozenset(),
        (f"{TICK}reason{TICK} 必填",),
    ),
    ApiBodyContract(
        "POST",
        "/api/admin/files/{file_id}/sync",
        "application/json",
        _fields("dataset_mapping_id"),
        _fields("reason"),
        (f"{TICK}dataset_mapping_id{TICK}", f"1–1000 字符的 {TICK}reason{TICK}"),
    ),
    ApiBodyContract(
        "PUT",
        "/api/admin/configs/{group}",
        "application/json",
        _fields("items"),
        frozenset(),
        (f"JSON 必填 {TICK}items{TICK}",),
    ),
    ApiBodyContract(
        "POST",
        "/api/saved-views",
        "application/json",
        _fields("page_key name definition_schema_version"),
        _fields("scope department_id query_definition column_preferences"),
        (
            f"JSON 必填 {TICK}page_key,name,definition_schema_version{TICK}",
            f"可选 {TICK}scope,department_id,query_definition,column_preferences{TICK}",
        ),
        (
            ("page_key", _fields("my_files review_files task_logs statistics")),
            ("scope", _fields("private department")),
        ),
    ),
    ApiBodyContract(
        "PATCH",
        "/api/saved-views/{saved_view_id}",
        "application/json",
        _fields("row_version"),
        _fields("name definition_schema_version query_definition column_preferences"),
        (
            f"JSON 必填 {TICK}row_version{TICK}",
            f"可选 {TICK}name,definition_schema_version,query_definition,column_preferences{TICK}",
        ),
    ),
)

QUERY_CONTRACTS: Final = (
    ApiQueryContract(
        "GET",
        "/api/files",
        _fields("page page_size q status extension tag_id expiry_status sort order"),
        enums=(
            ("expiry_status", _fields("never active expiring expired")),
            ("sort", _fields("uploaded_at updated_at original_name title size status")),
            ("order", _fields("asc desc")),
        ),
        standard_pagination=True,
    ),
    ApiQueryContract(
        "GET",
        "/api/files/{file_id}/content",
        _fields("disposition"),
        enums=(("disposition", _fields("inline attachment")),),
    ),
    ApiQueryContract(
        "GET",
        "/api/review/files",
        _fields(
            "page page_size q queue extension tag_id department_id sensitive_risk_level sort order"
        ),
        enums=(
            ("queue", _fields("unclaimed mine due_soon overdue")),
            ("sensitive_risk_level", _fields("none low medium high critical")),
            ("sort", _fields("submitted_at review_due_at uploaded_at original_name risk")),
            ("order", _fields("asc desc")),
        ),
        standard_pagination=True,
    ),
    ApiQueryContract(
        "GET",
        "/api/tasks",
        _fields("file_id task_type status department_id sort order page page_size"),
        enums=(
            (
                "task_type",
                _fields("ragflow_upload ragflow_parse ragflow_status_check ragflow_delete"),
            ),
            ("status", _fields("queued running succeeded failed canceled")),
            ("sort", _fields("created_at updated_at started_at finished_at")),
            ("order", _fields("asc desc")),
        ),
        standard_pagination=True,
    ),
    ApiQueryContract(
        "GET",
        "/api/notifications",
        _fields("page page_size unread_only"),
        standard_pagination=True,
    ),
    ApiQueryContract(
        "GET",
        "/api/admin/configs",
        _fields("group"),
        required=_fields("group"),
    ),
    ApiQueryContract(
        "GET",
        "/api/admin/audit-logs",
        _fields("page page_size actor_id action target_type created_from created_to"),
        standard_pagination=True,
    ),
    ApiQueryContract(
        "GET",
        "/api/admin/statistics/capacity",
        _fields("start_at end_before group_by physical_dimension page page_size"),
        enums=(
            (
                "group_by",
                _fields("none department file_type processing_stage day"),
            ),
            ("physical_dimension", _fields("cluster department file_type")),
        ),
        standard_pagination=True,
    ),
    ApiQueryContract(
        "GET",
        "/api/admin/statistics/llm-usage",
        _fields("start_at end_before group_by page page_size"),
        enums=(("group_by", _fields("none department provider model day")),),
        standard_pagination=True,
    ),
    ApiQueryContract(
        "GET",
        "/api/admin/statistics/ragflow-usage",
        _fields("start_at end_before group_by page page_size"),
        enums=(
            (
                "group_by",
                _fields("none department operation result failure_category day"),
            ),
        ),
        standard_pagination=True,
    ),
    ApiQueryContract(
        "GET",
        "/api/saved-views",
        _fields("page_key scope q page page_size"),
        required=_fields("page_key"),
        enums=(
            ("page_key", _fields("my_files review_files task_logs statistics")),
            ("scope", _fields("private department")),
        ),
        standard_pagination=True,
        max_lengths=(("q", 200),),
    ),
)

RUNTIME_ROUTE_EXCLUSIONS: Final = {
    ("GET", "/openapi.json"): "FastAPI OpenAPI discovery document",
    ("HEAD", "/openapi.json"): "FastAPI OpenAPI discovery HEAD route",
    ("GET", "/docs"): "FastAPI Swagger UI",
    ("HEAD", "/docs"): "FastAPI Swagger UI HEAD route",
    ("GET", "/docs/oauth2-redirect"): "FastAPI Swagger OAuth redirect",
    ("HEAD", "/docs/oauth2-redirect"): "FastAPI Swagger OAuth redirect HEAD route",
    ("GET", "/redoc"): "FastAPI ReDoc UI",
    ("HEAD", "/redoc"): "FastAPI ReDoc UI HEAD route",
    ("GET", "/metrics"): "Prometheus operational scrape endpoint",
}
API_SEMANTIC_MARKERS: Final = (
    "private_per_owner_page:100",
    "department_per_department_page:100",
    f"{TICK}SAVED_VIEW_QUOTA_EXCEEDED{TICK}",
    "历史超额数据仍可读取、搜索、修改和删除",
)
API_METHODS: Final = ("get", "post", "put", "patch", "delete")


def _read(root: Path, relative_path: Path) -> str:
    try:
        return (root / relative_path).read_text(encoding="utf-8")
    except OSError as error:
        raise BaselineContractError(f"cannot read baseline source: {relative_path}") from error


def _document_text(
    root: Path,
    relative_path: Path,
    text_overrides: dict[Path, str] | None,
) -> str:
    if text_overrides is not None and relative_path in text_overrides:
        return text_overrides[relative_path]
    return _read(root, relative_path)


def _section(text: str, start: str, end: str) -> str:
    if start not in text or end not in text:
        raise BaselineContractError(f"missing section boundary: {start}")
    return text.split(start, maxsplit=1)[1].split(end, maxsplit=1)[0]


def _transition_edges(expression: str, states: set[str]) -> set[Edge]:
    parts = re.split(r"\s*(<->|->)\s*", expression.strip())
    nodes = [set(part.split("|")) for part in parts[::2]]
    operators = parts[1::2]
    if not nodes or len(nodes) != len(operators) + 1:
        raise BaselineContractError(f"unparseable transition expression: {expression}")
    unknown = set().union(*nodes) - states
    if unknown:
        raise BaselineContractError(f"unknown states in transition expression: {sorted(unknown)}")
    edges: set[Edge] = set()
    for index, operator in enumerate(operators):
        left = nodes[index]
        right = nodes[index + 1]
        edges.update((source, target) for source in left for target in right)
        if operator == "<->":
            edges.update((target, source) for source in left for target in right)
    return edges


def _canonical_state_contract(
    root: Path,
    text_overrides: dict[Path, str] | None = None,
) -> tuple[set[str], set[Edge]]:
    text = _document_text(root, STATE_API_SPEC, text_overrides)
    state_section = _section(text, "### 2.1 状态定义", "### 2.2 正常路径")
    state_pattern = (
        r"^\|\s*" + re.escape(TICK) + r"([^" + re.escape(TICK) + r"]+)" + re.escape(TICK) + r"\s*\|"
    )
    states = set(re.findall(state_pattern, state_section, re.MULTILINE))
    allowed_section = _section(text, "### 2.3 允许转换", "### 2.4 自动提交")
    edges: set[Edge] = set()
    for raw in BACKTICK_EXPRESSION.findall(allowed_section):
        expression = raw.strip()
        if ARROWISH.search(expression) is None or not _state_tokens(expression, states):
            continue
        if TRANSITION_EXPRESSION.fullmatch(expression) is None:
            raise BaselineContractError(f"invalid canonical transition: {expression}")
        edges.update(_transition_edges(expression, states))
    if not states or not edges:
        raise BaselineContractError("canonical state contract is empty")
    return states, edges


def _state_tokens(text: str, states: set[str]) -> set[str]:
    return {
        state
        for state in states
        if re.search(rf"(?<![a-z_]){re.escape(state)}(?![a-z_])", text) is not None
    }


def audit_state_claims(
    root: Path = ROOT,
    *,
    text_overrides: dict[Path, str] | None = None,
) -> dict[str, object]:
    states, allowed_edges = _canonical_state_contract(root, text_overrides)
    manual_by_key = {
        (Path(claim.document).as_posix(), claim.statement): claim for claim in MANUAL_STATE_CLAIMS
    }
    if len(manual_by_key) != len(MANUAL_STATE_CLAIMS):
        raise BaselineContractError("duplicate manual state claim")
    occurrences = {key: 0 for key in manual_by_key}
    candidate_lines = 0
    automatic_lines = 0
    manual_transition_lines = 0
    state_set_lines = 0
    allowed_references: set[Edge] = set()
    forbidden_references: set[Edge] = set()
    claims: list[dict[str, object]] = []

    for relative_path in AUTHORITY_DOCUMENTS:
        for line_number, raw_line in enumerate(
            _document_text(root, relative_path, text_overrides).splitlines(),
            start=1,
        ):
            statement = raw_line.strip()
            tokens = _state_tokens(statement, states)
            if not tokens:
                continue
            expression_states: set[str] = set()
            expression_edges: set[Edge] = set()
            expressions = []
            for raw_expression in BACKTICK_EXPRESSION.findall(statement):
                expression = raw_expression.strip()
                if not _state_tokens(expression, states) or ARROWISH.search(expression) is None:
                    continue
                if TRANSITION_EXPRESSION.fullmatch(expression) is None:
                    raise BaselineContractError(
                        f"unparseable structured transition: {relative_path}:{line_number}"
                    )
                expressions.append(expression)
                expression_states.update(_state_tokens(expression, states))
                expression_edges.update(_transition_edges(expression, states))

            outside_backticks = BACKTICK_FRAGMENT.sub("", statement)
            needs_manual = ARROWISH.search(outside_backticks) is not None or (
                len(tokens) >= 2 and bool(tokens - expression_states)
            )
            if not expressions and not needs_manual:
                continue
            candidate_lines += 1
            key = (relative_path.as_posix(), statement)
            manual = manual_by_key.get(key)
            if needs_manual and manual is None:
                raise BaselineContractError(
                    f"unclassified nonstructured state claim: {relative_path}:{line_number}"
                )
            if manual is not None:
                occurrences[key] += 1
                if not needs_manual:
                    raise BaselineContractError(
                        f"stale manual state disposition: {relative_path}:{line_number}"
                    )

            if expression_edges:
                if expression_edges <= allowed_edges:
                    allowed_references.update(expression_edges)
                elif expression_edges.isdisjoint(allowed_edges) and any(
                    marker in statement for marker in NEGATION_MARKERS
                ):
                    forbidden_references.update(expression_edges)
                else:
                    unexpected = sorted(expression_edges - allowed_edges)
                    raise BaselineContractError(
                        f"conflicting transition at {relative_path}:{line_number}: {unexpected}"
                    )

            manual_edges: set[Edge] = set()
            kind = "automatic"
            if manual is None:
                automatic_lines += 1
            elif manual.kind == "allowed_edges":
                manual_edges = set(manual.edges)
                if not manual_edges or not manual_edges <= allowed_edges:
                    raise BaselineContractError(
                        f"invalid manual transition at {relative_path}:{line_number}"
                    )
                allowed_references.update(manual_edges)
                manual_transition_lines += 1
                kind = manual.kind
            elif manual.kind == "state_set":
                if manual.edges:
                    raise BaselineContractError(
                        f"state-set disposition contains edges: {relative_path}:{line_number}"
                    )
                state_set_lines += 1
                kind = manual.kind
            else:
                raise BaselineContractError(
                    f"unknown manual disposition at {relative_path}:{line_number}"
                )

            claims.append(
                {
                    "document": relative_path.as_posix(),
                    "line": line_number,
                    "kind": kind,
                    "statement_sha256": hashlib.sha256(statement.encode("utf-8")).hexdigest(),
                    "automatic_edge_count": len(expression_edges),
                    "manual_edge_count": len(manual_edges),
                }
            )

    missing = [key for key, count in occurrences.items() if count != 1]
    if missing:
        raise BaselineContractError(
            f"manual state dispositions do not bind exactly once: {len(missing)}"
        )
    return {
        "canonical_state_count": len(states),
        "canonical_allowed_edge_count": len(allowed_edges),
        "candidate_line_count": candidate_lines,
        "automatic_transition_line_count": automatic_lines,
        "manual_transition_line_count": manual_transition_lines,
        "state_set_line_count": state_set_lines,
        "referenced_allowed_edge_count": len(allowed_references),
        "referenced_forbidden_edge_count": len(forbidden_references),
        "claims": claims,
    }


def extract_spec_api_endpoints(root: Path = ROOT) -> set[tuple[str, str]]:
    text = _read(root, STATE_API_SPEC)
    if "GET/PUT /api/" in text or "/retry|cancel" in text:
        raise BaselineContractError("ambiguous API method/path shorthand remains in 05")
    endpoints = {(method, path) for method, path in TABLE_ENDPOINT.findall(text)}
    endpoints.update((method, path) for method, path in INLINE_ENDPOINT.findall(text))
    if not endpoints:
        raise BaselineContractError("05 does not expose machine-readable API endpoints")
    return endpoints


def _mapping(value: object, *, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise BaselineContractError(f"invalid OpenAPI mapping: {label}")
    return cast(dict[str, object], value)


def _load_openapi() -> dict[str, object]:
    backend_path = str((ROOT / "backend").resolve())
    inserted = backend_path not in sys.path
    if inserted:
        sys.path.insert(0, backend_path)
    try:
        from app.main import app
    except ImportError as error:
        raise BaselineContractError(
            "backend dependencies are required for the API semantic audit"
        ) from error
    finally:
        if inserted:
            sys.path.remove(backend_path)
    return _mapping(app.openapi(), label="root")


def _openapi_operations(openapi: dict[str, object]) -> set[tuple[str, str]]:
    paths = _mapping(openapi.get("paths"), label="paths")
    operations: set[tuple[str, str]] = set()
    for path, raw_path_item in paths.items():
        path_item = _mapping(raw_path_item, label=path)
        for method in API_METHODS:
            if method not in path_item:
                continue
            _mapping(path_item[method], label=f"{method.upper()} {path}")
            operations.add((method.upper(), path))
    return operations


def _load_runtime_non_schema_routes() -> set[tuple[str, str]]:
    backend_path = str((ROOT / "backend").resolve())
    inserted = backend_path not in sys.path
    if inserted:
        sys.path.insert(0, backend_path)
    try:
        from app.main import app
    except ImportError as error:
        raise BaselineContractError(
            "backend dependencies are required for the API semantic audit"
        ) from error
    finally:
        if inserted:
            sys.path.remove(backend_path)

    routes: set[tuple[str, str]] = set()
    for route in app.routes:
        if getattr(route, "include_in_schema", False):
            continue
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not isinstance(path, str) or not isinstance(methods, set):
            raise BaselineContractError("invalid runtime route metadata")
        if not all(isinstance(method, str) for method in methods):
            raise BaselineContractError("invalid runtime route methods")
        routes.update((method.upper(), path) for method in methods)
    return routes


def _operation(
    openapi: dict[str, object],
    method: str,
    path: str,
) -> dict[str, object]:
    paths = _mapping(openapi.get("paths"), label="paths")
    path_item = _mapping(paths.get(path), label=path)
    return _mapping(path_item.get(method.lower()), label=f"{method} {path}")


def _resolve_schema(
    openapi: dict[str, object],
    raw_schema: object,
) -> dict[str, object]:
    schema = _mapping(raw_schema, label="schema")
    seen: set[str] = set()
    while "$ref" in schema:
        reference = schema.get("$ref")
        if (
            not isinstance(reference, str)
            or not reference.startswith("#/components/schemas/")
            or reference in seen
        ):
            raise BaselineContractError("invalid or cyclic OpenAPI schema reference")
        seen.add(reference)
        name = reference.rsplit("/", maxsplit=1)[1]
        components = _mapping(openapi.get("components"), label="components")
        schemas = _mapping(components.get("schemas"), label="components.schemas")
        schema = _mapping(schemas.get(name), label=f"schema {name}")
    return schema


def _enum_values(raw_schema: object) -> frozenset[str]:
    values: set[str] = set()

    def walk(value: object) -> None:
        if isinstance(value, dict):
            raw_enum = value.get("enum")
            if isinstance(raw_enum, list):
                values.update(item for item in raw_enum if isinstance(item, str))
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(raw_schema)
    return frozenset(values)


def _numeric_keyword_values(raw_schema: object, keyword: str) -> frozenset[int | float]:
    values: set[int | float] = set()

    def walk(value: object) -> None:
        if isinstance(value, dict):
            candidate = value.get(keyword)
            if isinstance(candidate, int | float) and not isinstance(candidate, bool):
                values.add(candidate)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(raw_schema)
    return frozenset(values)


def _request_schema(
    openapi: dict[str, object],
    contract: ApiBodyContract,
) -> dict[str, object]:
    operation = _operation(openapi, contract.method, contract.path)
    request_body = _mapping(operation.get("requestBody"), label="requestBody")
    if request_body.get("required") is not True:
        raise BaselineContractError(
            f"request body is not required: {contract.method} {contract.path}"
        )
    content = _mapping(request_body.get("content"), label="requestBody.content")
    media = _mapping(content.get(contract.media_type), label=contract.media_type)
    return _resolve_schema(openapi, media.get("schema"))


def _query_parameters(
    openapi: dict[str, object],
    contract: ApiQueryContract,
) -> dict[str, dict[str, object]]:
    operation = _operation(openapi, contract.method, contract.path)
    raw_parameters = operation.get("parameters", [])
    if not isinstance(raw_parameters, list):
        raise BaselineContractError("invalid OpenAPI parameters")
    result: dict[str, dict[str, object]] = {}
    for raw_parameter in raw_parameters:
        parameter = _mapping(raw_parameter, label="parameter")
        if parameter.get("in") != "query":
            continue
        name = parameter.get("name")
        if not isinstance(name, str) or name in result:
            raise BaselineContractError(
                f"invalid query parameter: {contract.method} {contract.path}"
            )
        result[name] = parameter
    return result


def audit_api_contract(
    root: Path = ROOT,
    openapi: dict[str, object] | None = None,
    runtime_non_schema_routes: set[tuple[str, str]] | None = None,
) -> dict[str, object]:
    spec = _read(root, STATE_API_SPEC)
    spec_endpoints = extract_spec_api_endpoints(root)
    schema = openapi if openapi is not None else _load_openapi()
    runtime_paths = _mapping(schema.get("paths"), label="paths")
    runtime_operations = _openapi_operations(schema)
    missing = sorted(spec_endpoints - runtime_operations)
    undocumented = sorted(runtime_operations - spec_endpoints)
    if missing or undocumented:
        raise BaselineContractError(
            f"05/runtime API operation drift: missing={missing}, undocumented={undocumented}"
        )

    observed_non_schema = (
        runtime_non_schema_routes
        if runtime_non_schema_routes is not None
        else _load_runtime_non_schema_routes()
    )
    unexpected_routes = sorted(observed_non_schema - set(RUNTIME_ROUTE_EXCLUSIONS))
    stale_exclusions = sorted(set(RUNTIME_ROUTE_EXCLUSIONS) - observed_non_schema)
    if unexpected_routes or stale_exclusions:
        raise BaselineContractError(
            "runtime route exclusion drift: "
            f"unexpected={unexpected_routes}, stale={stale_exclusions}"
        )
    for marker in API_SEMANTIC_MARKERS:
        if marker not in spec:
            raise BaselineContractError(f"API semantic marker missing from 05: {marker}")

    body_results: list[dict[str, object]] = []
    for contract in BODY_CONTRACTS:
        if (contract.method, contract.path) not in spec_endpoints:
            raise BaselineContractError(
                f"body semantic contract is absent from 05: {contract.method} {contract.path}"
            )
        for marker in contract.markers:
            if marker not in spec:
                raise BaselineContractError(
                    f"request field semantic marker missing from 05: {marker}"
                )
        request_schema = _request_schema(schema, contract)
        properties = _mapping(request_schema.get("properties"), label="properties")
        required_raw = request_schema.get("required", [])
        if not isinstance(required_raw, list) or not all(
            isinstance(value, str) for value in required_raw
        ):
            raise BaselineContractError("invalid required-field list")
        required = frozenset(cast(list[str], required_raw))
        expected_properties = contract.required | contract.optional
        if required != contract.required or frozenset(properties) != expected_properties:
            raise BaselineContractError(f"request field drift: {contract.method} {contract.path}")
        for field, expected_values in contract.enums:
            if _enum_values(properties[field]) != expected_values:
                raise BaselineContractError(
                    f"request enum drift: {contract.method} {contract.path} {field}"
                )
        body_results.append(
            {
                "method": contract.method,
                "path": contract.path,
                "required_fields": sorted(required),
                "optional_fields": sorted(contract.optional),
            }
        )

    query_results: list[dict[str, object]] = []
    for query_contract in QUERY_CONTRACTS:
        if (query_contract.method, query_contract.path) not in spec_endpoints:
            raise BaselineContractError(
                f"query semantic contract is absent from 05: {query_contract.method} "
                f"{query_contract.path}"
            )
        parameters = _query_parameters(schema, query_contract)
        if frozenset(parameters) != query_contract.parameters:
            raise BaselineContractError(
                f"query parameter drift: {query_contract.method} {query_contract.path}"
            )
        observed_required = frozenset(
            name for name, value in parameters.items() if value.get("required") is True
        )
        if observed_required != query_contract.required:
            raise BaselineContractError(
                f"query requiredness drift: {query_contract.method} {query_contract.path}"
            )
        for field, expected_values in query_contract.enums:
            parameter_schema = _mapping(
                parameters[field].get("schema"),
                label=f"query schema {field}",
            )
            if _enum_values(parameter_schema) != expected_values:
                raise BaselineContractError(
                    f"query enum drift: {query_contract.method} {query_contract.path} {field}"
                )
        for field, expected_max_length in query_contract.max_lengths:
            parameter_schema = _mapping(
                parameters[field].get("schema"),
                label=f"query schema {field}",
            )
            if _numeric_keyword_values(parameter_schema, "maxLength") != frozenset(
                {expected_max_length}
            ):
                raise BaselineContractError(
                    f"query maxLength drift: {query_contract.method} {query_contract.path} {field}"
                )
        if query_contract.standard_pagination:
            _check_pagination(query_contract, parameters)
        query_results.append(
            {
                "method": query_contract.method,
                "path": query_contract.path,
                "parameters": sorted(parameters),
            }
        )

    return {
        "spec_endpoint_count": len(spec_endpoints),
        "runtime_path_count": len(runtime_paths),
        "runtime_operation_count": len(runtime_operations),
        "body_contract_count": len(body_results),
        "query_contract_count": len(query_results),
        "endpoints": [{"method": method, "path": path} for method, path in sorted(spec_endpoints)],
        "body_contracts": body_results,
        "query_contracts": query_results,
        "runtime_route_exclusions": [
            {
                "method": method,
                "path": path,
                "reason": RUNTIME_ROUTE_EXCLUSIONS[(method, path)],
            }
            for method, path in sorted(RUNTIME_ROUTE_EXCLUSIONS)
        ],
    }


def _check_pagination(
    contract: ApiQueryContract,
    parameters: dict[str, dict[str, object]],
) -> None:
    for field, default, minimum, maximum in (
        ("page", 1, 1, None),
        ("page_size", 20, 1, 100),
    ):
        schema = _mapping(
            parameters[field].get("schema"),
            label=f"query schema {field}",
        )
        invalid = (
            schema.get("default") != default
            or schema.get("minimum") != minimum
            or (maximum is not None and schema.get("maximum") != maximum)
        )
        if invalid:
            raise BaselineContractError(
                f"pagination semantic drift: {contract.method} {contract.path} {field}"
            )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise BaselineContractError(f"cannot hash source: {path}") from error
    return digest.hexdigest()


def _source_digests(root: Path) -> dict[str, str]:
    return {
        path.as_posix(): _sha256(root / path)
        for path in sorted(SOURCE_FILES, key=lambda item: item.as_posix())
    }


def verify_candidate_identity(expected_git_sha: str) -> tuple[str, str]:
    try:
        snapshot = verify_git_snapshot(
            ROOT,
            expected_sha=expected_git_sha,
            relative_paths=SOURCE_FILES,
            source_sha256=_source_digests(ROOT),
        )
    except AcceptanceGitError as error:
        raise BaselineContractError(str(error)) from error
    return snapshot.head, snapshot.tree


def _junit_tag(element: ElementTree.Element) -> str:
    return element.tag.rsplit("}", maxsplit=1)[-1]


def _required_junit_count(suite: ElementTree.Element, attribute: str) -> int:
    raw_value = suite.get(attribute)
    if raw_value is None or not raw_value.isdigit():
        raise BaselineContractError(f"baseline pytest JUnit has invalid {attribute} count")
    return int(raw_value)


def _validate_baseline_junit(junit_path: Path) -> tuple[int, tuple[str, ...]]:
    try:
        root = ElementTree.parse(junit_path).getroot()
    except (OSError, ElementTree.ParseError) as error:
        raise BaselineContractError("baseline pytest JUnit is missing or invalid") from error

    suites = [child for child in root if _junit_tag(child) == "testsuite"]
    if _junit_tag(root) != "testsuites" or len(suites) != 1:
        raise BaselineContractError("baseline pytest JUnit must contain exactly one test suite")
    suite = suites[0]
    counts = {
        attribute: _required_junit_count(suite, attribute)
        for attribute in ("tests", "errors", "failures", "skipped")
    }
    expected_count = len(BASELINE_TEST_NODES)
    if counts != {
        "tests": expected_count,
        "errors": 0,
        "failures": 0,
        "skipped": 0,
    }:
        raise BaselineContractError(
            f"baseline pytest must pass exactly {expected_count} expected nodes without "
            "skip, xfail, failure, or error"
        )

    testcases = [child for child in suite if _junit_tag(child) == "testcase"]
    if len(testcases) != expected_count:
        raise BaselineContractError("baseline pytest JUnit testcase count does not match summary")

    observed_nodes: list[str] = []
    for testcase in testcases:
        classname = testcase.get("classname")
        name = testcase.get("name")
        if not classname or not name:
            raise BaselineContractError("baseline pytest JUnit testcase identity is incomplete")
        outcome_tags = {
            _junit_tag(child)
            for child in testcase
            if _junit_tag(child) in {"error", "failure", "skipped"}
        }
        if outcome_tags:
            raise BaselineContractError("baseline pytest JUnit testcase is not a pass")
        observed_nodes.append(f"{classname.replace('.', '/')}.py::{name}")

    if len(set(observed_nodes)) != expected_count or set(observed_nodes) != set(
        BASELINE_TEST_NODES
    ):
        raise BaselineContractError("baseline pytest node identity mismatch")
    return expected_count, tuple(observed_nodes)


def _run_baseline_tests() -> tuple[str, int, int, tuple[str, ...]]:
    with TemporaryDirectory(prefix="knowledge-uploader-baseline-") as temporary_dir:
        runtime_dir = Path(temporary_dir).resolve()
        if runtime_dir == ROOT.resolve() or runtime_dir.is_relative_to(ROOT.resolve()):
            raise BaselineContractError("baseline pytest runtime must be outside the repository")
        pycache_prefix = runtime_dir / "pycache"
        if pycache_prefix.exists():
            raise BaselineContractError("baseline pytest pycache prefix must be fresh")
        claim_token = secrets.token_hex(32)
        claim_marker = runtime_dir / str(_acceptance_entry.CLAIM_FILENAME)
        marker_descriptor = os.open(
            claim_marker,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
        try:
            os.write(marker_descriptor, claim_token.encode("ascii"))
            os.fsync(marker_descriptor)
        finally:
            os.close(marker_descriptor)
        pytest_config = runtime_dir / "pytest.ini"
        pytest_config.write_text("[pytest]\n", encoding="utf-8", newline="\n")
        junit_path = runtime_dir / "baseline-pytest.xml"
        command = [
            sys.executable,
            "-I",
            "-S",
            "-X",
            "utf8",
            "-X",
            f"pycache_prefix={pycache_prefix}",
            "-c",
            PYTEST_BOOTSTRAP,
            "-q",
            "-c",
            str(pytest_config),
            "--rootdir",
            str(ROOT),
            "--runxfail",
            "--junitxml",
            str(junit_path),
            *BASELINE_TEST_NODES,
        ]
        environment = _pytest_environment(dict(os.environ), runtime_dir=runtime_dir)
        environment.update(
            {
                str(_acceptance_entry.CLAIM_TOKEN_ENV): claim_token,
                str(_acceptance_entry.CLAIM_MARKER_ENV): str(claim_marker),
                str(_acceptance_entry.CLAIM_RUNTIME_ENV): str(runtime_dir),
            }
        )
        try:
            completed = subprocess.run(
                command,
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise BaselineContractError("baseline pytest execution failed") from error
        finally:
            try:
                claim_marker.unlink(missing_ok=True)
            except OSError as error:
                raise BaselineContractError("baseline pytest claim cleanup failed") from error
        if completed.returncode != 0:
            raise BaselineContractError("baseline pytest did not pass")
        if pytest_config.read_text(encoding="utf-8") != "[pytest]\n":
            raise BaselineContractError("baseline pytest config changed during execution")
        passed_count, observed_nodes = _validate_baseline_junit(junit_path)
        display_command = (
            "python -I -S -X utf8 -X pycache_prefix=<fresh-external-path> "
            "-c <trusted-bootstrap> -q "
            "-c <temporary-pytest-config> --rootdir <repository> --runxfail "
            "--junitxml=<temporary-junit> " + " ".join(BASELINE_TEST_NODES)
        )
        log = (
            f"$ {display_command}\n"
            f"exit_code={completed.returncode}\n"
            f"passed_count={passed_count}\n"
            f"expected_node_count={len(BASELINE_TEST_NODES)}\n"
            + "".join(f"nodeid={nodeid}\n" for nodeid in observed_nodes)
        )
    return log, completed.returncode, passed_count, observed_nodes


def _prepare_output_dir(output_dir: Path) -> Path:
    if output_dir.exists() and output_dir.is_symlink():
        raise BaselineContractError("output directory must not be a symlink")
    resolved = output_dir.resolve()
    if resolved.is_relative_to(ROOT.resolve()):
        raise BaselineContractError("output directory must be outside the repository")
    if resolved.exists():
        if not resolved.is_dir() or any(resolved.iterdir()):
            raise BaselineContractError("output directory must be empty")
    else:
        try:
            resolved.mkdir(parents=True)
        except OSError as error:
            raise BaselineContractError("cannot create output directory") from error
    return resolved


def _atomic_write(path: Path, payload: bytes) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    if temporary.exists() or path.exists():
        raise BaselineContractError("evidence output already exists")
    try:
        temporary.write_bytes(payload)
        temporary.replace(path)
    except OSError as error:
        if temporary.is_file():
            temporary.unlink()
        raise BaselineContractError("cannot write evidence output") from error


def collect_candidate_evidence(
    *,
    expected_git_sha: str,
    output_dir: Path,
) -> tuple[Path, Path]:
    _require_isolated_runtime()
    head, tree = verify_candidate_identity(expected_git_sha)
    source_before = _source_digests(ROOT)
    state_report = audit_state_claims(ROOT)
    api_report = audit_api_contract(ROOT)
    (
        raw_log,
        exit_code,
        passed_count,
        observed_nodes,
    ) = _run_baseline_tests()
    source_after = _source_digests(ROOT)
    if source_after != source_before:
        raise BaselineContractError("baseline sources changed during verification")
    final_head, final_tree = verify_candidate_identity(expected_git_sha)
    if (final_head, final_tree) != (head, tree):
        raise BaselineContractError("candidate identity changed during verification")

    output = _prepare_output_dir(output_dir)
    log_payload = raw_log.encode("utf-8")
    log_name = f"baseline-contract-{head}.log"
    evidence_name = f"baseline-contract-{head}.json"
    log_path = output / log_name
    evidence_path = output / evidence_name
    evidence: dict[str, object] = {
        "schema": "knowledge-uploader.baseline-contract-evidence.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "passed",
        "scope": "local-baseline-only",
        "git_sha": head,
        "git_tree": tree,
        "worktree_clean": True,
        "git_replace_refs_absent": True,
        "git_grafts_absent": True,
        "git_hidden_index_flags_absent": True,
        "git_info_exclude_inactive": True,
        "git_global_excludes_disabled": True,
        "untracked_execution_inputs_absent": True,
        "minimum_git_version": "2.36.0",
        "sources_match_commit_blobs": True,
        "pytest": {
            "command": (
                "python -I -S -X utf8 -X pycache_prefix=<fresh-external-path> "
                "-c <trusted-bootstrap> "
                "<13 explicit nodeids> -q -c <temporary-pytest-config> "
                "--rootdir <repository> --runxfail --junitxml=<temporary-junit>"
            ),
            "passed_count": passed_count,
            "expected_node_count": len(BASELINE_TEST_NODES),
            "nodeids": list(observed_nodes),
            "exit_code": exit_code,
            "log_file": log_name,
            "log_sha256": hashlib.sha256(log_payload).hexdigest(),
        },
        "source_sha256": source_after,
        "state_claim_audit": state_report,
        "api_contract_audit": api_report,
        "external_release_status": "not_evaluated",
        "does_not_prove": [
            "protected_ci",
            "real_external_services",
            "dgx_arm64_runtime",
            "production_alert_delivery",
            "production_backup_restore",
        ],
    }
    evidence_payload = (
        json.dumps(
            evidence,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    _atomic_write(log_path, log_payload)
    try:
        _atomic_write(evidence_path, evidence_payload)
    except BaselineContractError:
        log_path.unlink(missing_ok=True)
        raise
    return evidence_path, log_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-git-sha", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    try:
        evidence_path, log_path = collect_candidate_evidence(
            expected_git_sha=arguments.expected_git_sha,
            output_dir=arguments.output_dir,
        )
    except BaselineContractError as error:
        sys.stderr.write(f"baseline contract verification failed: {error}\n")
        return 1
    sys.stdout.write(
        json.dumps(
            {
                "status": "passed",
                "evidence": evidence_path.name,
                "log": log_path.name,
            },
            sort_keys=True,
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
