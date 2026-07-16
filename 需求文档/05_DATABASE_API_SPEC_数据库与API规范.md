# 05. 数据库、状态机与 API 规范

> 版本：2.0 · 2026-07-16
>
> 本文是文件状态与 HTTP 契约的唯一权威源。旧 `docs/api.md` 仅是历史实现快照；若冲突，以本文为目标契约，并在验收矩阵中标记未实现项。

## 1. 数据模型最小契约

核心表包括：`users`、`departments`、`department_admins`、`files`、`document_analysis`、`review_records`、`review_claims`（或 files 上等价字段）、`categories`、`tags`、`file_tags`、`dataset_mappings`、`sync_tasks`、`task_logs`、`notifications`、`system_configs`、`audit_logs`、`event_outbox`。

`files` 至少保存：上传人/部门、对象 key、原名/MIME/大小/SHA256、可见性、主状态、分类与 Dataset 映射、AI 开关快照、敏感等级、提交/审核时间、负责人/到期、RAGFlow 文档与解析状态、版本关系、软删时间和乐观版本。历史审核与同步任务只能追加，不覆盖。

所有时间为带时区 UTC，API 输出 RFC 3339。主键 UUID。新增非空列必须提供可审计回填和分阶段迁移。

## 2. 单一文件状态机

### 2.1 状态定义

| 状态 | 产品文案 | 含义 |
|---|---|---|
| `uploaded` | 草稿 | 原件已存储，尚未提交；不是“已进入审核” |
| `extracting_text` | 提取文本 | AI 路径预处理 |
| `analysis_queued` | 等待 AI | 已进入 AI 队列 |
| `analyzing` | AI 分析中 | worker 正在分析 |
| `analyzed` | 分析完成 | 分析完成、仍是可提交草稿 |
| `analysis_failed` | AI 分析失败 | 可重试；是否可提交由策略决定 |
| `sensitive_review_required` | 敏感复核 | 自动提交被阻断，必须人工确认 |
| `pending_review` | 待审核 | 已提交，等待/正在部门审核 |
| `approved` | 已批准 | 已批准但尚未排队；可能为仅批准不入库 |
| `rejected` | 已驳回 | 等待员工修改并重提 |
| `queued` | 等待同步 | 唯一 RAGFlow 任务已创建 |
| `syncing` | 上传 RAGFlow | 正在上传远端 |
| `uploaded_to_ragflow` | 已上传待解析 | 已有远端文档 id |
| `parsing` | RAGFlow 解析中 | 远端解析进行中 |
| `parsed` | 已入库 | 远端解析成功 |
| `failed` | 同步失败 | 同步链失败，可按失败阶段重试 |
| `disabled` | 已归档 | 业务不可用，远端保留/删除按策略 |
| `deleted` | 已删除 | 本地软删，必要时等待远端清理 |
| `ragflow_cleanup_failed` | 远端清理失败 | 本地已删但远端清理需恢复 |

`review_status`、`ragflow_parse_status` 和 `expiry_status` 是派生/辅助维度，不得与主状态竞争主流程含义。

### 2.2 正常路径

```text
AI 关闭
uploaded ──手工/自动提交──> pending_review

AI 开启
uploaded -> extracting_text -> analysis_queued -> analyzing
                                             ├-> analyzed ──提交──> pending_review
                                             ├-> sensitive_review_required ──人工确认──> pending_review
                                             └-> analysis_failed ──策略允许提交──> pending_review

审核与同步
pending_review ──驳回──> rejected ──重提──> pending_review
pending_review ──批准且 approve_only──> approved
pending_review ──批准且 sync──> approved -> queued -> syncing
  -> uploaded_to_ragflow -> parsing -> parsed
```

### 2.3 允许转换

实现必须静态声明以下边，禁止 service 运行时修改集合：

- AI：`uploaded -> extracting_text`；`extracting_text -> analysis_queued|analysis_failed`；`analysis_queued -> analyzing|analysis_failed`；`analyzing -> analyzed|sensitive_review_required|analysis_failed`；`analysis_failed -> extracting_text|analysis_queued`；`analyzed -> analysis_queued|analysis_failed`。
- 提交：`uploaded|analyzed|analysis_failed|sensitive_review_required|rejected -> pending_review`。其中 `analysis_failed` 和敏感路径必须先过策略/权限前置条件。
- 审核：`pending_review -> approved|rejected`。
- 同步：`approved -> queued -> syncing -> uploaded_to_ragflow -> parsing -> parsed`。
- 同步失败/重试：`queued|syncing|uploaded_to_ragflow|parsing -> failed`；`failed -> syncing|parsing`，重试目标由任务已取得的远端 id 决定。
- 归档：`approved|parsed|failed|rejected|analyzed|pending_review -> disabled`。
- 软删：稳定、未运行任务的 `uploaded|pending_review|approved|rejected|failed|parsed|analysis_failed|analyzed|sensitive_review_required|disabled -> deleted`；`deleted <-> ragflow_cleanup_failed` 仅用于远端清理结果。

任何未列边非法并返回 409/422；`queued/syncing/parsing` 等运行态不能直接删除。状态变更必须同时留下审计或领域事件证据。

### 2.4 自动提交与敏感规则

- `submit_after_upload=false`：AI 关停在 `uploaded`；AI 开停在 `analyzed`/`analysis_failed`/`sensitive_review_required`。
- `submit_after_upload=true`：AI 关直接待审核；AI 开由分析完成 handler 自动提交，不能由上传请求提前跳状态。
- `critical` 保持 `sensitive_review_required`，默认不能自动提交且永远不能同步。`high` 的同步需要配置允许、显式确认和理由。

## 3. 通用 API 约定

