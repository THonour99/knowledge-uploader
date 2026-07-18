# 可观测性与告警处理

Prometheus 只通过 Compose 私有网络抓取 backend-api:8000/metrics、
outbox-dispatcher:9101/metrics、RabbitMQ、MinIO、Linux host node exporter 和备份
textfile exporter。公网 Nginx
明确对 /metrics 返回 404，宿主 Prometheus 端口也只绑定 127.0.0.1。

启动与规则校验：

    docker compose -f docker-compose.yml -f docker-compose.observability.yml up -d
    docker compose -f docker-compose.yml -f docker-compose.observability.yml exec prometheus promtool check config /etc/prometheus/prometheus.yml
    docker compose -f docker-compose.yml -f docker-compose.observability.yml exec prometheus promtool check rules /etc/prometheus/alerts.yml

本机默认挂载 `alertmanager.example.yml` 的 blackhole receiver，仅用于规则开发。staging/production
必须通过 `ALERTMANAGER_CONFIG_FILE` 挂载由 secret manager 管理的真实 receiver 配置，并保存
一次测试告警从 firing 到 resolved 的通知证据；缺少任一项时 protected release gate 必须失败。

OBS-001 本机运行时验收使用唯一 Compose project、node-exporter textfile fixture 与生产
`alerts.yml`，按真实 `for` 窗口观察 outbox backlog、document worker offline、审核 SLA overdue
和 RAGFlow failure rate 四个规则从 pending 到 firing，再写入健康指标并观察 resolved。执行时必须
绑定完整、干净的候选 SHA，证据目录必须位于仓库外且不存在：

    python -I -S -X utf8 scripts/acceptance_launcher.py observability --expected-git-sha <40位SHA> --output-dir <仓库外新目录>

本机验收编排只允许以下两份批准的官方 manifest 引用，禁止回退为可变 tag：

- `prom/prometheus:v3.12.0@sha256:69f5241418838263316593f7274a304b095c40bcf22e57272865da91bd60a8ac`
- `quay.io/prometheus/node-exporter:v1.11.1@sha256:0f422f62c15f154af8d8572b23d623aebfb10cec73a5c654d18f911f3f9df241`

运行器必须先解析 `docker compose config --format json` 并逐项精确比对引用，随后才允许启动
promtool 一次性容器和常驻服务。常驻服务启动后还必须同时核对容器 `Config.Image`、容器实际
image ID、本机镜像 ID 与 `RepoDigests` 中的批准 repository digest；任一缺失或不一致均
fail closed。证据只记录引用、digest、镜像/容器 ID、OS 与架构，不归档容器环境或原始 inspect
输出。该镜像身份校验仍只是本机 OBS-001 证据，不能替代真实 Alertmanager/receiver 与
`EXT-WEBHOOK-001` 门禁。

证据有效期为 24 小时；合并产生新 SHA 后必须重跑，失败、过期、候选不一致或清理不完整的证据
不得填为“通过”。该验收不会启动 Alertmanager，也不会发送或伪造 Webhook；它只能证明本机
Prometheus 抓取、生产规则窗口、状态转换与 runbook 绑定。`EXT-WEBHOOK-001` 仍必须在受保护环境
由独立 receiver 生成 firing/resolved 投递收据。
生产 `prometheus.yml` 的本机 promtool 语法检查只挂载显式 synthetic placeholder，以满足
`credentials_file` 必须存在的解析前提；它不验证受保护 MinIO 鉴权。证据必须固定记录
`protected_minio_auth_verified=false` 与 `synthetic_auth_placeholder=true`，真实 TLS、token
权限、轮换和抓取证据仍按下方 protected 流程执行。

staging/production 还必须叠加 `docker-compose.observability.protected.yml`，显式设置 `PROMETHEUS_CONFIG_FILE=./ops/observability/prometheus.protected.yml` 和 `MINIO_TLS_DIR=<含 public.crt、private.key、ca.crt 的目录>`。`public.crt` 的 SAN 必须包含 `DNS:minio`，MinIO 健康检查和 Prometheus 都必须用 `ca.crt` 校验该名称。MinIO 抓取必须显示为 `https://minio:9000/minio/v2/metrics/cluster`，`server_name=minio`，且 `/api/v1/targets` 中 `job=minio` 的 health 必须为 `up`；禁止通过 HTTP 或 `insecure_skip_verify` 规避证书问题。

