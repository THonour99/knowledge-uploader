# 运行时配置契约

> 版本：1.0 · 2026-07-16
>
> 目的：设置页只允许出现真正影响运行行为的配置。表中“当前消费者”来自代码核对；`无` 代表上线阻断，不代表可继续保留占位。

## 1. 读取与生效规则

业务配置优先级：`system_configs` 有效值 > 环境变量映射 > 代码默认。通过管理 API 修改后发布 `ConfigChanged` 并失效进程缓存；安全组最迟 5 秒、其他热更新最迟 15 秒生效。Worker 必须同样读取运行时配置或订阅失效事件，不能只有 API 进程生效。

基础设施/启动密钥（数据库 URL、broker、Redis、MinIO、JWT、Fernet、SMTP 凭据、镜像/端口）只允许环境或 secret manager 配置，标记“重启”。它们不得同时出现在可热更新设置页。

Secret 写入前 Fernet 加密，GET 只返回 `has_value` 和 mask；审计只记录 key 和变更者，不记录旧值/新值。解密失败必须告警并安全失败，不能静默使用空字符串继续外部调用。

## 2. 业务配置逐项登记

生效：`热(5s/15s)` 表示缓存上限；`新任务` 表示不回改已创建任务；`重启` 不允许进设置页。

| Key | 默认 | Secret | 当前运行时消费者 | 生效 | 上线动作 |
|---|---:|:---:|---|---|---|
| `upload.enabled` | `true` | 否 | document policy 读取，但定义缺失 | 热(15s) | **P0 实现定义并在上传端点强制** |
| `upload.allowed_extensions` | 7 类 | 否 | document API/service | 热(15s) | 保留；扩展名与 MIME 联动测试 |
| `upload.max_file_size_mb` | `50` | 否 | document API/service | 热(15s) | 保留；流式限制 |
| `upload.user_quota_mb` | `0` | 否 | document service | 热(15s) | 保留；0=不限 |
| `upload.allow_multi_file` | `true` | 否 | policy 响应（服务端单请求单文件） | 热(15s) | 保留为客户端能力；不得绕过逐文件校验 |
| `upload.allow_user_delete` | `false` | 否 | document service/policy | 热(15s) | 保留 |
| `upload.enable_duplicate_check` | `true` | 否 | **无（当前始终去重）** | 热(15s) | P0 实现分支或删除；推荐实现且 false 仍记录 hash |
| `processing.auto_parse_on_upload` | `true` | 否 | **无** | 新文件 | 删除；由文件 AI 快照决定解析 |
| `processing.auto_sync_after_parse` | `false` | 否 | **无且语义与人工审核冲突** | — | **删除** |
| `processing.sync_after_ai_analysis` | `true` | 否 | **无且易与自动提交混淆** | — | **删除**；使用用户 `submit_after_upload` |
| `processing.task_max_retries` | `3` | 否 | **无** | 新任务 | 实现为 Celery/outbox 通用重试上限，或删除后使用队列专属值 |
| `processing.task_timeout_seconds` | `600` | 否 | **无** | 新任务 | 实现 worker soft/hard timeout，或删除 |
| `processing.parse_max_pages` | `200` | 否 | AI extraction service | 新分析 | 保留 |
| `processing.parse_max_chars` | `20000` | 否 | AI extraction service | 新分析 | 保留 |
| `security.allowed_email_domains` | `company.com` | 否 | auth service | 热(5s) | 保留 |
| `security.password_min_length` | `8` | 否 | auth service | 热(5s) | 保留；只影响新密码 |
| `security.login_max_failed_attempts` | `5` | 否 | auth service | 热(5s) | 保留 |
| `security.login_lock_minutes` | `15` | 否 | auth service | 热(5s) | 保留 |
| `security.require_email_verification` | `false` | 否 | auth service | 热(5s) | 保留；已创建用户不得被错误激活 |
| `security.require_review_before_sync` | `true` | 否 | **无且属于架构红线** | — | **删除**；人工审核不可配置绕过 |
| `security.block_critical_sensitive_sync` | `true` | 否 | review/ragflow service | 热(5s) | 保留但生产必须 true；不得通过普通设置关闭 |
| `basic.system_name` | `knowledge-uploader` | 否 | **无** | 热(15s) | P1 实现公共 branding 响应与前端消费者，否则删除 |
| `basic.system_logo_url` | 空 | 否 | **无** | 热(15s) | P1 实现受控同源/可信 URL 与前端消费者，否则删除 |
| `basic.default_language` | `zh-CN` | 否 | **无** | — | 删除，直至真正支持 i18n |
| `basic.default_timezone` | `Asia/Shanghai` | 否 | **无** | 新聚合 | P1 用于统计边界/SLA/展示，否则删除 |
| `basic.notification_channels` | `[email]` | 否 | **无** | 新通知 | P1 notification handler 消费，否则删除 |
| `basic.admin_contact_email` | 空 | 否 | **无** | 热(15s) | P1 门禁/错误页消费，否则删除 |
| `ragflow.base_url` | `http://ragflow:9380` | 否 | ragflow runtime/API | 热(15s) | 保留；URL allowlist/SSRF 防护 |
| `ragflow.api_key` | 空 | **是** | ragflow runtime/API | 热(15s) | 保留；加密/mask |
| `ragflow.default_dataset_id` | 空 | 否 | **无且违反显式 Dataset 决策** | — | **删除** |
| `ragflow.auto_sync_enabled` | `false` | 否 | **无且违反显式审批决策** | — | **删除** |
| `ragflow.sync_max_retries` | `3` | 否 | ragflow service | 新任务 | 保留 |
| `ragflow.sync_timeout_seconds` | `60` | 否 | ragflow runtime/API | 新请求 | 保留 |
| `ragflow.allow_high_risk_sync` | `false` | 否 | **无** | 热(5s) | P1 实现审批确认+理由；critical 不受此项放宽 |
| `ragflow.delete_remote_on_file_delete` | `false` | 否 | document service | 新删除 | 保留 |
| `ragflow.keep_remote_on_archive` | `true` | 否 | document service | 新归档 | 保留 |

