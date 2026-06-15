# 测试与验收

阶段 9 验收目标：

- 新开发者按 README 能启动项目。
- 主要流程有测试覆盖。
- 生产部署参数清晰。

## 命令

完整验收命令：

```powershell
docker compose run --rm backend-api ruff check app
python scripts/check_module_boundaries.py
docker compose run --rm backend-api mypy app
docker compose run --rm backend-api pytest -q
npm --prefix frontend test -- --run
npm --prefix frontend run lint
npm --prefix frontend run build
docker compose up -d --build
docker compose exec backend-api alembic upgrade head
curl http://localhost:18000/api/system/health
```

常用封装：

```powershell
invoke lint
invoke test
invoke up
invoke migrate
```

## 覆盖矩阵

| 主流程 | 覆盖文件 | 覆盖点 |
|---|---|---|
| 健康检查 | `backend/app/tests/unit/test_health.py` | `/api/system/health` 返回 200 |
| 模块边界 | `backend/app/tests/unit/test_module_boundaries.py`, `scripts/check_module_boundaries.py` | 禁止跨模块 service/repository import |
| 注册与邮箱验证 | `backend/app/tests/unit/test_auth_api.py`, `test_auth_models.py` | 域名限制、验证 outbox、token hash、重复邮箱泛化响应 |
| 登录与会话 | `backend/app/tests/unit/test_auth_api.py` | JWT、`/me`、注销、锁定、失败审计、限流 |
| 文件上传 | `backend/app/tests/unit/test_document_api.py` | MinIO 写入、去重、扩展名/MIME 校验、大小限制、保留名清洗、上传审计 |
| 事件分发 | `backend/app/tests/unit/test_outbox_dispatcher.py` | outbox 投递、失败重试、敏感异常脱敏、AI/RAGFlow task 派发 |
| 分类与 Dataset | `backend/app/tests/unit/test_review_api.py` | 分类 CRUD、Dataset 映射、权限、审计 |
| 审核流程 | `backend/app/tests/unit/test_review_api.py` | 提交审核、通过、驳回、状态机、敏感阻断、分析失败策略 |
| RAGFlow 同步 | `backend/app/tests/unit/test_ragflow_task_api.py`, `test_ragflow_client.py` | 任务创建、Redis 锁、重试/取消、上传解析、allowlist、API Key 脱敏 |
| AI 配置与分析 | `backend/app/tests/unit/test_ai_api.py`, `test_ai_tasks.py` | Provider 加密、功能开关、外部模型限制、默认分析、敏感检测、失败状态 |
| 上传到同步全链路 | `backend/app/tests/e2e/test_full_pipeline.py` | 上传、AI mock 分析、提交审核、审核通过、outbox 派发、RAGFlow mock 上传解析 |
| 统计分析 | `backend/app/tests/unit/test_statistics_api.py` | 总览、用户、部门、分类、趋势、失败统计、CSV 转义、权限 |
| 用户管理 | `backend/app/tests/unit/test_user_admin_api.py` | 列表、详情、禁用、启用、自禁用保护、审计 |
| 日志脱敏 | `backend/app/tests/unit/test_logging.py` | RAGFlow Key、Provider Key、Bearer token、敏感字段递归脱敏 |
| 前端状态标签 | `frontend/src/components/StatusTag.test.tsx` | 文件、审核、RAGFlow 状态展示 |
| 前端 AI 配置 | `frontend/src/pages/AiConfig/index.test.tsx` | 配置展示、Provider 操作、权限页面基本行为 |
| 前端统计 | `frontend/src/pages/Statistics/index.test.tsx` | 指标、图表、筛选、导出入口 |

## 浏览器验收

前端改动或阶段验收时使用 Codex 浏览器检查：

- `http://localhost/login`
- `http://localhost/upload`
- `http://localhost/datasets`
- `http://localhost/ai-config`
- `http://localhost/statistics`
- `http://localhost/settings`

检查点：

- 页面能加载，无空白页。
- 控制台无运行时错误。
- 文案、布局、状态标签与 `docs/design/images/` 参考图一致。
- 桌面和移动视口不出现不可接受的横向溢出。

### 自动化浏览器验收脚本

新增轻量脚本：

```powershell
npm --prefix frontend run e2e:acceptance
```

脚本位置：`frontend/e2e/acceptance.mjs`。

默认访问 `http://127.0.0.1:5173`，可通过环境变量覆盖：

```powershell
$env:E2E_BASE_URL="http://127.0.0.1:4173"
npm --prefix frontend run e2e:acceptance
```

脚本使用 Playwright 的 Chromium 运行真实浏览器，并 mock 前端所需 API：

- `/upload`：验证上传页加载、上传提交审核开关、AI 分析开关。
- `/files/file-e2e`：验证文件详情质量评分、相似文档、表格预览、过期指标。

本次没有把 `@playwright/test` 作为依赖写入 `package.json` / `package-lock.json`，避免在未授权更新锁文件的情况下新增依赖。需要执行浏览器脚本时先安装本地运行时：

```powershell
npm --prefix frontend install --save-dev @playwright/test
npx playwright install chromium
```

## 测试数据原则

- RAGFlow 联调只创建新的测试 Dataset 和测试文档。
- 不删除、不修改既有 RAGFlow 知识库。
- 测试 API Key 只写入 `.env` 或后端配置，不写入前端、日志、提交或报告。
- CI 测试不依赖外网，RAGFlow 和 LLM 均使用 mock 或本地替身。
