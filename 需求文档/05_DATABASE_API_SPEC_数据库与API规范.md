# 05. 数据库与 API 规范

## 1. 核心数据表

### 1.1 users

```text
id
name
email
email_domain
password_hash
department
phone
role
status
email_verified
auth_provider
external_user_id
ding_user_id
employee_no
failed_login_count
locked_until
last_login_at
last_login_ip
created_at
updated_at
```

### 1.2 files

```text
id
original_name
stored_name
extension
mime_type
size
hash
storage_type
bucket
object_key
uploader_id
department
category_id
dataset_mapping_id
visibility
description
tags
status
review_status
ragflow_dataset_id
ragflow_document_id
ragflow_parse_status
ragflow_error_message
ai_analysis_enabled_at_upload
ai_config_snapshot
uploaded_at
last_sync_at
created_at
updated_at
```

### 1.3 categories

```text
id
name
code
description
parent_id
require_review
default_dataset_id
allow_employee_select
allow_ai_recommend
default_visibility
keywords
classification_prompt
ai_analysis_enabled
sensitive_detection_enabled
auto_sync_enabled
created_at
updated_at
```

### 1.4 dataset_mappings

```text
id
name
category_id
ragflow_dataset_id
ragflow_dataset_name
enabled
created_at
updated_at
```

### 1.5 sync_tasks

```text
id
file_id
task_type
status
retry_count
max_retry_count
error_message
started_at
finished_at
created_at
updated_at
```

### 1.6 document_analysis

```text
id
file_id
summary
suggested_category_id
suggested_dataset_id
suggested_tags
sensitive_level
sensitive_items
quality_score
quality_reasons
detected_expire_at
detected_version
similar_files
model_provider
model_name
token_usage
status
error_message
created_at
updated_at
```

### 1.7 ai_providers

```text
id
name
provider_type
base_url
api_key_encrypted
chat_model
embedding_model
vision_model
is_internal
enabled
priority
timeout_seconds
max_retry_count
max_input_tokens
max_output_tokens
temperature
top_p
created_at
updated_at
```

### 1.8 statistics_snapshots

```text
id
snapshot_date
snapshot_type
scope_type
scope_id
metrics_json
created_at
```

### 1.9 user_upload_statistics

```text
id
user_id
department
total_files
approved_files
synced_files
failed_files
pending_review_files
rejected_files
sensitive_files
total_file_size
last_upload_at
last_success_sync_at
updated_at
```

### 1.10 tags

```text
id
name
code
description
usage_count
source
enabled
created_by
created_at
updated_at
```

### 1.11 file_tags

```text
id
file_id
tag_id
source
created_by
created_at
```

### 1.12 review_records

```text
id
file_id
reviewer_id
action
from_status
to_status
comment
reject_reason
sensitive_confirmed
category_id
dataset_mapping_id
created_at
```

### 1.13 ragflow_configs

```text
id
base_url
api_key_encrypted
default_dataset_id
allowed_dataset_ids
auto_sync_enabled
sync_after_review_only
allow_high_risk_sync
allow_critical_risk_sync
request_timeout_seconds
max_retry_count
enabled
created_at
updated_at
```

### 1.14 ragflow_sync_logs

```text
id
file_id
sync_task_id
ragflow_dataset_id
ragflow_document_id
operation
status
request_payload
response_payload
error_message
retry_count
operator_id
started_at
finished_at
created_at
```

### 1.15 system_settings

```text
id
scope
key
value_json
is_secret
description
updated_by
created_at
updated_at
```

### 1.16 audit_logs

```text
id
actor_id
actor_role
action
object_type
object_id
before_json
after_json
result
failure_reason
ip_address
user_agent
request_id
created_at
```

---

## 2. 文件状态

`files.status` 是文件主状态的唯一实现字段，状态变更只能通过 `DocumentStateMachine.transition(from, to)` 执行。

| 状态 | 中文 | 说明 |
|---|---|---|
| uploaded | 已上传 | 文件已通过校验并写入 MinIO 与元数据 |
| extracting_text | 文本抽取中 | AI 开启时的文本抽取或预处理 |
| analysis_queued | 等待分析 | AI 分析任务已入队 |
| analyzing | AI 分析中 | 正在生成摘要、分类、标签或敏感检测结果 |
| analysis_failed | AI 分析失败 | AI 分析失败但文件上传记录保留 |
| analyzed | AI 分析完成 | AI 结果已生成，等待审核 |
| pending_review | 待审核 | 等待管理员确认分类、标签、Dataset 和同步策略 |
| sensitive_review_required | 敏感审核 | 存在高风险或严重风险，需要管理员确认 |
| approved | 已审核 | 管理员审核通过，可创建同步任务 |
| rejected | 已拒绝 | 管理员拒绝同步或退回处理 |
| queued | 等待同步 | RAGFlow 同步任务已创建 |
| syncing | 同步中 | 正在上传到 RAGFlow |
| uploaded_to_ragflow | 已上传至 RAGFlow | RAGFlow 返回 document_id，等待或已触发解析 |
| parsing | RAGFlow 解析中 | 正在轮询 RAGFlow 解析状态 |
| parsed | RAGFlow 解析完成 | 文件已进入 RAGFlow 知识库可用状态 |
| failed | 失败 | 非 AI 类处理、同步或解析失败 |
| disabled | 已禁用或归档 | 保留记录但不再参与同步 |
| deleted | 已删除 | 文件已删除或软删除 |