## 3. 启动环境契约

| 分类 | 变量 | Secret | 生效 | 校验 |
|---|---|:---:|---|---|
| 应用 | `APP_ENV`, `APP_BASE_URL`, `APP_NAME` | 否 | 重启 | protected env 禁本机/占位值 |
| 认证 | `JWT_SECRET`, `ENCRYPTION_KEY` | 是 | 重启 | JWT ≥32 bytes；Fernet 合法 |
| PostgreSQL | `DATABASE_URL`, `ALEMBIC_DATABASE_URL` | 是 | 重启 | TLS/凭据/迁移；禁止 SQLite |
| RabbitMQ | `CELERY_BROKER_URL`, queue names | 是/否 | 重启 | 连接、exchange/queue/DLQ 声明 |
| Redis | `CACHE_REDIS_URL`, `CELERY_RESULT_BACKEND` | 是 | 重启 | 连接、DB 隔离、密码 |
| MinIO | `MINIO_ENDPOINT/ACCESS_KEY/SECRET_KEY/BUCKET/SECURE` | 是 | 重启 | protected env 必须 TLS |
| SMTP | `SMTP_HOST/PORT/USER/PASSWORD/FROM/TLS` | 是 | 重启 | 验证开启时必须可投递或明确阻断注册 |
| 外部 AI | `ALLOW_EXTERNAL_LLM` | 否 | 重启策略门禁 | 外部调用默认 false；provider key 入库配置 |
| 上传限流 | `UPLOAD_RATE_LIMIT_PER_MINUTE` | 否 | 重启 | 正整数；DB runtime config 不重复定义 |

## 4. 禁止死配置的 CI 检查

每个 `ConfigDefinition` 必须在契约表登记，并满足至少一个非测试业务消费者。CI 扫描只能作为提示，最终用行为测试证明“改值前后结果不同”。无消费者、新旧同义 key、设置页写入但只在启动读取，均视为失败。

发布验收输出三份证据：定义清单、消费者清单、逐 key 行为测试结果。表中标粗的删除/实现项未清零前，不得标记阶段 9 完成。
