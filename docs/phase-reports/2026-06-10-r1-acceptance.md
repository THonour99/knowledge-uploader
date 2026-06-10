# R1 批次验收报告（认证前端接线 + 多格式文档解析）

日期：2026-06-10
执行计划：`docs/plan/2026-06-10-r1-auth-and-parsing.md`
方案总览：`docs/plan/2026-06-10-remediation-overview.md`（缺陷 #1 #2）

## 1. 交付内容

### 前端（缺陷 #1：认证三页断链）

- `frontend/src/api/client.ts`：新增 `register` / `forgotPassword` / `resetPassword` / `changePassword` / `resendVerification` 函数与对应请求/响应类型，字段与后端 `auth/schemas.py` 逐一对齐（含 `current_password` —— 以后端契约为准修正了计划文档中的 `old_password` 笔误）。
- `Register` / `ForgotPassword` / `ResetPassword` 三页接通 `Form onFinish → useMutation → API`，成功/失败提示与跳转齐备，严格复用 Login 页样板。
- 新增 3 个测试文件共 10 个测试（成功提交参数断言、后端错误展示、本地校验阻止、token 缺失 disabled 回归）。

### 后端（缺陷 #2：解析仅支持 txt/md/csv）

- `backend/app/modules/ai/parsers.py`（新建）：按扩展名分发的解析器注册表——txt/md/csv（多编码 utf-8/utf-8-sig/gb18030）、pdf（pypdf，页上限 200）、docx（段落+表格）、xlsx（read_only 流式，行上限 2000，含工作表名）、pptx（逐 slide/shape）；统一截断 20000 字符；doc/xls/ppt 旧格式按裁决 D2 抛出"转存为 docx/xlsx/pptx"提示；未知扩展名返回空串保持向后兼容。
- `exceptions.py`：新增 `DocumentParseError`（错误信息含格式名与原因、不含文件内容）与 `AiAnalysisTransientError`。
- `service.py`：`extract_text` 签名不变委托 parsers；解析失败的 `error_message` 记录结构化原因；存储读取失败按瞬态/永久分流。
- `adapters/minio_client.py`：`STORAGE_TRANSIENT_ERRORS` + `PERMANENT_S3_ERROR_CODES` + `is_transient_storage_error()`——NoSuchKey/AccessDenied 等永久错误不重试、直接落 `analysis_failed`。
- `tasks.py`：`ai.analyze_file` 任务 `bind=True`、仅对瞬态错误指数退避重试（30/60/120s，max_retries=3）、重试耗尽落 `analysis_failed`（文案与常量联动）、软超时 `soft_time_limit=600/time_limit=660` 并捕获 `SoftTimeLimitExceeded` 标记"分析超时"、`AiAnalysisPreconditionError` 分支补 warning 日志。
- `core/document_state.py`：新增 `analysis_failed → extracting_text` 转移（见偏差 D-1）。
- 依赖：`pypdf==6.13.1`、`python-docx==1.2.0`、`openpyxl==3.1.5`、`python-pptx==1.0.2`、`urllib3==2.7.0`（显式声明）、dev 增 `types-openpyxl`；`scripts/check_arm64_wheels.py` allowlist 同步。
- 测试：`test_ai_parsers.py` 21 个 + `test_ai_task_retry.py` 16 个，均为纯单元测试（不依赖 DB）。

## 2. 验证结果

| 验证项 | 命令 | 结果 |
|---|---|---|
| 前端全量测试 | `npm --prefix frontend run test:run` | ✅ 6 文件 19/19 通过 |
| 前端 lint | `npm --prefix frontend run lint` | ✅ 0 错误 0 警告 |
| 前端构建 | `npm --prefix frontend run build`（tsc 双 tsconfig + vite） | ✅ 通过（主 chunk >1MB 为既有警告） |
| 后端新增单测 | `pytest test_ai_parsers.py test_ai_task_retry.py --noconftest` | ✅ 37/37 通过 |
| 后端既有纯单测回归 | `pytest test_logging.py test_module_boundaries.py test_config.py --noconftest` | ✅ 13/13 通过 |
| ruff | `ruff check app` | ✅ All checks passed |
| mypy strict 全量 | `mypy app` | ✅ 197 文件无错误 |
| ARM64 | `invoke check-arm64` | ✅ 全部依赖 allowlisted |
| ARM64 独立交叉验证 | `pip download --platform aarch64` 实测 + PyPI 实查 | ✅ 5 个新库均纯 Python wheel；传递依赖 lxml/Pillow 需 manylinux_2_28（Debian 12 基础镜像满足，R5 Docker 构建时复核） |

