# R1 修复计划：认证前端接线 + 多格式文档解析

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 解除两项 P0 阻断（总览缺陷 #1 #2）：新用户能完成注册 → 邮箱验证 → 登录 → 忘记密码 → 重置密码全链路；PDF / Word / Excel / PPT 文档能被解析进入 AI 分析与同步流水线。

**Architecture:** 前端三页严格复用 Login 页样板（useMutation + Form onFinish + 统一错误处理），API 函数集中在 `api/client.ts`；后端解析能力重构为按扩展名分发的解析器注册表（独立 `parsers.py`），`extract_text` 退化为注册表入口，解析失败返回结构化原因写入 `DocumentAnalysis.error_message`。不改动状态机与模块边界。图片扩展名按总览裁决 D1 **不在本批次放开**。

**Tech Stack:** React + TanStack Query + Ant Design, FastAPI, Celery, pypdf, pdfplumber, python-docx, openpyxl, python-pptx, pytest, Vitest.

**前置依赖:** 无（最先执行）。

---

### Task 1: 前端认证 API 函数与类型

**Files:**
- Modify: `frontend/src/api/client.ts`
- Read: `backend/app/modules/auth/api.py`（端点契约：`POST /api/auth/register`、`/forgot-password`、`/reset-password`、`/change-password`、`/verify-email`、`/resend-verification`）

- [ ] **Step 1: 补类型定义**

新增 `RegisterRequest`（name/email/password/department 可选）、`ForgotPasswordRequest`、`ResetPasswordRequest`（token + new_password）、`ChangePasswordRequest`（old_password + new_password），对照后端 `auth/schemas.py` 字段命名保持 snake_case 一致。

- [ ] **Step 2: 补 API 函数**

实现 `register` / `forgotPassword` / `resetPassword` / `changePassword` / `resendVerification`，全部走既有 axios 实例与 `unwrapResponse` 解包（参照 `login` 实现，client.ts:367-374）。

### Task 2: 注册页接线

**Files:**
- Modify: `frontend/src/pages/Register/index.tsx`
- Create: `frontend/src/pages/Register/index.test.tsx`
- Read: `frontend/src/pages/Login/index.tsx`（useMutation + onFinish + message.error 样板，第 23–58 行）

- [ ] **Step 1: 写失败测试（RED）**

参照 `AiConfig/index.test.tsx` 模式（vi.mock client + QueryClientProvider 包裹）：
- 填写合法表单提交 → `register` 被以正确参数调用，成功后出现"请查收验证邮件"提示；
- 后端返回邮箱后缀不允许错误 → 页面展示错误信息；
- 两次密码不一致 → 表单本地校验阻止提交。

```powershell
npm --prefix frontend run test:run -- Register
```

预期：失败（页面无提交逻辑）。

- [ ] **Step 2: 实现提交逻辑（GREEN）**

Form 增加 `onFinish` → `useMutation({ mutationFn: register })`；onSuccess 提示注册成功与邮箱验证指引并跳转 `/login`；onError 用 `message.error` 展示后端 message。公司邮箱后缀规则提示文案保留。

```powershell
npm --prefix frontend run test:run -- Register
```

预期：通过。

### Task 3: 忘记密码与重置密码页接线

**Files:**
- Modify: `frontend/src/pages/ForgotPassword/index.tsx`
- Modify: `frontend/src/pages/ResetPassword/index.tsx`
- Create: `frontend/src/pages/ForgotPassword/index.test.tsx`
- Create: `frontend/src/pages/ResetPassword/index.test.tsx`

- [ ] **Step 1: 写失败测试（RED）**

- ForgotPassword：提交邮箱 → `forgotPassword` 被调用 → 成功提示"重置邮件已发送"；
- ResetPassword：URL token + 新密码提交 → `resetPassword` 被以 `{token, new_password}` 调用 → 成功跳转 `/login`；token 缺失时提交按钮保持 disabled（已有逻辑，断言不回归）。

- [ ] **Step 2: 实现提交逻辑（GREEN）**

两页各接 useMutation；ResetPassword 从 `useParams` 取 token（既有第 13 行）。运行：

```powershell
npm --prefix frontend run test:run -- ForgotPassword ResetPassword
npm --prefix frontend run lint
npm --prefix frontend run build
```

预期：全部通过。

### Task 4: 解析库依赖与 ARM64 检查

**Files:**
- Modify: `backend/requirements.txt`

- [ ] **Step 1: 添加依赖（pinned 版本）**

```text
pypdf
pdfplumber
python-docx
openpyxl
python-pptx
```

版本选 2026-06 时点最新稳定版并 pin（与现有文件风格一致 `==`）。

- [ ] **Step 2: ARM64 与构建验证**

```powershell
python -m invoke check-arm64
docker compose build backend-api
```

