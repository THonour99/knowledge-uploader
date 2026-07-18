# 05. 数据库、状态机与 API 规范

> 版本：2.1 · 2026-07-17
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

详情中的 `analysis.cost_status` 必须是 `known`、`unknown_pricing`、`unknown_usage`、
`legacy_unverifiable` 四态之一。`analysis.estimated_cost_microunits` 的 JSON 类型固定为
`decimal-string|null`：只有 `cost_status=known`（包括明确核实的 0）返回十进制字符串；其余三态
一律返回 `null`。字符串口径避免超过 JavaScript 安全整数后丢失精度。滚动发布期间数据库可为
旧 writer 保留非空金额兼容值，但 API 不得据此把未知成本显示
为 0 或其他金额。

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

### 8.1 容量与成本统计通用契约

以下三个端点只允许 `system_admin`；已登录的其他角色统一返回 403，未登录仍按认证契约返回
401。成功响应继续使用通用 envelope。每次成功读取必须写一条管理员审计，审计 metadata 只含
UTC 时间窗、分组、分页和必要的物理维度，不得记录返回行、文件名、对象 key、prompt、原文、
邮箱、API Key 或 bearer token。

公共查询参数：

- `start_at`、`end_before` 可选，但传入时必须是带时区的 RFC 3339 时间；服务端换算为 UTC，
  查询窗口固定为半开区间 `[start_at, end_before)`。
- `end_before` 缺省为服务端当前 UTC 时间；`start_at` 缺省为 `end_before - 30 days`。
  `start_at >= end_before`、任一时间无时区或窗口超过 366 天均返回 422。
- `page=1&page_size=20`，`page_size` 最大 100；非法分页返回 422。页码超出范围时返回空
  `items`，但保留真实 `total` 与 `total_pages`。
- 所有业务计量整数——次数、字节、token、微货币单位和毫秒——都以十进制字符串返回，避免
  JavaScript 精度损失；`pagination` 的页码与行数仍为 JSON integer。
- 没有聚合数据时返回 `items=[]`、`total=0`、`total_pages=0`，不是 404，也不得伪造一行 0。
  容量响应中的最新物理快照状态与逻辑聚合是否为空相互独立。
- `dimension_key` 是稳定下钻值，`dimension_label` 是显示值；未知部门或已删除维度必须归入
  明确的 `unknown`/“未知部门”，不得借统计接口暴露个人身份或原文。

### 8.2 三个统计端点

| 方法与路径 | 查询与口径 | 响应关键字段 | 成功审计动作 |
|---|---|---|---|
| `GET /api/admin/statistics/capacity` | `group_by=none\|department\|file_type\|processing_stage\|day`；`physical_dimension=cluster\|department\|file_type`。逻辑口径 `basis=database_file_rows_uploaded_in_window`，只聚合窗口内上传且 `storage_type=minio` 的数据库文件引用 | 每行返回 `file_count`、`active_logical_bytes`、`retained_inactive_bytes`、`total_referenced_bytes`；另有独立 `physical` | `statistics.capacity.view` |
| `GET /api/admin/statistics/llm-usage` | `group_by=none\|department\|provider\|model\|day`；口径 `basis=ai_usage_logs_created_in_window` | 每行返回 `total_calls`、按币种拆分的 `known_costs` 和按未知原因拆分的 `unknown_costs` | `statistics.llm_usage.view` |
| `GET /api/admin/statistics/ragflow-usage` | `group_by=none\|department\|operation\|result\|failure_category\|day`；口径 `basis=ragflow_api_calls_started_in_window` | 每行返回 `calls`、`completed_calls`、`failure_calls`、`in_progress_calls`、`total_latency_ms`；失败原因通过 `group_by=failure_category` 明确下钻 | `statistics.ragflow_usage.view` |

容量的逻辑字节是数据库引用口径，不等同于 MinIO 去重后的磁盘占用。物理容量只支持
`physical_dimension=cluster`，来自最新 `minio_cluster_metrics` 原始集群快照：15 分钟内为
`available`；更旧但有效的快照为 `stale` 并保留数值；无快照或时间异常为 `unavailable` 且数值
为空。请求 `department` 或 `file_type` 时返回 `unsupported_dimension`，不得把集群物理容量按逻辑
比例虚构分摊。`physical.scope` 永远是 `cluster`，物理快照是最新观测而不是请求时间窗内求和。