MinIO root 凭据只允许进入 MinIO、`minio-bootstrap`、`minio-metrics-token-init`。幂等 `minio-bootstrap` 使用 root 创建桶、独立数据面用户和桶级最小策略；应用只获得数据面凭据，`operational-metrics` 不获得任何可用 S3 凭据。两组凭据相同或 bootstrap 失败时，后端与指标消费者必须拒绝启动。

MinIO 指标在所有环境都固定使用 `MINIO_PROMETHEUS_AUTH_TYPE=jwt`。一次性
`minio-metrics-token-init` 用管理员凭据调用 `mc admin prometheus generate`，把唯一 bearer
token 原子写入命名卷 `/run/secrets/minio-metrics/token`，权限必须为 `0440`、属主/属组必须为
`65534:65534`。`operational-metrics` 与 Prometheus 只读挂载该目录；token 不得出现在环境变量、
Compose 参数、API 响应或日志中，初始化容器必须 `exited 0` 且日志为空。匿名访问 cluster
endpoint 必须返回 401/403，不能改回 public，也不能把 MinIO 管理员密钥交给采集器。

常规 JWT 刷新只是签发新 token 并通过原子文件替换让既有消费者动态读取，不是吊销；不得重启消费者来掩盖动态读取缺陷。MinIO 在同一 root 凭据下重新签发后，旧 JWT 在其 `exp` 前仍可返回 200；禁止把“文件已替换”写成“旧凭据已失效”。执行前必须已导出非默认且彼此独立的 `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD`、`MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY`，以及 `MINIO_TLS_DIR` 和 `PROMETHEUS_CONFIG_FILE`。所有 protected 命令都必须叠加完整三个 Compose 文件：

    docker compose -f docker-compose.yml -f docker-compose.observability.yml -f docker-compose.observability.protected.yml run --rm --no-deps minio-metrics-token-init

刷新后必须验证 token 文件权限与单行三段 JWT 形状、初始化输出不含 JWT、旧 JWT 与新 JWT 均为 200 的自动化语义证据、两个消费者容器 ID 刷新前后不变、`component="minio_capacity"` 的 last-success 前进，以及 Prometheus `job=minio` 刷新前后均为 `up`。TERM/HUP/INT 会清理本次唯一临时文件；SIGKILL 可能留下 `.token.tmp.*`，但没有共享锁且下一次 init 必须成功。只有在以下命令证明没有初始化器运行后，才可清理孤立临时文件；不得读取、复制或输出文件内容：

    docker compose -f docker-compose.yml -f docker-compose.observability.yml -f docker-compose.observability.protected.yml ps --all minio-metrics-token-init
    docker compose -f docker-compose.yml -f docker-compose.observability.yml -f docker-compose.observability.protected.yml run --rm --no-deps --entrypoint /bin/sh minio-metrics-token-init -c 'rm -f /run/secrets/minio-metrics/.token.tmp.*'
    docker compose -f docker-compose.yml -f docker-compose.observability.yml -f docker-compose.observability.protected.yml run --rm --no-deps minio-metrics-token-init

紧急吊销只允许在已批准维护窗执行。root 凭据变化会让 MinIO 重启前签发的全部 metrics JWT 在重启后返回 403，同时 MinIO 数据面和两条指标链会出现短暂中断；预期窗口从 MinIO 停止开始，到 bootstrap、重签和两个消费者恢复且连续两个抓取周期为 `up` 结束。执行顺序固定为：在 secret manager 生成新的非默认 root 凭据版本；强制重建 MinIO；重新协调数据面用户/策略；重新签发；保持 `operational-metrics` 和 Prometheus 原进程不变，等待它们原地自动恢复：

    docker compose -f docker-compose.yml -f docker-compose.observability.yml -f docker-compose.observability.protected.yml up -d --no-build --force-recreate minio
    docker compose -f docker-compose.yml -f docker-compose.observability.yml -f docker-compose.observability.protected.yml run --rm --no-deps minio-bootstrap
    docker compose -f docker-compose.yml -f docker-compose.observability.yml -f docker-compose.observability.protected.yml run --rm --no-deps minio-metrics-token-init

上线证据必须在不输出 token 或摘要的前提下证明：轮换前 token 在常规刷新后仍为 200；root 轮换并重启后轮换前 token 为 403；新 token 为 200；两个消费者容器 ID 不变且 collector / Prometheus 原地自动恢复；匿名请求为 401/403；数据面目标桶读写正常且第二桶 list/get/put 与 admin 操作全部拒绝。任一项失败都阻塞发布；任一消费者只有重启才能恢复也视为发布失败，不得把 recreate 后绿灯当作动态切换证据。

