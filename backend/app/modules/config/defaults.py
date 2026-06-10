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


CONFIG_DEFINITIONS: tuple[ConfigDefinition, ...] = (
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
        description="单文件最大大小 MB",
        min_value=1,
        max_value=10240,
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
        key="upload.enable_duplicate_check",
        group="upload",
        value_type="bool",
        default=True,
        description="是否启用文件去重校验",
    ),
    ConfigDefinition(
        key="processing.auto_parse_on_upload",
        group="processing",
        value_type="bool",
        default=True,
        description="上传后是否自动解析文本",
    ),
    ConfigDefinition(
        key="processing.auto_sync_after_parse",
        group="processing",
        value_type="bool",
        default=False,
        description="解析完成后是否自动同步 RAGFlow",
    ),
    ConfigDefinition(
        key="processing.sync_after_ai_analysis",
        group="processing",
        value_type="bool",
        default=True,
        description="AI 分析完成后是否继续同步流程",
    ),
    ConfigDefinition(
        key="processing.task_max_retries",
        group="processing",
        value_type="int",
        default=3,
        description="后台任务最大重试次数",
        min_value=0,
        max_value=10,
    ),
    ConfigDefinition(
        key="processing.task_timeout_seconds",
        group="processing",
        value_type="int",
        default=600,
        description="后台任务超时时间秒",
        min_value=30,
        max_value=86400,
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
        default=True,
        description="注册后是否要求邮箱验证",
    ),
    ConfigDefinition(
        key="security.require_review_before_sync",
        group="security",
        value_type="bool",
        default=True,
        description="同步 RAGFlow 前是否必须人工审核",
    ),
    ConfigDefinition(
        key="security.block_critical_sensitive_sync",
        group="security",
        value_type="bool",
        default=True,
        description="critical 敏感等级是否阻止同步",
    ),
    ConfigDefinition(
        key="basic.system_name",
        group="basic",
        value_type="string",
        default="knowledge-uploader",
        description="系统名称",
    ),
    ConfigDefinition(
        key="basic.system_logo_url",
        group="basic",
        value_type="string",
        default="",
        description="系统 Logo 地址",
    ),
    ConfigDefinition(
        key="basic.default_language",
        group="basic",
        value_type="string",
        default="zh-CN",
        description="默认界面语言",
    ),
    ConfigDefinition(
        key="basic.default_timezone",
        group="basic",
        value_type="string",
        default="Asia/Shanghai",
        description="默认时区",
    ),
    ConfigDefinition(
        key="basic.notification_channels",
        group="basic",
        value_type="list",
        default=["email"],
        description="启用的通知渠道列表",
    ),
    ConfigDefinition(
        key="basic.admin_contact_email",
        group="basic",
        value_type="string",
        default="",
        description="管理员联系邮箱",
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
        key="ragflow.default_dataset_id",
        group="ragflow",
        value_type="string",
        default="",
        description="默认同步的 RAGFlow 数据集 ID",
    ),
    ConfigDefinition(
        key="ragflow.auto_sync_enabled",
        group="ragflow",
        value_type="bool",
        default=False,
        description="审核通过后是否自动同步 RAGFlow",
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
        key="ragflow.allow_high_risk_sync",
        group="ragflow",
        value_type="bool",
        default=False,
        description="是否允许 high 风险文件同步",
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
