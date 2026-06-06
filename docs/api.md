# API 文档

本文记录当前阶段 9 已实现的 HTTP API。所有业务响应统一使用 envelope：

```json
{
  "success": true,
  "data": {},
  "message": "ok",
  "request_id": "..."
}
```

错误响应统一为：

```json
{
  "success": false,
  "error_code": "validation_error",
  "message": "request validation failed",
  "request_id": "..."
}
```

除公开认证接口外，请求必须携带：

```http
Authorization: Bearer <access_token>
```

## 角色

| 角色 | 说明 |
|---|---|
| `employee` | 普通员工，可上传文件、查看自己的文件 |
| `knowledge_admin` | 知识库管理员，可提交审核、审核文件、查看统计、管理 RAGFlow 任务 |
| `system_admin` | 系统管理员，可管理 Dataset、AI 配置、用户；系统设置页当前为前端占位 |

## 系统

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| `GET` | `/api/system/health` | 公开 | 健康检查，返回 `{"status":"ok"}` |

## 认证

| 方法 | 路径 | 权限 | 请求体 | 说明 |
|---|---|---|---|---|
| `POST` | `/api/auth/register` | 公开 | `name`, `email`, `password`, `department?`, `phone?` | 注册账号，受邮箱域和 IP 限流约束 |
| `POST` | `/api/auth/login` | 公开 | `email`, `password` | 登录并返回 JWT 和用户资料 |
| `POST` | `/api/auth/logout` | 登录用户 | 无 | 注销当前 JWT |
| `GET` | `/api/auth/me` | 登录用户 | 无 | 返回当前用户资料 |
| `POST` | `/api/auth/verify-email` | 公开 | `token` | 验证邮箱 |
| `POST` | `/api/auth/resend-verification` | 公开 | `email` | 重发邮箱验证 |
| `POST` | `/api/auth/forgot-password` | 公开 | `email` | 发起密码重置 |
| `POST` | `/api/auth/reset-password` | 公开 | `token`, `new_password` | 重置密码 |
| `POST` | `/api/auth/change-password` | 登录用户 | `current_password`, `new_password` | 修改当前账号密码 |

安全约束：

- 登录失败达到 `LOGIN_MAX_FAILED_ATTEMPTS` 后锁定账号。
- 注册、登录、重置密码、重发验证邮件均有 Redis 限流。
- token 入库前只保存 SHA256 hash，原文只出现在邮件通知中。

## 文件

| 方法 | 路径 | 权限 | 请求体或参数 | 说明 |
|---|---|---|---|---|
| `POST` | `/api/files/upload` | 登录用户 | `multipart/form-data`: `file`, `description?`, `visibility` | 上传文件，写 MinIO、文件元数据、上传审计和 outbox 事件 |
| `GET` | `/api/files` | 登录用户 | 无 | 查看自己的文件列表 |
| `GET` | `/api/files/{file_id}` | 登录用户 | `file_id` | 查看自己的文件详情 |

上传约束由环境变量控制：

- `UPLOAD_MAX_FILE_SIZE_BYTES`
- `UPLOAD_RATE_LIMIT_PER_MINUTE`
- `UPLOAD_ALLOWED_EXTENSIONS`
- `UPLOAD_ALLOWED_MIME_TYPES`

文件会经过扩展名白名单、MIME 二次校验、空文件检查、Windows 保留名清洗、SHA256 去重和 MinIO 存储。

## 分类与 Dataset

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| `GET` | `/api/categories` | 知识库管理员、系统管理员 | 分类列表 |
| `POST` | `/api/categories` | 系统管理员 | 创建分类 |
| `PATCH` | `/api/categories/{category_id}` | 系统管理员 | 更新分类 |
| `GET` | `/api/datasets` | 知识库管理员、系统管理员 | Dataset 映射列表 |
| `POST` | `/api/datasets` | 系统管理员 | 创建 Dataset 映射 |
| `PATCH` | `/api/datasets/{mapping_id}` | 系统管理员 | 更新 Dataset 映射 |
| `DELETE` | `/api/datasets/{mapping_id}` | 系统管理员 | 删除 Dataset 映射 |

