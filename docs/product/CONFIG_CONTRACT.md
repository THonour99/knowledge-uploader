# 运行时配置契约

> 版本：2.0 · 2026-07-16
>
> 本文是 system_configs、管理 API、设置页和运行时消费者的共同权威源。
> 当前契约恰好包含 26 个 active key 和 15 个 deleted key。

## 1. 读取、缓存与失败语义

数据库查询成功时，已存值优先；行缺失或值为空才使用环境映射/代码默认。安全组缓存最多
5 秒，其他组最多 15 秒。管理 API 提交成功后把已校验明文写入本进程可信缓存；secret
落库前仍使用 Fernet 加密，API、审计和日志不得返回值。

数据库不可用与 key 缺失不得混为一谈。数据库不可用时，进程使用最后可信值并在 1 秒后
重试；从未取得可信值时使用下表逐项 fail-closed 值。最后可信值仅保存在当前进程内存，
不会持久化或跨 API/worker 实例同步；进程重启后若数据库仍不可用，直接进入 fail-closed。
禁止在数据库故障时直接使用普通环境 fallback，以免把 upload.enabled=false、
require_email_verification=true 等策略放宽。

ConfigChanged 只使 TTL 缓存失效；历史任务保存创建时快照的字段不得被新配置追溯改写。
security.block_critical_sensitive_sync 永远强制为 true，任何持久化 false 都记指标并纠正。

AI Provider 的输入价、输出价和币种不属于 `system_configs`，由 `ai_providers` 的独立价格确认契约
管理。管理 API 返回的 `pricing_configured` 是有效值：仅当原始声明为 true，且内部保存的输入价、
输出价、币种确认快照均存在并与当前三元组完全一致时才为 true。旧版本 writer 修改任一价格字段或
币种、或插入带价格但无确认快照的 Provider，均必须返回 false 并把后续成本归入
`unknown_pricing`。明确的 0 价格只有由新版本 writer 同步包含 0 和币种的完整确认快照后才是
已知口径；设置页不得自行从价格数值推断确认状态，也不得把未知显示成 0 成本。

PATCH 中值为 null 的价格/币种字段是 no-op，不得触发隐式确认或审计伪变更；只有非 null 价格字段
才构成兼容隐式确认输入。

## 2. Active 配置（26）

| Key | 类型；默认；范围 | 真实消费者 | 生效边界 | 无可信缓存时 fail-closed |
|---|---|---|---|---|
| upload.enabled | bool；true | document service 上传门禁 | 热更新 15s；新上传 | false，关闭上传 |
| upload.allowed_extensions | list；pdf/docx/xlsx/pptx/txt/md/csv | document service 扩展名与 MIME 校验 | 热更新 15s；新上传 | [__blocked__] |
| upload.max_file_size_mb | int；50；1..200 | document API/service 内存上传硬上限 | 热更新 15s；新上传 | 1 |
| upload.user_quota_mb | int；0；0..1048576，0=不限 | document service 用户用量检查 | 热更新 15s；新上传 | 1 |
| upload.allow_multi_file | bool；true | document policy API 与前端能力 | 热更新 15s；每文件仍独立校验 | false |
| upload.allow_user_delete | bool；false | document service 删除权限 | 热更新 15s；新删除 | false |
| outbox.publish_max_retries | int；3；0..10 | outbox dispatcher；不控制 Celery 任务 | 下一次发布循环 | 0，首次失败即隔离 |
| processing.parse_max_pages | int；200；1..2000 | AI parser/extraction service | 新分析任务 | 1 |
| processing.parse_max_chars | int；20000；1000..1000000 | AI parser/extraction service | 新分析任务 | 1 |
| security.allowed_email_domains | list；[company.com] | auth 注册域名门禁 | 热更新 5s；新注册 | [blocked.invalid] |
| security.password_min_length | int；8；6..128 | auth 注册/改密/重置 | 热更新 5s；新密码 | 128 |
| security.login_max_failed_attempts | int；5；1..100 | auth 登录锁定 | 热更新 5s；新失败尝试 | 1 |
| security.login_lock_minutes | int；15；1..43200 | auth 登录锁定 | 热更新 5s；新锁定 | 1440 |
| security.require_email_verification | bool；false | auth 注册、登录和现有 token 门禁 | 热更新 5s；新注册/登录 | true |
| security.block_critical_sensitive_sync | bool；true；immutable | review/ragflow 强制阻断 | 始终立即强制 | true |
| review.claim_timeout_minutes | int；30；5..1440 | core review_policy 与 review service | 仅新领取；已持久化过期时间不回算 | 5 |
| review.sla_hours | int；24；1..720 | core review_policy 与 review service | 仅新提交；已持久化截止时间不回算 | 1 |
| ragflow.base_url | string；http://ragflow:9380 | core ragflow_runtime、API、tasks/service | 热更新 15s；新请求 | 空字符串 |
| ragflow.api_key | secret；空 | core ragflow_runtime、API、tasks/service | 热更新 15s；新请求 | 空，禁用集成 |
| ragflow.sync_max_retries | int；3；0..10 | ragflow service 领域重试 | 新同步任务 | 0 |
| ragflow.sync_timeout_seconds | int；60；5..3600 | ragflow runtime/client | 新外部请求 | 1，令请求安全失败 |
| ragflow.parse_poll_timeout_seconds | int；3600；60..86400 | ragflow parse polling service | 新轮询任务；与单请求超时独立 | 60 |
| ragflow.allow_high_risk_sync | bool；false | review/ragflow 高风险理由门禁 | 热更新 15s；新审批 | false |
| ragflow.delete_remote_on_file_delete | bool；false | document service 删除决策 | 新删除 | false |
| ragflow.keep_remote_on_archive | bool；true | document service 归档决策 | 新归档 | true |
| ragflow.keep_replaced_remote | bool；false | document service 在创建替代候选时冻结 delete/archive 动作；ragflow service 仅消费快照 | 仅影响新候选；archive 表示保留旧远端并写入 is_current_version=false 元数据，不表示物理隐藏 | true，避免配置存储故障时破坏性删除 |

