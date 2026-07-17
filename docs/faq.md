# 常见问题

## 为什么后端宿主机端口是 18000，不是 8000？

容器内后端仍监听 `8000`，但宿主机默认映射为 `127.0.0.1:18000`，避免与当前机器上已有 Docker 服务的 `8000` 端口冲突。访问健康检查使用：

```powershell
curl http://localhost:18000/api/system/health
```

## Nginx 的 80 端口被占用怎么办？

在 `.env` 中改：

```env
NGINX_HTTP_PORT=8080
```

然后重启：

```powershell
docker compose up -d --build
```

前端入口变为 `http://localhost:8080`。

## `.env` 中的密钥可以用示例值吗？

只能在本地开发短期使用。`APP_ENV=production`、`prod` 或 `staging` 时，后端会拒绝占位 `JWT_SECRET`、默认 `ENCRYPTION_KEY` 和 `MINIO_SECURE=false`。

生成 Fernet key：

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## 启动后接口返回迁移错误怎么办？

执行数据库迁移：

```powershell
docker compose exec backend-api alembic upgrade head
```

如果容器未启动，先运行：

```powershell
docker compose up -d --build
```

## 上传文件失败怎么排查？

检查：

- 文件扩展名是否在 `UPLOAD_ALLOWED_EXTENSIONS`。
- MIME 是否在 `UPLOAD_ALLOWED_MIME_TYPES`。
- 文件大小是否超过 `UPLOAD_MAX_FILE_SIZE_BYTES`。
- MinIO 是否 healthy。
- Nginx `client_max_body_size` 是否小于上传限制。
- 当前用户是否触发 `UPLOAD_RATE_LIMIT_PER_MINUTE`。

查看日志：

```powershell
invoke logs --service=backend-api
invoke logs --service=minio
```

## RAGFlow API Key 配了之后启动失败怎么办？

`RAGFLOW_API_KEY` 非空时必须同时配置：

```env
RAGFLOW_ALLOWED_DATASET_IDS=<允许同步的测试 Dataset id>
```

这是防止误同步到既有知识库的硬约束。

## 如何联调 `http://192.168.4.46:8092`？

在 RAGFlow 中新建测试 Dataset，记录 Dataset id，然后配置：

```env
RAGFLOW_BASE_URL=http://192.168.4.46:8092
RAGFLOW_API_KEY=<后端环境变量>
RAGFLOW_ALLOWED_DATASET_IDS=<新建测试 Dataset id>
```

重启后只在 `/datasets` 创建这个测试 Dataset 的映射。不要删除或修改 RAGFlow 服务器上的既有知识库。

`DEFAULT_DATASET_ID` 已删除。每次同步都必须使用审核决定中明确选择、且已在 `/datasets`
启用并进入 `RAGFLOW_ALLOWED_DATASET_IDS` allowlist 的 Dataset 映射；不存在隐式默认目标。

## 首个系统管理员怎么创建？

先完成迁移，然后在后端容器内执行 seed 脚本：

```powershell
$env:SEED_ADMIN_PASSWORD="<至少 8 位的初始密码>"
docker compose exec -e SEED_ADMIN_PASSWORD backend-api python scripts/seed_admin.py --email admin@company.com --name "System Admin"
Remove-Item Env:\SEED_ADMIN_PASSWORD
```

脚本默认只允许首次 bootstrap；系统内已存在 `system_admin` 时会拒绝执行。仅在明确恢复既有 `system_admin` 账号时追加 `--force-existing-system-admin`，脚本会重置目标账号并写 `user.seed_system_admin` 审计日志。共享环境初始化后应立即登录修改密码。

## 为什么普通用户访问 `/datasets` 或 `/ai-config` 会跳转？

这些页面需要管理员角色：

- `/datasets`、`/ai-config`、`/users` 需要 `system_admin`；`/settings` 当前是系统设置占位页，也受 `system_admin` 前端路由保护。
- `/dashboard`、`/statistics` 需要 `system_admin`；`/files` 需要 `dept_admin` 或 `system_admin`，部门管理员按管辖部门隔离。
- `/upload`、`/my-files` 对登录用户开放。

后端也有对应权限校验，前端路由保护不是唯一防线。

## AI 功能没有配置模型也能启动吗？

可以。默认 `LLM_PROVIDER=disabled`，文档分析仍可使用本地规则完成摘要、分类建议、标签和敏感检测的基础逻辑。需要真实模型时，在系统管理员确认后配置 Provider，并保证 API Key 只进入后端环境或加密存储。

未启用 Provider 或没有可用 Provider 时，系统不会发起模型网络请求，而是使用确定性规则分析。只要已经选中启用的 Provider，配置无效、地址不在精确 allowlist、环境策略拒绝或调用失败都会安全失败并留下失败分类，不会静默伪装成规则分析成功。详情页会如实展示本次使用的分析引擎。

真实模型分析采用版本化 Prompt 和严格 JSON 输出契约。本地敏感检测仍是最终权威：模型输出只能提高风险等级，不能覆盖或降低本地规则结果。系统只保存分析所需的审计元数据（Provider/模型、Prompt 版本、输入摘要哈希与字符数、候选分类数量、截断标记、Token、耗时、失败分类和预估成本），不保存或记录完整 Prompt、文档正文、模型原始响应或 API Key。

分析引擎含义如下：

- “确定性规则”：本次没有调用模型。
- “LLM”：只执行了模型能力。
- “规则 + LLM”：模型结果与本地权威规则共同生成结果。

Provider 的输入/输出价格在界面中按“计价币种 / 百万 Token”填写，服务端以整数微货币单位保存；详情中的金额是依据本次 Token 用量计算的预估值，不等同于供应商账单。`mock` Provider 只允许本地测试，`staging` 和 `production` 会拒绝使用。无外网测试使用进程内模拟传输验证协议、错误分类、限流和超大响应保护，不代表真实供应商连通性已经验收。

## 为什么 AI 配置中没有 OCR 或 Embedding 开关？

当前生产分析链没有 OCR、向量 Embedding 或视觉模型消费者，因此不暴露会造成错误预期的开关、环境变量或 Provider 字段。扫描版 PDF、图片文字识别和向量化能力必须在完成真实任务链、失败处理、指标与验收后再重新引入；现阶段请先将扫描件转换为可提取文本的受支持文档。过去只做连通测试的 Embedding/视觉表面能力已经删除，未来必须随真实任务链一起实现。


## 前端构建出现依赖或缓存问题怎么办？

先确认 Node.js 版本为 20 或以上：

```powershell
node --version
```

再执行：

```powershell
npm --prefix frontend install
npm --prefix frontend run build
```

如果 Windows 上出现临时文件占用，关闭正在运行的 Vite 或测试进程后重试。

## 如何确认 API Key 没有泄露？

运行：

```powershell
docker compose run --rm backend-api pytest -q app/tests/unit/test_logging.py
docker compose run --rm backend-api pytest -q app/tests/unit/test_ragflow_client.py
```

审查日志时只允许看到脱敏值，例如 `sk-****abcd` 或 `ragflow-****abcd`。
