# R2 批次验收报告（系统配置中枢）

日期：2026-06-10
执行计划：`docs/plan/2026-06-10-r2-system-config.md`
方案总览：`docs/plan/2026-06-10-remediation-overview.md`（缺陷 #3）

## 1. 交付内容

### 数据层

- `system_configs` 表（迁移 `e5b8c0d1f2a3`，基于 `c7f1a2b9d6e4`）：key 唯一、group/value_type CheckConstraint、JSONB value、敏感标记；种子 **35 项配置、5 组**（upload 6 / processing 7 / security 7 / basic 6 / ragflow 9）；downgrade 完整可逆，往返验证通过。
- `config/defaults.py`：配置注册表（key/组/类型/默认值/描述/int 上下界），读路径合并视图与写路径校验的代码内单一来源。

### config 模块写路径（原空壳 9 文件全部实体化）

- `GET /api/admin/configs?group=`（knowledge_admin 可读）、`PUT /api/admin/configs/{group}`（仅 system_admin）。
- 逐 key 存在性 / 类型 / **int 上下界**校验（校验先于写入，失败无半写）；secret 项 Fernet 加密入库、空字符串=清除；响应 secret 恒 `value=None` + 掩码（短于 8 位直接 `****` 不回显）。
- 同事务写审计（`config.view` / `config.update`，target_id 用 `uuid5("system-config-group:{group}")` 确定性标识，**secret 值绝不入审计**）+ outbox 事件 `config.settings.updated`。

### core 运行时配置读取器

- `core/runtime_config.py`：text-SQL 直查（不反向依赖 modules）、进程内 TTL 缓存（默认 60s、security 组 30s）、DB 值 > 环境变量回退（FALLBACKS 注册表覆盖 35 key，与种子集合双向一致性由测试锁定）、secret 自动解密、config 写路径 commit 后逐 key invalidate。

### 业务模块读取点切换（盘点清单）

| 文件 | 旧来源 | 新 key |
|---|---|---|
| document/service.py + api.py（同源 helper 防半切换） | settings.upload_max_file_size_bytes / upload_allowed_extensions | upload.max_file_size_mb（**单位字节→MB**）/ upload.allowed_extensions |
| auth/service.py | allowed_email_domains / require_email_verification / login_max_failed_attempts / login_lock_minutes / password_min_length | security.* 对应 5 key |
| ai/service.py（parsers 保持纯同步、参数注入） | parsers 模块常量 | processing.parse_max_pages / parse_max_chars |
| review/service.py | 硬编码阻止 critical | security.block_critical_sensitive_sync（缺省 True 保守） |
| ragflow/service.py | ragflow_max_retry_count / 硬编码 critical 门禁 | ragflow.sync_max_retries / security.block_critical_sensitive_sync（与 review 侧语义一致） |
| ragflow/tasks.py + api.py | settings ragflow 连接参数 | ragflow.base_url / api_key / sync_timeout_seconds |

`security.require_review_before_sync`：经核实**无现成读取点**（唯一入队路径 approve_file 本身即审核后置位），按计划指示未造逻辑——该 key 与 processing.task_max_retries / task_timeout_seconds、upload.user_quota_mb / allow_multi_file / allow_user_delete / enable_duplicate_check、ragflow.delete_remote_on_file_delete / keep_remote_on_archive、basic 组等为 **R3/R4/R5 预埋**（落库可改可审计，消费方在对应批次接入）。

### RAGFlow 测试连接 + 前端

- `POST /api/admin/ragflow/test-connection`（仅 system_admin）：client 新增显式抛错的 `check_connection()`（替代吞异常的 ping，异常已收窄、无裸 except），返回 `{ok, latency_ms, error}`，error 经 `redact_secret` 脱敏且空 key 短路。
- Settings 页 5 组 Tabs 真实接线：按 value_type 渲染控件、secret 只写不读留空不提交、安全组保存二次确认、测试连接两态反馈；7 个组件测试。

## 2. 验证结果

| 验证项 | 结果 |
|---|---|
| 迁移往返（upgrade → downgrade -1 → upgrade，主库 + scratch 库双验） | ✅ 通过，种子 35 行 5 组 |
| 后端全量 pytest（docker，含 e2e） | ✅ **199 passed, 1 skipped**（修复后含 e2e 全链路） |
| ruff check / ruff format（触达文件） | ✅ 零错误 |
| mypy strict 全量 | ✅ 203 文件零错误 |
| 模块边界检查 | ✅ 通过（core 不反向依赖 modules、config 不跨模块 import） |
| 前端测试 / lint / build | ✅ 26/26 通过（7 文件）、零警告、构建成功 |
| 种子数据运行时验证 | ✅ system_configs 35 行 5 组 |

## 3. 评审发现处置表（三视角：安全 / 规范 / 契约）

