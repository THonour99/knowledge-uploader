# R3 修复计划：管理与运营页面补全

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把"后端已就绪、前端缺页或假数据"的 6 处功能闭环（总览缺陷 #5 #6 #7 #11 #18 #19）：审计日志查询 + 操作日志页、任务日志页、个人中心页、分类管理独立页、Dashboard 真实数据、FileDetail 完整展示。

**Architecture:** 后端仅 audit 模块需补查询 API（模型与写入已就绪），其余全部是前端工程：新页面遵循既有模式（路由注册 `router/routes.tsx` + RoleGuard 角色白名单 + Sidebar 菜单自动渲染 + client.ts 集中 API + StatusTag 状态展示 + theme tokens）。不新增数据库迁移。

**Tech Stack:** FastAPI, SQLAlchemy async ORM, React + Ant Design + TanStack Query, Vitest.

**前置依赖:** 无硬依赖，可与 R1 / R2 并行（#19 解析结果展示需 R1 产出数据才有内容，页面结构先行）。

---

### Task 1: audit 查询 API（后端）

**Files:**
- Modify: `backend/app/modules/audit/api.py`
- Modify: `backend/app/modules/audit/service.py`
- Modify: `backend/app/modules/audit/repository.py`
- Modify: `backend/app/modules/audit/schemas.py`
- Modify: `backend/app/main.py`（确认 audit 路由注册）
- Create: `backend/app/tests/unit/test_audit_api.py`

- [ ] **Step 1: 写失败测试（RED）**

- `GET /api/admin/audit-logs` 支持分页（page/page_size）与筛选：actor_id、action、target_type、时间范围（created_from / created_to）；
- 返回按 created_at 倒序；
- knowledge_admin 可读、employee 403（PRD 权限表"查看操作日志：管理员支持"）；
- 响应不含任何敏感配置明文（metadata_json 原样返回前在 service 层过滤 secret 字段）。

```powershell
docker compose run --rm backend-api pytest app/tests/unit/test_audit_api.py
```

预期：失败（无查询端点）。

- [ ] **Step 2: 实现并验证 GREEN**

repository 增 `search_logs`（动态 where + count）；schemas 增 `AuditLogResponse` / `AuditLogListResponse`；api 暴露查询端点。运行测试 + ruff + mypy 通过。

### Task 2: 操作日志页（§7.2.12）

**Files:**
- Create: `frontend/src/pages/AuditLogs/index.tsx`
- Create: `frontend/src/pages/AuditLogs/index.test.tsx`
- Modify: `frontend/src/api/client.ts`（`listAuditLogs(params)`）
- Modify: `frontend/src/router/routes.tsx`（`/audit-logs`，KNOWLEDGE_ADMIN + SYSTEM_ADMIN，入侧边栏菜单）

- [ ] **Step 1: 写失败测试（RED）**

mock `listAuditLogs` → 表格渲染操作人 / 操作类型 / 对象 / IP / 时间 / 结果列；切换操作类型筛选触发带参重查；详情抽屉展示 metadata。

- [ ] **Step 2: 实现（GREEN）**

表格 + 筛选区（操作人、操作类型 Select、时间范围 RangePicker）+ 分页 + 详情 Drawer。queryKey：`["audit-logs", params]`。

### Task 3: 任务日志页（§7.2.11）

**Files:**
- Create: `frontend/src/pages/TaskLogs/index.tsx`
- Create: `frontend/src/pages/TaskLogs/index.test.tsx`
- Modify: `frontend/src/api/client.ts`（`listTasks(params)` / `getTask(id)` / `retryTask(id)` / `cancelTask(id)`，对接既有 `backend/app/modules/ragflow/api.py:71-143`）
- Modify: `frontend/src/router/routes.tsx`（`/task-logs`，KNOWLEDGE_ADMIN + SYSTEM_ADMIN）

- [ ] **Step 1: 写失败测试（RED）**

- 列表渲染任务类型 / 关联文件 / 状态（StatusTag）/ 重试次数 / 起止时间；
- 按任务类型与状态筛选触发重查；
- 失败任务行出现"重试"按钮 → 点击调 `retryTask` → 成功后 invalidate 列表；
- 运行中任务可"取消"（二次确认）。

- [ ] **Step 2: 实现（GREEN）**

详情 Drawer 展示任务日志明细（SyncTaskLog 行）与失败原因全文。运行 `npm --prefix frontend run test:run -- TaskLogs` 通过。

### Task 4: 个人中心页（§7.1.8）

**Files:**
- Create: `frontend/src/pages/Profile/index.tsx`
- Create: `frontend/src/pages/Profile/index.test.tsx`
- Modify: `frontend/src/api/client.ts`（`getMe()`；`changePassword` 已在 R1 添加，若 R1 未先行则在此添加）
- Modify: `frontend/src/router/routes.tsx`（`/profile`，所有登录角色，不入侧边栏）
- Modify: `frontend/src/layouts/TopHeader.tsx`（用户下拉菜单加"个人中心"项，第 51–58 行 items 数组）

- [ ] **Step 1: 写失败测试（RED）**

- 渲染显示姓名 / 邮箱 / 部门 / 角色 / 邮箱验证状态（来自 `/api/auth/me`）；
- 修改密码表单：原密码 + 新密码 + 确认 → `changePassword` 被正确调用 → 成功提示并清空表单；
- 新旧密码相同 / 确认不一致 → 本地校验阻止。

- [ ] **Step 2: 实现（GREEN）**

两卡片布局（资料卡 + 修改密码卡）。修改密码成功后提示"下次登录使用新密码"。

### Task 5: 分类管理独立页（§7.2.5）

