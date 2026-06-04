# 03. 后端开发规范

## 1. 后端目标

后端负责：

- 用户认证与权限
- 文件上传与元数据管理
- MinIO 文件存储
- 审核流程
- AI 分析调度
- RAGFlow 同步调度
- 任务状态管理
- 统计分析
- 系统配置
- 审计日志

---

## 2. 后端技术栈

```text
FastAPI
SQLAlchemy
Alembic
Pydantic
PostgreSQL
RabbitMQ
Redis
Celery
MinIO Python SDK
httpx
passlib / argon2 / bcrypt
python-jose / PyJWT
```

---

## 3. 推荐目录结构

```text
backend/
  app/
    main.py
    core/
      config.py
      security.py
      permissions.py
      logging.py
      exceptions.py
    db/
      session.py
      base.py
      migrations/
    modules/
      auth/
      user/
      document/
      review/
      ragflow/
      ai/
      statistics/
      notification/
      config/
      audit/
    adapters/
      ragflow_client.py
      openai_compatible_client.py
      minio_client.py
      email_client.py
    workers/
      celery_app.py
      document_tasks.py
      ai_tasks.py
      ragflow_tasks.py
      statistics_tasks.py
      notification_tasks.py
    utils/
      hash.py
      filename.py
      file_validate.py
      crypto.py
      token.py
    tests/
```

---

## 4. 模块职责

### 4.1 auth

负责：

- 注册
- 公司邮箱域名限制
- 邮箱验证
- 登录
- 忘记密码
- 重置密码
- JWT 签发与校验
- 登录失败锁定

### 4.2 user

负责：

- 用户信息
- 角色管理
- 用户启用/禁用
- 用户上传统计入口

### 4.3 document

负责：

- 文件上传
- 文件校验
- hash 去重
- 文件元数据
- 文件状态流转
- 文件详情

### 4.4 review

负责：

- 审核通过
- 审核拒绝
- 敏感风险确认
- 修改分类 / 标签 / Dataset
- 审核日志

### 4.5 ragflow

负责：

- Dataset 映射
- RAGFlow 上传
- 触发解析
- 查询状态
- 删除 RAGFlow 文档
- 更新 metadata

### 4.6 ai

负责：

- AI 总开关
- 模型供应商配置
- Prompt 模板
- 文本抽取
- 摘要
- 自动分类
- 标签
- 敏感检测
- AI 日志

AI 模块必须可关闭。

### 4.7 statistics

负责：

- 用户上传统计
- 部门统计
- 分类统计
- 趋势统计
- 失败任务统计
- 统计导出

### 4.8 notification

负责：

- 邮箱验证邮件
- 忘记密码邮件
- 审核通知
- 同步失败通知

### 4.9 config

负责：

- 系统配置
- 上传限制
- AI 配置
- RAGFlow 配置
- SMTP 配置

### 4.10 audit

负责：

- 登录日志
- 上传日志
- 审核日志
- 配置变更日志
- 统计导出日志
- 管理员操作日志

---

## 5. 文件状态流转

### 5.1 AI 关闭

```text
uploaded → pending_review → approved → queued → syncing → uploaded_to_ragflow → parsing → parsed
```

### 5.2 AI 开启

```text
uploaded → extracting_text → analysis_queued → analyzing → analyzed → pending_review → approved → queued → syncing → parsed
```

### 5.3 状态规则

- AI 关闭时，不创建 AI 任务。
- AI 关闭时，不能进入 `extracting_text`、`analysis_queued`、`analyzing`、`analyzed`。
- AI 分析失败不能导致文件上传失败。
- 敏感风险高的文件进入 `sensitive_review_required`。

---

## 6. 异步任务设计

使用 Celery。

任务类型：

```text
extract_text
ai_analyze
sensitive_scan
ragflow_upload
ragflow_parse
ragflow_status_check
statistics_snapshot
send_email
```

要求：

- 任务必须幂等
- 支持重试
- 支持超时
- 支持失败日志
- 支持手动重试
- 同一文件不能重复创建多个同步任务
- PostgreSQL 记录最终状态
- Redis 记录临时结果
- RabbitMQ 承载任务消息

---

## 7. 权限规范

角色：

```text
employee
knowledge_admin
system_admin
```

权限检查必须在后端完成，不能只靠前端隐藏按钮。

---

## 8. 安全规范

- 密码使用 bcrypt 或 argon2。
- verification token / reset token 入库前必须 hash。
- API Key 必须加密保存或使用环境变量。
- 日志不可打印 API Key。
- 上传文件必须做扩展名、MIME、大小校验。
- 防止路径穿越。
- 所有管理员操作记录审计日志。
