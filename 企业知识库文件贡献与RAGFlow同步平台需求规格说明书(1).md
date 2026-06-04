# 企业知识库文件贡献与 RAGFlow 同步平台需求规格说明书

版本：v1.0  
项目代号：Knowledge Uploader  
目标环境：DGX Spark / 公司内网服务器  
核心目标：让公司员工通过 Web 页面上传文档，经过整理、审核、AI 分析后，同步到 RAGFlow，持续丰富钉钉客服机器人的知识库。

---

## 1. 项目背景

当前公司已有内部客服机器人，整体架构为：

```text
钉钉机器人
  ↓
LangBot
  ↓
Dify
  ↓
RAGFlow
  ↓
企业知识库
```

目前主要问题是：RAGFlow 中的知识库内容不足，很多公司内部文档、技术资料、流程制度、FAQ、架构文档、项目资料没有及时进入知识库，导致机器人回答能力有限。

因此需要开发一个公司内部 Web 平台，让员工可以主动上传文档，由系统统一完成：

```text
上传 → 校验 → 去重 → AI 分析 → 管理员审核 → 同步 RAGFlow → 状态追踪
```

该平台不是另一个问答机器人，而是一个 **企业知识库文档贡献、治理与同步平台**。

---

## 2. 项目目标

### 2.1 核心目标

建设一个多人可访问的内部 Web 服务，支持：

- 用户注册、登录、忘记密码、重置密码
- 使用公司邮箱注册
- 多人上传文件
- 文件分类、标签、说明、可见范围管理
- 管理员审核文档
- 自动或手动同步到 RAGFlow
- 查看 RAGFlow 上传和解析状态
- 失败重试
- AI 文档分析
- 后台配置 AI 模型供应商和分析能力
- 管理员统计分析，包括每个用户上传数量、同步成功率、失败数量、分类分布等
- 后续支持钉钉登录、公司统一认证、SSO

### 2.2 不在第一阶段强制完成的能力

以下能力预留扩展，不作为 MVP 必须项：

- 钉钉登录
- LDAP / OIDC / SSO
- OCR 完整落地
- Vision 模型识别图片
- 相似文档检测
- 对象存储 MinIO / OSS / S3
- 多租户
- MCP 工具封装
- 自动从钉钉知识库批量拉取文件

---

## 3. 整体架构

推荐架构如下：

```text
公司员工 / 管理员
        ↓
Web 前端
        ↓
FastAPI 后端
        ↓
PostgreSQL / SQLite
        ↓
Redis / 任务队列
        ↓
文件存储
        ↓
AI 文档分析服务
        ↓
RAGFlow API
        ↓
RAGFlow Dataset
        ↓
Dify / LangBot / 钉钉机器人
```

### 3.1 核心模块

```text
Knowledge Uploader
  ├── 用户认证模块
  ├── 文件上传模块
  ├── 文件管理模块
  ├── 审核模块
  ├── RAGFlow 同步模块
  ├── AI 文档分析模块
  ├── Dataset 映射模块
  ├── 后台配置模块
  ├── 任务队列模块
  ├── 日志审计模块
  └── 系统监控模块
```

### 3.2 DGX Spark 的定位

DGX Spark 或公司内部服务器主要用于部署本系统。

该系统本身不强依赖 GPU。基础能力包括上传、审核、同步、管理，CPU 服务器即可运行。

如果后续启用本地模型、OCR、Embedding、敏感信息检测、文档摘要等 AI 能力，可以利用 DGX Spark 部署本地模型服务，例如：

```text
vLLM / Ollama / LM Studio / 内部 OpenAI-compatible 服务
```

---

## 4. 用户角色与权限

系统至少包含三类用户。

### 4.1 普通员工 employee

普通员工可以：

- 注册账号
- 登录系统
- 上传文件
- 查看自己上传的文件
- 查看自己的文件同步状态
- 编辑自己上传文件的标题、分类、说明、标签
- 查看部分 AI 分析结果
- 申请删除文件
- 手动重新提交审核

普通员工不能：

- 查看其他人的私有文件
- 修改 RAGFlow Dataset 配置
- 修改 AI 配置
- 删除已经进入公共知识库的文件
- 查看敏感信息检测的详细命中内容
- 修改用户角色

### 4.2 知识库管理员 knowledge_admin

知识库管理员可以：

- 查看所有用户上传的文件
- 审核文件
- 修改文件分类、标签、目标 Dataset
- 手动触发 RAGFlow 同步
- 重试失败任务
- 禁用文件
- 删除 RAGFlow 中对应文档
- 查看同步日志和错误详情
- 查看 AI 分析结果
- 接受或修改 AI 推荐分类
- 忽略敏感风险，但必须记录审计日志

知识库管理员不能：

- 修改系统级 AI 模型供应商配置
- 查看完整 API Key
- 修改系统管理员账号

### 4.3 系统管理员 system_admin

系统管理员拥有全部权限，包括：

- 用户管理
- 角色管理
- Dataset 映射管理
- RAGFlow 配置
- AI 模型供应商配置
- AI 功能开关配置
- Prompt 模板管理
- 敏感规则配置
- 系统配置
- 任务队列状态查看
- 审计日志查看
- 存储使用情况查看
- 查看用户上传统计、部门贡献统计、知识库增长趋势、同步成功率等统计报表

---

## 5. 用户注册与认证

### 5.1 当前认证方式

MVP 阶段使用本地账号密码体系。

后续预留：

```text
local
dingtalk
ldap
oidc
oauth
sso
```

### 5.2 注册要求

