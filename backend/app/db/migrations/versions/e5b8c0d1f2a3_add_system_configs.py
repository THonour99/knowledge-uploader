"""add system configs

Revision ID: e5b8c0d1f2a3
Revises: c7f1a2b9d6e4
Create Date: 2026-06-10 00:00:00.000000
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e5b8c0d1f2a3"
down_revision: str | None = "c7f1a2b9d6e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (key, group, value_type, value, is_secret, description)
SEED_CONFIGS: tuple[tuple[str, str, str, object, bool, str], ...] = (
    (
        "upload.allowed_extensions",
        "upload",
        "list",
        ["pdf", "docx", "xlsx", "pptx", "txt", "md", "csv"],
        False,
        "允许上传的文件扩展名白名单",
    ),
    ("upload.max_file_size_mb", "upload", "int", 50, False, "单文件最大大小 MB"),
    ("upload.user_quota_mb", "upload", "int", 0, False, "单用户存储配额 MB 0 表示不限制"),
    ("upload.allow_multi_file", "upload", "bool", True, False, "是否允许一次选择多个文件上传"),
    (
        "upload.allow_user_delete",
        "upload",
        "bool",
        False,
        False,
        "是否允许员工删除自己上传的文件",
    ),
    ("upload.enable_duplicate_check", "upload", "bool", True, False, "是否启用文件去重校验"),
    (
        "processing.auto_parse_on_upload",
        "processing",
        "bool",
        True,
        False,
        "上传后是否自动解析文本",
    ),
    (
        "processing.auto_sync_after_parse",
        "processing",
        "bool",
        False,
        False,
        "解析完成后是否自动同步 RAGFlow",
    ),
    (
        "processing.sync_after_ai_analysis",
        "processing",
        "bool",
        True,
        False,
        "AI 分析完成后是否继续同步流程",
    ),
    ("processing.task_max_retries", "processing", "int", 3, False, "后台任务最大重试次数"),
    ("processing.task_timeout_seconds", "processing", "int", 600, False, "后台任务超时时间秒"),
    ("processing.parse_max_pages", "processing", "int", 200, False, "文本解析的最大页数上限"),
    ("processing.parse_max_chars", "processing", "int", 20000, False, "文本解析的最大字符数上限"),
    (
        "security.allowed_email_domains",
        "security",
        "list",
        ["company.com"],
        False,
        "允许注册的邮箱域名列表",
    ),
    ("security.password_min_length", "security", "int", 8, False, "密码最小长度"),
    ("security.login_max_failed_attempts", "security", "int", 5, False, "连续登录失败锁定阈值"),
    ("security.login_lock_minutes", "security", "int", 15, False, "登录锁定时长分钟"),
    (
        "security.require_email_verification",
        "security",
        "bool",
        True,
        False,
        "注册后是否要求邮箱验证",
    ),
    (
        "security.require_review_before_sync",
        "security",
        "bool",
        True,
        False,
        "同步 RAGFlow 前是否必须人工审核",
    ),
    (
        "security.block_critical_sensitive_sync",
        "security",
        "bool",
        True,
        False,
        "critical 敏感等级是否阻止同步",
    ),
    ("basic.system_name", "basic", "string", "knowledge-uploader", False, "系统名称"),
    ("basic.system_logo_url", "basic", "string", "", False, "系统 Logo 地址"),
    ("basic.default_language", "basic", "string", "zh-CN", False, "默认界面语言"),
    ("basic.default_timezone", "basic", "string", "Asia/Shanghai", False, "默认时区"),
    ("basic.notification_channels", "basic", "list", ["email"], False, "启用的通知渠道列表"),
    ("basic.admin_contact_email", "basic", "string", "", False, "管理员联系邮箱"),
    ("ragflow.base_url", "ragflow", "string", "http://ragflow:9380", False, "RAGFlow 服务地址"),
    ("ragflow.api_key", "ragflow", "secret", None, True, "RAGFlow API Key 加密存储"),
    (
        "ragflow.default_dataset_id",
        "ragflow",
        "string",
        "",
        False,
        "默认同步的 RAGFlow 数据集 ID",
    ),
    (
        "ragflow.auto_sync_enabled",
        "ragflow",
        "bool",
        False,
        False,
        "审核通过后是否自动同步 RAGFlow",
    ),
    ("ragflow.sync_max_retries", "ragflow", "int", 3, False, "RAGFlow 同步最大重试次数"),
    ("ragflow.sync_timeout_seconds", "ragflow", "int", 60, False, "RAGFlow 同步请求超时秒"),
    ("ragflow.allow_high_risk_sync", "ragflow", "bool", False, False, "是否允许 high 风险文件同步"),
    (
        "ragflow.delete_remote_on_file_delete",
        "ragflow",
        "bool",
        False,
        False,
        "删除本地文件时是否删除远端文档",
    ),
    (
        "ragflow.keep_remote_on_archive",
        "ragflow",
        "bool",
        True,
        False,
        "归档文件时是否保留远端文档",
    ),
)


def _seed_table() -> sa.TableClause:
    return sa.table(
        "system_configs",
        sa.column("id", sa.Uuid()),
        sa.column("key", sa.String()),
        sa.column("group", sa.String()),
        sa.column("value", postgresql.JSONB(astext_type=sa.Text())),  # type: ignore[no-untyped-call]
        sa.column("value_type", sa.String()),
        sa.column("is_secret", sa.Boolean()),
        sa.column("description", sa.Text()),
    )


def _seed_rows() -> list[dict[str, object]]:
    return [
        {
            "id": uuid.uuid4(),
            "key": key,
            "group": group,
            "value": value,
            "value_type": value_type,
            "is_secret": is_secret,
            "description": description,
        }
        for key, group, value_type, value, is_secret, description in SEED_CONFIGS
    ]


def upgrade() -> None:
    op.create_table(
        "system_configs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("group", sa.String(length=20), nullable=False),
        sa.Column(
            "value",
            postgresql.JSONB(astext_type=sa.Text()),  # type: ignore[no-untyped-call]
            nullable=True,
        ),
        sa.Column("value_type", sa.String(length=20), nullable=False),
        sa.Column("is_secret", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "\"group\" IN ('upload', 'processing', 'security', 'basic', 'ragflow')",
            name="ck_system_configs_group",
        ),
        sa.CheckConstraint(
            "value_type IN ('string', 'int', 'bool', 'list', 'secret')",
            name="ck_system_configs_value_type",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_system_configs_key", "system_configs", ["key"], unique=True)
    op.create_index("idx_system_configs_group", "system_configs", ["group"])
    op.bulk_insert(_seed_table(), _seed_rows())


def downgrade() -> None:
    op.drop_index("idx_system_configs_group", table_name="system_configs")
    op.drop_index("uq_system_configs_key", table_name="system_configs")
    op.drop_table("system_configs")
