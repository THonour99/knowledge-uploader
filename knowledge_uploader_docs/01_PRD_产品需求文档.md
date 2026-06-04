# 01. 产品需求文档 PRD

## 1. 项目背景

公司当前已有内部客服机器人，链路为：

```text
钉钉机器人 → LangBot → Dify → RAGFlow → 企业知识库
```

当前问题是 RAGFlow 内知识不足，很多技术文档、流程制度、FAQ、架构文档、项目资料没有及时进入知识库，导致机器人回答能力有限。

本项目提供一个公司内部 Web 平台，让员工可以上传文件，管理员审核后同步到 RAGFlow，从而持续丰富机器人知识库。

---

## 2. 项目目标

建设一个多人可访问的内部知识库文件贡献平台，支持：

- 用户注册、登录、忘记密码、重置密码
- 使用公司邮箱注册
- 员工上传文件
- 文件分类、标签、说明、可见范围配置
- 管理员审核
- 可选 AI 摘要、分类、标签、敏感检测
- RAGFlow 同步与解析状态追踪
- 失败重试
- 管理员统计分析
- Dataset 映射配置
- AI 模型供应商后台配置
- Prompt 模板后台配置
- 敏感规则后台配置
- 审计日志

---

## 3. 用户角色

### 3.1 普通员工 employee

可以：

- 注册和登录
- 上传文件
- 查看自己的文件
- 查看自己的同步状态
- 编辑自己上传文件的标题、分类、标签、说明
- 申请删除文件
- 查看自己的上传统计

不能：

- 查看其他人的私有文件
- 审核文件
- 修改 Dataset 映射
- 修改 AI 配置
- 直接删除已进入公共知识库的文件

### 3.2 知识库管理员 knowledge_admin

可以：

- 查看所有文件
- 审核文件
- 修改分类、标签、目标 Dataset
- 手动同步 RAGFlow
- 重试失败任务
- 禁用文件
- 删除 RAGFlow 中对应文档
- 查看同步日志
- 查看 AI 分析结果
- 查看统计分析

### 3.3 系统管理员 system_admin

拥有全部权限，包括：

- 用户管理
- 角色管理
- 系统配置
- RAGFlow 配置
- Dataset 映射配置
- AI 模型供应商配置
- Prompt 模板配置
- 敏感规则配置
- 统计分析
- 审计日志

---

## 4. 核心业务流程

### 4.1 用户注册

```text
用户填写姓名、公司邮箱、密码
  ↓
校验邮箱域名
  ↓
创建账号
  ↓
发送邮箱验证邮件
  ↓
用户验证邮箱
  ↓
账号激活
```

注册要求：

- 只能使用配置中的公司邮箱域名
- 邮箱不能重复注册
- 密码至少 8 位且包含字母和数字
- 默认角色为 employee

### 4.2 文件上传

```text
员工上传文件
  ↓
后端校验文件格式、大小、MIME
  ↓
保存到 MinIO
  ↓
计算 hash 去重
  ↓
写入数据库
  ↓
根据配置决定是否进入 AI 分析
```

### 4.3 AI 关闭时

```text
uploaded
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

### 4.4 AI 开启时

```text
uploaded
  ↓
extracting_text
  ↓
analysis_queued
  ↓
analyzing
  ↓
analyzed
  ↓
pending_review / sensitive_review_required
  ↓
approved
  ↓
queued
  ↓
syncing
  ↓
parsed
```

### 4.5 RAGFlow 同步

```text
管理员审核通过
  ↓
创建 ragflow_upload 任务
  ↓
Worker 从 MinIO 读取文件
  ↓
上传到 RAGFlow Dataset
  ↓
保存 ragflow_document_id
  ↓
触发解析
  ↓
轮询解析状态
  ↓
更新文件状态
```

---

## 5. 支持文件格式

MVP 支持：

```text
pdf, doc, docx, xls, xlsx, ppt, pptx, txt, md, csv, json, html
```

可选支持：

```text
jpg, jpeg, png
```

图片主要用于后续 OCR 或 Vision 模型分析。

---

## 6. 管理员统计功能

管理员可以查看：

- 每个用户上传文件数量
- 每个用户同步成功数量
- 每个用户失败数量
- 每个用户待审核数量
- 每个用户总上传文件大小
- 部门上传统计
- 分类上传统计
- RAGFlow 同步成功率
- RAGFlow 解析失败率
- AI 分析失败数量
- 上传趋势
- 失败任务统计

支持筛选：

- 时间范围
- 用户
- 部门
- 分类
- 状态
- 审核状态
- 同步状态

支持导出：

- 用户统计 CSV / Excel
- 部门统计 CSV / Excel
- 分类统计 CSV / Excel
- 失败任务 CSV / Excel

---

## 7. 非目标

第一阶段不做：

- 完整微服务拆分
- Kubernetes
- 多租户
- 钉钉知识库自动同步
- MCP 工具封装
- OCR 完整生产能力
- Vision 模型分析
- 相似文档检测生产落地

但架构必须预留这些扩展能力。