用户注册时必须使用公司邮箱。

允许注册的邮箱域名通过配置控制，例如：

```env
ALLOWED_EMAIL_DOMAINS=company.com,corp.company.com
```

注册字段包括：

- 姓名
- 公司邮箱
- 密码
- 确认密码
- 部门，可选
- 手机号，可选

注册规则：

- 邮箱格式必须正确
- 邮箱域名必须在允许列表中
- 邮箱不能重复注册
- 密码至少 8 位
- 密码至少包含字母和数字
- 两次密码必须一致
- 注册用户默认角色为 employee
- 注册用户不能自动成为管理员

### 5.3 邮箱验证

系统支持邮箱验证。

流程：

```text
用户注册
  ↓
系统创建 pending_email_verification 用户
  ↓
生成邮箱验证 token
  ↓
发送验证邮件
  ↓
用户点击验证链接
  ↓
账号激活为 active
```

开发环境可以将验证链接打印在后端日志中。

生产环境必须使用 SMTP 或企业邮件服务。

相关配置：

```env
REQUIRE_EMAIL_VERIFICATION=true
SMTP_HOST=
SMTP_PORT=
SMTP_USER=
SMTP_PASSWORD=
SMTP_FROM=
SMTP_TLS=true
EMAIL_VERIFICATION_EXPIRE_HOURS=24
```

### 5.4 忘记密码

忘记密码流程：

```text
用户输入邮箱
  ↓
系统生成 reset token
  ↓
发送重置密码邮件
  ↓
用户点击链接
  ↓
输入新密码
  ↓
系统更新密码
  ↓
token 失效
```

安全要求：

- 无论邮箱是否存在，都返回统一提示
- 不泄露用户是否注册
- reset token 只能使用一次
- reset token 入库前必须 hash
- token 默认 30 分钟过期
- 重置成功后记录审计日志

### 5.5 登录安全

登录要求：

- 使用邮箱 + 密码登录
- 密码使用 bcrypt 或 argon2 哈希保存
- 登录成功返回 JWT
- 被禁用用户不能登录
- 未验证邮箱用户不能登录，或只能进入验证提示页
- 连续登录失败超过限制后锁定账号

相关配置：

```env
JWT_SECRET=
JWT_EXPIRE_MINUTES=1440
LOGIN_MAX_FAILED_ATTEMPTS=5
LOGIN_LOCK_MINUTES=15
PASSWORD_RESET_EXPIRE_MINUTES=30
```

---

## 6. 文件上传功能

### 6.1 上传入口

系统提供 Web 上传页面，支持：

- 拖拽上传
- 点击选择文件
- 多文件批量上传
- 上传进度条
- 上传成功/失败提示
- 上传后填写文档信息
- 上传后自动进入审核或同步流程

### 6.2 支持文件格式

MVP 支持：

```text
PDF
DOC
DOCX
XLS
XLSX
PPT
PPTX
TXT
MD
CSV
JSON
HTML
```

可选支持：

```text
JPG
JPEG
PNG
```

图片主要用于后续 OCR 或视觉模型分析。

### 6.3 上传时填写的信息

用户上传时需要填写或选择：

- 文档标题
- 文档分类
- 目标知识库
- 目标 RAGFlow Dataset
- 文档说明
- 标签
- 可见范围
- 是否立即同步
- 是否需要管理员审核

可见范围包括：

```text
public      公开
department  部门可见
private     私有
```

### 6.4 文件校验

上传后系统需要校验：

- 文件大小
- 文件扩展名
- MIME 类型
- 是否空文件
- 文件名是否合法
- 文件名是否过长
- 是否重复文件
- 上传用户是否有权限
- 分类是否允许普通用户选择

### 6.5 文件去重

系统计算文件 SHA256 hash。

如果发现重复文件：

- 提示用户已有相同文件
- 允许管理员决定是否保留新版本
- 避免重复同步到 RAGFlow
- 支持后续版本管理

---

## 7. 文件状态设计

文件主状态：

```text
uploaded                 已上传
extracting_text           文本抽取中
analysis_queued           等待 AI 分析
analyzing                 AI 分析中
analysis_failed           AI 分析失败
analyzed                  AI 分析完成
pending_review            待审核
sensitive_review_required 敏感信息需审核
approved                  已审核
rejected                  已拒绝
queued                    等待同步
syncing                   同步中
uploaded_to_ragflow       已上传到 RAGFlow
parsing                   RAGFlow 解析中
parsed                    解析完成
failed                    失败
disabled                  已禁用
deleted                   已删除
```

基础流程：

```text
uploaded
  ↓
extracting_text
  ↓
analyzing
  ↓
pending_review
  ↓
approved
  ↓
queued
  ↓
syncing
  ↓
uploaded_to_ragflow
  ↓
parsing
  ↓
parsed
```

当 AI 关闭时：

```text
uploaded
  ↓
pending_review
  ↓
approved
  ↓
syncing
  ↓
parsed
```

---

## 8. RAGFlow 集成

### 8.1 RAGFlow Client

后端需要封装 RAGFlow Client，不能在业务代码中直接散落请求逻辑。

RAGFlow Client 至少支持：

- 上传文件到指定 Dataset
- 触发文档解析
- 查询文档解析状态
- 删除文档
- 更新文档 metadata
- 批量上传
- 失败重试
- 超时处理
- 错误日志
- API Key 脱敏

### 8.2 RAGFlow 配置

配置来源：

- 环境变量
- 系统后台配置

配置项：

