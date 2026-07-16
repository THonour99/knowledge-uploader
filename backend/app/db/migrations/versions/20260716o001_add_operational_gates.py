"""add operational configuration and outbox dead-letter gates

Revision ID: 20260716o001
Revises: 20260716d002
Create Date: 2026-07-16 04:00:00.000000
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260716o001"
down_revision: str | None = "20260716d002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ConfigSeed = tuple[str, str, str, object, bool, str]

ACTIVE_CONFIGS: tuple[ConfigSeed, ...] = (
    ("upload.enabled", "upload", "bool", True, False, "是否允许员工发起新的文件上传"),
    (
        "upload.allowed_extensions",
        "upload",
        "list",
        ["pdf", "docx", "xlsx", "pptx", "txt", "md", "csv"],
        False,
        "允许上传的文件扩展名白名单",
    ),
    (
        "upload.max_file_size_mb",
        "upload",
        "int",
        50,
        False,
        "单文件最大大小 MB 当前内存上传架构硬上限 200MB 可下调不可上调",
    ),
    ("upload.user_quota_mb", "upload", "int", 0, False, "单用户存储配额 MB 0 表示不限制"),
    ("upload.allow_multi_file", "upload", "bool", True, False, "是否允许一次选择多个文件上传"),
    ("upload.allow_user_delete", "upload", "bool", False, False, "是否允许员工删除自己上传的文件"),
    (
        "outbox.publish_max_retries",
        "outbox",
        "int",
        3,
        False,
        "Outbox 事件发布最大重试次数 不控制 Celery 领域任务",
    ),
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
    (
        "security.login_max_failed_attempts",
        "security",
        "int",
        5,
        False,
        "连续登录失败锁定阈值",
    ),
    ("security.login_lock_minutes", "security", "int", 15, False, "登录锁定时长分钟"),
    (
        "security.require_email_verification",
        "security",
        "bool",
        False,
        False,
        "注册后是否要求邮箱验证 - 当前默认关闭",
    ),
    (
        "security.block_critical_sensitive_sync",
        "security",
        "bool",
        True,
        False,
        "critical 敏感等级是否阻止同步",
    ),
    (
        "review.claim_timeout_minutes",
        "review",
        "int",
        30,
        False,
        "审核领取有效分钟数 修改仅影响新领取 已有领取不追溯缩短",
    ),
    (
        "review.sla_hours",
        "review",
        "int",
        24,
        False,
        "审核 SLA 小时数 修改仅影响新提交 已有截止时间不追溯缩短",
    ),
    ("ragflow.base_url", "ragflow", "string", "http://ragflow:9380", False, "RAGFlow 服务地址"),
    ("ragflow.api_key", "ragflow", "secret", None, True, "RAGFlow API Key 加密存储"),
    ("ragflow.sync_max_retries", "ragflow", "int", 3, False, "RAGFlow 同步最大重试次数"),
    (
        "ragflow.parse_poll_timeout_seconds",
        "ragflow",
        "int",
        3600,
        False,
        "RAGFlow 解析状态轮询总时限秒 与请求重试次数相互独立",
    ),
    ("ragflow.sync_timeout_seconds", "ragflow", "int", 60, False, "RAGFlow 同步请求超时秒"),
    (
        "ragflow.allow_high_risk_sync",
        "ragflow",
        "bool",
        False,
        False,
        "是否允许管理员填写理由后批准 high 风险文件同步",
    ),
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

DELETED_CONFIG_KEYS: tuple[str, ...] = (
    "upload.enable_duplicate_check",
    "processing.auto_parse_on_upload",
    "processing.auto_sync_after_parse",
    "processing.sync_after_ai_analysis",
    "processing.task_timeout_seconds",
    "processing.task_max_retries",
    "security.require_review_before_sync",
    "basic.system_name",
    "basic.system_logo_url",
    "basic.default_language",
    "basic.default_timezone",
    "basic.notification_channels",
    "basic.admin_contact_email",
    "ragflow.default_dataset_id",
    "ragflow.auto_sync_enabled",
)

LEGACY_DELETED_CONFIGS: tuple[ConfigSeed, ...] = (
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
    ("processing.task_timeout_seconds", "processing", "int", 600, False, "后台任务超时时间秒"),
    ("processing.task_max_retries", "processing", "int", 3, False, "后台任务最大重试次数"),
    (
        "security.require_review_before_sync",
        "security",
        "bool",
        True,
        False,
        "同步 RAGFlow 前是否必须人工审核",
    ),
    ("basic.system_name", "basic", "string", "knowledge-uploader", False, "系统名称"),
    ("basic.system_logo_url", "basic", "string", "", False, "系统 Logo 地址"),
    ("basic.default_language", "basic", "string", "zh-CN", False, "默认界面语言"),
    ("basic.default_timezone", "basic", "string", "Asia/Shanghai", False, "默认时区"),
    ("basic.notification_channels", "basic", "list", ["email"], False, "启用的通知渠道列表"),
    ("basic.admin_contact_email", "basic", "string", "", False, "管理员联系邮箱"),
    ("ragflow.default_dataset_id", "ragflow", "string", "", False, "默认同步的 RAGFlow 数据集 ID"),
    (
        "ragflow.auto_sync_enabled",
        "ragflow",
        "bool",
        False,
        False,
        "审核通过后是否自动同步 RAGFlow",
    ),
)

NEW_CONFIG_KEYS: tuple[str, ...] = (
    "upload.enabled",
    "outbox.publish_max_retries",
    "review.claim_timeout_minutes",
    "review.sla_hours",
    "ragflow.parse_poll_timeout_seconds",
)
ACTIVE_CONFIG_KEYS: tuple[str, ...] = tuple(seed[0] for seed in ACTIVE_CONFIGS)
CONFIG_BACKUP_TABLE = "o001_deleted_system_configs_backup"


def _config_table() -> sa.TableClause:
    return sa.table(
        "system_configs",
        sa.column("id", sa.Uuid()),
        sa.column("key", sa.String()),
        sa.column("group", sa.String()),
        sa.column("value", postgresql.JSONB(astext_type=sa.Text())),  # type: ignore[no-untyped-call]
        sa.column("value_type", sa.String()),
        sa.column("is_secret", sa.Boolean()),
        sa.column("description", sa.Text()),
        sa.column("updated_by", sa.Uuid()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )


def _backup_table() -> sa.TableClause:
    return sa.table(
        CONFIG_BACKUP_TABLE,
        sa.column("id", sa.Uuid()),
        sa.column("key", sa.String()),
        sa.column("group", sa.String()),
        sa.column("value", postgresql.JSONB(astext_type=sa.Text())),  # type: ignore[no-untyped-call]
        sa.column("value_type", sa.String()),
        sa.column("is_secret", sa.Boolean()),
        sa.column("description", sa.Text()),
        sa.column("updated_by", sa.Uuid()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )


CONFIG_BACKUP_COLUMNS: tuple[str, ...] = (
    "id",
    "key",
    "group",
    "value",
    "value_type",
    "is_secret",
    "description",
    "updated_by",
    "created_at",
    "updated_at",
)


def _row(seed: ConfigSeed) -> dict[str, object]:
    key, group, value_type, value, is_secret, description = seed
    return {
        "id": uuid.uuid4(),
        "key": key,
        "group": group,
        "value": value,
        "value_type": value_type,
        "is_secret": is_secret,
        "description": description,
    }


def _insert_missing(seeds: tuple[ConfigSeed, ...]) -> None:
    table = _config_table()
    connection = op.get_bind()
    existing = set(connection.execute(sa.select(table.c.key)).scalars())
    missing_rows = [_row(seed) for seed in seeds if seed[0] not in existing]
    if missing_rows:
        connection.execute(sa.insert(table), missing_rows)


def upgrade() -> None:
    table = _config_table()
    connection = op.get_bind()
    existing_keys = set(connection.execute(sa.select(table.c.key)).scalars())
    recognized_keys = {*ACTIVE_CONFIG_KEYS, *DELETED_CONFIG_KEYS}
    unknown_count = len(existing_keys - recognized_keys)
    if unknown_count:
        # Do not print keys or values: custom config names can themselves reveal secrets.
        raise RuntimeError(
            f"operational config migration blocked by {unknown_count} unknown row(s)"
        )
    op.create_table(
        CONFIG_BACKUP_TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("group", sa.String(length=20), nullable=False),
        sa.Column(
            "value",
            postgresql.JSONB(astext_type=sa.Text()),  # type: ignore[no-untyped-call]
            nullable=True,
        ),
        sa.Column("value_type", sa.String(length=20), nullable=False),
        sa.Column("is_secret", sa.Boolean(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key"),
        sa.UniqueConstraint("id"),
    )
    backup = _backup_table()
    connection.execute(
        sa.insert(backup).from_select(
            list(CONFIG_BACKUP_COLUMNS),
            sa.select(*(table.c[name] for name in CONFIG_BACKUP_COLUMNS)).where(
                table.c.key.in_(DELETED_CONFIG_KEYS)
            ),
        )
    )
    connection.execute(sa.delete(table).where(table.c.key.in_(DELETED_CONFIG_KEYS)))
    op.drop_constraint("ck_system_configs_group", "system_configs", type_="check")
    op.create_check_constraint(
        "ck_system_configs_group",
        "system_configs",
        "\"group\" IN ('upload', 'processing', 'security', 'review', 'ragflow', 'outbox')",
    )
    _insert_missing(ACTIVE_CONFIGS)
    for key, group, value_type, _value, is_secret, description in ACTIVE_CONFIGS:
        normalized_value = (
            True if key == "security.block_critical_sensitive_sync" else table.c.value
        )
        connection.execute(
            sa.update(table)
            .where(table.c.key == key)
            .values(
                group=group,
                value=normalized_value,
                value_type=value_type,
                is_secret=is_secret,
                description=description,
            )
        )

    op.add_column(
        "event_outbox",
        sa.Column("first_publish_failed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "event_outbox",
        sa.Column("last_publish_failed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "outbox_dead_letters",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column(
            "first_failed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "last_failed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error_type", sa.String(length=120), nullable=False),
        sa.Column("correlation_id", sa.String(length=80), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column(
            "payload_summary",
            postgresql.JSONB(astext_type=sa.Text()),  # type: ignore[no-untyped-call]
            nullable=False,
        ),
        sa.Column("replay_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_replayed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_replayed_by", sa.Uuid(), nullable=True),
        sa.Column("last_replay_reason", sa.Text(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'requeued', 'resolved')",
            name="ck_outbox_dead_letters_status",
        ),
        sa.CheckConstraint(
            "attempts >= 0",
            name="ck_outbox_dead_letters_attempts_nonnegative",
        ),
        sa.CheckConstraint(
            "replay_count >= 0",
            name="ck_outbox_dead_letters_replay_count_nonnegative",
        ),
        sa.ForeignKeyConstraint(["event_id"], ["event_outbox.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["last_replayed_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_outbox_dead_letters_event_id",
        "outbox_dead_letters",
        ["event_id"],
        unique=True,
    )
    op.create_index(
        "idx_outbox_dead_letters_status_last_failed_at",
        "outbox_dead_letters",
        ["status", "last_failed_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "idx_outbox_dead_letters_status_last_failed_at",
        table_name="outbox_dead_letters",
    )
    op.drop_index("uq_outbox_dead_letters_event_id", table_name="outbox_dead_letters")
    op.drop_table("outbox_dead_letters")
    op.drop_column("event_outbox", "last_publish_failed_at")
    op.drop_column("event_outbox", "first_publish_failed_at")

    table = _config_table()
    connection = op.get_bind()
    connection.execute(sa.delete(table).where(table.c.key.in_(NEW_CONFIG_KEYS)))
    op.drop_constraint("ck_system_configs_group", "system_configs", type_="check")
    op.create_check_constraint(
        "ck_system_configs_group",
        "system_configs",
        "\"group\" IN ('upload', 'processing', 'security', 'basic', 'ragflow')",
    )
    backup = _backup_table()
    connection.execute(sa.delete(table).where(table.c.key.in_(DELETED_CONFIG_KEYS)))
    connection.execute(
        sa.insert(table).from_select(
            list(CONFIG_BACKUP_COLUMNS),
            sa.select(*(backup.c[name] for name in CONFIG_BACKUP_COLUMNS)),
        )
    )
    op.drop_table(CONFIG_BACKUP_TABLE)
