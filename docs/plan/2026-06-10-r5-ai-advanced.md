# R5 修复计划：AI 高级能力与定时任务

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补齐 PRD 全部高级 AI 能力（总览缺陷 #12–#16、#20 后半）：OCR（图片白名单随本批次放开，裁决 D1）、表格结构识别、文档质量评分、相似文档检测、文档过期提醒，以及重复 / 过期文档统计指标。完成后 PRD §6.6 九项 AI 能力 100% 可用。

**Architecture:** 全部能力挂接既有 AI feature 开关机制（`ai/service.py` `_default_feature_definitions` 注册即自动同步数据库、前端 AiConfig 开关自动出现）。OCR 走 `OcrEngine` 抽象（ADR-2：默认 rapidocr-onnxruntime 离线推理，LLM vision 备选受 `allow_external_llm` 约束）；相似检测 SimHash 自实现零依赖（ADR-3）；质量评分规则启发式基线（ADR-4）；过期提醒挂既有 scheduler（Celery beat）每日扫描并经事件总线通知。所有阈值 / 上限读 R2 runtime_config。

**Tech Stack:** rapidocr-onnxruntime, pdfplumber, openpyxl, python-docx, Celery beat, SimHash（自实现）, FastAPI, Alembic, React, pytest, Vitest.

**前置依赖:** R1（解析器注册表）、R2（运行时配置）、R4（`archive_file`、统计联动）。迁移 rebase 到 R4 之后的 head。

---

### Task 1: OCR 引擎与图片白名单（§6.6.8）

**Files:**
- Modify: `backend/requirements.txt`（`rapidocr-onnxruntime` + `onnxruntime`，pinned）
- Modify: `backend/Dockerfile`（OCR 模型离线打入镜像，禁运行时下载）
- Create: `backend/app/modules/ai/ocr.py`（`OcrEngine` Protocol + `RapidOcrEngine` + `LlmVisionOcrEngine` + `MockOcrEngine`）
- Modify: `backend/app/modules/ai/parsers.py`（png/jpg/jpeg 解析器 → OCR；扫描版 PDF：pypdf 文本为空时按页转图走 OCR）
- Modify: `backend/app/modules/document/service.py`（扩展名白名单与 MIME 校验放行 png/jpg/jpeg，联动 `upload.allowed_extensions` 配置默认值更新）
- Create: `backend/app/tests/unit/test_ocr.py`、`backend/app/tests/fixtures/parsing/`（含文字图片样例）

- [ ] **Step 1: 依赖与多架构验证**

```powershell
python -m invoke check-arm64
docker compose build backend-api
```

预期：onnxruntime aarch64 wheel 可用；镜像构建通过且含模型文件（总览风险 #7，体积增量记录入验收报告）。

- [ ] **Step 2: 写失败测试（RED）**

- `ocr` feature 关闭时上传图片 → 不进 OCR、解析结果为空但不报错（占位语义）；
- 开启时文字图片提取出预期关键词；
- MockOcrEngine 供 CI 无模型环境使用（测试不依赖外网红线）；
- 图片 MIME 与扩展名交叉校验（jpg 改名 pdf 被拒）——总览风险 #2。

- [ ] **Step 3: 实现（GREEN）**

`OcrEngine.recognize(image_bytes) -> OcrResult(text, confidence)`；引擎选择读配置 `ai.ocr_engine`（rapidocr / llm_vision / mock）；LLM vision 实现复用 ai_providers 框架并受 `allow_external_llm` 约束。OCR 置信度写入分析记录（供质量评分使用）。

### Task 2: 表格结构识别（§6.6.9）

**Files:**
- Modify: `backend/app/modules/ai/service.py`（`_default_feature_definitions` 新增 `table_extraction`，约 464–520 行）
- Modify: `backend/app/modules/ai/parsers.py`（表格提取函数）
- Modify: `backend/app/modules/ai/models.py` + 迁移（`DocumentAnalysis` 增 `tables_json` JSONB 与 `table_count`）
- Create: `backend/app/tests/unit/test_table_extraction.py`

- [ ] **Step 1: 写失败测试（RED）**