| # | 发现（severity） | 处置 |
|---|---|---|
| 1 | e2e fixture 仍 patch 已无调用方的 build_ragflow_client，全量测试 1 失败（blocker） | ✅ 已修复：fixture 改 patch `build_ragflow_client_from_runtime_config`，死代码（tasks 包装层 + adapter 工厂）一并清除，e2e 通过 |
| 2 | 空 api_key 时 `str.replace("", "****")` 腐化错误消息（blocker） | ✅ 已修复：复用 adapter `redact_secret`（空 secret 短路），补回归测试 |
| 3 | test-connection 裸 `except Exception` 违反红线 + `except RagflowClientError` 死分支（major） | ✅ 已修复：client 增 `check_connection()` 显式抛错，端点只捕 RagflowClientError，未知异常冒泡全局 handler |
| 4 | config/api.py response 可能未绑定（mypy strict 隐患，major） | ✅ 不修：mypy strict 全量实测零错误（NoReturn 标注被正确处理），评审假设不成立 |
| 5 | 前端 ConfigItem.updated_at 缺 `\| null`（major） | ✅ 已修复 |
| 6 | int 配置无上界（极大 lock_minutes 可致登录路径 OverflowError 500）（minor） | ✅ 已修复：11 个 int key 设 min/max + 超界 400 用例 |
| 7 | security.password_min_length 半切换（落库未消费）+ labelMap 幽灵 key（minor） | ✅ 已修复：auth 接入 resolve helper（保持校验函数纯同步），labelMap 改用真实 key，补 12 位下限拒绝 8 位密码用例 |
| 8 | ragflow.sync_max_retries 落库无消费、worker 侧 critical 门禁未切换（minor） | ✅ 已修复：两处接入 runtime_config（critical 门禁非 bool 一律保守阻止） |
| 9 | mask_secret ≤4 位回显全量（minor） | ✅ 已修复：len<8 返回固定 `****` |
| 10 | 审计 target_id 误用 actor 自身 ID（minor） | ✅ 已修复：确定性 uuid5 |
| 11 | parse_pdf 二次截断语义不清（minor） | ✅ 已处理：保留外层切片为统一硬上限并加注释（页级粗截断可溢出末页） |
| 12 | 前端留空不提交导致 UI 无法清除已存 secret（minor，产品决策类） | ⚠️ 记录为已知限制：后端支持空串清除，UI"清除"按钮留待 R3/产品裁决 |
| 13 | 工作区混入 R1 遗留与 PRD 文档删除（minor，提交卫生） | ✅ 已处置：R1 幂等修复拆为独立 `fix(ai)` 提交；PRD 根文档删除与 docs/audit/ 为用户自有整理，未纳入 |

## 4. 计划偏差记录

| # | 偏差 | 理由 |
|---|---|---|
| D-1 | `security.password_policy` 实现为 `security.password_min_length` | 对齐既有 settings 字段语义，避免无结构的"策略"字符串 |
| D-2 | 种子数据在迁移与 defaults.py 双份维护 | 迁移自包含不 import 业务代码（迁移最佳实践）；双份一致性由 FALLBACKS/种子集合测试锁定 key 维度 |
| D-3 | GET 读取也写 `config.view` 审计 | 对齐 CLAUDE.md"所有管理员操作必须写 audit_logs"与 review 模块读操作先例 |
| D-4 | 单文件大小限制配置粒度从字节改为 MB（`upload.max_file_size_mb`） | 管理界面友好；service 内换算并注释 |
| D-5 | 主库 alembic_version 曾被废弃分支 `codex/frontend-design-alignment` 的同名迁移污染 | 已重置到 c7f1a2b9d6e4 并应用本迁移；**该分支若复活将与 main 的 config 实现冲突，需先 rebase** |

## 5. 遗留事项

1. **浏览器端到端走查**（计划 Task 8 Step 2）：Settings 页改"单文件最大大小"→ 60s 内上传校验生效（TTL 验证）、RAGFlow 测试连接两态、改邮箱后缀注册验证——自动化已覆盖等价逻辑（199 测试含配置生效/门禁/掩码用例），浏览器人工走查待执行。
2. UI 清除已存 secret 能力（处置表 #12）。
3. 预埋 key 的消费接入按批次推进：R3（basic 组）、R4（upload 配额/删除策略、ragflow 删除联动、task 重试参数）、R5（解析高级参数）。

## 6. 提交清单

| 提交 | 内容 |
|---|---|
| `fix(ai): 补全分析任务重复投递幂等` | R1 评审 High 的幂等修复 + 3 个回归测试（独立拆分提交） |
| `feat(config): 添加 system_configs 表与种子迁移` | 模型 + 迁移（35 项种子） |
| `feat(config): 添加 core 运行时配置读取器` | runtime_config.py + 12 测试 |
| `feat(config): 实现配置读写 API 与审计` | config 模块 9 文件 + main.py + 8 测试 |
| `refactor(config): 业务模块配置读取点切换` | document/auth/ai/review 切换 + conftest fixture + 测试修复 |
| `feat(ragflow): 添加配置化连接与测试连接端点` | check_connection + 配置消费 + e2e 修复 + 9 测试 |
| `feat(frontend): 系统设置页与 RAGFlow 配置接线` | Settings 页 + client.ts + 7 测试 |
| `docs(report): 添加 R2 批次验收报告` | 本文档 |

## 7. 结论

R2 缺陷 #3（config 模块空壳、24+ 项系统配置无落点、RAGFlow 仅环境变量配置）已修复：35 项配置落库可管理、可审计、可经 TTL 缓存被各模块消费且不破坏模块边界；评审 2 blocker + 2 major 全部闭环。R4（依赖 R2 的配额与删除策略配置）前置条件就绪；R3 可随时启动。
