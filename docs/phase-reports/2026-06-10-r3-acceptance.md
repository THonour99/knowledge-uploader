# R3 批次验收报告（管理与运营页面补全）

日期：2026-06-10
执行计划：`docs/plan/2026-06-10-r3-admin-pages.md`
方案总览：`docs/plan/2026-06-10-remediation-overview.md`（缺陷 #5 #6 #7 #11 #18 #19）

## 1. 交付内容

### 后端

- **audit 查询 API**（缺陷 #5）：`GET /api/admin/audit-logs`——分页（page_size 上限 100，超界 422）、动态筛选（actor_id / action / target_type / 时间范围）、created_at 倒序、LEFT JOIN users 取操作人姓名邮箱（容忍已删用户）、metadata 中 secret/password/token/api_key 类 key 值脱敏为 `***`；角色 knowledge_admin+system_admin 可读、employee 403；**查询操作本身不写审计**（避免"读审计产生审计"雪崩，代码注释记录裁决）；main.py 注册 audit 路由。
- **文件详情增补**（缺陷 #19 后端）：`GET /api/files/{id}` 响应增 `category_name`、`analysis`（status/summary/sensitive_risk_level/quality_score 占位/extracted_text_preview 前 500 字/error_message/finished_at）、`sync_error`（最近失败同步任务原因）；employee 仅本人文件（既有校验不动），**knowledge_admin/system_admin 可看任意文件并写 `file.view_detail` 审计**（审计在全部查询成功后与之同事务提交）。

### 前端（6 个页面 + 骨架）

| 交付 | 说明 |
|---|---|
| 骨架 | client.ts 新增 listAuditLogs/listTasks/getTask/retryTask/cancelTask/getMe 与类型（任务类型照后端 schemas 实定义，未臆造字段）；routes.tsx 四条新路由（/audit-logs、/task-logs 限 KNOWLEDGE_ADMIN+SYSTEM_ADMIN 入菜单；/categories 限 SYSTEM_ADMIN 入菜单；/profile 全角色不入菜单）；TopHeader 用户下拉加"个人中心" |
| 操作日志页（#5/§7.2.12） | 筛选（操作人/类型/对象/时间范围）+ 服务端分页 + 详情 Drawer（metadata JSON 美化）；4 测试 |
| 任务日志页（#6/§7.2.11） | 类型/状态筛选、失败行重试（Popconfirm）、queued/running 行取消、详情 Drawer 含 SyncTaskLog 时间线与 error 全文；StatusTag sync kind、Timeline 图标颜色走 theme tokens；5 测试 |
| 个人中心页（#7/§7.1.8） | 资料卡（getMe）+ 修改密码卡（current_password 契约、本地校验、成功重置表单）；4 测试 |
| 分类管理页（#11/§7.2.5） | 列表/新增/编辑 Modal（含关联知识库 Select、AI/敏感/自动同步开关）、行内启用 Switch；4 测试 |
| Dashboard（#18/§7.2.1） | 删除全部硬编码数组，接 getStatisticsOverview/Trends/Categories/Users/Failures 五路真实数据，Skeleton/Empty 态，饼图颜色读 `--ku-color-*` CSS 变量；6 测试 |
| FileDetail（#19/§7.1.7） | 新增 AI 分析卡（摘要/风险 StatusTag/提取文本预览折叠/失败 Alert）、分类与标签卡、同步失败 Alert、admin 任务处理时间线（listTasks 按 file_id 过滤）；StatusTag 补 risk:none 与 sync 任务态映射；7 测试 |

## 2. 验证结果

| 验证项 | 结果 |
|---|---|
| 后端全量 pytest（docker） | ✅ 219 passed, 1 skipped（实现期）；修复后定向 35/35（audit 16 + document 19） |
| ruff check / mypy strict | ✅ 零错误（204 文件） |
| 前端全量测试 | ✅ 13 文件 56/56 通过 |
| 前端 lint / build | ✅ 零错误 / 构建成功 |
| 角色守卫静态走查 | ✅ 四条路由角色配置与菜单可见性符合计划 |
| Dashboard 硬编码残留 grep | ✅ 无残留 |

