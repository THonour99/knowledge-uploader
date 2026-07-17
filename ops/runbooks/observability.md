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

staging/production 还必须叠加 `docker-compose.observability.protected.yml`，显式设置 `PROMETHEUS_CONFIG_FILE=./ops/observability/prometheus.protected.yml` 和 `PROMETHEUS_TLS_DIR=<只含 ca.crt 的目录>`。MinIO 抓取必须显示为 `https://minio:9000/minio/v2/metrics/cluster`，`server_name=minio`，且 `/api/v1/targets` 中 `job=minio` 的 health 必须为 `up`；禁止通过 HTTP 或 `insecure_skip_verify` 规避证书问题。

指标标签只允许方法、路由模板、状态类别、固定任务/服务族和固定结果。禁止增加用户 ID、
文件 ID、邮箱、原始 URL、token、prompt、异常文本或对象 key。

`knowledge_uploader_logical_document_references_bytes{backend="minio"}` 只统计状态不为
`deleted` 或 `ragflow_cleanup_failed` 的文件行所引用字节之和；后者表示本地文件已经删除、仅远端
RAGFlow 清理失败。去重后共用同一对象的多个文件行仍分别计数，因此不代表 MinIO 物理磁盘。
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

collector 每 30 秒请求一次真实 /api/system/ready，连续三次失败才触发。按响应中的 PostgreSQL、
Redis、RabbitMQ、MinIO 分类逐项修复；不能用 /health 的进程存活代替 ready。

## KnowledgeUploaderOperationalCollectorStale

collector 进程在线但数据库采集时间戳过旧时也会报警。检查数据库 schema 是否已迁移到当前
head，尤其是 review_due_at；修复后确认时间戳每 30 秒前进。时间戳超过当前时间五分钟同样
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

## KnowledgeUploaderMinioCapacityLow

Prometheus 通过私网 v2 cluster endpoint 读取 MinIO 实际 usable free/total bytes；低于 15%
持续十分钟报警。MinIO metrics down 单独报警，避免把缺失数据误判为容量充足。

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