AI 关闭时跳过 `extracting_text`、`analysis_queued`、`analyzing`、`analysis_failed`、`analyzed` 等 AI 相关状态。

推荐主流程：

```text
AI 关闭：
uploaded → pending_review → approved → queued → syncing → uploaded_to_ragflow → parsing → parsed

AI 开启：
uploaded → extracting_text → analysis_queued → analyzing → analyzed → pending_review → approved → queued → syncing → uploaded_to_ragflow → parsing → parsed
```

异常分支：

```text
analyzing → analysis_failed
analyzed → sensitive_review_required
pending_review / sensitive_review_required → rejected
queued / syncing / uploaded_to_ragflow / parsing → failed
approved / parsed → disabled
任意非 deleted 状态 → deleted
```

---

## 3. API 设计

### 3.1 Auth

```http
POST /api/auth/register
POST /api/auth/login
POST /api/auth/logout
GET  /api/auth/me
POST /api/auth/verify-email
POST /api/auth/resend-verification
POST /api/auth/forgot-password
POST /api/auth/reset-password
POST /api/auth/change-password
```

### 3.2 Users

```http
GET   /api/users
GET   /api/users/{id}
PATCH /api/users/{id}
POST  /api/users/{id}/disable
POST  /api/users/{id}/enable
```

### 3.3 Files

```http
POST   /api/files/upload
GET    /api/files
GET    /api/files/{id}
PATCH  /api/files/{id}
DELETE /api/files/{id}
POST   /api/files/{id}/submit-review
POST   /api/files/{id}/approve
POST   /api/files/{id}/reject
POST   /api/files/{id}/sync
POST   /api/files/{id}/retry
POST   /api/files/{id}/disable
POST   /api/files/{id}/reanalyze
GET    /api/files/{id}/sync-logs
GET    /api/files/{id}/review-records
```

### 3.4 Tasks

```http
GET  /api/tasks
GET  /api/tasks/{id}
POST /api/tasks/{id}/retry
POST /api/tasks/{id}/cancel
```

### 3.5 Categories / Tags / Dataset

```http
GET    /api/admin/categories
POST   /api/admin/categories
PATCH  /api/admin/categories/{id}
DELETE /api/admin/categories/{id}

GET    /api/admin/tags
POST   /api/admin/tags
PATCH  /api/admin/tags/{id}
DELETE /api/admin/tags/{id}

GET    /api/datasets
POST   /api/datasets
PATCH  /api/datasets/{id}
DELETE /api/datasets/{id}
```

### 3.6 RAGFlow Admin

```http
GET   /api/admin/ragflow/config
PATCH /api/admin/ragflow/config
POST  /api/admin/ragflow/test
GET   /api/admin/ragflow/sync-logs
GET   /api/admin/ragflow/datasets
POST  /api/admin/ragflow/datasets/sync
```

### 3.7 AI Admin

```http
GET   /api/admin/ai/config
PATCH /api/admin/ai/config

GET    /api/admin/ai/providers
POST   /api/admin/ai/providers
GET    /api/admin/ai/providers/{id}
PATCH  /api/admin/ai/providers/{id}
DELETE /api/admin/ai/providers/{id}
POST   /api/admin/ai/providers/{id}/test

GET   /api/admin/ai/features
PATCH /api/admin/ai/features/{feature_name}

GET   /api/admin/ai/prompts
PATCH /api/admin/ai/prompts/{id}
POST  /api/admin/ai/prompts/{id}/restore-default

GET    /api/admin/ai/sensitive-rules
POST   /api/admin/ai/sensitive-rules
PATCH  /api/admin/ai/sensitive-rules/{id}
DELETE /api/admin/ai/sensitive-rules/{id}
```

`prompt_templates` 与 `sensitive_rules` 属于后续增强或内部配置；本期页面不要求独立 Prompt 模板管理页。

### 3.8 Settings / Audit

```http
GET   /api/admin/settings
PATCH /api/admin/settings/{scope}
GET   /api/admin/audit-logs
GET   /api/admin/audit-logs/{id}
```

### 3.9 Statistics

```http
GET /api/admin/statistics/overview
GET /api/admin/statistics/users
GET /api/admin/statistics/users/{user_id}
GET /api/admin/statistics/categories
GET /api/admin/statistics/trends
GET /api/admin/statistics/failures
```

查询参数：

```text
start_date
end_date
user_id
category_id
status
review_status
sync_status
group_by=day/week/month
page
page_size
sort_by
sort_order
```

---

## 4. API 返回格式

统一返回：

```json
{
  "success": true,
  "data": {},
  "message": "ok",
  "request_id": "xxx"
}
```

错误返回：

```json
{
  "success": false,
  "error_code": "FILE_TOO_LARGE",
  "message": "文件超过最大上传限制",
  "request_id": "xxx"
}
```
