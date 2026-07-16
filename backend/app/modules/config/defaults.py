from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ConfigDefinition:
    key: str
    group: str
    value_type: str
    default: object
    description: str
    is_secret: bool = False
    min_value: int | None = None
    max_value: int | None = None
    immutable: bool = False


CONFIG_DEFINITIONS: tuple[ConfigDefinition, ...] = (
    ConfigDefinition(
        key="upload.enabled",
        group="upload",
        value_type="bool",
        default=True,
        description="是否允许员工发起新的文件上传",
    ),
    ConfigDefinition(
        key="upload.allowed_extensions",
        group="upload",
        value_type="list",
        default=["pdf", "docx", "xlsx", "pptx", "txt", "md", "csv"],
        description="允许上传的文件扩展名白名单",
    ),
    ConfigDefinition(
        key="upload.max_file_size_mb",
        group="upload",
        value_type="int",
        default=50,
        description="单文件最大大小 MB 当前内存上传架构硬上限 200MB 可下调不可上调",
        min_value=1,
        max_value=200,
    ),
    ConfigDefinition(
        key="upload.user_quota_mb",
        group="upload",
        value_type="int",
        default=0,
        description="单用户存储配额 MB 0 表示不限制",
        min_value=0,
        max_value=1048576,
    ),
    ConfigDefinition(
        key="upload.allow_multi_file",
        group="upload",
        value_type="bool",
        default=True,
        description="是否允许一次选择多个文件上传",
    ),
    ConfigDefinition(
        key="upload.allow_user_delete",
        group="upload",
        value_type="bool",
        default=False,
        description="是否允许员工删除自己上传的文件",
    ),
    ConfigDefinition(
        key="outbox.publish_max_retries",
        group="outbox",
        value_type="int",
        default=3,
        description="Outbox 事件发布最大重试次数 不控制 Celery 领域任务",
        min_value=0,
        max_value=10,
    ),
    ConfigDefinition(
        key="processing.parse_max_pages",
        group="processing",
        value_type="int",
        default=200,
        description="文本解析的最大页数上限",
        min_value=1,
        max_value=2000,
    ),
    ConfigDefinition(
        key="processing.parse_max_chars",
        group="processing",
        value_type="int",
        default=20000,
        description="文本解析的最大字符数上限",
        min_value=1000,
        max_value=1000000,
    ),
    ConfigDefinition(
        key="security.allowed_email_domains",
        group="security",
        value_type="list",
        default=["company.com"],
        description="允许注册的邮箱域名列表",
    ),
    ConfigDefinition(
        key="security.password_min_length",
        group="security",
        value_type="int",
        default=8,
        description="密码最小长度",
        min_value=6,
        max_value=128,
    ),
    ConfigDefinition(
        key="security.login_max_failed_attempts",
        group="security",
        value_type="int",
        default=5,
        description="连续登录失败锁定阈值",
        min_value=1,
        max_value=100,
    ),
    ConfigDefinition(
        key="security.login_lock_minutes",
        group="security",
        value_type="int",
        default=15,
        description="登录锁定时长分钟",
        min_value=1,
        max_value=43200,
    ),
    ConfigDefinition(
        key="security.require_email_verification",
        group="security",
        value_type="bool",
        default=False,
        description="注册后是否要求邮箱验证 - 当前默认关闭",
    ),
    ConfigDefinition(
        key="security.block_critical_sensitive_sync",
        group="security",
        value_type="bool",
        default=True,
        description="critical 敏感等级是否阻止同步",
        immutable=True,
    ),
    ConfigDefinition(
        key="review.claim_timeout_minutes",
        group="review",
        value_type="int",
        default=30,
        description="审核领取有效分钟数 修改仅影响新领取 已有领取不追溯缩短",
        min_value=5,
        max_value=1440,
    ),
    ConfigDefinition(
        key="review.sla_hours",
        group="review",
        value_type="int",
        default=24,
        description="审核 SLA 小时数 修改仅影响新提交 已有截止时间不追溯缩短",
        min_value=1,
        max_value=720,
    ),
    ConfigDefinition(
        key="ragflow.base_url",
        group="ragflow",
        value_type="string",
        default="http://ragflow:9380",
        description="RAGFlow 服务地址",
    ),
    ConfigDefinition(
        key="ragflow.api_key",
        group="ragflow",
        value_type="secret",
        default="",
        description="RAGFlow API Key 加密存储",
        is_secret=True,
    ),
    ConfigDefinition(
        key="ragflow.sync_max_retries",
        group="ragflow",
        value_type="int",
        default=3,
        description="RAGFlow 同步最大重试次数",
        min_value=0,
        max_value=10,
    ),
    ConfigDefinition(
        key="ragflow.sync_timeout_seconds",
        group="ragflow",
        value_type="int",
        default=60,
        description="RAGFlow 同步请求超时秒",
        min_value=5,
        max_value=3600,
    ),
    ConfigDefinition(
        key="ragflow.parse_poll_timeout_seconds",
        group="ragflow",
        value_type="int",
        default=3600,
        description="RAGFlow 解析状态轮询总时限秒 与请求重试次数相互独立",
        min_value=60,
        max_value=86400,
    ),
    ConfigDefinition(
        key="ragflow.allow_high_risk_sync",
        group="ragflow",
        value_type="bool",
        default=False,
        description="是否允许管理员填写理由后批准 high 风险文件同步",
    ),
    ConfigDefinition(
        key="ragflow.delete_remote_on_file_delete",
        group="ragflow",
        value_type="bool",
        default=False,
        description="删除本地文件时是否删除远端文档",
    ),
    ConfigDefinition(
        key="ragflow.keep_remote_on_archive",
        group="ragflow",
        value_type="bool",
        default=True,
        description="归档文件时是否保留远端文档",
    ),
)

CONFIG_GROUPS: frozenset[str] = frozenset(definition.group for definition in CONFIG_DEFINITIONS)

DEFINITIONS_BY_KEY: dict[str, ConfigDefinition] = {
    definition.key: definition for definition in CONFIG_DEFINITIONS
}


def definitions_for_group(group: str) -> tuple[ConfigDefinition, ...]:
    return tuple(definition for definition in CONFIG_DEFINITIONS if definition.group == group)