```env
RAGFLOW_BASE_URL=
RAGFLOW_API_KEY=
DEFAULT_DATASET_ID=
RAGFLOW_REQUEST_TIMEOUT=300
RAGFLOW_MAX_RETRY_COUNT=3
```

### 8.3 Dataset 映射

系统支持将不同类型文档同步到不同 Dataset。

默认分类建议：

```text
技术支持知识库
  ├── 技术文档
  ├── 架构文档
  ├── 接口文档
  ├── 部署文档
  ├── 故障排查
  └── 运维手册

人事行政知识库
  ├── 考勤制度
  ├── 报销流程
  ├── 员工手册
  ├── 行政制度
  └── 入职离职流程

公司通用知识库
  ├── 公司介绍
  ├── 产品介绍
  ├── 常见问题
  ├── 组织流程
  └── 通用规范

项目知识库
  ├── 项目文档
  ├── 客户资料
  ├── 需求文档
  ├── 会议纪要
  └── 交付资料

临时审核知识库
  ├── 未确认分类
  ├── 待审核文件
  └── 低质量文档
```

### 8.4 同步流程

```text
管理员审核通过
  ↓
创建同步任务
  ↓
Worker 获取任务
  ↓
读取文件
  ↓
调用 RAGFlow 上传接口
  ↓
保存 ragflow_document_id
  ↓
触发 RAGFlow 解析
  ↓
更新状态为 parsing
  ↓
轮询解析状态
  ↓
成功后状态变为 parsed
```

### 8.5 Metadata

上传到 RAGFlow 时建议写入 metadata：

```json
{
  "source": "knowledge_uploader",
  "file_id": "本系统文件ID",
  "uploader": "上传人",
  "department": "部门",
  "category": "分类",
  "tags": ["标签1", "标签2"],
  "visibility": "public",
  "summary": "AI摘要",
  "version": "版本号",
  "uploaded_at": "上传时间"
}
```

---

## 9. AI 文档分析能力

AI 能力是增强模块，不是基础流程强依赖。

要求：

```text
AI_ANALYSIS_ENABLED=false 时：
上传、审核、同步 RAGFlow 仍然可以正常工作。
```

### 9.1 AI 能力范围

支持或预留以下能力：

- 文档内容摘要
- 自动分类
- 自动生成标签
- 敏感信息检测
- OCR
- 表格结构识别
- 文档质量评分
- 文档过期提醒
- 相似文档检测

### 9.2 推荐优先级

MVP 阶段优先实现：

```text
文档摘要
自动分类
自动标签
敏感信息检测
```

二期实现：

```text
质量评分
过期提醒
表格结构识别
```

三期实现：

```text
OCR
相似文档检测
Vision 模型分析
```

### 9.3 AI 分析流程

```text
文件上传
  ↓
文本抽取
  ↓
AI 分析任务入队
  ↓
摘要生成
  ↓
分类推荐
  ↓
标签生成
  ↓
敏感信息检测
  ↓
质量评分
  ↓
生成分析结果
  ↓
管理员审核确认
```

### 9.4 AI 只做建议

AI 分析结果不能直接决定最终入库。

正确方式：

```text
AI 推荐分类
AI 推荐标签
AI 发现敏感风险
AI 给出质量评分
        ↓
管理员确认
        ↓
同步 RAGFlow
```

### 9.5 文本抽取

不同文件处理方式：

```text
txt / md / json / html
  → 直接读取文本

pdf
  → 提取文本
  → 扫描版 PDF 后续走 OCR

doc / docx
  → 提取标题和正文

xls / xlsx / csv
  → 提取 sheet 名、表头、样例行

ppt / pptx
  → 提取每页标题和正文

图片
  → 后续走 OCR 或 Vision 模型
```

大文件需要截断或分段摘要，避免一次性把全文发给模型。

---

## 10. AI 后台配置

AI 能力必须由管理员后台配置，不能写死在代码中。

### 10.1 全局开关

后台配置项：

```env
AI_ANALYSIS_ENABLED=true
ALLOW_EXTERNAL_LLM=false
ENABLE_SUMMARY=true
ENABLE_AUTO_CATEGORY=true
ENABLE_AUTO_TAGS=true
ENABLE_SENSITIVE_DETECTION=true
ENABLE_OCR=false
ENABLE_TABLE_ANALYSIS=false
ENABLE_QUALITY_SCORE=true
ENABLE_EXPIRE_DETECTION=true
ENABLE_SIMILARITY_DETECTION=false
ALLOW_SYNC_WHEN_ANALYSIS_FAILED=true
```

### 10.2 模型供应商配置

系统支持多个模型供应商。

供应商类型：

```text
openai_compatible
local_openai_compatible
ollama
vllm
lmstudio
custom
disabled
```

供应商配置字段：

- 供应商名称
- 供应商类型
- Base URL
- API Key
- Chat 模型名称
- Embedding 模型名称
- Vision 模型名称
- 是否内网模型
- 是否启用
- 优先级
- 超时时间
- 最大重试次数
- 最大输入 token
- 最大输出 token
- temperature
- top_p

API Key 要求：

- 加密保存
- 前端不可完整回显
- 日志不可打印
- 只能显示脱敏形式，例如：`sk-****abcd`

### 10.3 模型连接测试

后台需要支持：

- Chat 模型测试
- Embedding 模型测试
- Vision 模型测试，可选
- RAGFlow 连接测试

测试接口不能泄露密钥。

### 10.4 AI 任务配置

管理员可以配置每个 AI 任务。

#### 文档摘要

