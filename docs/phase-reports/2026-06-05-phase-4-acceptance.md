# Phase 4 Acceptance Report - Task Queue

Date: 2026-06-05
Branch: `codex/phase-3-review-dataset`

## Scope

Phase 4 implements task queue persistence and execution control:

- `sync_tasks` table for durable task state.
- `sync_task_logs` table for task status history.
- Idempotent active `ragflow_upload` task creation per file.
- `/api/tasks` list/detail/retry/cancel endpoints.
- Audit logs for admin task list/detail/retry/cancel operations.
- Review approval creates a `ragflow_upload` task when a Dataset mapping is selected.
- `ragflow.upload` Celery task is registered and routed to `ragflow_queue`.
- `worker-ragflow` can execute the placeholder upload task and update task status.

External RAGFlow API calls remain a Phase 5 boundary. Phase 4 does not use or persist the real RAGFlow server URL or API key.

## Key Implementation Notes

- `ragflow` owns task state because the fixed module list has no standalone task module and `ragflow` owns synchronization scheduling.
- `ReviewService.approve_file` calls `create_ragflow_upload_sync_task` before commit, so file approval and task creation are in the same database transaction.
- The active-task uniqueness rule is enforced by a PostgreSQL partial unique index on `sync_tasks(file_id)` for `task_type='ragflow_upload'` and status in `queued/running`.
- Manual retry only allows `failed` tasks whose `retry_count < max_retry_count`.
- Cancel only allows `queued` tasks.
- The worker task loads `app.db.models` before writing task logs so standalone Celery workers have the full SQLAlchemy metadata graph.
- The Phase 4 worker placeholder only moves `queued -> running -> succeeded`; real upload, parse, and status polling are left for Phase 5.

## Verification

### Targeted Tests

Command:

```powershell
docker compose run --rm backend-api pytest app/tests/unit/test_ragflow_task_api.py
```

Result:

```text
7 passed
```

Review regression:

```powershell
docker compose run --rm backend-api pytest app/tests/unit/test_review_api.py
```

Result:

```text
11 passed
```

### Lint And Types

Command:

```powershell
python -m invoke lint
```

Result:

```text
All checks passed!
Module boundary check passed.
Success: no issues found in 180 source files
frontend eslint passed
```

Targeted checks also passed:

```powershell
docker compose run --rm backend-api ruff check app/modules/ragflow app/modules/review app/tests/unit/test_ragflow_task_api.py
docker compose run --rm backend-api mypy app/modules/ragflow app/modules/review
```

Results:

```text
All checks passed!
Success: no issues found in 23 source files
```

### Full Tests

Command:

```powershell
python -m invoke test
```

Result:

```text
backend: 68 passed, 1 skipped
frontend: 1 test file passed, 3 tests passed
```

Environment note: the managed sandbox blocks Node `child_process.spawn` with `EPERM`, so the full test command was run with approved escalation for the frontend Vitest/esbuild subprocess.

### ARM64 Dependency Check

Command:

```powershell
python -m invoke check-arm64
```

Result:

```text
All 31 checked dependencies are ARM64 allowlisted.
```

No Python dependencies were added in Phase 4.

### Migration

Commands:

```powershell
docker compose run --rm backend-api alembic upgrade head
docker compose run --rm backend-api alembic downgrade -1
docker compose run --rm backend-api alembic upgrade head
docker compose run --rm backend-api alembic current
```

Result:

```text
a91c4e5d7b20 (head)
```

### Services

Command:

```powershell
python -m invoke up
python -m invoke migrate
docker compose ps
```

Result:

```text
backend-api healthy: 127.0.0.1:18000->8000/tcp
frontend healthy
nginx healthy
postgres healthy
rabbitmq healthy
redis healthy
minio healthy
outbox-dispatcher healthy
scheduler healthy
worker-document healthy
worker-ai healthy
worker-ragflow healthy
worker-statistics healthy
worker-notification healthy
```

Health endpoints:

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:18000/api/system/health
Invoke-RestMethod -Uri http://localhost/api/system/health
```

Result:

```json
{"status":"ok"}
```

## Runtime Acceptance

Runtime acceptance used only new Phase 4 test users, a new category, a new Dataset mapping, and a new uploaded text file. It did not call the external RAGFlow server.

Workflow:

1. Created a system admin test user and an employee test user.
2. Created a Phase 4 category through `/api/categories`.
3. Created a Phase 4 Dataset mapping through `/api/datasets`.
4. Uploaded a test text file through `/api/files/upload`.
5. Submitted the file for review.
6. Approved the file with the category and Dataset mapping.
7. Verified one queued `ragflow_upload` task through `/api/tasks`.
8. Sent `ragflow.upload` to Celery.
9. Verified `worker-ragflow` updated the task to `succeeded`.

Runtime evidence:

```json
{
  "admin_id": "8b81673c-e030-487b-9b5a-3dfc165aa50c",
  "employee_id": "04658893-6de8-4e78-860d-86ae6186bade",
  "category_id": "d2d48e11-a35c-4852-87bb-755f82c756c5",
  "dataset_mapping_id": "bb95ac12-d544-41be-8b20-a30947330a10",
  "file_id": "78cd5326-703a-4935-a83b-795d118084ad",
  "task_id": "1ddfd0ef-f639-44ea-9a06-7f297eaa0cc6",
  "queued_status": "queued",
  "final_status": "succeeded",
  "log_statuses": ["queued", "running", "succeeded"],
  "celery_task_id": "f9bae697-ddb3-4bfc-877e-0098286f999c"
}
```

## Acceptance Status

Phase 4 acceptance criteria are met:

- 审核通过后创建任务.
- Worker 可以执行任务.
- 失败任务可以重试.
- 任务状态可查询.
- 手动取消 queued task 可用.
- 同一文件不会创建多个 active `ragflow_upload` 任务.
- Host port `8000` is not used by backend runtime; backend remains on `127.0.0.1:18000`.
