# R4 修复计划：文件生命周期 / 标签 / 用户管理

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补齐文件生命周期操作（删除 / 归档 / 重新解析 / 重新分析 / 手动同步，含删除联动 RAGFlow）、标签一等实体化、用户管理闭环、上传体验（多文件 / 进度 / 配额）与文件筛选维度（总览缺陷 #4 #8 #9 #10 #17 #20 前半）。

**Architecture:** 删除走软删（状态机 `→ deleted`）+ 同事务 outbox 事件 `document.file.deleted`，ragflow 模块经 handler 订阅创建远端删除 Celery 任务（最终一致、404 幂等，**禁止在 HTTP 事务内调 RAGFlow**）；是否删远端读 R2 的 `ragflow.delete_remote_on_file_delete` 配置。标签按 ADR-6 独立 `tags` + `file_tags` 表，归 review 模块。重新解析 / 重新分析 / 手动同步经事件或既有 Celery 任务触发，不跨模块 import service。

**Tech Stack:** FastAPI, SQLAlchemy async ORM, Alembic, Celery, RabbitMQ (outbox), React + Ant Design, axios onUploadProgress, pytest, Vitest.

**前置依赖:** R1（重新解析依赖多格式解析）、R2（配额值 / 删除策略读 runtime_config）。迁移 rebase 到 R2 之后的 head。

---

### Task 1: Tag 模型、迁移与回填

**Files:**
- Modify: `backend/app/modules/review/models.py`（新增 `Tag` / `FileTag`）
- Create: `backend/app/db/migrations/versions/<revision>_add_tags_tables.py`
- Create: `backend/app/tests/unit/test_tag_models.py`

- [ ] **Step 1: 模型定义**

`Tag`：`id`、`name`（唯一索引）、`description`、`is_system_generated`、`enabled`、`usage_count`（冗余计数，service 维护）、`created_at`、`updated_at`。
`FileTag`：`file_id` + `tag_id` 复合主键，双外键 + 双索引。

- [ ] **Step 2: 回填迁移**

迁移内遍历 `files.tags` JSONB：去重建 `tags`（`is_system_generated=true`）+ 建 `file_tags` 关联，`ON CONFLICT DO NOTHING` 保证可重入；downgrade 仅删两张新表、不动 JSONB 列（总览风险 #8）。`files.tags` 语义降级为"AI 建议标签"。

- [ ] **Step 3: 迁移往返验证**

```powershell
python -m invoke migrate --msg="add tags tables"
python -m invoke migrate
docker compose exec -T backend-api alembic downgrade -1
docker compose exec -T backend-api alembic upgrade head
```

### Task 2: 标签 CRUD API 与标签管理页（§6.9.2 / §7.2.6）

**Files:**
- Modify: `backend/app/modules/review/{api,service,repository,schemas}.py`
- Create: `backend/app/tests/unit/test_tag_api.py`
- Create: `frontend/src/pages/Tags/index.tsx` + `index.test.tsx`
- Modify: `frontend/src/api/client.ts`、`frontend/src/router/routes.tsx`（`/tags`，SYSTEM_ADMIN）

- [ ] **Step 1: 后端（先 RED 后 GREEN）**

端点：`GET /api/tags`（含 usage_count、enabled 筛选）、`POST /api/tags`、`PATCH /api/tags/{id}`（重命名 / 启用禁用 / 改描述）、`POST /api/tags/{id}/merge`（merge into target：迁移 file_tags 关联、累加 usage_count、删源标签）、`DELETE /api/tags/{id}`（无关联时物理删，有关联需先合并或确认级联解除）。写操作仅 system_admin、全部写审计。

- [ ] **Step 2: 前端（先 RED 后 GREEN）**

表格（名称 / 描述 / 使用次数 / 来源 / 启用）+ 新增 / 编辑 / 合并 Modal + 启用禁用开关。

### Task 3: 文件删除 / 归档与 RAGFlow 联动（§6.10 / §6.8.8）

**Files:**
- Modify: `backend/app/modules/document/{service,api,events,schemas}.py`
- Modify: `backend/app/modules/ragflow/{handlers,tasks,service}.py`
- Modify: `backend/app/core/document_state.py`（确认 `→ deleted` / `→ disabled` 转移已允许）
- Create: `backend/app/tests/unit/test_document_lifecycle.py`

- [ ] **Step 1: 写失败测试（RED）**