- 是否启用
- 使用哪个模型供应商
- 摘要长度
- 最大输入字符数
- 是否分段摘要
- 是否写入 RAGFlow metadata

#### 自动分类

- 是否启用
- 使用哪个模型供应商
- 候选分类范围
- 置信度阈值
- 低置信度是否进入人工分类
- 是否允许 AI 自动推荐 Dataset

#### 自动标签

- 是否启用
- 标签数量范围
- 是否允许创建新标签
- 是否只从已有标签中选择
- 是否写入 RAGFlow metadata

#### 敏感信息检测

- 是否启用
- 是否启用规则检测
- 是否启用 LLM 检测
- 高风险是否阻止同步
- 中风险是否进入人工审核
- 管理员是否允许忽略风险

#### 质量评分

- 是否启用
- 最低质量分阈值
- 低于阈值是否禁止自动同步
- 低于阈值是否必须审核

#### 相似文档检测

- 是否启用
- 使用哪个 embedding 模型
- 相似度阈值
- 是否只在同分类下比较
- 是否允许覆盖旧文档
- 是否允许保留多个版本

---

## 11. Prompt 模板配置

后台需要支持 Prompt 模板管理。

模板类型包括：

- 摘要 Prompt
- 分类 Prompt
- 标签生成 Prompt
- 敏感检测 Prompt
- 质量评分 Prompt
- 过期检测 Prompt
- 表格分析 Prompt

模板变量：

```text
{{file_name}}
{{file_type}}
{{category_candidates}}
{{document_text}}
{{existing_tags}}
{{department}}
{{uploader_name}}
```

要求：

- 系统提供默认模板
- 管理员可以编辑模板
- 模板有版本号
- 支持恢复默认模板
- 支持测试 Prompt
- Prompt 修改记录审计日志

---

## 12. 敏感信息检测

### 12.1 检测方式

敏感信息检测采用混合方案：

```text
规则 / 正则检测
  +
LLM 辅助判断
```

优先本地规则检测，避免把敏感内容直接发给外部模型。

### 12.2 检测内容

默认检测：

- 手机号
- 身份证号
- 邮箱
- 内网 IP
- API Key
- Token
- 密码
- Access Key
- Secret Key
- 数据库连接串
- 客户名称
- 合同金额
- 内部系统地址

### 12.3 风险等级

```text
low      低风险
medium   中风险
high     高风险
critical 严重风险
```

处理策略：

```text
low
  → 允许继续审核

medium
  → 标记提醒管理员

high
  → 进入 sensitive_review_required

critical
  → 默认阻止同步 RAGFlow
```

---

## 13. 管理后台页面

系统至少包含以下页面。

### 13.1 登录页

- 邮箱
- 密码
- 登录按钮
- 注册入口
- 忘记密码入口

### 13.2 注册页

- 姓名
- 公司邮箱
- 密码
- 确认密码
- 部门
- 手机号
- 注册按钮
- 公司邮箱域名提示

提示文案：

```text
仅支持公司邮箱注册。
```

### 13.3 忘记密码页

流程：

```text
输入邮箱
  ↓
提示邮件已发送
  ↓
重置密码
  ↓
重置成功
```

提示文案：

```text
如果该邮箱已注册，我们会发送一封密码重置邮件。
```

### 13.4 首页 / 仪表盘

显示：

- 总上传文件数
- 已同步文件数
- 解析成功数量
- 解析失败数量
- 待审核数量
- AI 分析失败数量
- 敏感风险文件数量
- 最近上传记录
- 最近失败任务
- 今日上传数量
- 本周上传数量
- 本月上传数量
- 上传人数
- 活跃贡献用户数
- RAGFlow 同步成功率


### 13.5 文件上传页

包括：

- 拖拽上传区域
- 文件列表
- 上传进度
- 分类选择
- 标签输入
- 可见范围选择
- 目标 Dataset 选择
- 是否立即同步
- 提交按钮

### 13.6 我的文件页

普通员工查看自己的文件：

- 文件名
- 分类
- 上传时间
- 审核状态
- 同步状态
- RAGFlow 解析状态
- 操作：查看详情、重新提交、申请删除

### 13.7 文件管理页

管理员可见：

- 查看全部文件
- 按上传人、分类、状态、时间筛选
- 审核文件
- 修改分类
- 修改 Dataset
- 手动同步
- 失败重试
- 禁用文件
- 删除文件

### 13.8 文件详情页

展示：

- 文件基本信息
- 上传人
- 文件 hash
- 文件大小
- 文件路径
- 分类
- 标签
- RAGFlow dataset_id
- RAGFlow document_id
- AI 摘要
- AI 推荐分类
- AI 推荐标签
- 敏感风险
- 质量评分
- 相似文档
- 同步日志
- 错误详情
- 操作历史

### 13.9 Dataset 配置页

系统管理员可见：

- 新增分类
- 修改分类
- 绑定 RAGFlow Dataset
- 设置是否需要审核
- 设置默认可见范围
- 是否允许普通用户选择
- 是否允许 AI 推荐

### 13.10 AI 能力配置页

系统管理员可见：

- AI 总开关
- 摘要开关
- 自动分类开关
- 自动标签开关
- 敏感检测开关
- OCR 开关
- 质量评分开关
- 相似检测开关
- 外部模型调用开关
- 分析失败处理策略

### 13.11 模型供应商配置页

系统管理员可见：

- 供应商列表
- 新增供应商
- 编辑供应商
- 禁用供应商
- 测试连接
- 设置默认供应商

### 13.12 Prompt 模板配置页

系统管理员可见：

