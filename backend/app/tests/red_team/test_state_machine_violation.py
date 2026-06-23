"""红队：文件状态机非法跃迁攻击。

靶心: app/core/document_state.py::DocumentStateMachine
铁律: 跑红 = 漏洞真实存在; 跑绿 = 假设被证伪(防御有效, 保留为防回归)。
本文件为纯逻辑攻击, 不依赖 DB/Redis。
"""

from __future__ import annotations

import pytest

from app.core.document_state import DocumentStateError, DocumentStateMachine

# 确定性非法跃迁: 业务语义上绝不该允许（05 §2 / CLAUDE.md §8）。
# 预期【跑绿】= 白名单正确拒绝 = 防御有效。逐条已对照 _allowed_transitions 确认不在白名单。
ILLEGAL_TRANSITIONS = [
    ("rejected", "parsed"),  # 被拒文件直接变已解析
    ("rejected", "approved"),  # 被拒文件复活为通过
    ("deleted", "approved"),  # 已删除复活
    ("deleted", "uploaded"),  # 已删除回到初始
    ("uploaded", "parsed"),  # 跳过整个审核 + 同步流程
    ("uploaded", "approved"),  # 跳过审核直接通过
    ("parsed", "analyzing"),  # 已入库回到 AI 分析
    ("pending_review", "parsed"),  # 跳过审批与同步
    ("syncing", "deleted"),  # 流水线中间态应先 failed 再删
    ("analyzing", "approved"),  # 分析中跳过审核
    ("parsed", "queued"),  # 已入库回到等待同步
    ("approved", "uploaded"),  # 已审核回到初始态
    ("disabled", "approved"),  # 已禁用复活为通过
]


@pytest.mark.parametrize(("from_status", "to_status"), ILLEGAL_TRANSITIONS)
def test_illegal_transition_is_rejected(from_status: str, to_status: str) -> None:
    """攻击: 强制非法状态跃迁。期望被 DocumentStateError 拦截。

    跑红(未抛异常) = 状态机放行了危险跃迁 = 漏洞。
    """
    with pytest.raises(DocumentStateError):
        DocumentStateMachine.transition(from_status, to_status)


def test_unknown_or_injected_status_is_rejected() -> None:
    """攻击: 注入不存在 / 伪造 / SQL 风格的状态值。期望被拒。"""
    with pytest.raises(DocumentStateError):
        DocumentStateMachine.transition("uploaded", "ragflow_god_mode")
    with pytest.raises(DocumentStateError):
        DocumentStateMachine.transition("'; DROP TABLE files;--", "parsed")


def test_queued_cannot_skip_ragflow_upload_to_parsing() -> None:
    """防回归: queued 不得直接跃迁到 parsing。

    queued -> parsing 会跳过 syncing -> uploaded_to_ragflow 两步, 使文件在尚无
    ragflow_document_id 时进入解析轮询(parsing 语义即"轮询 RAGFlow 解析状态")。
    对照 05_DATABASE_API_SPEC §2 确认: queued 仅允许 -> syncing(主流程) 或 -> failed;
    queued -> parsing 属设计疏漏, 已从 _allowed_transitions 移除, 本测试守护其不复现。
    """
    with pytest.raises(DocumentStateError):
        DocumentStateMachine.transition("queued", "parsing")


# 05_DATABASE_API_SPEC §2 异常分支: queued / syncing / uploaded_to_ragflow / parsing -> failed
# 均为合法失败分支。预期【跑绿】= 白名单正确放行 = 失败可被如实落库。
LEGAL_FAILURE_TRANSITIONS = [
    ("queued", "failed"),
    ("syncing", "failed"),
    ("uploaded_to_ragflow", "failed"),
    ("parsing", "failed"),
]


@pytest.mark.parametrize(("from_status", "to_status"), LEGAL_FAILURE_TRANSITIONS)
def test_sync_pipeline_failure_branch_is_allowed(from_status: str, to_status: str) -> None:
    """防回归: 同步流水线各状态 -> failed 必须被放行 (05_DATABASE_API_SPEC §2 异常分支)。

    重点守护 queued -> failed: 同步任务在置 syncing 之前前置校验失败时必须能落到 failed,
    否则文件卡死在 queued, 且 _try_mark_file_failed 静默吞掉 DocumentStateError 致失败原因丢失。
    跑红(抛 DocumentStateError) = 状态机拒绝了合法失败分支 = 文件将卡死在该状态 = 漏洞。
    """
    assert DocumentStateMachine.transition(from_status, to_status) == to_status