- 员工删除自己文件（`upload.allow_user_delete` 配置开启时）→ 软删 + outbox 事件；配置关闭时 403；
- 员工删他人文件 404/403；管理员可删任意文件、可归档（`→ disabled`）；
- 删除事件 payload 含 `ragflow_document_id` 与 `delete_remote` 决策位（读 `ragflow.delete_remote_on_file_delete`；归档读 `ragflow.keep_remote_on_archive`）；
- ragflow handler 收到事件创建 `ragflow_delete` 任务；任务对远端 404 返回成功（幂等）；超过 max_retries 文件标记 `ragflow_cleanup_failed` 可在任务日志页重试；
- 全部操作写审计。

```powershell
docker compose run --rm backend-api pytest app/tests/unit/test_document_lifecycle.py
```

- [ ] **Step 2: 实现（GREEN）**

`document/service.py` 增 `delete_file` / `archive_file`（状态机转移 + MinIO 对象保留——软删不动对象，物理清理留运维脚本）+ `DOCUMENT_FILE_DELETED` / `DOCUMENT_FILE_ARCHIVED` 事件；`document/api.py` 增 `DELETE /api/files/{id}`、`POST /api/admin/files/{id}/archive`。ragflow `handlers.py` 用 `@event_handler` 订阅并入队删除任务（新任务类型 `ragflow_delete` 入 SyncTask 类型约束）。

### Task 4: 重新解析 / 重新 AI 分析 / 手动同步（§6.10.2 / §7.2.4）

**Files:**
- Modify: `backend/app/modules/document/api.py`（`POST /api/admin/files/{id}/reparse`、`/reanalyze`）
- Modify: `backend/app/modules/ragflow/api.py`（`POST /api/admin/files/{id}/sync` 手动同步，复用既有分布式锁 `lock:sync:{file_id}`）
- Modify: `frontend/src/pages/FileManagement/index.tsx`（启用同步 / 删除 / 归档 / 重试按钮，约 476–488 行 disabled 项）
- Create/Modify: `backend/app/tests/unit/test_file_admin_ops.py`

- [ ] **Step 1: 后端（先 RED 后 GREEN）**

- reanalyze：仅 `analysis_failed` / `analyzed` 等合法源状态可触发，状态转回 `analysis_queued` 并入队 `ai.analyze_file`（R1 幂等保证生效）；AI 总开关关闭时 409；
- 手动同步：仅 `approved` / `failed` 可触发，复用审核门禁校验（敏感 critical 阻断除非配置允许）与分布式锁；
- 全部写审计。

- [ ] **Step 2: 前端（先 RED 后 GREEN）**

FileManagement 操作列启用：手动同步（approved/failed 态）、删除（Popconfirm）、归档、重新分析（失败态）；操作成功 invalidate 列表。补测试。

### Task 5: 用户管理闭环（§7.2.2）

**Files:**
- Modify: `backend/app/modules/user/{service,api,schemas,repository}.py`
- Create/Modify: `backend/app/tests/unit/test_user_admin_api.py`
- Modify: `frontend/src/pages/Users/index.tsx`（去 mock 全接线）+ 既有测试同步修改
- Modify: `frontend/src/api/client.ts`（`listUsers` / `disableUser` / `enableUser` / `changeUserRole` / `resetUserPassword`）

- [ ] **Step 1: 后端补 API（先 RED 后 GREEN）**

- `PATCH /api/users/{id}/role`：仅 system_admin；不能改自己角色；不能把最后一个 system_admin 降级（复用 `count_active_system_admins`）；
- `POST /api/users/{id}/reset-password`：仅 system_admin；走既有重置 token 邮件流程（复用 auth 模块逻辑经事件或共享 schemas，**不跨模块 import service**——由 user 模块发 `user.password_reset_requested` outbox 事件，auth/notification 侧订阅发邮件）；
- `GET /api/users` 增分页 / 搜索（email、name 模糊）/ 角色与状态筛选，响应附每用户上传统计（联查 files 计数）；
- 全部写审计。

- [ ] **Step 2: 前端去 mock（先改测试 RED 后实现 GREEN）**

Users 页接真实 API：搜索框 → 带参重查；启用 / 禁用 / 改角色（Select + 确认）/ 重置密码（确认 Modal）接线；上传统计列显示真实计数。同步改写原 mock 断言测试（总览风险 #9）。

### Task 6: 多文件上传、字节级进度与配额（§6.3.1 / §6.14.1）