- 查看模板
- 编辑模板
- 版本记录
- 恢复默认模板
- 测试 Prompt

### 13.13 敏感规则配置页

系统管理员可见：

- 新增正则规则
- 修改规则
- 禁用规则
- 设置风险等级
- 测试规则

### 13.14 AI 分析日志页

展示：

- 文件名
- 分析任务类型
- 使用模型
- token 消耗
- 状态
- 错误信息
- 开始时间
- 结束时间
- 重新分析按钮

### 13.15 统计分析页

管理员后台需要增加“统计分析”页面，用于查看公司知识库建设情况和员工文档贡献情况。

系统管理员和知识库管理员可以查看统计分析页。

普通员工只能查看自己的上传统计，不能查看全员排行和其他人的详细数据。

统计分析页至少包括以下内容：

#### 13.15.1 总览统计

展示：

- 总上传文件数
- 总上传用户数
- 总文件大小
- 已同步到 RAGFlow 文件数
- RAGFlow 解析成功数
- RAGFlow 解析失败数
- 待审核文件数
- 敏感风险文件数
- AI 分析完成文件数
- AI 分析失败文件数
- 平均审核耗时
- 平均同步耗时
- 同步成功率
- 解析成功率

#### 13.15.2 用户上传统计

管理员可以查看每个用户的上传贡献情况。

字段包括：

- 用户姓名
- 邮箱
- 部门
- 角色
- 上传文件总数
- 已审核通过数量
- 已同步成功数量
- 同步失败数量
- 待审核数量
- 被拒绝数量
- 敏感风险文件数量
- 总上传文件大小
- 最近上传时间
- 最近登录时间

支持排序：

- 按上传数量排序
- 按同步成功数量排序
- 按失败数量排序
- 按最近上传时间排序
- 按文件总大小排序

支持筛选：

- 时间范围
- 部门
- 用户
- 文件分类
- 同步状态
- 审核状态
- 是否包含敏感风险

#### 13.15.3 部门统计

展示每个部门的知识贡献情况。

字段包括：

- 部门名称
- 上传用户数
- 上传文件数
- 已同步文件数
- 待审核文件数
- 失败文件数
- 总文件大小
- 主要文档分类分布
- 最近上传时间

#### 13.15.4 分类统计

展示不同知识分类的文档增长情况。

字段包括：

- 分类名称
- 文件总数
- 已同步数量
- 待审核数量
- 失败数量
- 占比
- 最近更新时间

#### 13.15.5 时间趋势

需要支持按时间查看趋势：

- 每日上传趋势
- 每周上传趋势
- 每月上传趋势
- 每日同步成功趋势
- 每日失败趋势
- 每日审核数量趋势

可视化建议：

- 折线图：上传趋势
- 柱状图：用户上传排行
- 饼图 / 环形图：分类分布
- 堆叠柱状图：部门上传分布
- 表格：用户明细统计

#### 13.15.6 失败统计

展示系统中失败任务的统计信息：

- RAGFlow 上传失败数量
- RAGFlow 解析失败数量
- AI 分析失败数量
- 文件抽取失败数量
- 最常见失败原因
- 失败文件列表
- 可重试文件数量

管理员可以从失败统计中跳转到对应文件详情页，并手动重试。

#### 13.15.7 导出统计

管理员可以导出统计数据。

支持导出：

- 用户上传统计 CSV / Excel
- 部门统计 CSV / Excel
- 分类统计 CSV / Excel
- 失败任务统计 CSV / Excel

导出行为需要记录审计日志。

---

## 14. 后端 API 设计

### 14.1 认证 API

```http
POST /api/auth/register
POST /api/auth/login
POST /api/auth/logout
GET  /api/auth/me
POST /api/auth/verify-email
POST /api/auth/resend-verification
POST /api/auth/forgot-password
POST /api/auth/reset-password
POST /api/auth/change-password
```

### 14.2 用户管理 API

```http
GET   /api/users
GET   /api/users/{id}
PATCH /api/users/{id}
POST  /api/users/{id}/disable
POST  /api/users/{id}/enable
```

### 14.3 文件 API

```http
POST   /api/files/upload
GET    /api/files
GET    /api/files/{id}
PATCH  /api/files/{id}
DELETE /api/files/{id}
POST   /api/files/{id}/submit-review
POST   /api/files/{id}/approve
POST   /api/files/{id}/reject
POST   /api/files/{id}/sync
POST   /api/files/{id}/retry
POST   /api/files/{id}/disable
POST   /api/files/{id}/reanalyze
```

### 14.4 任务 API

```http
GET  /api/tasks
GET  /api/tasks/{id}
POST /api/tasks/{id}/retry
POST /api/tasks/{id}/cancel
```

### 14.5 Dataset API

```http
GET    /api/datasets
POST   /api/datasets
PATCH  /api/datasets/{id}
DELETE /api/datasets/{id}
```

### 14.6 AI 配置 API

```http
GET   /api/admin/ai/config
PATCH /api/admin/ai/config

GET    /api/admin/ai/providers
POST   /api/admin/ai/providers
GET    /api/admin/ai/providers/{id}
PATCH  /api/admin/ai/providers/{id}
DELETE /api/admin/ai/providers/{id}
POST   /api/admin/ai/providers/{id}/test

GET   /api/admin/ai/features
PATCH /api/admin/ai/features/{feature_name}

GET   /api/admin/ai/prompts
POST  /api/admin/ai/prompts
GET   /api/admin/ai/prompts/{id}
PATCH /api/admin/ai/prompts/{id}
POST  /api/admin/ai/prompts/{id}/test
POST  /api/admin/ai/prompts/{id}/restore-default

GET    /api/admin/ai/sensitive-rules
POST   /api/admin/ai/sensitive-rules
PATCH  /api/admin/ai/sensitive-rules/{id}
DELETE /api/admin/ai/sensitive-rules/{id}

GET /api/admin/ai/usage-logs
GET /api/admin/ai/analysis-logs
```