若 root 轮换因配置错误失败且事件并非凭据泄露，可在同一维护窗恢复 secret manager 中上一版本 root 凭据，强制重建 MinIO，并按 bootstrap → token init → 消费者原地恢复 的同一顺序回滚。若事件涉及凭据泄露，禁止回滚到已暴露版本；应保留服务中断、修复新凭据或切换到另一个未暴露版本。无论成功或回滚，都必须记录中断起止时间、secret 版本的安全引用、旧/新 HTTP 状态和消费者恢复时间；不得记录凭据、JWT 或 JWT 摘要。

指标标签只允许方法、路由模板、状态类别、固定任务/服务族和固定结果。禁止增加用户 ID、
文件 ID、邮箱、原始 URL、token、prompt、异常文本或对象 key。

`knowledge_uploader_logical_document_references_bytes{backend="minio"}` 只统计状态不为
`disabled`、`deleted` 或 `ragflow_cleanup_failed` 的活动文件行引用字节。三类状态均在治理 API
单列为 retained inactive；其中 cleanup failed 表示本地原件已删除、仅远端清理失败。去重后共用
同一对象的多个文件行仍分别计数，因此该指标不代表 MinIO 物理磁盘。
`knowledge_uploader_postgres_database_size_bytes` 是
`pg_database_size()` 返回的数据库物理大小，二者不得聚合。MinIO 物理容量来自其 exporter，
PostgreSQL 所在宿主文件系统来自 Linux node exporter。
`knowledge_uploader_operational_collector_db_pool_connections` 只代表 collector 自己的连接池，
不代表 API 或 Celery worker 的连接池。
当前通用 task/external counter 只承诺 outbox→RabbitMQ 投递；业务 worker 结果使用持久化任务
状态、队列 consumer/depth 与 RAGFlow 窗口指标，未埋点的 LLM 调用不得声称已被该 counter 覆盖。

## KnowledgeUploaderApiDown

1. 查询 docker compose ps backend-api postgres redis rabbitmq minio。
2. 查看 /api/system/ready 的依赖分类，不复制凭据或完整连接串到工单。
3. 依赖恢复后确认 up{job="knowledge-uploader-api"} == 1 持续两个采集周期。

## KnowledgeUploaderHttp5xxRateHigh

按路由模板聚合 5xx 和时延。先确认是否单一路由，再关联 request ID 查结构化日志。不得把
原始请求体、API Key 或文件内容加入指标标签。

## KnowledgeUploaderCriticalSyncInvariantViolated

运行时已强制把 security.block_critical_sensitive_sync 纠正为 true。检查数据库配置变更审计和
加密/迁移一致性；critical 文件仍必须无条件禁止同步，不能把该告警作为临时放行理由。

## KnowledgeUploaderReadyConsecutiveFailures

collector 按 `OPERATIONAL_METRICS_INTERVAL_SECONDS` 请求真实 /api/system/ready，连续三次失败才触发。按响应中的 PostgreSQL、
Redis、RabbitMQ、MinIO 分类逐项修复；不能用 /health 的进程存活代替 ready。

## KnowledgeUploaderOperationalCollectorStale

collector 进程在线但数据库采集时间戳过旧时也会报警。检查数据库 schema 是否已迁移到当前
head，尤其是 review_due_at；修复后确认时间戳按配置周期前进。stale 阈值为配置周期的两倍且
不低于 120 秒；合法的 300 秒周期不会被固定两分钟阈值持续误报。时间戳超过当前时间五分钟同样
报警，必须修复 collector/Prometheus 主机时钟，不能让未来时间掩盖 stale。

## KnowledgeUploaderOutboxBacklog

确认 dispatcher 存活、RabbitMQ 可达以及 knowledge_uploader_outbox_oldest_seconds 是否继续
增长。若失败达到上限，转到 KnowledgeUploaderDeadLetterPresent 流程。

## KnowledgeUploaderOutboxDispatcherDown

确认 outbox-dispatcher 容器和私网 9101 端口。恢复进程后还必须确认 pending 与 oldest 同时
回落，不能只以 up 指标恢复作为完成。

## KnowledgeUploaderOperationalCollectorDown

确认 operational-metrics 容器、9102 私网端口和数据库权限；该 collector 不接收业务流量。
数据库与 Redis 邮件指标作为两个独立 component 采集，一个失败不会清空另一个已成功更新的
Gauge。分别查看固定标签 `component="database"` 与 `component="email_redis"` 的 errors_total 和
last_success_timestamp_seconds，不得因为旧 Gauge 仍存在就判定采集正常。

## KnowledgeUploaderOutboxOldestTooOld