备注：

- upload.max_file_size_mb 的 200MB 是当前 bytes/bytearray 内存上传架构硬上限，不得描述为流式上传。
- processing.parse_max_chars 的 fail-closed 值 1 是解析器显式接受的内部应急值，位于管理 API
  可配置范围 1000..1000000 之外；不得把它写入 system_configs。
- ragflow.sync_timeout_seconds 的 fail-closed 值 1 同样是外部请求内部应急值，位于管理 API
  可配置范围 5..3600 之外；其目的为快速失败，不得持久化为管理员配置。
- require_email_verification 的环境值是安全下限，运行时配置只能进一步收紧。
- `ragflow.base_url` 只能选择 `RAGFLOW_BASE_URL` 或环境所有者在
  `RAGFLOW_ALLOWED_BASE_URLS` 明确批准的完整 scheme/host/port/path；protected 环境还必须命中
  `RAGFLOW_TLS_SPKI_PINS` 中同一完整 endpoint 的 pin。禁止 userinfo、query、fragment、实例
  metadata 地址与跨端点重定向，数据库管理员不能扩大 allowlist 或 pin 边界。
- 数据库中的 `ai_providers.base_url` 同样只是运行时选择值，只能精确命中
  `LLM_ALLOWED_BASE_URLS` 与 `LLM_TLS_SPKI_PINS` 的既有交集。数据库写入、管理员 API 和
  `system_configs` 均不能新增受信 endpoint、替换环境 pin 或把同一 pin 复用于不同 hostname。
- critical 文件不受 allow_high_risk_sync 放宽。
- review 的领取过期和 SLA 必须持久化，配置变化不能重算历史记录。

## 3. Deleted 配置（15）

下列 key 已删除：不得出现在 ConfigDefinition、FALLBACKS、fail-closed 表、数据库 seed、
管理 API、设置页或生产消费者中。CI 会扫描其复活。

| Deleted key | 删除原因/替代契约 |
|---|---|
| upload.enable_duplicate_check | 去重是固定完整性规则，不提供关闭开关 |
| processing.auto_parse_on_upload | 由上传/AI 状态机与任务快照决定 |
| processing.auto_sync_after_parse | 与人工审核及显式同步决策冲突 |
| processing.sync_after_ai_analysis | 使用上传请求的 submit_after_upload 与审核状态机 |
| processing.task_timeout_seconds | 无统一消费者；各真实任务使用各自有界超时 |
| processing.task_max_retries | 更名为 outbox.publish_max_retries，且只控制 outbox |
| security.require_review_before_sync | 人工审核是架构红线，不可配置绕过 |
| basic.system_name | 无真实后端/前端消费者 |
| basic.system_logo_url | 无可信 URL 校验和真实消费者 |
| basic.default_language | 尚无完整 i18n 能力 |
| basic.default_timezone | SLA 使用持久化绝对时间，尚无统一时区消费者 |
| basic.notification_channels | 通知通道不是可热更新的真实策略 |
| basic.admin_contact_email | 无错误页/通知消费者 |
| ragflow.default_dataset_id | Dataset 必须由映射和审批显式决定 |
| ragflow.auto_sync_enabled | 同步必须由明确审核决策触发 |

## 4. 启动与基础设施配置

基础设施、凭据、监听端口不进入 system_configs，只能来自环境或 secret manager，并在重启后
生效。JWT_SECRET 至少 32 字节；ENCRYPTION_KEY 必须是合法 Fernet key；protected 环境禁止
占位凭据、SQLite、非 TLS MinIO 和公开 metrics 端口。

