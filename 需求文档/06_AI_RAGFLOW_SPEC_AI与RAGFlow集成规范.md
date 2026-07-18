# 06. AI 与 RAGFlow 集成规范

## 1. 共通原则

前端不直连 AI/RAGFlow。Provider Key 使用 Fernet 加密，响应与日志只给 mask。外部调用通过 adapter，带超时、有限重试、结构化错误分类和 request correlation；任务必须幂等。

## 2. AI 分析

AI 分析开关在上传时快照到文件，保证队列执行期间全局配置变化不改变既有任务语义。AI 关闭时不创建提取/分析任务，也不进入 AI 状态。

分析结果必须区分 `engine_type=rule|llm|hybrid`。规则生成的摘要/分类不能标成 LLM 结果。真正 LLM 调用需记录：provider/model、prompt template/version、开始结束时间、token 用量、估算成本、重试次数和错误类别；不记录完整原文或密钥。

LLM 输出使用严格 JSON schema 校验，失败可修复一次，之后进入 `analysis_failed`。敏感检测至少保留确定性规则兜底；LLM 只能提高风险，不能把规则命中的 `critical` 降级。

## 3. 分析完成与自动提交

- 成功发布 `FileAnalyzed`，敏感命中发布 `SensitiveDetected`；handler 按上传时 `submit_after_upload` 快照决定是否提交。
- `critical` 永远停在 `sensitive_review_required`；其他风险在策略允许时可进入 `pending_review`。
- 重试使用同一文件和新的分析 attempt；历史结果追加保存，不覆盖审计字段。

## 4. RAGFlow 决策与同步

只有 `pending_review -> approved` 的明确决定或已批准文件的显式同步操作能创建任务。`sync_decision=sync` 必须解析到一个启用、allowlist 内、管理员有权使用的 Dataset 映射；`approve_only` 不创建任务。

同步步骤：

```text
FileApproved(sync)
  -> create unique active sync_task
  -> acquire lock:sync:{file_id}
  -> read original from MinIO
  -> upload document (idempotency metadata)
  -> start parse
  -> poll with bounded backoff
  -> parsed / failed
```

上传前再次检查状态、敏感策略、映射与对象存在性。远端 id 一经取得立即持久化；重试时若已有远端 id，不重复上传，继续解析/查询。取消只允许尚未产生不可逆远端动作的阶段。

已 `parsed` 文件的显式再次同步是只读身份/终态对账，不是状态回退或重新解析。管理员必须提交
原 Dataset mapping 和原因；服务端验证已持久化的 Dataset/document ID 后创建
`ragflow_status_check`。该任务只查询同一 document ID，成功终态则成功；任何非成功漂移均
失败关闭，不读取 MinIO、不上传、不更新 metadata、不启动解析。文件必须是已完成版本切换的
稳定当前版本（`is_current_version=true`、`remote_visibility=current`；初始版本
`not_required`、替代版本 `completed`），主状态始终为 `parsed`。活跃上传/对账任务、
非当前或切换未完成、目标不一致、远端 ID 缺失、敏感策略阻断或权限/部门越界均不得创建任务。

## 5. Metadata 与版本

发送到 RAGFlow 的 metadata 至少含本地 `file_id`、版本 id、部门、分类、标签、上传人 id（非邮箱）、审核人 id、审核时间、敏感等级和内容 hash。不得发送内部对象 key、密钥、私人说明或不必要个人信息。

替代版本生效时先成功同步新版本，再按配置归档/删除旧远端文档；任一步失败都保留可恢复任务，不制造“两份均声称当前”的静默状态。

## 6. 失败、DLQ 与观测

- 错误分类：配置/权限/敏感前置失败（不重试）、网络/限流/5xx（有限重试）、协议/解析失败（按阶段处理）。
- 超过最大 attempt 的事件/任务进入可查询 DLQ，保留 payload 摘要、错误类型、首次/末次时间和安全重放入口。
- 指标：调用量、延迟、重试、成功率、各阶段积压、DLQ 数、token/成本；标签不得包含文件名、邮箱、token 或高基数原文。

## 7. 测试

单测覆盖 schema、脱敏、状态前置与重试决策；集成测试使用协议级假 RAGFlow/LLM 服务验证超时、429、5xx、畸形 JSON、部分成功和幂等。真实生产 Dataset 联调只使用批准的测试目标，禁止删除/覆盖既有知识库。
