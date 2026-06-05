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
- Same-transaction `review.file.approved` outbox event after approval with Dataset mapping.
- `outbox-dispatcher` auto-dispatches Dataset-backed approval events to Celery task `ragflow.create_upload_task`.
- `outbox-dispatcher` auto-dispatches `ragflow.upload` to Celery `ragflow_queue`.
- `/api/tasks` list/detail/retry/cancel endpoints.
- Audit logs for admin task list/detail/retry/cancel operations.
- Review approval with Dataset mapping moves the file to `queued`; the RAGFlow module creates the `ragflow_upload` task asynchronously from the approval event.
- `worker-ragflow` executes the Phase 4 placeholder upload task and updates task status.

External RAGFlow API calls remain a Phase 5 boundary. Phase 4 does not use, persist, log, or return the real RAGFlow server URL or API key.

## Key Implementation Notes

- `ragflow` owns task state because the fixed module list has no standalone task module and `ragflow` owns synchronization scheduling.
- `ReviewService.approve_file` only records review audit, updates the file to the final `queued` state, and appends `review.file.approved` in the same transaction.
- `outbox-dispatcher` translates Dataset-backed approval events into `ragflow.create_upload_task`; the RAGFlow module owns sync task creation and then appends `ragflow.sync_task.queued`.
- The active-task uniqueness rule is enforced by both Redis lock `lock:sync:{file_id}` and a PostgreSQL partial unique index on active `ragflow_upload` tasks.
- `outbox-dispatcher` publishes normal domain events to RabbitMQ and additionally dispatches `ragflow.sync_task.queued` as Celery task `ragflow.upload`.
- Manual retry only allows `failed` tasks whose `retry_count < max_retry_count`, takes the same Redis sync lock, checks for other active upload tasks for the file, resets the task to `queued`, writes a retry log, and appends a new queue event.
- Cancel only allows `queued` tasks.
- Worker exceptions mark the DB task `failed` and store only the exception type, not the exception message.
- Stale worker messages cannot revive terminal `failed`, `canceled`, or `succeeded` tasks.
- RAGFlow Celery async wrappers dispose the SQLAlchemy async engine before the event loop closes, avoiding prefork worker reuse of asyncpg connections bound to a closed loop.
- The Phase 4 worker placeholder only moves `queued -> running -> succeeded`; real upload, parse, and status polling are left for Phase 5.

## Verification

### Targeted Tests

Commands:

```powershell
docker compose run --rm backend-api pytest app/tests/unit/test_ragflow_task_api.py app/tests/unit/test_outbox_dispatcher.py app/tests/unit/test_review_api.py -q
```

Results:

```text
36 passed
```

Covered behaviors:

- Approval with Dataset mapping writes a final-state `review.file.approved` event.
- Dataset-backed approval events dispatch `ragflow.create_upload_task`, which creates one queued `ragflow_upload` task and one `ragflow.sync_task.queued` outbox event.
- Active task creation is idempotent.
- Redis `lock:sync:{file_id}` prevents task creation when the sync lock is held.
- Admin task list/get/retry/cancel permissions and audit logs.
- Retry appends a new queue event.
- Retry returns 409 when the sync lock is busy or another active upload task already exists for the same file.
- Worker success, worker failure redaction, and stale terminal-state protection.
- Duplicate running worker messages cannot claim or execute the same queued task twice.
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
Success: no issues found in 181 source files
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
Success: no issues found in 25 source files
```

### Full Tests

Command:

```powershell
python -m invoke test
```

Result:

```text
backend: 83 passed, 1 skipped
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
8. Waited for `outbox-dispatcher` to publish `review.file.approved` and dispatch `ragflow.create_upload_task`.
9. Verified one queued `ragflow_upload` task through `/api/tasks`.
10. Waited for `outbox-dispatcher` to publish `ragflow.sync_task.queued` and dispatch `ragflow.upload` to `worker-ragflow`.
11. Verified `worker-ragflow` updated the task to `succeeded`.
12. Verified both approval and sync-task outbox events were published with zero failed attempts.

Runtime evidence:

```json
{
  "admin_id": "42341205-8026-42cc-86a6-308152d167cf",
  "employee_id": "f47a91ea-877e-442b-aebd-691de951b230",
  "category_id": "7bce18cc-d140-454e-815f-37c805ac8ad2",
  "dataset_mapping_id": "5f62550a-be4c-478a-88a4-3340098bc82f",
  "file_id": "4eab8488-a7f7-42cb-8502-513e45ee3fc1",
  "submitted_status": "pending_review",
  "approved_status": "queued",
  "task_id": "c54c8834-805e-4e72-ada5-10ba6dee49ff",
  "created_task_status": "queued",
  "final_status": "succeeded",
  "log_statuses": ["queued", "running", "succeeded"],
  "approval_outbox_published": true,
  "approval_outbox_attempts": 0,
  "approval_event_status": "queued",
  "task_outbox_published": true,
  "task_outbox_attempts": 0,
  "db_task_status": "succeeded",
  "db_task_type": "ragflow_upload"
}
```

## Review Findings Addressed

- DB task creation now produces a dispatchable outbox event.
- Review approval no longer imports or calls RAGFlow internals; it communicates through the outbox event and Celery.
- Manual retry now produces a dispatchable outbox event.
- Manual retry now takes `lock:sync:{file_id}` and returns a 409 conflict instead of hitting the partial unique index when another active upload task exists.
- `outbox-dispatcher` now dispatches RAGFlow queue events to Celery.
- `outbox-dispatcher` now dispatches Dataset-backed review approval events to RAGFlow task creation.
- Worker exceptions now mark DB tasks failed and redact secret-bearing messages.
- Terminal tasks are not revived by stale worker messages.
- Duplicate running worker messages cannot claim the same task after `queued -> running` is already taken.
- Celery worker failure re-raises sanitized exception types only, so secret-bearing messages do not reach worker logs/result backend.
- Celery async wrappers dispose DB engine pools before closing the per-task event loop.
- Admin task operations have permission and audit coverage.
- File status moves to `queued` after approval with Dataset mapping.
- Mutable schema defaults were replaced with `Field(default_factory=list)`.
- Redis sync lock was added for active `ragflow_upload` task creation.
- Redis sync lock contention is surfaced as a 409 task validation error rather than a 500.

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