`COST-002` 未定版期间，protected 环境（`staging`/`production`）必须保持
`ALLOW_EXTERNAL_LLM=false`；发现 `ALLOW_EXTERNAL_LLM=true` 必须拒绝启动，不能由数据库配置
或管理员确认放宽。已批准的内部非计费 Provider 仍使用 `ALLOW_EXTERNAL_LLM=false`。
`development` 可为受控开发联调临时开启，但不能把结果提升为 protected 发布证据。

| 环境变量 | 默认/范围 | 消费者与约束 |
|---|---|---|
| OUTBOX_METRICS_PORT | 9101；1..65535 | outbox-dispatcher 私网 metrics；Prometheus target 必须同步 |
| OPERATIONAL_METRICS_PORT | 9102；1..65535 | operational-metrics 私网 metrics；Prometheus target 必须同步 |
| OPERATIONAL_METRICS_INTERVAL_SECONDS | 30；5..3600 | operational collector 采集周期 |
| ALLOW_EXTERNAL_LLM | false | `COST-002` 未定版时 `staging`/`production` 的 true 值拒绝启动；内部非计费 Provider 保持 false；`development` 仅限受控开发 |
| RAGFLOW_ALLOWED_BASE_URLS / RAGFLOW_TLS_SPKI_PINS | 空 | 环境所有者批准的精确 endpoint 与 JSON SPKI SHA-256 pin 映射；protected 的数据库 base_url 只能从两者交集选择 |
| LLM_ALLOWED_BASE_URLS / LLM_TLS_SPKI_PINS | 空 | 环境所有者批准的精确 endpoint 与 JSON SPKI SHA-256 pin 映射；protected 的数据库 Provider 不能扩大边界 |
| MINIO_ROOT_USER / MINIO_ROOT_PASSWORD | 无生产默认 | 仅 MinIO 与两个一次性 init；不得进入业务服务 |
| MINIO_ACCESS_KEY / MINIO_SECRET_KEY | 无生产默认 | 独立数据面用户；由 minio-bootstrap 幂等创建，仅授予指定桶对象权限 |
| MINIO_SERVER_IMAGE | approved `minio/minio:RELEASE...@sha256:<64hex>` | protected and E2E accept only the repository-approved multi-arch manifest digest; tag-only or alternate-digest overrides fail closed |
| MINIO_MC_IMAGE | approved `minio/mc:RELEASE...@sha256:<64hex>` | every CI backend build forwards this value; backend and ops consume mc only from a `TARGETPLATFORM` stage |
| MINIO_TLS_DIR | 无默认 | protected overlay 的 `public.crt` / `private.key` / `ca.crt`；证书 SAN 必须含 `DNS:minio` |
| MINIO_METRICS_BEARER_TOKEN_FILE | `/run/secrets/minio-metrics/token` | 仅 operational-metrics 注入并 fail closed；其他后端 Settings 不要求且不得看到该路径，禁止传 token 原文 |

两个 metrics 服务位于不同容器，但 Prometheus 配置固定使用 9101/9102；修改端口而不同时修改
Prometheus target 会触发 Down 告警。不得把这些端口发布到公网。MinIO exporter 必须保持
JWT 鉴权；protected 环境的 root 凭据必须显式提供、拒绝已知默认值，并在 MinIO 与两个一次性 init 间完全一致；root 与数据面凭据必须分离。bootstrap 每次删除并重建目标数据面用户和专用策略，清除旧直接策略与组成员漂移后再授予指定桶权限。token init 每次向服务端重新签发，使用同卷唯一临时文件校验后原子发布，不使用跨容器 PID 文件名或永久锁。Prometheus 和 operational-metrics 只读消费；token 禁止进入环境变量、system_configs、API 返回、日志或发布证据。常规刷新不是吊销，旧 JWT 在 `exp` 前仍有效；紧急吊销必须轮换 root 凭据并重启 MinIO。两种流程均不得重启 Prometheus 或 operational-metrics 来掩盖 bearer 文件动态读取缺陷；容器 ID 不变且原地恢复才能通过上线门禁。

## 5. 自动化一致性门禁

scripts/check_runtime_config_consumers.py 必须同时验证：

1. 26 个 ConfigDefinition key 与 FALLBACKS 完全相等；
2. 26 个 key 与 FAIL_CLOSED_DEFAULTS 完全相等；
3. 除 immutable invariant 外，每个 active key 至少有一个真实生产 get_config 消费者；
4. 15 个 deleted key 不得在生产代码或部署表面复活；
5. DEFAULT_DATASET_ID 不得重新出现在 Compose 或环境样例。

迁移 seed、管理 API 返回和前端设置页还必须通过行为测试证明是 26 active / 15 deleted。
任一门禁失败、真实基础设施 E2E 缺失或 ARM64 实机证据缺失时，不得声明阶段 9 完成。