- xlsx（openpyxl）/ docx（python-docx tables）/ PDF（pdfplumber `extract_tables`）各提取出表头 + 行数据并转 Markdown 文本化结果；
- 表格数量计入 `table_count`；
- `table_extraction` 关闭时跳过且无开销；
- 表格 Markdown 拼入 extracted_text 供 RAGFlow 检索（受字符截断上限约束）。

- [ ] **Step 2: 实现（GREEN）**

输出结构对齐 PRD §6.6.9：`{title?, headers, rows, markdown}`。feature 注册后确认前端 AiConfig 自动出现新开关（无需前端改动，补一条前端测试断言开关渲染）。

### Task 3: 文档质量评分（§6.6.10）

**Files:**
- Create: `backend/app/modules/ai/quality.py`
- Modify: `backend/app/modules/ai/service.py`（接入 `run_file_analysis` 流水线，`quality_score` 开关已存在）
- Modify: `backend/app/modules/ai/models.py` + 迁移（`DocumentAnalysis` 增 `quality_score` int、`quality_detail` JSONB）
- Create: `backend/app/tests/unit/test_quality_score.py`

- [ ] **Step 1: 写失败测试（RED）**

- 规则引擎对样例文本输出 0–100 分与分项明细（内容长度 / 乱码率 / 结构化程度（标题与段落特征）/ 提取成功率 / OCR 置信度）；
- 等级映射：优秀 ≥85 / 良好 ≥70 / 一般 ≥50 / 较差 <50；
- 各分项权重读配置 `ai.quality_weights`（带默认值）；
- LLM 增强路径在 provider 可用且开关开启时合并评分，失败时降级规则分（不阻断分析）。

- [ ] **Step 2: 实现（GREEN）**

纯函数引擎（可单测、无 IO）；评分写入分析记录；FileDetail 的分析卡片增加评分与等级展示（R3 已留位）。

### Task 4: 相似文档检测（§6.6.12）

**Files:**
- Create: `backend/app/modules/ai/simhash.py`
- Modify: `backend/app/modules/document/models.py` + 迁移（files 增 `simhash` BigInteger、`simhash_band_0..3` 四个 SmallInteger 索引列）
- Modify: `backend/app/modules/ai/service.py`（分析流水线计算指纹 + 近重复查询，`similarity_detection` 开关已存在）
- Modify: `backend/app/modules/ai/models.py` + 同迁移（`DocumentAnalysis` 增 `similar_file_ids` JSONB）
- Modify: `frontend/src/pages/FileDetail/index.tsx`、`frontend/src/pages/FileManagement/index.tsx`（相似文档提示）
- Create: `backend/app/tests/unit/test_simhash.py`

- [ ] **Step 1: 写失败测试（RED）**

- SimHash 纯函数：相同文本指纹相同；小幅改动文本汉明距离 ≤3；无关文本距离大；
- band 召回：仅与任一 band 相同的候选参与精确距离计算；
- 流水线：第二份近似文档分析后 `similar_file_ids` 含第一份；已删除 / 归档文件不参与召回；
- 阈值 k 读配置 `ai.similarity_hamming_threshold`（默认 3）。

- [ ] **Step 2: 实现（GREEN）**

64-bit SimHash（分词用简单 n-gram，零依赖）；4×16-bit band 列建普通索引；审核页与文件详情展示"疑似与 X 份文档重复"警示（辅助管理员合并决策，PRD §6.6.12 用途）。

### Task 5: 文档过期提醒（§6.6.11）

**Files:**
- Modify: `backend/app/modules/document/models.py` + 迁移（files 增 `expires_at`，可空）
- Modify: `backend/app/modules/ai/service.py`（`_default_feature_definitions` 新增 `expiry_reminder` 开关）
- Create: `backend/app/modules/document/tasks.py` 内 `document.check_expiry` beat 任务（每日）
- Modify: `backend/app/workers/celery_app.py`（beat schedule 注册）
- Modify: `backend/app/modules/document/events.py`（`DOCUMENT_FILE_EXPIRING`）
- Modify: `backend/app/modules/notification/handlers.py`（订阅事件 → 站内通知）
- Modify: `backend/app/modules/review/service.py`（审核通过时按"分类默认有效期"计算 `expires_at`，无分类用全局 `processing.default_expiry_days`，0 表示永不过期）
- Create: `backend/app/tests/unit/test_expiry.py`