### 14.7 系统 API

```http
GET   /api/dashboard/stats
GET   /api/system/config
PATCH /api/system/config
GET   /api/system/health
```

### 14.8 统计分析 API

```http
GET /api/admin/statistics/overview
GET /api/admin/statistics/users
GET /api/admin/statistics/users/{user_id}
GET /api/admin/statistics/departments
GET /api/admin/statistics/categories
GET /api/admin/statistics/trends
GET /api/admin/statistics/failures
GET /api/admin/statistics/export
```

接口说明：

- `/api/admin/statistics/overview`：获取全局统计总览
- `/api/admin/statistics/users`：获取每个用户上传数量、同步成功数量、失败数量等
- `/api/admin/statistics/users/{user_id}`：获取指定用户的详细上传统计
- `/api/admin/statistics/departments`：获取部门维度统计
- `/api/admin/statistics/categories`：获取分类维度统计
- `/api/admin/statistics/trends`：获取上传、审核、同步的时间趋势
- `/api/admin/statistics/failures`：获取失败任务统计
- `/api/admin/statistics/export`：导出统计数据

统计 API 需要支持查询参数：

```text
start_date
end_date
department
user_id
category_id
status
review_status
sync_status
group_by=day/week/month
page
page_size
sort_by
sort_order
```

权限要求：

- system_admin 可以查看全部统计
- knowledge_admin 可以查看文件上传、审核、同步相关统计
- employee 不能访问 `/api/admin/statistics/*`
- employee 只能通过 `/api/files` 或个人中心查看自己的统计概览

---

## 15. 数据库设计

### 15.1 users

```text
id
name
email
email_domain
password_hash
department
phone
role
status
email_verified
auth_provider
external_user_id
ding_user_id
employee_no
failed_login_count
locked_until
last_login_at
last_login_ip
created_at
updated_at
```

### 15.2 email_verification_tokens

```text
id
user_id
token_hash
expires_at
used_at
created_at
```

### 15.3 password_reset_tokens

```text
id
user_id
token_hash
expires_at
used_at
created_at
```

### 15.4 files

```text
id
original_name
stored_name
extension
mime_type
size
hash
storage_type
storage_path
uploader_id
department
category_id
dataset_mapping_id
visibility
description
tags
status
review_status
ragflow_dataset_id
ragflow_document_id
ragflow_parse_status
ragflow_error_message
uploaded_at
last_sync_at
created_at
updated_at
```

### 15.5 categories

```text
id
name
code
description
parent_id
require_review
default_dataset_id
allow_employee_select
allow_ai_recommend
default_visibility
keywords
classification_prompt
created_at
updated_at
```

### 15.6 dataset_mappings

```text
id
name
category_id
ragflow_dataset_id
ragflow_dataset_name
enabled
created_at
updated_at
```

### 15.7 sync_tasks

```text
id
file_id
task_type
status
retry_count
max_retry_count
error_message
started_at
finished_at
created_at
updated_at
```

### 15.8 sync_logs

```text
id
file_id
task_id
level
message
detail
created_at
```

### 15.9 document_analysis

```text
id
file_id
summary
suggested_category_id
suggested_dataset_id
suggested_tags
sensitive_level
sensitive_items
quality_score
quality_reasons
detected_expire_at
detected_version
similar_files
model_provider
model_name
token_usage
status
error_message
created_at
updated_at
```

### 15.10 ai_providers

```text
id
name
provider_type
base_url
api_key_encrypted
chat_model
embedding_model
vision_model
is_internal
enabled
priority
timeout_seconds
max_retry_count
max_input_tokens
max_output_tokens
temperature
top_p
created_at
updated_at
```

### 15.11 ai_feature_configs

```text
id
feature_name
enabled
provider_id
model_name
config_json
created_at
updated_at
```

### 15.12 prompt_templates

```text
id
name
code
feature_name
template_content
version
enabled
created_by
created_at
updated_at
```

### 15.13 sensitive_rules

```text
id
name
rule_type
pattern
risk_level
enabled
description
created_at
updated_at
```

### 15.14 ai_usage_logs

```text
id
provider_id
model_name
feature_name
file_id
task_id
user_id
prompt_tokens
completion_tokens
total_tokens
estimated_cost
status
error_message
created_at
```

### 15.15 audit_logs

```text
id
user_id
action
target_type
target_id
detail
ip
user_agent
created_at
```

### 15.16 system_configs

```text
key
value
description
updated_at
```

### 15.17 statistics_snapshots，可选

统计数据可以实时从 users、files、sync_tasks、document_analysis 等表聚合。

如果数据量较大，可以增加统计快照表，用于提升后台统计查询性能。

```text
id
snapshot_date
snapshot_type
scope_type
scope_id
metrics_json
created_at
```

示例：

- snapshot_type：daily_uploads、monthly_uploads、user_contribution、department_contribution
- scope_type：global、user、department、category
- scope_id：对应用户 ID、部门名称、分类 ID，global 时为空
- metrics_json：保存上传数量、成功数量、失败数量、文件大小等聚合指标

### 15.18 user_upload_statistics，可选

