# Phase 2 验收报告

## 阶段范围

Phase 2 完成文件上传与 MinIO 存储基础能力，包含 `files` 表、上传 API、扩展名校验、MIME 校验、大小限制、SHA256 hash、本人范围重复文件识别、员工本人文件列表、文件详情基础信息，以及前端上传/我的文件/详情页面。

## 当前分支

- 分支：`codex/phase-2-file-upload`
- 基线：`main` 已合并 Phase 1 review 修复后的版本
- 阶段提交：待创建

## 实现内容

- 新增 `files` 表 Alembic 迁移 `6d8f2a4c1e90_add_files_table.py`，字段覆盖 MinIO 定位、审核状态、AI 开关快照、RAGFlow 同步信息和 `last_sync_at`。
- 新增 document 模块模型、repository、service、schemas、exceptions、events 和 API。
- 新增 `MinioDocumentStorage` adapter，文件上传时写入 MinIO bucket；新文件写入后若数据库提交失败，会 best-effort 删除新写入对象，避免孤儿对象。
- 上传校验覆盖空文件、大小限制、扩展名白名单、MIME 白名单、PDF 基础结构、OOXML 结构、文本 UTF-8 解码和 MIME mismatch；legacy OLE 类型 `doc/xls/ppt` 暂不支持，即使误配置进白名单也会拒绝。
- 文件名清洗过滤路径分隔符、控制字符和 Windows 保留名；运行态验证 `CON.txt` 被保存为 `CON_file.txt`。
- 重复内容按当前上传者范围识别；同一用户重复上传复用首个对象，跨用户同 hash 不返回重复信息且写入独立对象。
- 员工 API 响应不暴露 `bucket`、`object_key`、`hash`、`stored_name`、`ai_config_snapshot`、`ragflow_error_message` 等内部字段。
- 上传接口按用户执行 10 次/分钟限流，读取文件前先执行 Redis 限流检查。
- 上传成功写 `document.file.uploaded` outbox 事件。
- `GET /api/files` 只返回当前登录用户自己的文件。
- `GET /api/files/{id}` 只允许当前上传者查看；访问他人文件返回 `FILE_NOT_FOUND`。
- 前端新增真实上传页、我的文件列表、文件详情基础信息页；详情页只展示业务信息和同步状态，不展示内部存储定位。
- `docker-compose.yml` 默认后端宿主端口从 `8000` 改为 `127.0.0.1:18000`，避免与本机已有 Docker 服务冲突并减少绕过 nginx 的暴露面；容器内部端口仍为 `8000`。
- Vite 本地代理默认目标改为 `http://localhost:18000`，README 健康检查地址同步更新。
- nginx 上传体积限制调整为 `50m`，与后端默认 `UPLOAD_MAX_FILE_SIZE_BYTES=52428800` 对齐。

## 验收结果

| 验收项 | 证据 | 状态 |
|---|---|---|
| 文件上传到 MinIO | 运行态上传后 DB 中 `object_key=uploads/7b88f37e-c4b4-420e-a5c0-7d83c5b0b70d/7a862179-1859-4da1-889e-cda16edcb12c/7a862179-1859-4da1-889e-cda16edcb12c-CON_file.txt`，`Minio.stat_object()` 返回同一对象 `|32` | 通过 |
| API 不暴露内部存储字段 | 运行态上传响应字段为 `id,original_name,extension,mime_type,size,uploader_id,department,category_id,dataset_mapping_id,visibility,description,tags,status,review_status,ragflow_dataset_id,ragflow_document_id,ragflow_parse_status,ai_analysis_enabled_at_upload,uploaded_at,last_sync_at,created_at,updated_at,duplicate,duplicate_file_id` | 通过 |
| 同用户重复文件可识别 | `test_duplicate_upload_is_identified_without_reuploading_object` 断言第二次上传 `duplicate=True`、`duplicate_file_id` 指向首个文件、MinIO 只写 1 个对象 | 通过 |
| 跨用户同 hash 不泄露重复信息 | `test_same_hash_from_another_user_is_not_reported_as_duplicate` 断言第二个用户 `duplicate=False`、`duplicate_file_id=None`、MinIO 写入独立对象 | 通过 |
| 员工只能查看自己的文件 | `test_employee_lists_and_views_only_own_files` 断言列表只含本人文件，访问他人文件返回 `FILE_NOT_FOUND` | 通过 |
| 扩展名白名单 | `test_upload_rejects_disallowed_extension` 覆盖 `.exe` 返回 `FILE_EXTENSION_NOT_ALLOWED` | 通过 |
| MIME mismatch | `test_upload_rejects_mime_mismatch` 覆盖 PNG 内容伪装 PDF 返回 `FILE_MIME_MISMATCH` | 通过 |
| 伪装 PDF 二进制拒绝 | `test_upload_rejects_unrecognized_binary_disguised_as_pdf` 覆盖普通二进制伪装 PDF 返回 `FILE_MIME_MISMATCH` | 通过 |
| 只有 PDF magic header 的伪装文件拒绝 | `test_upload_rejects_pdf_with_only_magic_header` 覆盖 `%PDF-` 前缀伪装文件返回 `FILE_MIME_MISMATCH` | 通过 |
| legacy OLE 类型拒绝 | `test_upload_rejects_legacy_ole_extension_even_if_configured` 覆盖 `doc` 即使误配置进白名单也返回 `FILE_EXTENSION_NOT_ALLOWED` | 通过 |
| 文件名清洗 | 运行态上传 `filename=CON.txt`，API 和 DB 均保存 `original_name=CON_file.txt` | 通过 |
| 上传限流 | `test_upload_is_rate_limited_per_user` 覆盖同一用户超过限制返回 `RATE_LIMITED` | 通过 |
| DB 提交失败清理新 MinIO 对象 | `test_upload_deletes_new_object_when_database_commit_fails` 覆盖新对象写入后提交失败会删除对象 | 通过 |
| 重复文件提交失败不删除原对象 | `test_duplicate_upload_does_not_delete_reused_object_when_commit_fails` 覆盖复用对象场景提交失败不会删除已存在对象 | 通过 |
| 前端上传/列表/详情编译 | `npm --prefix frontend run build` 通过；`python -m invoke up` 中 frontend Docker build 成功 | 通过 |
| 后端端口不占用宿主 8000 | `docker compose ps backend-api` 显示 `127.0.0.1:18000->8000/tcp` | 通过 |