成功 envelope：`{ "success": true, "data": ..., "message": "ok", "request_id": "..." }`。错误：`{ "success": false, "error_code": "...", "message": "...", "request_id": "...", "details": ... }`。不把 HTTPException 的内部形态直接泄露给客户端。

分页请求统一 `page=1&page_size=20`（最大 100），响应：

```json
{"items": [], "page": 1, "page_size": 20, "total": 0, "total_pages": 0}
```

搜索参数为 `q`；排序为白名单 `sort` 和 `order=asc|desc`。时间、状态、部门等筛选均服务端执行。非法分页 422，越权数据不计入 `total`。

## 4. 认证与部门

| 方法 | 路径 | 关键契约 |
|---|---|---|
| `GET` | `/api/auth/registration-departments` | 仅返回可注册选择的启用且非 unassigned 部门 `id/name/code`；稳定排序、受公共限流 |
| `POST` | `/api/auth/register` | `name,email,password,department_id,phone?` |
| `POST` | `/api/auth/login` | 未验证返回 `EMAIL_NOT_VERIFIED`（403），不发 JWT |
| `POST` | `/api/auth/verify-email` | body `token`；一次性 |
| `POST` | `/api/auth/resend-verification` | 统一响应避免枚举 |
| `POST` | `/api/auth/forgot-password` | 统一响应；不改变验证状态 |
| `POST` | `/api/auth/reset-password` | `token,new_password`；不激活未验证账号 |
| `GET` | `/api/auth/me` | 返回角色、部门与 `email_verified/department_assigned` 门禁 |

用户/部门管理列表遵循通用分页搜索。角色和部门变更只允许系统管理员并写审计。

## 5. 文档 API

| 方法 | 路径 | 权限与行为 |
|---|---|---|
| `GET` | `/api/files/policy` | 登录用户；扩展名、大小、多选、上传开关、删除能力 |
| `POST` | `/api/files/upload` | 登录且已验证/有部门；multipart 含 `file,description?,visibility,submit_after_upload,ai_analysis_enabled?` |
| `GET` | `/api/files` | 本人；通用分页 + `q,status,extension,tag_id,expiry_status` |
| `GET` | `/api/files/{file_id}` | 本人或授权管理员；详情含分析/审核/同步时间线 |
| `PATCH` | `/api/files/{file_id}` | 草稿本人编辑元数据；管理员分类接口可共用但权限分支明确 |
| `POST` | `/api/files/{file_id}/submit-review` | 本人重提或首次提交；幂等冲突返回 409 |
| `GET` | `/api/files/{file_id}/content` | `disposition=inline|attachment`；鉴权流式、Range；管理员跨用户读取审计 |
| `DELETE` | `/api/files/{file_id}` | 策略允许且非运行态；软删 |

原件响应设置 `X-Content-Type-Options: nosniff`、安全 `Content-Disposition`、明确 MIME；HTML/SVG/可执行内容永不 inline。

## 6. 审核与 RAGFlow

| 方法 | 路径 | 契约 |
|---|---|---|
| `GET` | `/api/review/files` | 管理员；分页搜索 + `queue=unclaimed|mine|due_soon|overdue`、状态/部门/敏感等级 |
| `POST` | `/api/review/files/{file_id}/claim` | 原子领取；已被他人领取返回 409 |
| `DELETE` | `/api/review/files/{file_id}/claim` | 本人释放；系统管理员强制释放须 `reason` |
| `POST` | `/api/files/{file_id}/approve` | `sync_decision` 必填；见下方 schema |
| `POST` | `/api/files/{file_id}/reject` | `reason` 必填；只能由有效领取人或系统管理员 |
| `GET` | `/api/tasks` | 管理员数据域内分页、搜索、状态筛选 |
| `POST` | `/api/tasks/{task_id}/retry|cancel` | 稳定前置条件、审计、幂等 |
| `POST` | `/api/admin/files/{file_id}/sync` | 仅已批准且未活跃同步；必须显式 Dataset |

批准请求：

```json
{
  "sync_decision": "sync",
  "dataset_mapping_id": "uuid-required-when-sync",
  "category_id": "uuid-or-null",
  "reason": "optional except risk override"
}
```

`sync` 时 Dataset 缺失/禁用/越权返回 422，不得退化为仅批准；`approve_only` 时忽略 Dataset 是错误，应要求客户端不传并返回 422。响应返回 `status`、`sync_decision` 和可空 `sync_task_id`。

## 7. 通知与工作台

| 方法 | 路径 | 契约 |
|---|---|---|
| `GET` | `/api/notifications` | 分页，`unread_only`；返回 `unread_count` |
| `POST` | `/api/notifications/{id}/read` | 幂等标已读 |
| `POST` | `/api/notifications/read-all` | 当前用户全部标已读 |
| `GET` | `/api/dashboard` | 按角色返回授权 KPI、待办、最近活动和下钻 filter |

通知 metadata 只存结构化资源 id/type，深链由前端白名单生成，禁止任意 URL。

## 8. 配置、审计与统计

- `GET/PUT /api/admin/configs/{group}`：只暴露 [配置契约](../docs/product/CONFIG_CONTRACT.md) 中有消费者的 key；secret 永不回显。
- `GET /api/audit-logs`：系统管理员分页搜索；审计不可通过普通 API 修改/删除。
- 统计端点统一分页或时间粒度，并明确时区、空数据和分母。容量/成本为 P2，但上线前必须有队列/outbox/DLQ/SLA 基础指标。

## 9. 兼容与演进

新增必填字段先以服务端兼容窗口发布，再升级前端，最后收紧；但安全门禁和明确同步决定不得长期双语义。API breaking change 记录版本与迁移截止日期，不能靠前端猜测可空字段含义。