如果需要经常查看用户排行，可以增加用户上传统计缓存表。

```text
id
user_id
department
total_files
approved_files
synced_files
failed_files
pending_review_files
rejected_files
sensitive_files
total_file_size
last_upload_at
last_success_sync_at
updated_at
```

说明：

- MVP 阶段可以不建该表，直接 SQL 聚合查询。
- 当文件量较大或统计查询变慢时，再通过定时任务生成统计缓存。

---

## 16. 异步任务队列

上传、AI 分析、RAGFlow 同步都不能阻塞 Web 请求。

推荐使用：

```text
Celery + Redis
```

MVP 可以使用：

```text
FastAPI BackgroundTasks
```

但项目结构必须预留切换到 Celery/RQ 的能力。

### 16.1 任务类型

```text
extract_text
ai_analyze
ragflow_upload
ragflow_parse
ragflow_status_check
sensitive_scan
similarity_check
statistics_snapshot
```

### 16.2 任务要求

- 支持并发执行
- 支持失败重试
- 支持最大重试次数
- 支持手动重试
- 支持任务状态查询
- 避免重复任务
- 任务失败记录错误日志
- 同一个文件不能同时存在多个同步任务
- 状态更新需要防止并发覆盖

---

## 17. 存储设计

MVP 阶段使用本地文件系统。

建议目录：

```text
/data/knowledge-upload/original
/data/knowledge-upload/temp
/data/knowledge-upload/archive
```

文件存储要求：

- 文件名重命名
- 保留原始文件名
- 按日期或 hash 分目录
- 防止路径穿越
- 防止文件覆盖
- 上传完成后计算 hash
- 临时文件定期清理
- 生产环境预留对象存储接口

后续支持：

```text
MinIO
S3
阿里云 OSS
内部文件服务器
```

---

## 18. 安全要求

### 18.1 基础安全

- 登录鉴权
- RBAC 权限控制
- 文件格式白名单
- 文件大小限制
- MIME 校验
- 文件名清洗
- 防路径穿越
- 防任意文件覆盖
- API 权限校验
- 上传频率限制
- 审计日志
- 密钥环境变量配置
- 错误信息脱敏
- 后续预留病毒扫描

### 18.2 AI 安全

- API Key 加密保存
- 日志不打印 API Key
- 不保存完整敏感原文
- 外部模型调用可关闭
- 外部模型 Base URL 可配置白名单
- 高敏文档禁止发送给外部模型
- 发送给模型前可脱敏
- 限制最大发送字符数
- 记录模型调用日志和 token 用量

### 18.3 RAGFlow 安全

- RAGFlow API Key 不写死
- API Key 不返回前端
- 调用失败日志脱敏
- 删除 RAGFlow 文档需要管理员权限
- 禁用文件后不能继续同步
- 敏感文档需要审核后才能入库

---

## 19. 系统配置

### 19.1 认证配置

```env
AUTH_PROVIDER=local
ALLOW_REGISTER=true
REQUIRE_EMAIL_VERIFICATION=true
ALLOWED_EMAIL_DOMAINS=company.com,corp.company.com
PASSWORD_MIN_LENGTH=8
LOGIN_MAX_FAILED_ATTEMPTS=5
LOGIN_LOCK_MINUTES=15
EMAIL_VERIFICATION_EXPIRE_HOURS=24
PASSWORD_RESET_EXPIRE_MINUTES=30
JWT_EXPIRE_MINUTES=1440
```

### 19.2 文件上传配置

```env
MAX_UPLOAD_SIZE_MB=100
ALLOWED_FILE_EXTENSIONS=pdf,doc,docx,xls,xlsx,ppt,pptx,txt,md,csv,json,html
STORAGE_TYPE=local
STORAGE_LOCAL_PATH=/data/knowledge-upload
```

### 19.3 RAGFlow 配置

```env
RAGFLOW_BASE_URL=
RAGFLOW_API_KEY=
DEFAULT_DATASET_ID=
RAGFLOW_REQUEST_TIMEOUT=300
RAGFLOW_MAX_RETRY_COUNT=3
```

### 19.4 AI 配置

```env
AI_ANALYSIS_ENABLED=true
ALLOW_EXTERNAL_LLM=false
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=
LLM_API_KEY=
LLM_MODEL=
EMBEDDING_PROVIDER=openai_compatible
EMBEDDING_BASE_URL=
EMBEDDING_API_KEY=
EMBEDDING_MODEL=
VISION_MODEL=
AI_REQUEST_TIMEOUT=120
AI_MAX_RETRY_COUNT=3
```

### 19.5 邮件配置

```env
SMTP_HOST=
SMTP_PORT=
SMTP_USER=
SMTP_PASSWORD=
SMTP_FROM=
SMTP_TLS=true
```

---

## 20. 推荐技术栈

### 20.1 前端

```text
React
TypeScript
Ant Design
Axios
React Router
Zustand / Redux Toolkit
```

### 20.2 后端

```text
Python
FastAPI
SQLAlchemy
Alembic
Pydantic
Celery / RQ
Redis
PostgreSQL
python-multipart
httpx / requests
```

### 20.3 部署

```text
Docker
Docker Compose
Nginx
PostgreSQL
Redis
可选 MinIO
```

### 20.4 MVP 简化版本

MVP 可以先使用：

```text
FastAPI
SQLite
本地文件系统
BackgroundTasks
React + Ant Design
```

但代码结构必须支持后续平滑升级到：

```text
PostgreSQL
Redis
Celery
MinIO
SSO
本地模型服务
```

