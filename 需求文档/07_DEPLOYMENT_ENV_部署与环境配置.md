# 07. 部署、环境与上线门禁

> 业务配置逐项契约见 [CONFIG_CONTRACT](../docs/product/CONFIG_CONTRACT.md)，逐条发布判定见 [ACCEPTANCE_MATRIX](../docs/product/ACCEPTANCE_MATRIX.md)。

## 1. 服务拓扑

生产服务：Nginx、frontend、backend-api、outbox-dispatcher、worker-document、worker-ai、worker-ragflow、worker-notification、scheduler、PostgreSQL 16、RabbitMQ、Redis、MinIO。RAGFlow、SMTP 与 LLM 是受控外部依赖。

API 无状态横向扩容；Worker 按队列独立扩容。Scheduler 保证单实例或具备分布式 leader。Dispatcher 至少一次投递，消费者幂等。

## 2. 环境分层

- `development`：允许本机 HTTP、默认非生产端口、外部 LLM 默认关闭；可为受控开发联调临时开启，但不能作为发布证据。
- `test/ci`：依赖容器真实运行；无公网；外部协议使用本地替身。
- `staging`：与生产同架构、TLS、密钥策略和 ARM64，使用隔离 Dataset/桶/数据库；`COST-002` 未定版时拒绝 `ALLOW_EXTERNAL_LLM=true` 启动。
- `production`：禁止占位密钥、默认凭据、`MINIO_SECURE=false`、开放依赖端口和未限定 RAGFlow Dataset；`COST-002` 未定版时拒绝 `ALLOW_EXTERNAL_LLM=true` 启动。

配置值不写入镜像。secret 来自部署环境/secret manager；`.env` 只用于本机且不提交。

在 `COST-002` 定版并完成对应实现与验收前，`staging`/`production` 必须显式保持
`ALLOW_EXTERNAL_LLM=false`；该启动硬门禁不能由管理员确认、数据库开关或人工豁免放宽。
已批准的内部非计费 Provider 同样使用 `ALLOW_EXTERNAL_LLM=false`；只有 `development` 可在
受控开发中临时使用 true，且不得把 mock 或开发结果提升为 protected 发布证据。

## 3. 健康、指标与告警

- `/api/system/health`：进程存活，不访问依赖。
- `/api/system/ready`：PostgreSQL、Redis、RabbitMQ、MinIO；任一核心依赖失败返回 503，错误只给类型。
- 指标：HTTP RED、DB pool、outbox oldest age/count、队列 depth、worker heartbeat、DLQ、任务状态/耗时、审核 SLA、RAGFlow/LLM 调用结果、MinIO 容量。
- 最低告警：ready 连续失败、dispatcher/worker 离线、outbox oldest age、DLQ>0、审核超 SLA、同步失败率、磁盘/桶容量、备份失败。每条告警链接 runbook。

指标 label 禁止文件名、邮箱、URL token、API Key、对象 key、prompt/原文和任意用户输入，避免隐私与高基数。

### 3.1 MinIO 指标 JWT 与私有 CA

MinIO exporter 在开发、staging、production 均必须使用 JWT；一次性
`minio-metrics-token-init` 把 bearer token 原子写入命名卷，权限固定 `0440/65534:65534`，
Prometheus 与 operational-metrics 只读消费。token 禁止进入环境、参数、日志、API 或
system_configs。上线证据必须同时证明初始化 `exited 0` 且日志为空、匿名请求 401/403、应用
collector 与 Prometheus 鉴权成功，以及服务端重新签发、消费者切换后仍恢复采集。

具体契约：

- 所有运行环境（含 test/ci）固定 `MINIO_PROMETHEUS_AUTH_TYPE=jwt`，禁止 `public`。指标端点
  只在内部网络可达；网络隔离不能替代 JWT。
- `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` 只允许注入 MinIO、`minio-bootstrap` 与 `minio-metrics-token-init`；`MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` 是独立数据面用户。`minio-bootstrap` 必须在 MinIO healthy 后幂等创建桶、用户与桶级最小对象策略；两组凭据相同、策略越权或 bootstrap 失败时，所有后端消费者 fail closed。
- `minio-metrics-token-init` 运行于后端 runtime 镜像；该镜像只从固定版本官方多架构 `minio/mc` 的 `TARGETPLATFORM` stage 复制 `mc`，Python 初始化器，只在 MinIO healthy 且 bootstrap 成功后运行，
  成功生成只读 Prometheus bearer credential 后退出。初始化失败必须 fail closed，Prometheus 与
  operational-metrics 通过 `service_completed_successfully` 拒绝带空 token 启动。
- token 文件固定为命名卷中的 `/run/secrets/minio-metrics/token`。初始化器每次向 MinIO 服务端重新签发，使用 `mktemp` 在同一命名卷创建跨 PID namespace 唯一临时文件；校验恰好一个 LF、无 CR/NUL/尾随字节、有限大小、三个非空 segment、非 `none` 算法与有效身份 claim 后再原子替换。最终 owner/group 为 `65534:65534`、mode 为 `0440`。初始化器不使用共享永久锁；TERM/HUP/INT 必须清理本次临时文件，SIGKILL 后遗留的唯一临时文件不得阻止下一次签发。初始化器对卷可写，所有消费者只能 `:ro` 挂载。
- 应用只接受 `MINIO_METRICS_BEARER_TOKEN_FILE` 文件路径；Prometheus 只用
  `authorization.credentials_file`。禁止提供 `MINIO_METRICS_BEARER_TOKEN` 明文环境变量，也禁止
  把 token 放入 Compose command、resolved config、容器日志、审计、phase report、指标 label 或
  system config。初始化和刷新脚本的 stdout/stderr 都不得包含 token。
