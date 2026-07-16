# 认证邮件投递契约

## 安全边界

- 邮箱验证和密码重置令牌在 PostgreSQL 中只保存 SHA-256 摘要。
- 领域 outbox、应用日志、审计日志和 API 响应不得包含原始令牌。
- 投递到 RabbitMQ 的收件人、主题和正文使用 `ENCRYPTION_KEY` 对整个信封做 Fernet
  加密。RabbitMQ 死信中只能出现密文，管理员安全重放接口不得重放邮件任务。
- 含认证 token 的 Celery 消息过期时间不得晚于 token 的 `expires_at`；worker 解密后还会
  二次检查，过期链接不会在停机恢复后继续投递。
- 轮换 `ENCRYPTION_KEY` 前必须排空 `notification_queue`；旧密钥销毁后，尚未消费的
  邮件信封将无法解密。

## 发布成功语义

认证接口先提交令牌摘要，再用持久化消息发布到持久化队列。为防止利用 RabbitMQ 故障枚举
账号，注册、重发验证和忘记密码接口在 publisher-confirm 失败时仍返回各自固定的通用
accepted 响应；真实账号、不存在账号、已验证或禁用账号的响应状态、正文和错误码不可区分。
失败由固定标签 `publish_failure` 持久化到 Redis 并告警，日志只记录固定 purpose 与异常
类型，不得记录邮箱、正文、token 或 broker 异常原文。指标写入失败同样不改变公共响应。
数据库中已提交的摘要不可逆，用户可重试注册、重发验证或忘记密码来签发新令牌；重复注册
一个仍待验证的账户会签发新的验证令牌，因此发布失败不会形成无法恢复的账户。
publisher confirm 在连接中断时仍可能处于“broker 已接收、调用方未收到确认”的歧义状态；
有界重试因此可能产生两封内容相同、token 相同的邮件，不能宣称 broker/SMTP exactly-once。
注册、重发验证和忘记密码的既有限流在失败重试时仍然生效；运维不得通过关闭限流来补偿
broker 或 SMTP 故障。

## SMTP 语义

SMTP 没有端到端 exactly-once 保证：服务端接受邮件后连接中断时，发送方无法可靠判断是否
已经投递。`notification.send_email` 因此覆盖全局 Celery 策略，使用 early ack，且不对
SMTP 异常自动重试，避免在结果不确定时自动发送第二封含令牌的邮件。

代价是 worker 在接收消息后、调用 SMTP 前崩溃时，邮件可能丢失；SMTP 返回错误时任务会
明确失败而不会伪装为成功，也不会进入 Rabbit DLQ。固定结果计数先持久化到 Redis，再由
operational collector 暴露给 Prometheus；任何计数和标签都不包含邮箱、正文或 token。
用户应通过“重发验证邮件”或“忘记密码”重新签发令牌。

## 上线门禁

- 生产/预发布必须配置 `SMTP_HOST` 和 `SMTP_FROM`（或有效 `SMTP_USER`）。
- 必须使用测试邮箱验证一次注册和一次密码重置，并确认 RabbitMQ 中仅存在密文信封。
- 必须验证 broker 不可用时，真实/不存在/已验证/禁用账号的公共响应不可区分，
  `publish_failure` 告警触发，恢复后重试可收到新的有效令牌。
- 邮件任务失败告警必须接入真实接收人；不能把缺失 SMTP 配置视为成功或跳过。
