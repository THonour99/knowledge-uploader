# Phase 3 Review Dataset Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement Phase 3: categories, dataset mappings, admin file review, approve/reject, and category/dataset assignment.

**Architecture:** Keep state changes in service methods and use a shared `DocumentStateMachine` helper for file status transitions. The `review` module owns category, dataset mapping, and review workflow APIs; it writes `audit_logs` in the same database transaction without importing cross-module services or repositories.

**Tech Stack:** FastAPI, SQLAlchemy async ORM, Alembic, PostgreSQL 16, pytest/httpx, React, Ant Design, TanStack Query.

---

### Task 1: Backend Review API Tests

**Files:**
- Create: `backend/app/tests/unit/test_review_api.py`
- Read: `backend/app/tests/unit/test_document_api.py`

- [ ] **Step 1: Write failing tests**

Create tests for:
- system admin can create a category with `require_review`, `ai_analysis_enabled`, `sensitive_detection_enabled`, and `auto_sync_enabled`;
- system admin can create a dataset mapping bound to a category;
- knowledge admin can list all files through admin review endpoint;
- employee cannot access admin review endpoint;
- admin can submit a file for review, approve it with category/dataset assignment, and an audit log is written;
- admin can reject a pending file with a reason and an audit log is written.

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
docker compose build backend-api
docker compose run --rm backend-api pytest app/tests/unit/test_review_api.py
```

Expected: fail because review endpoints and tables do not exist.

### Task 2: Backend Models and Migration

**Files:**
- Modify: `backend/app/modules/review/models.py`
- Modify: `backend/app/db/models.py`
- Create: `backend/app/db/migrations/versions/<revision>_add_review_category_dataset_tables.py`

- [ ] **Step 1: Add SQLAlchemy models**

Add `Category` and `DatasetMapping` under `review.models`.

`Category` fields:
- `id`, `name`, `code`, `description`, `parent_id`, `require_review`, `default_dataset_id`, `allow_employee_select`, `allow_ai_recommend`, `default_visibility`, `keywords`, `classification_prompt`, `ai_analysis_enabled`, `sensitive_detection_enabled`, `auto_sync_enabled`, `created_at`, `updated_at`.

`DatasetMapping` fields:
- `id`, `name`, `category_id`, `ragflow_dataset_id`, `ragflow_dataset_name`, `enabled`, `created_at`, `updated_at`.

- [ ] **Step 2: Add file foreign keys**

Update the new migration to add FKs from `files.category_id` to `categories.id` and `files.dataset_mapping_id` to `dataset_mappings.id`, with indexes.

- [ ] **Step 3: Verify migration**

Run:

```powershell
python -m invoke migrate --msg="add review categories and dataset mappings"
python -m invoke migrate
docker compose exec -T backend-api alembic downgrade -1
docker compose exec -T backend-api alembic upgrade head
```

Expected: upgrade/downgrade/upgrade all pass.

### Task 3: Backend Service and Routes

**Files:**
- Create: `backend/app/core/document_state.py`
- Modify: `backend/app/core/exceptions.py`
- Modify: `backend/app/modules/review/api.py`
- Modify: `backend/app/modules/review/exceptions.py`
- Modify: `backend/app/modules/review/repository.py`
- Modify: `backend/app/modules/review/schemas.py`
- Modify: `backend/app/modules/review/service.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Implement state machine**

Create `DocumentStateMachine.transition(from_status, to_status)` with allowed Phase 3 transitions:
- `uploaded -> pending_review`
- `analyzed -> pending_review`
- `sensitive_review_required -> pending_review`
- `pending_review -> approved`
- `pending_review -> rejected`

Raise `VALIDATION_ERROR` for invalid transitions.

- [ ] **Step 2: Implement admin role guard**

Allow `knowledge_admin` and `system_admin` for review workflows. Allow only `system_admin` for category and dataset mapping writes.

- [ ] **Step 3: Implement category/dataset APIs**

Expose:
- `GET /api/categories`
- `POST /api/categories`
- `PATCH /api/categories/{id}`
- `GET /api/datasets`
- `POST /api/datasets`
- `PATCH /api/datasets/{id}`
- `DELETE /api/datasets/{id}`

- [ ] **Step 4: Implement file review APIs**

Expose:
- `GET /api/review/files`
- `POST /api/files/{id}/submit-review`
- `POST /api/files/{id}/approve`
- `POST /api/files/{id}/reject`
- `PATCH /api/files/{id}`

Approval must set `status="approved"`, `review_status="approved"`, `category_id`, `dataset_mapping_id`, and `ragflow_dataset_id` from the selected dataset mapping.

Rejection must set `status="rejected"` and `review_status="rejected"`.

- [ ] **Step 5: Write audit logs**

For submit/approve/reject/category assignment actions, insert `audit_logs` rows in the same transaction:
- action: `file.submit_review`, `file.approve`, `file.reject`, `file.update_classification`
- target_type: `file`
- target_id: file id
- reason: request reason for approve/reject when supplied

- [ ] **Step 6: Verify GREEN**

Run:

```powershell
docker compose build backend-api
docker compose run --rm backend-api pytest app/tests/unit/test_review_api.py
docker compose run --rm backend-api ruff check app
docker compose run --rm backend-api mypy app
```

Expected: all pass.

### Task 4: Frontend Review and Dataset Screens

**Files:**
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/pages/FileManagement/index.tsx`
- Modify: `frontend/src/pages/DatasetConfig/index.tsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Add API types and functions**

Add `Category`, `DatasetMapping`, `AdminFileListResponse`, `createCategory`, `listCategories`, `createDatasetMapping`, `listDatasetMappings`, `listReviewFiles`, `submitFileForReview`, `approveFile`, `rejectFile`, and `updateFileClassification`.

- [ ] **Step 2: Replace file management placeholder**

Build a table showing file name, uploader, status, review status, category, dataset, and uploaded time. Add approve/reject actions with modal forms.

- [ ] **Step 3: Replace dataset config placeholder**

Build category and dataset mapping tables with create forms.

- [ ] **Step 4: Verify frontend**

Run:

```powershell
npm --prefix frontend run lint
npm --prefix frontend run build
```

Expected: both pass.

### Task 5: Phase 3 Acceptance

**Files:**
- Create: `docs/phase-reports/2026-06-05-phase-3-acceptance.md`

- [ ] **Step 1: Run full verification**

Run:

```powershell
python -m invoke lint
python -m invoke test
python -m invoke check-arm64
python -m invoke up
docker compose exec -T backend-api alembic current
```

- [ ] **Step 2: Runtime acceptance**

Use a running backend to create an admin, create a category, create a dataset mapping, upload a file, submit it for review, approve it, and verify:
- file status is `approved`;
- review status is `approved`;
- `category_id`, `dataset_mapping_id`, and `ragflow_dataset_id` are set;
- an audit log exists.

- [ ] **Step 3: Commit and PR**

Use atomic commits:
- `feat(review): 添加分类与Dataset映射`
- `feat(review): 添加文件审核流程`
- `feat(frontend): 添加审核与Dataset管理页面`
- `docs(report): 添加阶段三验收报告`

Then push `codex/phase-3-review-dataset` and create the Phase 3 PR.

---

## Self-Review

- Spec coverage: covers categories, dataset mappings, admin file management, approve, reject, classification, dataset assignment, category AI/review flags, audit logs, and acceptance commands.
- Placeholder scan: no TBD/TODO placeholders.
- Type consistency: category and dataset naming is consistent across backend, frontend, tests, and report.