LLM 成本只有 `known_costs` 可以相加，且必须按 `currency` 分开；金额单位为
`estimated_cost_microunits`。未知成本必须进入 `unknown_pricing`、`unknown_usage` 或
`legacy_unverifiable` 分桶，返回其 calls、已知 token 小计和 `calls_with_unknown_tokens`，不得把
未知价格、缺失用量或旧数据当作 0 成本。不同币种禁止在服务端或前端直接合计。

RAGFlow `calls` 包含窗口内开始的 `started/success/failure` 记录；`completed_calls` 只含
`success/failure`，`failure_calls` 只含 `failure`，`in_progress_calls` 只含 `started`，总耗时只累计
已有完成耗时的记录。
`failure_category=none` 表示该分组没有失败类别，不得解释为失败。任何未知持久化状态必须
fail closed，而不是落入成功桶。

除了通用参数校验外，未知 `group_by`/`physical_dimension` 枚举返回 422。验收至少覆盖三个端点
的 200、非系统管理员 403、无时区/反向时间窗/超过 366 天/非法分页与枚举的 422、空数据、
超页分页、unknown 成本不伪装为 0、物理 `stale/unavailable/unsupported_dimension`，以及审计动作
和隐私字段缺失。

容量/成本为 P2；统计可见性不能替代队列/outbox/DLQ/SLA 基础指标、预算门禁或上线证据。

## 9. 兼容与演进

新增必填字段先以服务端兼容窗口发布，再升级前端，最后收紧；但安全门禁和明确同步决定不得长期双语义。API breaking change 记录版本与迁移截止日期，不能靠前端猜测可空字段含义。

`20260716s002` 是成本四态的 expand 迁移：先增加带 `legacy_unverifiable` server default 的
`cost_status`，同时保留旧 writer 依赖的非空 `estimated_cost_microunits` 与 server default 0。
随后部署新 reader（以 `cost_status` 决定是否公开金额）和新 writer（未知成本在物理金额列写兼容
sentinel 0，并显式写四态）。确认旧版本实例全部退役并完成观测窗口后，才允许在独立的后续
contract revision 中移除兼容 default 或进一步收紧约束；不得在 `s002` 或同一 head 越过该窗口。

同一 expand 迁移中的 `ai_providers.pricing_configured` 只是价格口径的原始声明，不是可直接消费的
最终真值。数据库同时保存 `pricing_confirmed_input_microunits_per_million`、
`pricing_confirmed_output_microunits_per_million` 与 `pricing_confirmed_currency` 三元确认快照；只有原始
声明为 true、三个快照均非 null，且与当前输入价、输出价、币种逐项完全一致时，API、审计和 worker
才把 `pricing_configured` 解释为有效 true。数值 0 必须按 `is not null` 参与比较，不能当作缺失。

滚动发布期间，旧 writer 插入供应商时依靠 server default 得到 false 且无确认快照；旧 writer 修改
输入价、输出价或币种中的任一项时，原快照与当前值不再一致。两种情况都必须 fail closed 为
`unknown_pricing`，不得因为原始 bool 仍为 true 而产生已知 0 成本。新 writer 显式确认时同步当前
三元组，显式取消时清空三元组；未显式声明但提交了价格字段且至少一项非零时可同步确认。两项价格
均为 0 只有在新 writer 显式确认并同步包含 0 的完整三元组后才是已知免费口径。降级/重升级必须
通过 shadow backup 恢复原声明与确认快照，同时保留降级窗口内当前价格和币种，使任何漂移自然失效；
降级窗口中新建行按本迁移的正常回填规则处理。

其中“提交价格字段”仅指 PATCH 中值非 null 的输入价、输出价或币种。值为 null 一律按“未修改”
处理，既不能触发隐式确认，也不能在审计中伪报字段已变更；`pricing_configured=true` 仍可与这些
null no-op 字段同时出现，并显式确认当前三元组。