## 验证命令

```text
docker compose build backend-api
docker compose run --rm backend-api pytest app/tests/unit/test_document_api.py
docker compose run --rm backend-api ruff check app
docker compose run --rm backend-api mypy app
npm --prefix frontend run lint
npm --prefix frontend run build
python -m invoke lint
python -m invoke test
python -m invoke check-arm64
python -m invoke up
docker compose exec -T backend-api alembic current
```

阶段内已验证：

- `docker compose run --rm backend-api pytest app/tests/unit/test_document_api.py` 通过：15 tests passed。
- `docker compose run --rm backend-api ruff check app` 通过：0 errors。
- `docker compose run --rm backend-api mypy app` 通过：0 errors。
- `npm --prefix frontend run lint` 通过。
- `npm --prefix frontend run build` 通过，仅 Vite chunk size warning。
- `python -m invoke lint` 通过：后端 ruff、模块边界检查、mypy、前端 ESLint 均通过。
- `python -m invoke test` 通过：后端 50 tests passed、1 skipped；前端 2 tests passed。
- `python -m invoke check-arm64` 通过：31 个直接依赖 allowlisted。
- `python -m invoke up` 通过：14 个容器全部 healthy。
- `http://localhost:18000/api/system/health` 返回 `{"status":"ok"}`。

## 迁移验证

运行中开发库完成：

```text
docker compose run --rm backend-api alembic upgrade head
docker compose run --rm backend-api alembic current
docker compose run --rm backend-api alembic downgrade b8d4c2e1f903
docker compose run --rm backend-api alembic upgrade head
docker compose exec -T backend-api alembic current
```

结果：

- 迁移可前进到 `6d8f2a4c1e90 (head)`。
- 新增 `files` 表迁移可回退到 `b8d4c2e1f903` 并再次升级。
- 运行中 `backend-api` 当前版本为 `6d8f2a4c1e90 (head)`。

## 运行态上传验收

流程：

1. 在运行中 backend 容器内创建临时 active employee。
2. 通过 `http://localhost:18000/api/auth/login` 获取 JWT。
3. 通过 `POST http://localhost:18000/api/files/upload` multipart 上传 `filename=CON.txt`。
4. 确认 API 响应不包含内部存储字段。
5. 在 backend 容器内查询 DB，读取 `object_key`。
6. 在 backend 容器内用 MinIO client 执行 `stat_object(bucket, object_key)`。

结果：

```text
user_id=7b88f37e-c4b4-420e-a5c0-7d83c5b0b70d
file_id=7a862179-1859-4da1-889e-cda16edcb12c
api_fields=id,original_name,extension,mime_type,size,uploader_id,department,category_id,dataset_mapping_id,visibility,description,tags,status,review_status,ragflow_dataset_id,ragflow_document_id,ragflow_parse_status,ai_analysis_enabled_at_upload,uploaded_at,last_sync_at,created_at,updated_at,duplicate,duplicate_file_id
original_name=CON_file.txt
stored_name=7a862179-1859-4da1-889e-cda16edcb12c-CON_file.txt
object_key=uploads/7b88f37e-c4b4-420e-a5c0-7d83c5b0b70d/7a862179-1859-4da1-889e-cda16edcb12c/7a862179-1859-4da1-889e-cda16edcb12c-CON_file.txt
hash_prefix=3585cb3d3079
minio_stat=uploads/7b88f37e-c4b4-420e-a5c0-7d83c5b0b70d/7a862179-1859-4da1-889e-cda16edcb12c/7a862179-1859-4da1-889e-cda16edcb12c-CON_file.txt|32
```

## 阶段边界状态

Phase 2 本地验收已通过。处理质量评审和安全审计反馈后创建原子提交并推送 PR。Phase 3 在 PR review gate 通过前不开始。