预期：所有新依赖有 aarch64 wheel；镜像构建通过。

### Task 5: 解析器注册表（后端）

**Files:**
- Create: `backend/app/modules/ai/parsers.py`
- Modify: `backend/app/modules/ai/service.py`（`extract_text`，约 796 行）
- Modify: `backend/app/modules/ai/exceptions.py`
- Create: `backend/app/tests/unit/test_ai_parsers.py`
- Create: `backend/app/tests/fixtures/parsing/`（每格式一份最小样例文件 + 一份损坏文件）

- [ ] **Step 1: 写失败测试（RED）**

覆盖：
- pdf/docx/xlsx/pptx/txt/md/csv 各格式提取出非空文本；
- xlsx 提取含工作表名与单元格文本；pptx 提取含每页文本；
- 超长内容按 `MAX_EXTRACTED_TEXT_LENGTH` 截断；
- 损坏文件抛 `DocumentParseError`，错误信息含格式名与原因（不含文件内容）；
- 不支持扩展名（如 doc）返回结构化"不支持的旧格式"错误（裁决 D2 文案）。

```powershell
docker compose run --rm backend-api pytest app/tests/unit/test_ai_parsers.py
```

预期：失败（parsers.py 不存在）。

- [ ] **Step 2: 实现解析器注册表**

`parsers.py` 定义 `Parser = Callable[[bytes], str]` 与 `PARSER_REGISTRY: dict[str, Parser]`：
- `txt/md/csv` → 迁移现有多编码解码逻辑（utf-8 / utf-8-sig / gb18030）；
- `pdf` → pypdf 逐页提取，页数上限截断；
- `docx` → python-docx 段落 + 表格单元格文本；
- `xlsx` → openpyxl `read_only=True` 流式逐行，行数上限截断；
- `pptx` → python-pptx 逐 shape 文本。

所有解析在 `try/except` 内转 `DocumentParseError(format, reason)`。截断上限先用模块常量（页数 / 行数 / 字符数），R2 落库为配置项后切换读取。

- [ ] **Step 3: 接入 extract_text 并验证 GREEN**

`extract_text` 改为查注册表分发；未注册扩展名维持返回空串语义不破坏现有调用方，但 `run_file_analysis` 对 `DocumentParseError` 记录 `error_message` 并转 `analysis_failed` 状态（沿用既有失败路径）。

```powershell
docker compose run --rm backend-api pytest app/tests/unit/test_ai_parsers.py app/tests/unit/test_ai*.py
docker compose run --rm backend-api ruff check app
docker compose run --rm backend-api mypy app
```

预期：全部通过。

### Task 6: Celery 任务重试与幂等

**Files:**
- Modify: `backend/app/modules/ai/tasks.py`
- Modify: `backend/app/modules/ai/service.py`

- [ ] **Step 1: 补重试配置**

`ai.analyze_file` 任务增加 `autoretry_for=(临时性异常,)`、`max_retries=3`、`retry_backoff=True`、软超时；`AiAnalysisPreconditionError` 维持不重试语义。

- [ ] **Step 2: 幂等保证**

`run_file_analysis` 开始前检查同 file_id 的进行中/已完成分析记录，重试时复用或重置而非新插（upsert 语义），避免重复结果行（总览风险 #10）。补对应单测。

### Task 7: R1 批次验收

**Files:**
- Create: `docs/phase-reports/2026-06-10-r1-acceptance.md`

- [ ] **Step 1: 全量验证**

```powershell
python -m invoke lint
python -m invoke test
python -m invoke check-arm64
python -m invoke up
```

- [ ] **Step 2: 端到端运行时验收**

浏览器走查：注册新账号 → 收验证邮件（MailHog/日志确认）→ 验证 → 登录 → 退出 → 忘记密码 → 重置 → 新密码登录。
上传真实 PDF / docx / xlsx / pptx 各一份（AI 开启）：确认解析文本进入 `DocumentAnalysis.extracted_text`、状态走到 `analyzed`，上传损坏 PDF 确认 `analysis_failed` 且失败原因可见。

- [ ] **Step 3: 原子提交**

- `feat(frontend): 接通注册与找回密码页面提交逻辑`
- `feat(ai): 添加多格式文档解析器注册表`
- `fix(ai): 补全分析任务重试与幂等`
- `docs(report): 添加 R1 批次验收报告`

---

## Self-Review

- Spec coverage: 覆盖 PRD §6.1 注册/忘记密码前端闭环、§6.5 解析能力（PDF/Word/Excel/PPT/MD/TXT）、§6.5.4 解析失败处理、验收标准 §11.1。图片 OCR 按裁决 D1 留至 R5。
- Placeholder scan: 无 TBD/TODO 占位。
- Type consistency: 前端 snake_case 请求字段与后端 schemas 一致；DocumentParseError 命名与既有异常风格一致。
