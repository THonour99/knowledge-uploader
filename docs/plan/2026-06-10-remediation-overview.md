# PRD 缺失功能修复方案总览（R1–R5）

> **For agentic workers:** 本文档是修复批次的总览与决策记录，不直接执行。执行时按依赖顺序进入各批次计划文档（`2026-06-10-r1` ~ `2026-06-10-r5`），每份批次计划使用 superpowers:executing-plans 风格逐 Task 实施。

**Goal:** 将系统从"核心链路可用（约 60% PRD 覆盖）"修复到 **PRD（`需求文档/01_PRD_产品需求文档.md`）100% 功能覆盖**，并保证每个批次结束时系统可部署、可测试、可验收。

**审计基线:** 本方案基于两份审查结论：

1. `docs/audit/2026-06-09-implementation-status.md` —— 实现状态审计（核心业务闭环可用、生产部署不可投产，audit/config/notification 模块骨架、前端多页 mock）。
2. 2026-06-10 六路子代理 PRD 对照审查（本方案缺陷清单的直接来源）：核心链路（上传 → 审核 → RAGFlow 同步）已通且安全红线落实到位；但存在 20 项缺陷，分布于认证前端、文档解析、系统配置、标签、审计查询、文件生命周期与 AI 高级能力。

---

## 1. 缺陷清单与批次映射

| # | 缺陷 | PRD 章节 | 优先级 | 批次 |
|---|------|---------|--------|------|
| 1 | Register / ForgotPassword / ResetPassword 三页无提交逻辑，client.ts 缺对应 API 函数 | §6.1, §7.1.1–7.1.3 | P0 | R1 |
| 2 | 文档解析仅支持 txt/md/csv，PDF/Word/Excel/PPT 未实现；无解析库依赖 | §6.5 | P0 | R1（图片白名单除外，见裁决 D1） |
| 3 | config 模块完全空壳：§6.14 共 24 项系统配置无落点；RAGFlow 配置仅环境变量、无 §7.2.7 配置页 | §6.14, §7.2.7, §7.2.13 | P0 | R2 |
| 4 | 标签管理模块缺失（无 Tag 模型 / API / 页面，标签仅为 files.tags JSONB） | §6.9.2, §7.2.6 | P1 | R4 |
| 5 | audit 模块缺查询 API 与操作日志页（模型与写入已有） | §6.13.3, §7.2.12 | P1 | R3 |
| 6 | 任务日志页缺失（后端 /api/tasks 已有列表/详情/重试/取消） | §6.12.5, §7.2.11 | P1 | R3 |
| 7 | 个人中心页缺失（后端 /api/auth/me 与 change-password 已有） | §7.1.8 | P1 | R3 |
| 8 | 用户管理：Users 页 mock 数据；缺重置密码 / 改角色 API | §7.2.2 | P1 | R4 |
| 9 | 文件删除 / 归档未实现；删除时同步删除 RAGFlow 文档未实现 | §6.10, §6.8.8 | P1 | R4 |
| 10 | 管理员重新解析 / 重新 AI 分析 / 手动同步操作未实现（按钮 disabled） | §6.10.2, §7.2.4 | P1 | R4 |
| 11 | 分类管理独立页缺失（后端 categories API 完整） | §7.2.5 | P1 | R3 |
| 12 | OCR 空壳（开关已有、无实现） | §6.6.8 | P2 | R5 |
| 13 | 表格结构识别未实现（连开关都没有） | §6.6.9 | P2 | R5 |
| 14 | 文档质量评分空壳 | §6.6.10 | P2 | R5 |
| 15 | 相似文档检测空壳 | §6.6.12 | P2 | R5 |
| 16 | 文档过期提醒未实现（开关与功能都没有） | §6.6.11 | P2 | R5 |
| 17 | 多文件上传只传第一个；无字节级进度；无用户总配额 | §6.3.1, §6.14.1 | P2 | R4（配额项在 R2 预埋配置） |
| 18 | Dashboard 硬编码数据（statistics API 已就绪未接入） | §7.1.4, §7.2.1 | P2 | R3 |
| 19 | FileDetail 缺展示：解析结果 / AI 摘要 / 分类 / 标签 / 失败原因 / 处理日志 | §6.10.3, §7.1.7 | P2 | R3 |
| 20 | 文件筛选缺"按文件类型 / 按标签"维度；统计缺重复 / 过期文档指标 | §6.10.2, §6.11.2 | P2 | 筛选 → R4；统计指标 → R5 |

覆盖自检：#1–#20 全部有归属批次，无遗漏。

## 2. 批次划分与依赖

```text
R1（认证接线 + 多格式解析）──┬──→ R4（文件生命周期/标签/用户）──→ R5（AI 高级能力）
R2（系统配置中枢）──────────┘                                  ↗
R3（管理页面补全）────（与 R1/R2 可并行，无硬依赖）──────────────┘
```

