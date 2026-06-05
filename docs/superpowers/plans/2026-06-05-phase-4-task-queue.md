# Phase 4 Task Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement Phase 4 task queue persistence, status querying, retry/cancel controls, and a runnable RAGFlow upload worker placeholder.

**Architecture:** `ragflow` owns `sync_tasks` because the canonical module list has no independent task module and `ragflow` owns synchronization scheduling. Review approval creates an idempotent `ragflow_upload` task in the same DB transaction; the Celery worker executes a Phase 4 placeholder that updates task state without calling the external RAGFlow API, which remains Phase 5.

**Tech Stack:** FastAPI, SQLAlchemy async ORM, Alembic, PostgreSQL partial indexes, Celery, RabbitMQ, Redis result backend, pytest/httpx.

---

### Task 1: RED Tests For Task Queue Behavior

**Files:**
- Create: `backend/app/tests/unit/test_ragflow_task_api.py`

- [ ] **Step 1: Add failing tests**

Create tests that assert:
- approving a pending file with a Dataset mapping creates exactly one active `ragflow_upload` sync task;
- approving the same file twice cannot create a duplicate active task;
- system admin can list tasks and inspect one task;
- employee cannot list tasks;
- failed tasks can be manually retried and their retry counter increments;
- the RAGFlow upload Celery task marks a task `running` and then `succeeded`;
- canceling a queued task marks it `canceled`.

- [ ] **Step 2: Verify RED**

Run:

```powershell
docker compose run --rm backend-api pytest app/tests/unit/test_ragflow_task_api.py
```

Expected: fail because `SyncTask`, task APIs, and RAGFlow worker task do not exist.

### Task 2: Ragflow Task Models And Migration

**Files:**
- Modify: `backend/app/modules/ragflow/models.py`
- Modify: `backend/app/db/models.py`
- Create: `backend/app/db/migrations/versions/<revision>_add_sync_tasks.py`

- [ ] **Step 1: Add ORM models**

Add `SyncTask` with the fields from `05_DATABASE_API_SPEC §1.5`: `id`, `file_id`, `task_type`, `status`, `retry_count`, `max_retry_count`, `error_message`, `started_at`, `finished_at`, `created_at`, `updated_at`.

Add `SyncTaskLog` for Phase 4 task logs with `id`, `task_id`, `status`, `message`, `created_at`.

- [ ] **Step 2: Add indexes and constraints**

Migration must include:
- FK `sync_tasks.file_id -> files.id` with `ondelete="CASCADE"`;
- FK `sync_task_logs.task_id -> sync_tasks.id` with `ondelete="CASCADE"`;
- indexes on `file_id`, `status`, `task_type`, and `created_at`;
- a partial unique index preventing more than one active `ragflow_upload` task per file;
- CHECK constraints for task type and status values.

- [ ] **Step 3: Verify migration**

Run:

```powershell
docker compose run --rm backend-api alembic upgrade head
docker compose run --rm backend-api alembic downgrade -1
docker compose run --rm backend-api alembic upgrade head
```

Expected: all pass and the final revision is the new Phase 4 head.

### Task 3: Ragflow Task Service And API

**Files:**
- Modify: `backend/app/modules/ragflow/api.py`
- Modify: `backend/app/modules/ragflow/exceptions.py`
- Modify: `backend/app/modules/ragflow/repository.py`
- Modify: `backend/app/modules/ragflow/schemas.py`
- Modify: `backend/app/modules/ragflow/service.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Implement repository**

Repository methods:
- `create_ragflow_upload_task(file_id, max_retry_count=3)`;
- `get_active_task(file_id, task_type)`;
- `list_tasks()`;
- `get_task(task_id)`;
- `get_task_for_update(task_id)`;
- `list_logs(task_id)`;
- `add_log(task_id, status, message)`.

- [ ] **Step 2: Implement service**

Service methods:
- `create_ragflow_upload_task(file_id)` returns an existing active task if present, otherwise creates a queued task;
- `list_tasks(current_user, context)`;
- `get_task(current_user, task_id, context)`;
- `retry_task(current_user, task_id, context)`;
- `cancel_task(current_user, task_id, context)`;
- worker helpers `mark_running`, `mark_succeeded`, `mark_failed`.

Admin read/write operations must write `audit_logs`.

- [ ] **Step 3: Implement API**

Expose:

```http
GET  /api/tasks
GET  /api/tasks/{task_id}
POST /api/tasks/{task_id}/retry
POST /api/tasks/{task_id}/cancel
```

Only `knowledge_admin` and `system_admin` can access these endpoints.

- [ ] **Step 4: Verify GREEN**

Run:

```powershell
docker compose run --rm backend-api pytest app/tests/unit/test_ragflow_task_api.py
```

Expected: tests pass.

### Task 4: Review Approval Integration And Worker Placeholder

**Files:**
- Modify: `backend/app/modules/review/service.py`
- Modify: `backend/app/modules/ragflow/tasks.py`
- Modify: `backend/app/workers/celery_app.py`
- Test: `backend/app/tests/unit/test_ragflow_task_api.py`

- [ ] **Step 1: Integrate review approval**

When `ReviewService.approve_file` receives a Dataset mapping and sets `ragflow_dataset_id`, call the `ragflow` task service in the same session to create a `ragflow_upload` sync task. Do not call external RAGFlow.

- [ ] **Step 2: Add Celery task**

Define `ragflow.upload` in `backend/app/modules/ragflow/tasks.py`. The task accepts `sync_task_id`, marks the task `running`, then marks it `succeeded` for Phase 4 worker acceptance.

- [ ] **Step 3: Load task module**

Ensure `backend/app/workers/celery_app.py` imports or autodiscovers `app.modules.ragflow.tasks`, so `worker-ragflow` can execute the task.

- [ ] **Step 4: Verify targeted tests**

Run:

```powershell
docker compose build backend-api
docker compose run --rm backend-api pytest app/tests/unit/test_ragflow_task_api.py
docker compose run --rm backend-api ruff check app/modules/ragflow app/modules/review app/tests/unit/test_ragflow_task_api.py
docker compose run --rm backend-api mypy app/modules/ragflow app/modules/review
```

Expected: all pass.

### Task 5: Phase 4 Acceptance And Report

**Files:**
- Create: `docs/phase-reports/2026-06-05-phase-4-acceptance.md`

- [ ] **Step 1: Run full verification**

Run:

```powershell
python -m invoke lint
python -m invoke test
python -m invoke check-arm64
docker compose run --rm backend-api alembic current
docker compose ps
```

- [ ] **Step 2: Runtime acceptance**

Use the running backend on `127.0.0.1:18000` to approve a file with a Dataset mapping, verify a `ragflow_upload` task exists, execute `ragflow.upload` in a worker container or direct task call, then verify the task is `succeeded`.

- [ ] **Step 3: Commit**

Use atomic commits:
- `feat(ragflow): 添加同步任务队列表`
- `feat(ragflow): 添加任务查询与重试接口`
- `feat(ragflow): 添加审核通过后的同步任务创建`
- `docs(report): 添加阶段四验收报告`

---

## Self-Review

- Spec coverage: covers Celery config loading, RabbitMQ worker route, Redis result backend already configured, `sync_tasks`, task logs, manual retry, idempotent active task control, worker-ragflow execution, and task status query.
- Boundary: real RagflowClient upload/parse/status APIs remain Phase 5.
- Placeholder scan: no TBD/TODO placeholders.
- Type consistency: task type/status names are consistent across plan, model, API, service, and tests.