评审：三视角（正确性 / 项目规范 / 契约与回归）评审无 blocker；全部 major 与可行 minor 已修复（见 §4）。

## 3. 受环境限制未执行项（遗留）

本机 Docker Desktop 未运行，以下验收项**待容器环境执行**：

1. `invoke test` 全量后端测试（依赖 Postgres/Redis 的 130+ 测试，含 `test_ai_tasks.py` 与 E2E；已静态核对兼容：FakeAiStorage 的 RuntimeError 不在瞬态集合内仍走原失败路径，接口签名未动）。
2. `docker compose build backend-api`（新依赖镜像构建验证）。
3. 浏览器端到端走查：注册 → 邮箱验证 → 登录 → 忘记密码 → 重置 → 新密码登录；上传真实 PDF/docx/xlsx/pptx 确认解析入流水线；停 MinIO 实测瞬态重试链路。

**启动 Docker Desktop 后执行：`invoke up && invoke test` 即可补齐以上三项。**

## 4. 评审发现处置表

| # | 发现（severity） | 处置 |
|---|---|---|
| 1 | STORAGE_TRANSIENT_ERRORS 把 NoSuchKey 等永久 S3 错误当瞬态重试（major） | ✅ 已修复：`is_transient_storage_error` 按 S3 error code 分流 |
| 2 | 状态机新增转移与计划"不改动状态机"声明冲突（major） | ✅ 裁决保留，见偏差 D-1 |
| 3 | 缺软超时（minor，计划要求） | ✅ 已修复：soft_time_limit=600 + SoftTimeLimitExceeded 落失败 |
| 4 | 重试/耗尽路径无单测（minor，计划要求） | ✅ 已修复：test_ai_task_retry.py 16 用例 |
| 5 | 重试次数硬编码进文案（minor） | ✅ 已修复：f-string 联动常量 |
| 6 | pdfplumber 已 pin 但零引用（minor） | ✅ 已修复：移除，R5 表格识别时再引入 |
| 7 | urllib3 直接 import 未声明（minor） | ✅ 已修复：requirements.txt 显式 pin |
| 8 | 重试窗口内前置条件失效可能卡 extracting_text（minor，低概率竞态） | ⚠️ 已加 warning 日志可观测；完整自愈（中间态文件补标失败）记入 R4 重新分析任务一并处理 |

## 5. 计划偏差记录

| # | 偏差 | 理由 |
|---|---|---|
| D-1 | 计划 Architecture 段写"不改动状态机"，实际新增 `analysis_failed → extracting_text` 转移 | PRD §6.4.2 要求异常状态保留可重试入口；R4 管理员"重新分析"直接依赖此转移；消息重投递时避免 DocumentStateError 覆盖真实失败原因。已有单测正反例覆盖。需同步 `05_DATABASE_API_SPEC §2` 状态机定义（R4 时一并更新） |
| D-2 | 计划的 `tests/fixtures/parsing/` 目录未创建，样例文件改为测试内程序化生成（docx/xlsx/pptx 用对应库构造、PDF 手工最小字节） | 自洽、无二进制文件入库、可独立运行，优于静态 fixture |
| D-3 | pdfplumber 在计划 Task 4 依赖清单中但最终移除 | PDF 文本提取仅需 pypdf；pdfplumber 是 R5 表格识别的依赖，按原子变更原则延后引入 |
| D-4 | 重试耗尽文案"存储暂不可用。已重试 3 次"用句号（计划文案为逗号） | ruff RUF001 禁全角逗号 |
| D-5 | `changePassword` / `resendVerification` API 函数已实现但无页面调用 | 个人中心页属 R3 Task 4，本批次仅交付函数与类型 |

## 6. 提交清单

| 提交 | 内容 |
|---|---|
| `feat(frontend): 接通注册与找回密码页面提交逻辑` | client.ts + 三页接线 + 10 测试 |
| `feat(ai): 添加多格式文档解析器注册表` | requirements + parsers.py + 异常 + service 接线 + 存储错误分类 + 21 测试 + allowlist |
| `fix(ai): 补全分析任务重试与幂等` | tasks.py 重试/软超时 + 状态机转移 + 16 测试 |
| `docs(report): 添加 R1 批次验收报告` | 本文档 |

## 7. 结论

R1 两项 P0 缺陷（#1 认证前端断链、#2 文档解析能力）在本机可验证范围内全部修复并通过验证；遗留三项容器环境验收项（§3）不阻塞 R2/R3 启动（依赖关系见总览 DAG）。R4 启动前建议先在 Docker 环境补齐 §3 验收。
