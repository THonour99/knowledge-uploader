# 08. 开发任务拆解

## 阶段 0：项目初始化

- 初始化 monorepo 目录
- 创建 backend FastAPI 项目
- 创建 frontend React 项目
- 创建 docker-compose.yml
- 接入 PostgreSQL、RabbitMQ、Redis、MinIO
- 配置 Alembic
- 配置基础日志
- 配置统一异常处理

验收：

- `docker compose up` 可以启动基础服务
- 后端 `/api/system/health` 返回正常
- 前端可以访问登录页

---

## 阶段 1：认证与用户

任务：

- users 表
- email_verification_tokens 表
- password_reset_tokens 表
- 注册接口
- 公司邮箱域名限制
- 邮箱验证
- 登录接口
- JWT 鉴权
- 忘记密码
- 重置密码
- 用户启用/禁用
- RBAC 基础权限

验收：

- 公司邮箱可以注册
- 非公司邮箱不能注册
- 可以登录
- 可以重置密码
- disabled 用户不能登录

---

## 阶段 2：文件上传与 MinIO

任务：

- MinIO Client
- 文件上传接口
- 文件扩展名校验
- MIME 校验
- 文件大小限制
- 文件 hash
- 去重逻辑
- files 表
- 我的文件页
- 文件详情页基础信息

验收：

- 文件上传到 MinIO
- 数据库保存 object_key
- 重复文件可识别
- 员工只能查看自己的文件

---

## 阶段 3：审核与 Dataset 映射

任务：

- categories 表
- dataset_mappings 表
- 管理员文件管理页
- 审核通过
- 审核拒绝
- 修改分类
- 修改 Dataset
- 分类级 AI 开关
- 分类级审核开关

验收：

- 管理员可以审核文件
- 审核后文件状态正确
- 分类可以绑定 RAGFlow Dataset

---

## 阶段 4：任务队列

任务：

- Celery 配置
- RabbitMQ Broker
- Redis Result Backend
- sync_tasks 表
- 任务日志
- 手动重试
- 幂等控制
- worker-document
- worker-ragflow

验收：

- 审核通过后创建任务
- Worker 可以执行任务
- 失败任务可以重试
- 任务状态可查询

---

## 阶段 5：RAGFlow 集成

任务：

- RagflowClient
- 上传文档
- 触发解析
- 查询解析状态
- 保存 document_id
- 删除文档
- metadata 生成
- 同步日志

验收：

- 文件可以同步到指定 Dataset
- 可以看到 RAGFlow document_id
- 可以看到解析状态
- 失败可重试

---

## 阶段 6：AI 配置与分析

任务：

- ai_providers 表
- ai_feature_configs 表
- prompt_templates 表
- sensitive_rules 表
- OpenAI-compatible Client
- AI 总开关
- 模型供应商配置页
- 模型测试连接
- 文本抽取
- 摘要
- 自动分类
- 标签生成
- 敏感检测

验收：

- AI 关闭时不创建 AI 任务
- AI 开启时可以生成摘要、分类、标签
- 敏感风险文件进入敏感审核状态
- AI 分析失败不影响上传

---

## 阶段 7：统计分析

任务：

- 统计总览
- 用户上传统计
- 部门统计
- 分类统计
- 上传趋势
- 失败任务统计
- 统计导出
- statistics_snapshots，可选
- user_upload_statistics，可选

验收：

- 管理员能看到每个用户上传数量
- 能按时间、部门、分类筛选
- 能导出统计

---

## 阶段 8：安全与审计

任务：

- audit_logs 表
- 登录日志
- 上传日志
- 审核日志
- 配置修改日志
- 统计导出日志
- API Key 加密
- 日志脱敏
- 上传频率限制

验收：

- 管理员操作有审计记录
- API Key 不出现在日志和前端
- 普通用户不能访问管理员接口

---

## 阶段 9：联调与文档

任务：

- README
- .env.example
- API 文档
- Docker Compose 完善
- 测试用例
- 部署说明
- 常见问题

验收：

- 新开发者按 README 可以启动项目
- 主要流程有测试覆盖
- 生产部署参数清晰