`RAGFLOW_API_KEY` 非空时，Dataset id 必须在 `RAGFLOW_ALLOWED_DATASET_IDS` 中。联调只能创建新的测试 Dataset 映射，不操作原有知识库。

## 审核

| 方法 | 路径 | 权限 | 请求体 | 说明 |
|---|---|---|---|---|
| `GET` | `/api/review/files` | 知识库管理员、系统管理员 | 查询参数 | 审核队列 |
| `POST` | `/api/files/{file_id}/submit-review` | 知识库管理员、系统管理员 | 无 | 提交审核 |
| `POST` | `/api/files/{file_id}/approve` | 知识库管理员、系统管理员 | `category_id?`, `dataset_mapping_id?`, `reason?` | 审核通过，按映射创建 RAGFlow 任务 |
| `POST` | `/api/files/{file_id}/reject` | 知识库管理员、系统管理员 | `reason` | 审核驳回 |
| `PATCH` | `/api/files/{file_id}` | 知识库管理员、系统管理员 | `category_id?`, `dataset_mapping_id?` | 更新文件分类或 Dataset 映射 |

关键规则：

- 状态变更只能通过 service 层状态机。
- `critical` 敏感等级默认阻止同步 RAGFlow。
- AI 关闭时文件不能进入 AI 相关状态。
- 管理员审核操作写 `audit_logs`。

## RAGFlow 任务

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| `GET` | `/api/tasks` | 知识库管理员、系统管理员 | 查看同步任务 |
| `GET` | `/api/tasks/{task_id}` | 知识库管理员、系统管理员 | 查看任务详情和日志 |
| `POST` | `/api/tasks/{task_id}/retry` | 知识库管理员、系统管理员 | 重试失败或已取消任务 |
| `POST` | `/api/tasks/{task_id}/cancel` | 知识库管理员、系统管理员 | 取消排队任务 |

RAGFlow worker 会从 MinIO 读取对象，调用 RAGFlow 上传、解析、轮询状态，并写任务日志。每个文件同一时间只能存在一个活跃上传任务，Redis 锁 key 为 `lock:sync:{file_id}`。

## AI 配置

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| `GET` | `/api/admin/ai/config` | 知识库管理员、系统管理员 | 查看全局 AI 开关、功能开关、Provider、Prompt 和敏感规则 |
| `PATCH` | `/api/admin/ai/features/{feature_key}` | 系统管理员 | 更新功能开关 |
| `POST` | `/api/admin/ai/providers` | 系统管理员 | 创建模型供应商 |
| `PATCH` | `/api/admin/ai/providers/{provider_id}` | 系统管理员 | 更新模型供应商 |
| `POST` | `/api/admin/ai/providers/{provider_id}/test` | 系统管理员 | 测试供应商连通性 |

API Key 字段使用 Fernet 加密保存，响应只返回 `has_api_key` 和脱敏后的 `api_key_masked`。

## 统计

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| `GET` | `/api/admin/statistics/overview` | 知识库管理员、系统管理员 | 总览指标 |
| `GET` | `/api/admin/statistics/users` | 知识库管理员、系统管理员 | 用户贡献排行 |
| `GET` | `/api/admin/statistics/users/{user_id}` | 知识库管理员、系统管理员 | 用户明细 |
| `GET` | `/api/admin/statistics/departments` | 知识库管理员、系统管理员 | 部门统计 |
| `GET` | `/api/admin/statistics/categories` | 知识库管理员、系统管理员 | 分类统计 |
| `GET` | `/api/admin/statistics/trends` | 知识库管理员、系统管理员 | 趋势统计 |
| `GET` | `/api/admin/statistics/failures` | 知识库管理员、系统管理员 | 失败原因统计 |
| `GET` | `/api/admin/statistics/export` | 知识库管理员、系统管理员 | 导出 CSV |

CSV 导出会转义 Excel 公式注入风险字符。

## 用户管理

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| `GET` | `/api/users` | 系统管理员 | 用户列表 |
| `GET` | `/api/users/{user_id}` | 系统管理员 | 用户详情 |
| `POST` | `/api/users/{user_id}/disable` | 系统管理员 | 禁用用户 |
| `POST` | `/api/users/{user_id}/enable` | 系统管理员 | 启用用户 |

系统管理员不能禁用自己，也不能越权处理同级系统管理员。