- [ ] **Step 1: 写失败测试（RED）**

- 审核通过时按分类有效期正确落 `expires_at`；
- beat 任务扫描：`expires_at` 进入提前提醒窗口（`processing.expiry_remind_days`，默认 7 天）→ 发 `document.file.expiring` 事件，已发过的当日不重发（幂等标记）；
- notification handler 收事件生成上传人 + 管理员站内通知；
- `expiry_reminder` 开关关闭时任务空转。

- [ ] **Step 2: 实现（GREEN）**

beat 任务批量分页扫描（避免全表载入）；事件 payload 含 file_id / 名称 / 到期日 / 上传人。过期文档不自动禁用（仅提醒，处置留给管理员用 R4 的归档能力）。

### Task 6: 统计补重复 / 过期指标（§6.11.2）

**Files:**
- Modify: `backend/app/modules/statistics/{service,repository,schemas}.py`
- Modify: `frontend/src/pages/Statistics/index.tsx`、`frontend/src/pages/Dashboard/index.tsx`
- Modify: 既有统计测试 + 前端测试

- [ ] **Step 1: 后端（先 RED 后 GREEN）**

overview 响应增加：`duplicate_file_count`（`similar_file_ids` 非空或 hash 重复的文件计数）、`expiring_file_count`（提醒窗口内）、`expired_file_count`（已过期）。

- [ ] **Step 2: 前端（先 RED 后 GREEN）**

Statistics 页与 Dashboard 增对应指标卡；过期文件可点击跳转 FileManagement 预置筛选。

### Task 7: R5 批次验收

**Files:**
- Create: `docs/phase-reports/2026-06-10-r5-acceptance.md`

- [ ] **Step 1: 全量验证**

```powershell
python -m invoke lint
python -m invoke test
python -m invoke check-arm64
python -m invoke up
docker compose exec -T backend-api alembic current
npm --prefix frontend run test:run
npm --prefix frontend run build
```

- [ ] **Step 2: 端到端运行时验收**

- 开启 OCR 后上传文字图片 → 提取文本出现在 FileDetail 解析预览；关闭 OCR 上传图片不报错；
- 上传含表格的 xlsx → 分析结果含表格 Markdown 与 table_count；
- 上传两份近似文档 → 第二份详情出现重复提示，Statistics 重复指标 +1；
- 质量评分在详情页显示分数与等级；
- 把某文件 `expires_at` 手动调到明天 → 手动触发 beat 任务（`docker compose exec scheduler celery ... document.check_expiry`）→ 上传人收到站内通知，Dashboard 过期指标变化；
- AiConfig 页可见并可切换全部新开关（table_extraction / expiry_reminder），PRD §7.2.8 十个开关齐备。

- [ ] **Step 3: 原子提交**

- `feat(ai): 添加 OCR 引擎与图片上传支持`
- `feat(ai): 添加表格结构识别`
- `feat(ai): 添加文档质量评分引擎`
- `feat(ai): 添加 SimHash 相似文档检测`
- `feat(document): 添加文档过期提醒定时任务`
- `feat(statistics): 补充重复与过期文档指标`
- `docs(report): 添加 R5 批次验收报告`

---

## Self-Review

- Spec coverage: 覆盖 PRD §6.6.8 OCR、§6.6.9 表格结构识别（输出含表头 / 行 / 文本化）、§6.6.10 质量评分（0–100 + 四等级）、§6.6.11 过期提醒（分类默认有效期 + 管理员配置）、§6.6.12 相似检测（避免重复上传 / 提醒合并）、§6.11.2 重复与过期统计、§7.2.8 十开关、验收标准 §11.5。至此 PRD 九项 AI 能力全部落地。
- Placeholder scan: 无 TBD/TODO 占位。
- Type consistency: 新 feature key（table_extraction / expiry_reminder）与既有 snake_case 开关命名一致；事件 `document.file.expiring` 符合命名约定；配置 key 归入 R2 的 group 体系。