- 常规刷新只重新运行一次性初始化器，不重启 Prometheus 与 operational-metrics；验收必须确认文件确已替换、匿名请求仍为 401/403、两个消费者恢复采集且所有命令/日志无 JWT。常规刷新不会吊销旧 JWT，旧 JWT 在其 `exp` 前仍可通过鉴权。紧急吊销必须在维护窗轮换 `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD`、重启 MinIO、重新运行 bootstrap 与 token init，且保持消费者原进程不变，并证明轮换前 JWT 为 403、轮换后 JWT 为 200。证据只记 HTTP 状态、时间与非敏感状态，不归档 token、摘要或内容。

staging/production 的 MinIO 必须启用 TLS，protected overlay 固定从 `MINIO_TLS_DIR` 读取 `public.crt`、`private.key`、`ca.crt`；服务端 `public.crt` 的 SAN 必须包含 `DNS:minio`，并提供完整证书链。使用企业私有 CA 时：

- backend/worker/operational-metrics 只读挂载 CA PEM，并令 `MINIO_CA_CERT_FILE` 指向该文件；
  `MINIO_SECURE=true`，缺 CA 或校验失败直接拒绝启动/采集。
- token 初始化器只读挂载同一 CA，并同时通过 `MINIO_CA_CERT_FILE` 与 `SSL_CERT_FILE` 交给 Python HTTPS 校验和 `mc`；Prometheus 用只读 CA 文件配置
  `tls_config.ca_file`、正确 `server_name` 和 `insecure_skip_verify=false`。
- MinIO 服务端私钥只能存在于 MinIO 的受控 secret mount，绝不挂入任一客户端、初始化器、
  Prometheus 或证据目录。禁止 `--insecure`、`insecure_skip_verify=true`、静默回退 HTTP，或把
  私有 CA 校验失败解释为容量暂时不可用后继续发布。

## 4. DLQ 与恢复

RabbitMQ 为每个业务队列配置 dead-letter exchange/queue。事件记录 attempt、首次/末次错误类型和 correlation id；达到上限后停止自动重试并告警。重放 API/脚本必须要求系统管理员、原因、幂等检查并写审计，不能直接复制未知 payload 到生产队列。

状态卡死的恢复先核对数据库聚合、outbox、任务和远端 id，再选择重投/补偿；禁止直接 SQL 改文件状态。

## 5. 备份与灾难恢复

- PostgreSQL：每日全量 + 持续 WAL/PITR（或等价托管能力）；备份加密、异地、保留策略明确。
- MinIO：启用 versioning/复制或一致快照；对象恢复点必须能与数据库恢复点配对。
- 配置/密钥：备份密文与密钥版本，密钥材料单独受控；恢复演练验证可解密但不输出明文。
- RabbitMQ/Redis 不作为唯一业务事实；灾后由 PostgreSQL outbox/任务状态恢复，验证幂等。

每季度在隔离环境做恢复演练，记录 RPO、RTO、行数、对象存在性、关键 hash、迁移 revision 和主链 smoke。只有“备份成功”而无恢复证明视为未通过。

## 6. ARM64 生产

目标为 DGX Spark Linux ARM64。CI buildx `linux/arm64` 是预检，不是实机验收。发布前在目标设备运行镜像并执行：compose 启动、Alembic upgrade、health/ready、上传/审核/同步协议替身 E2E、worker 重启恢复和备份 smoke。

Dockerfile 使用官方多架构 base；构建原生 Python/Node 依赖的 stage 必须针对 `TARGETPLATFORM`，不能在 `$BUILDPLATFORM` 安装后跨架构复制二进制。新增 Python 依赖先 `invoke check-arm64`。

## 7. 发布步骤

1. 冻结候选 commit，生成 SBOM/镜像 digest，执行 secret 与依赖扫描。
2. 在 staging 从上一版本备份恢复，执行 Alembic upgrade 与完整真实基础设施 E2E。
3. DGX Spark 实机运行 ARM64 门禁；确认指标、DLQ 与告警。
4. 备份生产，滚动部署 API/Worker；迁移按兼容顺序执行。
5. 运行 smoke 与关键主链，观察 outbox/队列/错误率；达到阈值立即回滚应用或执行已验证的前向修复。
6. 将所有证据归档到 phase report，再更新验收矩阵。

## 8. 常用验证

```powershell
invoke check
invoke ship
invoke check-arm64
docker compose config --quiet
docker compose up -d --build
docker compose exec backend-api alembic upgrade head
curl http://localhost:18000/api/system/health
curl http://localhost:18000/api/system/ready
```

具体命令通过不等于发布完成；以验收矩阵的证据和最低发布判定为准。