**Files:**
- Modify: `frontend/src/pages/Upload/index.tsx`
- Modify: `frontend/src/api/client.ts`（`uploadDocument` 增 `onUploadProgress` 回调参数）
- Modify: `backend/app/modules/document/service.py`（上传前配额校验）
- Modify: `backend/app/modules/document/repository.py`（`sum_size_for_uploader`）
- Create/Modify: `backend/app/tests/unit/test_upload_quota.py`

- [ ] **Step 1: 后端配额（先 RED 后 GREEN）**

`upload_file` 校验链增加：`sum(size) + 新文件 size > upload.user_quota_mb * 1024 * 1024` → 拒绝（明确错误码与剩余额度信息）；配额读 runtime_config（R2 预埋 key），0/null 表示不限。

- [ ] **Step 2: 前端多文件与进度（先 RED 后 GREEN）**

- Upload 组件 `multiple` + `upload.allow_multi_file` 配置控制；
- 选 N 个文件 → 逐文件并发上传（并发上限 3），每文件独立 Progress 条（axios `onUploadProgress` 字节进度）与结果态（成功 / 重复 / 失败原因）；
- 超配额时展示后端返回的剩余额度提示。

### Task 7: 文件筛选补维度（§6.10.2 / §7.1.6）

**Files:**
- Modify: `backend/app/modules/document/{api,repository,schemas}.py`（列表接口增 `extension` / `tag_id` 过滤参数）
- Modify: `backend/app/modules/review/{api,repository}.py`（管理员列表同步增参）
- Modify: `frontend/src/pages/MyFiles/index.tsx`、`frontend/src/pages/FileManagement/index.tsx`（增"文件类型""标签"筛选控件）

- [ ] **Step 1: 后端过滤参数（先 RED 后 GREEN）**

`tag_id` 经 `file_tags` 关联查询（Task 1 索引保证性能）；`extension` 精确匹配白名单值。

- [ ] **Step 2: 前端筛选控件（先 RED 后 GREEN）**

文件类型 Select（来自 `upload.allowed_extensions` 配置）+ 标签 Select（`listTags`）；MyFiles 同步补"时间范围"筛选（审查发现缺失）。

### Task 8: R4 批次验收

**Files:**
- Create: `docs/phase-reports/2026-06-10-r4-acceptance.md`

- [ ] **Step 1: 全量验证**

```powershell
python -m invoke lint
python -m invoke test
python -m invoke up
docker compose exec -T backend-api alembic current
```

- [ ] **Step 2: 端到端运行时验收**

- 删除一个已同步文件 → 任务日志页可见 `ragflow_delete` 任务成功（mock RAGFlow 下断言调用；真实环境确认远端文档消失）；
- 标签管理页合并两个标签 → 文件关联与使用次数正确；按标签筛选文件列表命中；
- Users 页禁用一个账号 → 该账号登录被拒；改角色立即生效；重置密码触发邮件；
- 一次选 5 个文件上传 → 逐文件字节进度可见、全部入库；把配额改小 → 超配额上传被拒并提示剩余额度；
- 管理员对失败文件"重新分析"→ 状态回到分析流水线。

- [ ] **Step 3: 原子提交**

- `feat(review): 添加标签表与回填迁移`
- `feat(review): 添加标签管理 API 与页面`
- `feat(document): 添加文件删除与归档及 RAGFlow 联动`
- `feat(document): 添加重新解析与重新分析入口`
- `feat(ragflow): 添加手动同步与远端删除任务`
- `feat(user): 补全角色变更与重置密码管理`
- `feat(frontend): 用户管理页接入真实接口`
- `feat(document): 添加用户上传配额校验`
- `feat(frontend): 多文件上传与字节级进度`
- `feat(document): 文件列表按类型与标签筛选`
- `docs(report): 添加 R4 批次验收报告`

---

## Self-Review

- Spec coverage: 覆盖 PRD §6.9.2 标签管理全字段与操作、§6.10.1/6.10.2 用户与管理员文件操作（删除 / 归档 / 重试 / 8 维筛选补齐）、§6.8.8 删除同步策略、§7.2.2 用户管理页全功能、§6.3.1 多文件与进度、§6.14.1 用户配额、§6.3.3 配额校验。
- Placeholder scan: 无 TBD/TODO 占位。
- Type consistency: 事件命名 `document.file.deleted/archived` 符合 `<module>.<aggregate>.<action>` 约定；新任务类型 `ragflow_delete` 与 SyncTask 既有类型风格一致。
