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

---

## 2. 文件状态

```text
uploaded
extracting_text
analysis_queued
analyzing
analysis_failed
analyzed
pending_review
sensitive_review_required
approved
rejected
queued
syncing
uploaded_to_ragflow
parsing
parsed
failed
disabled
deleted
```

AI 关闭时跳过所有 AI 相关状态。

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
```

### 3.4 Tasks

```http
GET  /api/tasks
GET  /api/tasks/{id}
POST /api/tasks/{id}/retry
POST /api/tasks/{id}/cancel
```

### 3.5 Dataset

```http
GET    /api/datasets
POST   /api/datasets
PATCH  /api/datasets/{id}
DELETE /api/datasets/{id}
```

### 3.6 AI Admin

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
POST  /api/admin/ai/prompts
GET   /api/admin/ai/prompts/{id}
PATCH /api/admin/ai/prompts/{id}
POST  /api/admin/ai/prompts/{id}/test
POST  /api/admin/ai/prompts/{id}/restore-default

GET    /api/admin/ai/sensitive-rules
POST   /api/admin/ai/sensitive-rules
PATCH  /api/admin/ai/sensitive-rules/{id}
DELETE /api/admin/ai/sensitive-rules/{id}
```

### 3.7 Statistics

```http
GET /api/admin/statistics/overview
GET /api/admin/statistics/users
GET /api/admin/statistics/users/{user_id}
GET /api/admin/statistics/departments
GET /api/admin/statistics/categories
GET /api/admin/statistics/trends
GET /api/admin/statistics/failures
GET /api/admin/statistics/export
```

查询参数：

```text
start_date
end_date
department
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