**Files:**
- Create: `frontend/src/pages/Categories/index.tsx`
- Create: `frontend/src/pages/Categories/index.test.tsx`
- Modify: `frontend/src/router/routes.tsx`（`/categories`，SYSTEM_ADMIN）
- Read: `frontend/src/pages/DatasetConfig/index.tsx`（现有分类编辑逻辑可抽取复用）

- [ ] **Step 1: 写失败测试（RED）**

- 列表渲染分类名 / 描述 / 排序 / 启用状态 / 关联知识库 / 默认有效期；
- 新增分类 Modal 提交 → `createCategory` 正确调用；
- 编辑与启用/禁用切换 → `updateCategory` 正确调用。

- [ ] **Step 2: 实现（GREEN）**

复用 client.ts 既有 `listCategories` / `createCategory` / `updateCategory`；表单含"关联 RAGFlow 知识库"（listDatasetMappings 下拉）与"默认文档有效期（天）"字段（为 R5 过期提醒预埋展示，后端字段已在 Category 模型）。DatasetConfig 页中的分类编辑入口保留并指向本页。

### Task 6: Dashboard 接真实数据（§7.1.4 / §7.2.1）

**Files:**
- Modify: `frontend/src/pages/Dashboard/index.tsx`（硬编码数据位于约 60–236 行）
- Create: `frontend/src/pages/Dashboard/index.test.tsx`

- [ ] **Step 1: 写失败测试（RED）**

mock `getStatisticsOverview` / `getStatisticsTrends` / `getStatisticsUsers` / `getStatisticsFailures` → 断言指标卡 / 趋势图 / 排行 / 失败任务列表渲染 mock 值而非硬编码值。

- [ ] **Step 2: 实现（GREEN）**

- 指标卡 ← `getStatisticsOverview`（文件总数 / 今日上传 / 同步成功率 / 失败任务 / 风险文件）；
- 趋势图 ← `getStatisticsTrends`；分类占比 ← `getStatisticsCategories`；
- 上传排行 ← `getStatisticsUsers`；最近失败 ← `getStatisticsFailures`；
- 员工角色视图只展示"我的文件"相关指标（沿用现有角色分支）。
- 删除全部硬编码数组；loading/empty 态用 antd Skeleton/Empty。

### Task 7: FileDetail 补全展示（§7.1.7 / §6.10.3）

**Files:**
- Modify: `frontend/src/pages/FileDetail/index.tsx`
- Modify: `frontend/src/api/client.ts`（KnowledgeFile 类型补字段）
- Modify: `backend/app/modules/document/api.py` + `schemas.py`（详情响应增补：`error_message`、分析摘要联查）
- Modify: `backend/app/modules/document/repository.py`（联查 DocumentAnalysis 摘要 / 风险 / 提取文本预览）
- Create/Modify: `backend/app/tests/unit/test_document_api.py`（详情字段断言）

- [ ] **Step 1: 后端详情字段（先 RED 后 GREEN）**

`GET /api/files/{id}` 响应增加：`category_name`（联查 categories）、`analysis`（summary / risk_level / quality_score 预留 / extracted_text_preview 前 500 字 / error_message）、`sync_error`（最近失败任务的 error_message）。员工只能看自己文件（既有归属校验不动）。

- [ ] **Step 2: 前端展示（先 RED 后 GREEN）**

新增卡片：AI 分析（摘要 / 风险等级 StatusTag / 提取文本预览折叠面板）、分类与标签（Tag 列表）、失败原因（仅失败态显示 Alert）、处理日志（任务记录时间线，复用 Task 3 的 `listTasks({file_id})`）。

```powershell
npm --prefix frontend run test:run
npm --prefix frontend run lint
npm --prefix frontend run build
```

预期：全部通过。

### Task 8: R3 批次验收

**Files:**
- Create: `docs/phase-reports/2026-06-10-r3-acceptance.md`

- [ ] **Step 1: 全量验证**

```powershell
python -m invoke lint
python -m invoke test
python -m invoke up
```

- [ ] **Step 2: 端到端运行时验收（双角色走查）**

- system_admin：操作日志页可按操作人 / 类型 / 时间筛选并看到 R2 的 config.update 记录；任务日志页对一个失败任务重试成功；分类管理页新增 + 编辑 + 禁用分类；Dashboard 指标与数据库实际数量一致；
- employee：访问 `/audit-logs`、`/task-logs`、`/categories` 被路由守卫拒绝；个人中心改密码后旧密码登录失败、新密码成功；FileDetail 看到自己文件的摘要 / 标签 / 失败原因，访问他人文件 404/403。

- [ ] **Step 3: 原子提交**

- `feat(audit): 添加审计日志查询 API`
- `feat(frontend): 添加操作日志页`
- `feat(frontend): 添加任务日志页`
- `feat(frontend): 添加个人中心页`
- `feat(frontend): 添加分类管理页`
- `feat(frontend): 仪表盘接入统计接口`
- `feat(document): 文件详情补全分析与失败信息`
- `docs(report): 添加 R3 批次验收报告`

---

## Self-Review

- Spec coverage: 覆盖 PRD §6.13.3 日志查询、§7.2.12 操作日志页、§6.12.5 / §7.2.11 任务管理页、§7.1.8 个人中心、§7.2.5 分类管理页、§7.1.4 / §7.2.1 仪表盘、§6.10.3 / §7.1.7 文件详情展示、验收标准 §11.2"查看操作日志 / 重试失败任务"。
- Placeholder scan: 无 TBD/TODO 占位。
- Type consistency: 新页面路由 / queryKey / StatusTag kind 命名与既有约定一致；audit 响应字段与 AuditLog 模型一致。