## 3. 评审情况（重要偏差说明）

**正确性契约评审 agent 因订阅会话限额未能执行**（"session limit"）。替代措施：
1. haiku 验证 agent 完成了契约走查（client.ts 六函数与后端逐项核对、角色守卫、StatusTag 使用、API 方法清单全 PASS）；
2. 规范评审（sonnet）正常完成；
3. 主协调对其报出的全部 7 项发现逐一人工修复并复验（见下表）。
残余风险：缺一轮独立的深度正确性评审（分页边界、React 状态细节），由 56 个前端测试 + 35 个后端测试的行为覆盖兜底。

### 评审发现处置表（规范评审 + 验证 agent）

| # | 发现（severity） | 处置 |
|---|---|---|
| 1 | audit/api.py 用行内 noqa TID251 而非 pyproject per-file-ignores（major） | ✅ 已修复：加入 per-file-ignores、删行内注释 |
| 2 | TaskLogs Timeline 图标/dot 颜色硬编码 hex 违反 token 规则（major，验证 agent 同报） | ✅ 已修复：logIcon/logDotColor 改用 `colors.primary/danger/success/textDisabled` |
| 3 | audit 分页测试用共用 target_type="file" 隔离脆弱（major） | ✅ 已修复：改唯一 target_type 严格隔离，两处 |
| 4 | 前端 AuditLogItem.target_id 类型 `string \| null` 与后端非空不符（minor） | ✅ 已修复：改 `string` |
| 5 | Dashboard 饼图 color 数组硬编码 hex（minor） | ✅ 已修复：改读 `--ku-color-*` CSS 变量（purple/cyan 变量已存在于 tokens.ts） |
| 6 | get_file_detail admin 分支查询前 commit、与员工路径事务边界不对称（minor） | ✅ 已修复：审计移至全部查询成功后写入并提交，仅记录成功的查看 |
| 7 | 分页测试注释与实际筛选字段不符（minor） | ✅ 已修复（随 #3） |

## 4. 计划偏差与已知限制

| # | 项 | 说明 |
|---|---|---|
| D-1 | `/api/tasks` 无 file_id 服务端筛选且 admin-only | FileDetail 任务时间线仅 admin 角色渲染（员工请求必 403），前端拉全量按 file_id 过滤；任务量大时低效。**记入 R4**：给 ragflow 模块加 file_id 筛选与归属放行 |
| D-2 | 任务状态复用 StatusTag sync kind（running→syncing 等近似映射） | 精确文案需在 StatusTag 增 task kind，留待后续统一 |
| D-3 | quality_score 恒为 null 占位 | document_analysis 无该列，R5 质量评分批次落库 |
| D-4 | 操作日志页"操作人"筛选按 actor_id（非姓名模糊） | 与后端 query schema 一致；按名搜索需后端扩展 |
| D-5 | basic 组配置（system_name 等）本批次未消费 | 维持 R2 预埋标注 |

## 5. 提交清单

`e267478` feat(audit): 添加审计日志查询 API → `38d133e` feat(frontend): 添加管理页面路由与接口骨架 → `ee82dab` 操作日志页 → `a2b73bb` 任务日志页 → `196497c` 个人中心页 → `4546ae5` 分类管理页 → `198d664` 仪表盘接入统计接口 → `38a7196` feat(document): 文件详情补全分析与失败信息 → 本报告。

## 6. 结论

R3 六项缺陷（#5 审计查询、#6 任务日志页、#7 个人中心、#11 分类管理页、#18 Dashboard 假数据、#19 FileDetail 缺展示）全部闭环；骨架先行策略实现了 6 路页面 agent 零文件冲突并行。遗留：浏览器双角色人工走查、D-1 任务筛选优化（并入 R4）。R4（文件生命周期）可启动。