| 批次 | 目标 | 缺陷 | 计划文档 |
|------|------|------|----------|
| R1 | P0 解除阻断：新用户能注册/找回密码；PDF/Word/Excel/PPT 能解析进流水线 | #1 #2 | `2026-06-10-r1-auth-and-parsing.md` |
| R2 | 系统配置中枢：24 项配置 + RAGFlow 配置落库可改，模块可读配置不破坏边界 | #3 | `2026-06-10-r2-system-config.md` |
| R3 | 管理页面补全：把"后端已就绪、前端缺页"的 7 处功能闭环 | #5 #6 #7 #11 #18 #19 | `2026-06-10-r3-admin-pages.md` |
| R4 | 文件生命周期 / 标签一等实体 / 用户管理可用 / 上传体验补齐 | #4 #8 #9 #10 #17 #20(筛选) | `2026-06-10-r4-file-lifecycle.md` |
| R5 | AI 高级能力 100% 补齐与定时任务 | #12–#16 #20(统计) + 图片白名单 | `2026-06-10-r5-ai-advanced.md` |

**依赖说明：**

- R4 依赖 R1（"重新解析"依赖多格式解析能力）与 R2（配额值、归档保留策略读运行时配置）。
- R5 依赖 R1（解析器注册表是表格识别 / 质量评分的载体）、R2（高级能力的阈值与开关参数读运行时配置）、R4（重复文档统计依赖指纹列；过期处置动作依赖 `archive_file`）。
- R3 无硬依赖，可与 R1 / R2 并行（#19 的"解析结果展示"需 R1 产出数据才有内容，但页面结构可先行）。
- **Alembic 迁移串行约束：** R2 / R4 / R5 各含迁移，必须按顺序 rebase 到当时的 head（当前 head 为 `c7f1a2b9d6e4`）。禁止并行开两条迁移链。

## 3. 技术选型决策记录（ADR）

### ADR-1 文档解析库

**决策：** `pypdf`（PDF 正文）+ `pdfplumber`（仅 PDF 表格识别）+ `python-docx` + `openpyxl` + `python-pptx`。

**理由：** 全部为纯 Python 或仅依赖有 aarch64 wheel 的 lxml/Pillow，可通过 `invoke check-arm64`；`ai/service.py` 的 `extract_text` 已是按扩展名分发结构，自建解析器注册表比引入 markitdown（依赖树大、版本迭代快、底层即上述库）更可控。

**否决项：** markitdown。

### ADR-2 OCR 引擎

**决策：** 默认 `rapidocr-onnxruntime`；抽象 `OcrEngine` 接口，提供 LLM vision 备选实现（复用 ai_providers 框架，受 `allow_external_llm` 开关约束）。

**理由：** pip 安装、模型随包离线、中文识别质量好、onnxruntime 有 aarch64 wheel；tesseract 需系统包 + 中文语言包，Windows 本地开发与多架构镜像维护成本高。

**否决项：** tesseract、PaddleOCR（ARM64 wheel 不稳定）。

### ADR-3 相似文档检测

**决策：** SimHash 自实现（约百行、零新依赖、完全离线）。files 表存 64-bit 指纹 + 4 个 16-bit band 列建索引做候选召回，应用层计算汉明距离（阈值 k≤3 判近重复）。

**理由：** 公司文档"重复上传检测"场景是**近重复**而非语义相似；embedding 方案需 torch/本地模型（镜像爆炸）或外网 API（CI 无外网，违反测试红线）。

**否决项：** embedding 相似度；datasketch/MinHash 记为备选。

### ADR-4 质量评分与过期提醒

**决策：** 质量评分以规则启发式引擎为基线（文本长度 / 乱码率 / 结构化程度 / 提取成功率 / OCR 置信度 → 0–100 分 + JSONB 明细），LLM 评分作为 feature 开关下的可选增强。过期提醒挂既有 scheduler 服务（docker-compose 已含 Celery beat）每日扫描，规则 = 全局默认天数 + 分类级覆盖（读运行时配置），通知经事件总线投递 notification 模块。

**理由：** 规则引擎可解释、零成本、离线可测；scheduler 基础设施已就绪无需新增服务；跨模块通知走事件不破坏边界。

### ADR-5 系统配置存储

**决策：** 单表 `system_configs`（key 唯一 + value JSONB + group + value_type + 敏感标记 + updated_by/updated_at），新建 `core/runtime_config.py` 读取器（core 层直查表 + 进程内 TTL 缓存 30–60s）供各模块读取；config 模块只负责写入 / 校验 / 审计 / 发 `config.updated` 事件。**优先级：数据库值 > 环境变量**，环境变量作为种子默认值与回退。敏感项（RAGFlow API Key、SMTP 密码）Fernet 加密存储、响应脱敏。

**理由：** 24 项配置规模不值得分组多表；core 层不属于业务模块，各模块经 core 读配置不违反"禁止跨模块 import service/repository"红线。

**否决项：** 分组多表；模块直接 import config service。

### ADR-6 Tag 数据模型

