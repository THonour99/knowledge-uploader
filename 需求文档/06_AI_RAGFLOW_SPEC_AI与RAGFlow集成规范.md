# 06. AI 与 RAGFlow 集成规范

## 1. 基本原则

AI 是增强模块，不是基础流程强依赖。

当 `AI_ANALYSIS_ENABLED=false`：

- 上传、审核、同步 RAGFlow 仍然正常工作
- 不创建 AI 分析任务
- 文件不进入 AI 分析相关状态
- 管理员手动选择分类、标签和 Dataset

当 `AI_ANALYSIS_ENABLED=true`：

- 可以异步执行文本抽取、摘要、分类、标签、敏感检测
- AI 结果只作为管理员审核建议
- AI 失败不能导致文件上传失败

---

## 2. AI Provider 架构

```text
AiAnalysisService
  ↓
AIProvider Interface
  ├── OpenAICompatibleProvider
  ├── LocalOpenAIProvider
  ├── OllamaProvider
  ├── VLLMProvider
  └── DisabledProvider
```

后台可配置多个供应商：

```text
openai_compatible
local_openai_compatible
ollama
vllm
lmstudio
custom
disabled
```

---

## 3. AI 配置项

```env
AI_ANALYSIS_ENABLED=true
ALLOW_EXTERNAL_LLM=false
ENABLE_SUMMARY=true
ENABLE_AUTO_CATEGORY=true
ENABLE_AUTO_TAGS=true
ENABLE_SENSITIVE_DETECTION=true
ENABLE_OCR=false
ENABLE_TABLE_ANALYSIS=false
ENABLE_QUALITY_SCORE=false
ENABLE_EXPIRE_DETECTION=false
ENABLE_SIMILARITY_DETECTION=false
ALLOW_SYNC_WHEN_ANALYSIS_FAILED=true
AUTO_SYNC_AFTER_REVIEW=false
BLOCK_HIGH_RISK_SYNC=true
BLOCK_CRITICAL_RISK_SYNC=true
```

规则：

- `AI_ANALYSIS_ENABLED=false` 时，所有 AI 子能力不执行。
- 即使 `ENABLE_SUMMARY=true`，只要总开关关闭，摘要任务也不执行。
- 分类级配置可以覆盖或细化全局配置。
- 文件详情页必须显示 AI 是否执行、是否跳过，以及跳过原因。
- OCR、表格结构识别、质量评分、过期提醒、相似文档检测在对应功能实现前必须默认关闭。
- 自动同步只能在文件审核通过后触发；高风险和严重风险同步策略以后台配置为准。

---

## 4. AI 分析任务

优先实现：

```text
文档摘要
自动分类
自动标签
敏感检测
```

后续实现：

```text
OCR
表格结构识别
质量评分
过期提醒
相似文档检测
```

---

## 5. 敏感检测

采用混合方案：

```text
本地正则 / 规则检测 + LLM 辅助判断
```

默认检测：

- 手机号
- 身份证号
- 银行卡号
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
- 个人隐私信息

风险等级：

```text
low
medium
high
critical
```

处理策略：

- low：允许继续审核
- medium：提醒管理员
- high：进入 sensitive_review_required
- critical：默认阻止同步 RAGFlow

`critical` 是高风险的阻断子级别。产品侧展示为“严重风险”，统计时可单独计数，也可归入高风险总数。

---

## 6. RAGFlow Client

后端必须封装 RAGFlow Client。

能力：

- 上传文件到指定 Dataset
- 触发解析
- 查询解析状态
- 删除文档
- 更新 metadata
- 错误处理
- 重试
- API Key 脱敏

前端不能直接调用 RAGFlow。

---

## 7. RAGFlow 同步流程

```text
approved
  ↓
create ragflow_upload task
  ↓
worker-ragflow 从 MinIO 读取文件
  ↓
调用 RAGFlow 上传接口
  ↓
保存 ragflow_document_id
  ↓
触发 RAGFlow 解析
  ↓
状态改为 parsing
  ↓
轮询解析状态
  ↓
parsed / failed
```

---

## 8. Metadata 建议

上传 RAGFlow 时写入 metadata：

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

## 9. 幂等要求

RAGFlow 同步必须幂等：

- 同一文件不能重复创建多个 RAGFlow 文档。
- 重试任务前检查是否已有 `ragflow_document_id`。
- 如果已有 document_id，优先查询状态，而不是重新上传。
- 管理员强制重建时，需要先删除旧文档或标记旧文档禁用。

---

## 10. 参考资料

- RAGFlow HTTP API 支持 Dataset、Documents、Chunks 等操作。
- RAGFlow Python SDK 可作为后续可选接入方式。