检查数据库锁、dispatcher 日志和 RabbitMQ 连通性。恢复后等待 oldest 指标回落；不要通过
直接更新 event_outbox 绕过 DLQ 和审计。

## KnowledgeUploaderDeadLetterPresent

1. 系统管理员通过 /api/admin/outbox/dead-letters?status=pending 查看元数据。
2. 修复根因后提交带原因的 replay 请求；接口不返回事件 payload。
3. 同一 DLQ 的重复 replay 是幂等的，只有第一次重新进入投递队列，所有尝试均写审计。

## KnowledgeUploaderOutboxFailureRateHigh

按固定 event_family 排查。异常文本只记录类型，任何密码、token 或消息 payload 均不得
进入 DLQ、日志或指标。

## KnowledgeUploaderRabbitQueueDepthHigh

比较各 worker 健康状态与 RabbitMQ queue 指标。扩容前先排除 poison task 和外部依赖持续
失败；禁止清空队列作为常规恢复手段。

## KnowledgeUploaderRabbitTaskDeadLetterPresent

四个 Celery 主队列都配置了 `knowledge.tasks.dlx` 和各自 `.dlq`，但 DLX 只接收 RabbitMQ
实际 reject/expire 的消息，不能把普通 Celery task exception 自动等同于“已进入 DLQ”。
认证邮件任务为避免 SMTP 确认歧义导致自动重复，明确使用 early ack；其 SMTP 失败不会进入
Rabbit DLQ，而是写 Redis 中的安全聚合计数并由 operational collector 暴露告警。其他允许
安全重放的任务仍须经系统管理员身份、原因、任务名白名单和参数 schema 校验；不得把未知
Celery payload 原样复制回主队列。

四个队列都可作为受审计的资格检查入口，但 clean-room 重放白名单仅包含
`ragflow.create_upload_task` 和 `ragflow.create_delete_task`，且只能回到
`ragflow_queue`。document、ai、notification 队列头部以及其他 ragflow 任务只能调查，
接口返回 `investigation-only` 后消息必须留在 DLQ；不得 raw republish、discard 或通过
RabbitMQ 控制台绕过审计。白名单重放会从任务 ID 重建单个 UUID 参数，使用确定性新 task ID
和持久消息发布，不复制原始 payload。

## KnowledgeUploaderWorkerOffline

RabbitMQ detailed endpoint 分别检查四个固定业务队列的 consumer 数。队列缺失和 consumers=0
都视为离线；恢复对应 worker 后确认 consumer 大于零。

## KnowledgeUploaderEmailDeliveryFailure

指标来自认证 API 的 `publish_failure` 与 notification worker 结果，统一写入 Redis 的固定
低基数计数和时间戳，再由 operational collector 抓取；不包含邮箱、主题、正文、token、
异常文本或 Rabbit payload。先区分 Rabbit publisher-confirm 失败、SMTP 配置错误、投递失败、
过期消息和密文信封无效。认证邮件使用 early ack 且不自动重试，失败不会进入 Rabbit DLQ；
修复 Rabbit/SMTP/密钥/时钟后，让用户通过重发验证或忘记密码重新签发新 token。
publisher confirm 结果不确定时可能产生两封内容相同、token 相同的邮件，不能宣称 SMTP
exactly-once。若指标缺失，同时检查 Redis 与 operational collector；不得把缺失数据当成零失败。

## KnowledgeUploaderEmailMetricsCollectorUnavailable

该告警专门覆盖 Redis 采集曾经成功、旧 email Gauge 仍留在进程中但后续读取持续失败的情况。
先比较 `component="email_redis"` 的 last-success 与当前时间，再查看最近五分钟 errors 增量；同时
检查 Redis 连通性、认证与 email metrics key 的读取错误。修复后必须确认 last-success 每个采集
周期继续前进且 errors 不再增长；旧的 persisted_total 数值本身不能作为恢复证据。数据库 component
仍可正常更新，这不代表邮件投递可观测性已恢复。
若采集周期 Gauge 缺失，本组件告警也会独立 fail closed；先恢复 collector 的周期指标，再按
last-success 判断陈旧程度，不能因时间戳暂时新鲜而静默。

## KnowledgeUploaderReviewSlaOverdue

指标读取提交时持久化的 review_due_at，不用当前配置回算历史 SLA。按最早截止时间处理，
需要转派时走领取/释放 API 并保留审计。

## KnowledgeUploaderRagflowSyncFailureRateHigh

