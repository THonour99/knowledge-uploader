# 07. 部署、环境与上线门禁

> 业务配置逐项契约见 [CONFIG_CONTRACT](../docs/product/CONFIG_CONTRACT.md)，逐条发布判定见 [ACCEPTANCE_MATRIX](../docs/product/ACCEPTANCE_MATRIX.md)。

## 1. 服务拓扑

生产服务：Nginx、frontend、backend-api、outbox-dispatcher、worker-document、worker-ai、worker-ragflow、worker-notification、scheduler、PostgreSQL 16、RabbitMQ、Redis、MinIO。RAGFlow、SMTP 与 LLM 是受控外部依赖。

API 无状态横向扩容；Worker 按队列独立扩容。Scheduler 保证单实例或具备分布式 leader。Dispatcher 至少一次投递，消费者幂等。

## 2. 环境分层

- `development`：允许本机 HTTP、默认非生产端口、外部 LLM 默认关闭。
- `test/ci`：依赖容器真实运行；无公网；外部协议使用本地替身。
- `staging`：与生产同架构、TLS、密钥策略和 ARM64，使用隔离 Dataset/桶/数据库。
- `production`：禁止占位密钥、默认凭据、`MINIO_SECURE=false`、开放依赖端口和未限定 RAGFlow Dataset。

配置值不写入镜像。secret 来自部署环境/secret manager；`.env` 只用于本机且不提交。

## 3. 健康、指标与告警

- `/api/system/health`：进程存活，不访问依赖。
- `/api/system/ready`：PostgreSQL、Redis、RabbitMQ、MinIO；任一核心依赖失败返回 503，错误只给类型。
- 指标：HTTP RED、DB pool、outbox oldest age/count、队列 depth、worker heartbeat、DLQ、任务状态/耗时、审核 SLA、RAGFlow/LLM 调用结果、MinIO 容量。
- 最低告警：ready 连续失败、dispatcher/worker 离线、outbox oldest age、DLQ>0、审核超 SLA、同步失败率、磁盘/桶容量、备份失败。每条告警链接 runbook。

指标 label 禁止文件名、邮箱、URL token、API Key、对象 key、prompt/原文和任意用户输入，避免隐私与高基数。

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