---

## 21. 项目目录结构

建议结构：

```text
knowledge-uploader/
  backend/
    app/
      main.py
      core/
        config.py
        security.py
        permissions.py
        email.py
      db/
        session.py
        models.py
        migrations/
      schemas/
        auth.py
        user.py
        file.py
        task.py
        ai.py
        dataset.py
      api/
        auth.py
        users.py
        files.py
        tasks.py
        datasets.py
        dashboard.py
        system.py
        admin_ai.py
      services/
        storage_service.py
        ragflow_client.py
        file_service.py
        task_service.py
        review_service.py
        ai_service.py
        text_extract_service.py
        sensitive_service.py
        email_service.py
      workers/
        celery_app.py
        sync_tasks.py
        ai_tasks.py
      utils/
        hash.py
        file_validate.py
        filename.py
        crypto.py
        token.py
      tests/
    Dockerfile
    requirements.txt
    alembic.ini

  frontend/
    src/
      pages/
        Login/
        Register/
        ForgotPassword/
        Dashboard/
        Upload/
        MyFiles/
        FileManagement/
        FileDetail/
        DatasetConfig/
        AiConfig/
        AiProviders/
        PromptTemplates/
        SensitiveRules/
        Users/
      components/
      api/
      store/
      router/
      types/
      utils/
    Dockerfile
    package.json

  nginx/
  docker-compose.yml
  README.md
  .env.example
```

---

## 22. MVP 范围

第一阶段必须完成：

```text
用户注册
公司邮箱域名限制
邮箱验证，开发环境可日志输出链接
登录
忘记密码
重置密码
JWT 鉴权
用户角色
文件上传
文件列表
管理员审核
Dataset 映射
RAGFlow 上传
RAGFlow 解析触发
同步状态展示
失败重试
AI 总开关
OpenAI-compatible 模型供应商配置
模型连接测试
文档摘要
自动分类
自动标签
敏感检测
管理员查看 AI 分析结果
管理员统计分析
每个用户上传数量统计
部门上传统计
分类统计
失败任务统计
Docker Compose 部署
README
```

MVP 暂缓：

```text
钉钉登录
SSO
OCR
Vision 模型
相似文档检测
对象存储
高级成本统计
Prompt 版本回滚
MCP 工具封装
钉钉知识库自动批量同步
```

---

## 23. 开发阶段规划

### 阶段一：基础平台

目标：让系统可以注册、登录、上传文件。

内容：

- 后端项目初始化
- 前端项目初始化
- 用户注册
- 公司邮箱限制
- 登录鉴权
- 忘记密码
- 文件上传
- 文件列表
- 本地存储
- 数据库模型
- Docker Compose

### 阶段二：审核与 RAGFlow 同步

目标：让管理员可以审核文件，并同步到 RAGFlow。

内容：

- 管理员文件管理
- Dataset 映射
- RAGFlow Client
- 上传 RAGFlow
- 触发解析
- 状态记录
- 失败重试
- 同步日志

### 阶段三：AI 文档分析

目标：让 AI 帮助管理员整理文档。

内容：

- 文本抽取
- OpenAI-compatible Client
- 模型供应商配置
- 摘要
- 自动分类
- 标签生成
- 敏感检测
- AI 分析日志
- 管理员确认 AI 推荐结果

### 阶段四：后台配置完善

目标：让系统管理员可以配置所有关键能力。

内容：

- AI 功能配置
- Prompt 模板配置
- 敏感规则配置
- 系统配置
- 用户管理
- 管理员统计分析
- 用户上传排行
- 部门统计
- 分类统计
- 失败任务统计
- 权限强化
- 审计日志
- 健康检查

### 阶段五：生产增强

目标：让系统具备生产可用性。

内容：

- PostgreSQL
- Redis
- Celery
- Nginx
- MinIO
- 日志监控
- 任务队列监控
- 上传限流
- 备份策略
- 安全加固
- 后续接入钉钉登录或 SSO

---

## 24. 交付物

项目最终需要交付：

```text
1. 完整前后端代码
2. 后端 FastAPI 服务
3. 前端 React 管理界面
4. 数据库模型和迁移脚本
5. RAGFlow Client 封装
6. 文件上传与校验逻辑
7. 异步任务队列
8. 管理员审核流程
9. AI 文档分析模块
10. AI 模型供应商配置
11. Prompt 模板配置
12. 敏感规则配置
13. 管理员统计分析页面和统计 API
14. Docker Compose 部署文件
15. .env.example
16. README.md
17. API 文档
18. 本地启动说明
19. 基础测试用例
```

---

## 25. 总结

本项目的核心定位是：

```text
公司员工文档贡献入口
  +
文档治理后台
  +
AI 文档整理助手
  +
RAGFlow 同步服务
```

最终形成的完整链路是：

```text
公司邮箱注册
  ↓
员工上传文件
  ↓
系统校验与去重
  ↓
AI 摘要 / 分类 / 标签 / 敏感检测
  ↓
管理员审核确认
  ↓
同步到 RAGFlow
  ↓
RAGFlow 解析入库
  ↓
Dify / LangBot / 钉钉机器人获得更完整知识
```

设计原则：

```text
基础功能稳定优先
AI 能力可配置
外部模型可关闭
所有密钥不写死
权限和审计必须完整
RAGFlow 同步必须可追踪
管理员统计必须清晰可导出
管理员始终拥有最终确认权
```

这份规范可以直接作为项目 PRD、技术设计文档，或交给 Claude Code / Codex / Cursor / Multica 作为开发输入。
