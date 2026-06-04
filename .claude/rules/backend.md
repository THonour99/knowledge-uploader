---
description: 后端 Python / FastAPI / SQLAlchemy 代码规则
paths:
  - backend/**
---

# 后端代码规则

## 1. 技术栈版本（已锁，详见补充 spec §6.2）

- Python 3.11
- FastAPI 0.110+
- SQLAlchemy 2.0 async + asyncpg + psycopg[binary] v3
- Pydantic 2.7+ + pydantic-settings 2.2+
- Celery 5.3+
- Redis 5.0+
- argon2-cffi、PyJWT、cryptography、structlog

**禁用清单**：`psycopg2*`、`python-magic*`、`mysqlclient`、`pycrypto`、`m2crypto`

## 2. 分层（强制）

```
api.py (FastAPI Router)
   ↓
service.py (业务编排 + 事务边界)
   ↓
repository.py (数据访问，无业务逻辑)
   ↓
models.py (SQLAlchemy ORM)
```

- ❌ Router 直接读写 ORM
- ❌ Service 跨模块导入其他模块的 service / repository
- ❌ Repository 包含业务逻辑（比如计算、决策）
- ✅ 跨模块通信只走：事件总线、Celery task、共享 schemas

## 3. 异步要求

- 所有 API route 必须 `async def`
- 所有 Service 方法默认 `async def`
- DB 操作用 `AsyncSession`
- HTTP 调用用 `httpx.AsyncClient`
- ❌ 禁止在 async 函数中使用 `requests`、`sync` DB session、阻塞 `sleep`

## 4. Pydantic Schema 约定

- 请求 schema 命名 `XxxCreate` / `XxxUpdate` / `XxxFilter`
- 响应 schema 命名 `XxxResponse` / `XxxDetail`
- 内部共享 DTO 命名 `XxxRef`（可跨模块导入）
- 不直接返回 ORM model，必须经 schema 序列化
- 所有 schema 必须有 `model_config` 或继承基类

## 5. 类型与签名

- 所有 public 函数 / 方法 / dependency 必须有完整类型注解
- 返回 `None` 必须显式写 `-> None`
- `Optional[X]` 用 `X | None`（Python 3.10+ 联合类型语法）
- 通用容器：`list[X]`、`dict[str, X]`，不用 `List` / `Dict`

## 6. 异常与错误码

- 业务异常继承 `app.core.exceptions.AppException`
- 错误码用枚举 `ErrorCode`，定义在 `app/core/exceptions.py`
- 不要 `except Exception:`，最多 `except (KnownError1, KnownError2):`
- 错误响应统一格式：`{"success": false, "error_code": "...", "message": "...", "request_id": "..."}`
- 不在异常消息暴露：路径 / 堆栈 / API Key / 用户敏感数据

## 7. 日志

- 用 `structlog.get_logger(__name__)`，**禁止 `print` 或 `logging.getLogger`**
- 日志必须结构化：`logger.info("file_uploaded", file_id=..., size=...)`
- ❌ 禁止打印任何 `api_key`、`password`、`token`、`secret`、`smtp_password`
- 敏感字段命名必须命中脱敏过滤器（统一在 `core/logging.py` 配置）

## 8. 数据库事务

- 一个 HTTP 请求 = 一个事务（通过依赖 `get_session`）
- Celery task 必须显式 `async with session.begin()`
- 写 outbox 事件必须和业务表写入**同一事务**
- 不要在事务内调用外部 HTTP（外部调用必须在事务后或 Celery task 内）

## 9. 路径与跨平台

- 路径用 `pathlib.Path`，**禁止 `os.path.join` 字符串拼接**
- 文件读写显式 `encoding="utf-8"`
- 文件名清洗用 `app/utils/filename.py::sanitize_filename`
- 临时文件用 `tempfile`，不要写死 `/tmp/`

## 10. Celery Task

- 任务必须幂等（重复执行结果一致）
- 同一文件不能同时存在多个同步任务（用 Redis 分布式锁，key 格式 `lock:sync:{file_id}`，TTL ≥ 任务超时）
- `max_retries` 至少 3 次，配合指数退避
- 失败超 max_retries → 进入死信队列
- ❌ Celery task 内不能直接 update ORM，必须走 service

## 11. 引入新依赖检查表

新增 / 更新 `backend/requirements.txt` 后：

```powershell
invoke check-arm64
```

如脚本报警包无 ARM64 wheel → 必须替换。

## 12. 常用结构示例

```python
# api.py
@router.post("/files", response_model=FileResponse, status_code=201)
async def upload_file(
    payload: FileCreate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_role(Roles.EMPLOYEE)),
) -> FileResponse:
    return await files_service.upload(session, payload, uploader=current_user)


# service.py
async def upload(session: AsyncSession, payload: FileCreate, *, uploader: User) -> FileResponse:
    async with session.begin():
        file = await files_repository.create(session, ...)
        await outbox_repository.append(
            session,
            event_type="files.file.uploaded",
            payload={"file_id": str(file.id), ...},
        )
    return FileResponse.model_validate(file)


# repository.py
async def create(session: AsyncSession, **kwargs) -> File:
    file = File(**kwargs)
    session.add(file)
    await session.flush()
    return file
```