**决策：** 独立 `tags` 表 + `file_tags` 关联表为唯一真源，归 review 模块（与 categories 同属内容治理域）。`files.tags` JSONB 降级为"AI 建议标签"语义保留，迁移含回填脚本。

**理由：** PRD §6.9.2 要求重命名 / 合并 / 使用统计——JSONB 下重命名需全表 UPDATE；"按标签筛选"需要关联表索引。

## 4. 范围裁决

| 编号 | 裁决 | 理由 |
|------|------|------|
| D1 | 图片扩展名（png/jpg/jpeg）**不在 R1 放开**，随 R5 OCR 同批进白名单 | 若 R1 放开而 OCR 在 R5，会出现"上传成功但解析为空、同步到 RAGFlow 是空文档"的窗口期。若业务方要求提前放开，R1 需为图片增加"待 OCR"占位状态并阻断其同步 |
| D2 | 旧二进制格式 .doc/.xls/.ppt **不支持解析**，上传时返回明确错误文案（提示转存为 docx/xlsx/pptx） | 解析旧格式需 LibreOffice/antiword 等系统级依赖，多架构镜像成本不成比例；PRD §6.3.2 为"建议支持"非硬性 |
| D3 | 修复批次编号采用 R 系列（R1–R5），不复用已完结的 Phase 0–9 编号 | 避免与 `docs/phase-reports/` 既有阶段验收报告混淆 |

## 5. 全局验收门槛（每批次必过）

```powershell
# 含迁移的批次（R2/R4/R5）：迁移往返
python -m invoke migrate
docker compose exec -T backend-api alembic downgrade -1
docker compose exec -T backend-api alembic upgrade head

# 新依赖批次（R1/R5）：ARM64 检查
python -m invoke check-arm64

# 后端
docker compose run --rm backend-api pytest
docker compose run --rm backend-api ruff check app
docker compose run --rm backend-api mypy app

# 前端
npm --prefix frontend run lint
npm --prefix frontend run test:run
npm --prefix frontend run build

# 全量门
python -m invoke lint
python -m invoke test
python -m invoke up
```

另加批次专属端到端验收（见各批次文档末尾 Task），每批次产出 `docs/phase-reports/2026-06-XX-r<N>-acceptance.md` 并按 `type(scope):中文描述` 原子提交。

## 6. 风险登记表

| # | 风险 | 缓解 |
|---|------|------|
| 1 | 图片白名单提前放开导致空文档同步 RAGFlow | 裁决 D1：图片随 R5 OCR 同批放开 |
| 2 | 放开图片后 filetype MIME 嗅探、扩展名白名单、动态配置三方校验不联动 | R5 测试必须覆盖"改配置后即时生效"与 MIME/扩展名交叉用例 |
| 3 | api / celery-worker / scheduler 多进程各持 runtime_config 缓存，存在 TTL 不一致窗口 | 安全敏感组（登录锁定阈值等）直读 DB 或缩短 TTL；R2 Task 含读取点盘点清单，避免半切换状态 |
| 4 | 删除文件与 RAGFlow 删除的事务一致性 | 最终一致：outbox 事件 → Celery 重试；禁止在 HTTP 事务内调 RAGFlow；RAGFlow 404 视为成功（幂等）；超过 max_retries 标记 `ragflow_cleanup_failed` 并可在任务日志页重试 |
| 5 | R2/R4/R5 并行开发产生 Alembic 分叉 | 批次串行 rebase 到最新 head；总览明文禁止并行迁移链 |
| 6 | 大 PDF/xlsx 解析导致 worker 内存峰值 | 解析器强制页数/行数/字符数截断上限（上限做成 R2 配置项）；Celery 任务设软超时 |
| 7 | rapidocr + onnxruntime 镜像体积 +100–150MB，模型不可运行时下载 | 模型离线打入镜像；Dockerfile 多架构构建验证纳入 R5 验收 |
| 8 | 标签 JSONB 回填迁移不可重入 / 不可逆 | 回填脚本幂等（ON CONFLICT DO NOTHING）；downgrade 仅删新表、不动原 JSONB 列，天然可逆 |
| 9 | Users 页去 mock 后现有前端测试连带失败 | R4 同批改写对应测试断言 |
| 10 | `ai.analyze_file` 补重试后重复分析产生重复结果行 | 重试前检查既有分析记录（upsert 语义），与项目规则对齐 |

## 7. 文档清单

| 文档 | 说明 |
|------|------|
| `docs/plan/2026-06-10-remediation-overview.md` | 本文档 |
| `docs/plan/2026-06-10-r1-auth-and-parsing.md` | R1 批次执行计划 |
| `docs/plan/2026-06-10-r2-system-config.md` | R2 批次执行计划 |
| `docs/plan/2026-06-10-r3-admin-pages.md` | R3 批次执行计划 |
| `docs/plan/2026-06-10-r4-file-lifecycle.md` | R4 批次执行计划 |
| `docs/plan/2026-06-10-r5-ai-advanced.md` | R5 批次执行计划 |