`knowledge_uploader_ragflow_sync_outcomes_window` 是最近 15 分钟的文档最终结果 Gauge：
成功必须同时满足文件为 `parsed`、RAGFlow run 为 `3/DONE` 且无 RAGFlow 错误；
失败必须同时满足文件为 `failed` 且存在 RAGFlow 错误或失败 run。解析轮询的每个
`sync_tasks.succeeded` 跳数不会重复计数，后续 AI 重分析失败也不会归因给 RAGFlow；
取消时间与文件 `last_sync_at` 比较，每个文档只进入最新的成功、失败或取消之一。
两个时间相同时文件最终状态优先；`finished_at` 或 `last_sync_at` 为空的候选不会误计。
至少五个成功/失败文档后才计算失败率。核对 RAGFlow 协议、
allowlist 与密钥解密告警，不在指标中加入 dataset/file ID。

## KnowledgeUploaderRagflowCallTelemetryCollectorUnavailable

该 component 按配置周期检查持久化的 `ragflow_api_calls`。先看
`component="ragflow_call_telemetry"` 的 last-success 是否持续前进，再检查 errors 增量和
数据库迁移是否已到当前 head。采集失败不得把旧 Gauge 当成实时值，也不得删除 started 行来
解除告警；修复后确认 last-success 前进且 errors 不再增长。
若采集周期 Gauge 缺失，本组件告警也会独立 fail closed；必须先恢复周期指标，不能把无法计算
陈旧阈值解释为遥测健康。

## KnowledgeUploaderRagflowApiCallStale

`started` 超过 15 分钟进入 stale Gauge，超过 30 分钟由 collector 以
`failure_category="unknown"` 批量收敛为终态，每轮最多 1000 条并使用行锁避免重复处理。
先关联业务任务状态和固定 operation 排查 worker/数据库中断；遥测表不保存 dataset、file、
URL、请求体、响应体或异常文本。自动收敛只修复遥测生命周期，不代表远端操作可以安全重试。
终态调用保留 400 天（API 最大查询窗为 366 天）后由同一 collector 清理；`started` 行在完成
收敛前不得按保留期直接删除。

## KnowledgeUploaderMinioCapacityLow

Prometheus 通过私网 v2 cluster endpoint 读取 MinIO 实际 usable free/total bytes；低于 15%
持续十分钟报警。MinIO metrics down 单独报警，避免把缺失数据误判为容量充足。
即使 scrape `up=1`，任一 usable free/total 指标缺失仍必须触发 metrics down；先核对 MinIO
版本和 v2 指标名称，再恢复容量阈值判断。

## KnowledgeUploaderMinioCapacityCollectorUnavailable

该告警覆盖应用侧 `/minio/v2/metrics/cluster` 快照采集的缺失、超过“配置周期两倍且不低于
120 秒”未成功或最近五分钟出现错误；它与 Prometheus 直接抓取 MinIO 的 `KnowledgeUploaderMinioMetricsDown` 相互独立。
若采集周期 Gauge 缺失，本组件告警也会独立 fail closed；必须先恢复周期指标，不能因快照时间戳
暂时新鲜而判定容量采集健康。
检查 TLS CA、只读 token 文件是否存在且权限为 `0440/65534:65534`、初始化容器是否
`exited 0` 且无日志、私网端点和 raw total/free 指标一致性，以及数据库迁移。匿名请求返回
401/403 是正确安全基线；带 token 的采集仍返回 401/403 才是故障。不得切换 public、不得回退
到管理员密钥 URL，也不得在排障输出中打印 bearer token。修复后确认
`component="minio_capacity"` 的 last-success 持续前进；旧快照超过 15 分钟必须继续显示 stale，
不能把缺失采集解释为零使用量或容量充足。

## KnowledgeUploaderLinuxHostMetricsDown

检查 `host-node-exporter` 是否以 `pid: host` 运行并把 Linux 宿主根文件系统只读挂载到
`/host`。该 exporter 不发布宿主端口，只允许 Prometheus 私网抓取。DGX Spark 上必须确认
`node_uname_info` 与实际设备一致；Windows Docker Desktop 的结果只代表其 Linux VM，不能
作为生产宿主容量证据。

## KnowledgeUploaderLinuxHostCapacityLow

告警覆盖所有可写的 ext4/xfs/btrfs/zfs 文件系统，因此 PostgreSQL 数据卷位于根盘或独立
数据盘都会纳入门禁。低于 15% 持续十分钟即报警。先确认触发的 `device` 与 `mountpoint`，
再清理可重建缓存或扩容；不得删除 PostgreSQL/MinIO 数据目录来解除告警。恢复后确认容量
高于阈值且 `up{job="linux-host"} == 1` 持续两个采集周期。
