# Phase 4 Acceptance Report - Task Queue

Date: 2026-06-05
Branch: `codex/phase-3-review-dataset`

## Scope

Phase 4 implements task queue persistence and execution control:

- `sync_tasks` table for durable task state.
- `sync_task_logs` table for task status history.
- Idempotent active `ragflow_upload` task creation per file.
- Redis distributed lock with key pattern `lock:sync:{file_id}` around RAGFlow upload task creation.
- Same-transaction `ragflow.sync_task.queued` outbox event after task creation and manual retry.
- `outbox-dispatcher` auto-dispatches `ragflow.upload` to Celery `ragflow_queue`.
- `/api/tasks` list/detail/retry/cancel endpoints.
- Audit logs for admin task list/detail/retry/cancel operations.
- Review approval with Dataset mapping moves the file to `queued` and creates a `ragflow_upload` task.
- `worker-ragflow` executes the Phase 4 placeholder upload task and updates task status.

External RAGFlow API calls remain a Phase 5 boundary. Phase 4 does not use, persist, log, or return the real RAGFlow server URL or API key.

## Key Implementation Notes

- `ragflow` owns task state because the fixed module list has no standalone task module and `ragflow` owns synchronization scheduling.
- `ReviewService.approve_file` calls `create_ragflow_upload_sync_task` before commit, so file approval, queued state, task creation, and outbox event creation stay in the same database transaction.
- The active-task uniqueness rule is enforced by both Redis lock `lock:sync:{file_id}` and a PostgreSQL partial unique index on active `ragflow_upload` tasks.
- `outbox-dispatcher` publishes normal domain events to RabbitMQ and additionally dispatches `ragflow.sync_task.queued` as Celery task `ragflow.upload`.
- Manual retry only allows `failed` tasks whose `retry_count < max_retry_count`, resets the task to `queued`, writes a retry log, and appends a new queue event.
- Cancel only allows `queued` tasks.
- Worker exceptions mark the DB task `failed` and store only the exception type, not the exception message.
- Stale worker messages cannot revive terminal `failed`, `canceled`, or `succeeded` tasks.
- The Phase 4 worker placeholder only moves `queued -> running -> succeeded`; real upload, parse, and status polling are left for Phase 5.

## Verification

### Targeted Tests

Commands:

```powershell
docker compose run --rm backend-api pytest app/tests/unit/test_ragflow_task_api.py -q
docker compose run --rm backend-api pytest app/tests/unit/test_outbox_dispatcher.py app/tests/unit/test_review_api.py -q
```

Results:

```text
14 passed
17 passed
```

Covered behaviors:

- Approval with Dataset mapping creates one queued `ragflow_upload` task and one `ragflow.sync_task.queued` outbox event.
- Active task creation is idempotent.
- Redis `lock:sync:{file_id}` prevents task creation when the sync lock is held.
- Admin task list/get/retry/cancel permissions and audit logs.
- Retry appends a new queue event.
- Worker success, worker failure redaction, and stale terminal-state protection.
- Duplicate running worker messages cannot claim or execute the same queued task twice.
- Redis lock busy approval path returns 409 instead of surfacing an internal error.
- Outbox dispatcher sends `ragflow.upload` to `ragflow_queue`.
- Review approval response and saved file status are `queued` when Dataset mapping is selected.

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
docker compose run --rm backend-api ruff check app/modules/ragflow app/modules/review app/workers/outbox_dispatcher.py app/tests/unit/test_ragflow_task_api.py app/tests/unit/test_outbox_dispatcher.py app/tests/unit/test_review_api.py
docker compose run --rm backend-api mypy app/modules/ragflow app/modules/review app/workers/outbox_dispatcher.py
```

Results:

```text
All checks passed!
Success: no issues found in 24 source files
```

### Full Tests

Command:

```powershell
python -m invoke test
```

Result:

```text
backend: 78 passed, 1 skipped
frontend: 1 test file passed, 3 tests passed
```

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
docker compose run --rm backend-api alembic current
python -m invoke migrate
```

Result:

```text
a91c4e5d7b20 (head)
```

### Services

Commands:

```powershell
python -m invoke up
python -m invoke migrate
docker compose ps
Invoke-RestMethod -Uri http://127.0.0.1:18000/api/system/health
Invoke-RestMethod -Uri http://localhost/api/system/health
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
direct backend health: {"status":"ok"}
nginx health: {"status":"ok"}
```

Note: after backend-api was recreated, nginx briefly returned 502 because it held the old upstream container IP. Recreating nginx with `docker compose up -d --force-recreate nginx` refreshed upstream DNS and both health endpoints passed.

### Browser Check

The in-app Browser was used to reload `http://localhost/datasets`.

Result:

```text
title: Knowledge Uploader
url: http://localhost/datasets
console errors: none
```

The page displayed the newly created Phase 4 runtime Dataset mapping.

## Runtime Acceptance

Runtime acceptance used only new Phase 4 test users, a new category, a new Dataset mapping, and a new uploaded text file. It did not call the external RAGFlow server and did not touch any existing RAGFlow knowledge base.

Workflow:

1. Created a system admin test user and an employee test user directly in the local app database.
2. Employee uploaded a new test text file through `/api/files/upload`.
3. Admin created a Phase 4 category through `/api/categories`.
4. Admin created a Phase 4 Dataset mapping through `/api/datasets`.
5. Admin submitted the uploaded file for review.
6. Admin approved the file with the category and Dataset mapping.
7. Verified the approved file status is `queued`.
8. Verified one queued `ragflow_upload` task through `/api/tasks`.
9. Waited for `outbox-dispatcher` to auto-dispatch `ragflow.upload` to `worker-ragflow`.
10. Verified `worker-ragflow` updated the task to `succeeded`.
11. Verified the `ragflow.sync_task.queued` outbox event was published with zero failed attempts.

Runtime evidence:

```json
{
  "admin_id": "dad2676b-bed2-44e8-9fc7-40e8561995b6",
  "employee_id": "41155398-7923-4e96-901b-d83bbce03604",
  "category_id": "22089f3b-fe24-4783-b15c-cd4534c5b9c7",
  "dataset_mapping_id": "8f444d79-2d6a-4301-aee3-3150e0d9b78c",
  "file_id": "f2882860-f741-4bcf-9f86-5baee89d7a63",
  "submitted_status": "pending_review",
  "approved_status": "queued",
  "task_id": "3333dfde-1c64-4dce-b1af-ea0094fe1a0c",
  "queued_status": "queued",
  "final_status": "succeeded",
  "log_statuses": ["queued", "running", "succeeded"],
  "outbox_published": true,
  "outbox_attempts": 0,
  "db_task_status": "succeeded",
  "db_task_type": "ragflow_upload"
}
```

## Review Findings Addressed

- DB task creation now produces a dispatchable outbox event.
- Manual retry now produces a dispatchable outbox event.
- `outbox-dispatcher` now dispatches RAGFlow queue events to Celery.
- Worker exceptions now mark DB tasks failed and redact secret-bearing messages.
- Terminal tasks are not revived by stale worker messages.
- Duplicate running worker messages cannot claim the same task after `queued -> running` is already taken.
- Celery worker failure re-raises sanitized exception types only, so secret-bearing messages do not reach worker logs/result backend.
- Admin task operations have permission and audit coverage.
- File status moves to `queued` after approval with Dataset mapping.
- Mutable schema defaults were replaced with `Field(default_factory=list)`.
- Redis sync lock was added for active `ragflow_upload` task creation.
- Redis sync lock contention is surfaced as a 409 review validation error rather than a 500.

## Acceptance Status

Phase 4 acceptance criteria are met:

- 审核通过后创建任务.
- Worker 可以执行任务.
- 失败任务可以重试.
- 任务状态可查询.
- 手动取消 queued task 可用.
- 同一文件不会创建多个 active `ragflow_upload` 任务.
- RAGFlow sync task creation uses Redis lock key `lock:sync:{file_id}`.
- Outbox dispatcher automatically dispatches queued RAGFlow tasks to Celery.
- Host port `8000` is not used by backend runtime; backend remains on `127.0.0.1:18000`.
