# Phase 3 Acceptance Report - Review and Dataset

Date: 2026-06-05
Branch: `codex/phase-3-review-dataset`

## Scope

Phase 3 implements the review and Dataset management workflow:

- `categories` table and SQLAlchemy model.
- `dataset_mappings` table and SQLAlchemy model.
- `files.category_id` and `files.dataset_mapping_id` foreign keys.
- Admin review APIs for file listing, submit-review, approve, reject, and classification updates.
- Category and Dataset mapping APIs with system-admin write permissions.
- Audit logs for file review, category/Dataset configuration, and admin read/list operations.
- Review domain outbox events for submit, approve, and reject.
- Frontend Dataset configuration page based on `docs/design/images/08_dataset_config.png`.
- Frontend file management and review page based on `docs/design/images/06_file_management.png`.

Backend runtime is exposed on host `127.0.0.1:18000`, mapped to container port `8000`, avoiding host port `8000`.

## Key Implementation Notes

- File status changes use `DocumentStateMachine.transition`.
- `knowledge_admin` and `system_admin` can run review workflows.
- Only `system_admin` can create/update category and Dataset mapping configuration.
- Employees cannot access review configuration list or mutation endpoints.
- Category PATCH honors explicit nullable clears through `model_fields_set`.
- Approval requires the selected Dataset mapping to belong to the selected category and be enabled.
- Review module avoids direct cross-module `models/repository/service` imports; file rows are accessed through a local typed record and SQLAlchemy Core table metadata.
- Admin audit logging is routed through `app.core.audit.record_admin_audit_log`.
- Review workflow events are persisted through `app.core.outbox.OutboxRepository` in the same transaction.
- Disabled Dataset mappings remain manageable by system admins, but cannot be used for file approval/classification.

## Verification

### Migration

Command:

```powershell
docker compose run --rm backend-api alembic current
```

Result:

```text
3f9a1c7d2b84 (head)
```

Migration upgrade/downgrade was also validated during implementation:

```powershell
docker compose run --rm backend-api alembic upgrade head
docker compose run --rm backend-api alembic downgrade -1
docker compose run --rm backend-api alembic upgrade head
```

### Lint

Command:

```powershell
python -m invoke lint
```

Result:

```text
All checks passed!
Module boundary check passed.
Success: no issues found in 178 source files
frontend eslint passed
```

### Tests

Command:

```powershell
python -m invoke test
```

Result:

```text
backend: 61 passed, 1 skipped
frontend: 1 test file passed, 3 tests passed
```

Targeted Phase 3 backend test:

```powershell
docker compose run --rm backend-api pytest app/tests/unit/test_review_api.py
```

Result:

```text
11 passed
```

Targeted frontend component test:

```powershell
npm test -- --run
```

Result:

```text
1 test file passed, 3 tests passed
```

Environment note: the managed sandbox blocks Node `child_process.spawn` with `EPERM`, which prevents esbuild/Vitest subprocess startup inside the sandbox. The frontend test and build commands were rerun with approved escalation and passed.

### ARM64 Dependency Check

Command:

```powershell
python -m invoke check-arm64
```

Result:

```text
All 31 checked dependencies are ARM64 allowlisted.
```

### Frontend Build

Command:

```powershell
npm run build
```

Result:

```text
vite build passed
```

Note: Vite emitted the existing large chunk warning for the bundled application JS. This is not a build failure.

### Services

Command:

```powershell
python -m invoke up
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

Health endpoint:

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:18000/api/system/health
```

Result:

```json
{"status":"ok"}
```

## Runtime Acceptance

Runtime acceptance was executed against `http://127.0.0.1:18000`.

Setup:

- Registered one system admin test user.
- Registered one knowledge admin test user.
- Registered one employee uploader test user.
- Activated only those test users and assigned roles through PostgreSQL.

HTTP workflow:

1. System admin created category.
2. System admin created Dataset mapping.
3. Employee uploaded a text file through `/api/files/upload`.
4. Knowledge admin submitted the file for review.
5. Knowledge admin approved the file with category and Dataset mapping.
6. PostgreSQL query verified file state and audit logs.

Runtime evidence:

```json
{
  "backend_url": "http://127.0.0.1:18000",
  "category_id": "2fbc9e93-5c7a-4b93-bf3b-e996215e2da0",
  "dataset_mapping_id": "02566085-610a-4d9f-9f8c-1d29bb943cf4",
  "file_id": "4593d9fa-41b4-4049-94e8-ab38a89773ab",
  "submit_status": "pending_review",
  "approved_status": "approved",
  "approved_review_status": "approved",
  "ragflow_dataset_id": "ragflow-phase3-20260605151338",
  "db_check": "approved,approved,ragflow-phase3-20260605151338,2"
}
```

The final `db_check` fields are:

```text
status,review_status,ragflow_dataset_id,review_audit_log_count
```

## Browser Visual Acceptance

The in-app Browser was used against `http://localhost/files` and `http://localhost/datasets`.
Frontend implementation was visually checked against:

- `docs/design/images/06_file_management.png`
- `docs/design/images/08_dataset_config.png`

Verified `/files`:

- Metric cards: `待审核`, `高风险文件`, `同步失败`, `今日新增`.
- Reference filter structure: search, uploader, category, review status, sync status, risk level, uploaded date range.
- Reference table columns: file name, uploader, department, category, size, review status, sync status, risk, upload time, actions.
- File visual details: file type icon, star marker, uploader avatar, compact row actions.
- `pending_review` renders as `待审核`, not `审核中`.
- No dead admin detail links to `/files/:id`.
- Top header includes the reference search placeholder, notification badge, and Chinese role display.
- Browser metrics: 8 rows, 4 metric cards, 8 file icons, 8 uploader avatars, no horizontal document overflow.

Verified `/datasets`:

- Metric cards: `已配置分类数`, `启用映射数`, `待完善映射`, `禁用映射数`.
- Reference action area in table panel: `新增分类`, `批量操作`, `新增映射`, refresh.
- Reference filter structure: search, status, review-required, employee-select.
- Reference table columns: category name, category code, target Dataset, review-required, default visibility, employee-select, status, actions.
- Dataset target uses compact pill with link icon.
- Dataset status display uses shared `StatusTag` with dot variant.
- Browser metrics: 5 rows, 4 metric cards, 5 status dots, Dataset pill/link actions present, no horizontal document overflow.

Design consistency was reviewed by a `quality-reviewer` subagent. Its blocking and high-impact findings were addressed before final browser verification.

## Acceptance Status

Phase 3 acceptance criteria are met:

- Admin can review files.
- Approved files reach correct file and review statuses.
- Category can bind to a RAGFlow Dataset through `dataset_mappings`.
- Review and configuration admin operations write audit logs.
- Review workflow writes same-transaction outbox events.
- Host port `8000` is not used by backend runtime; backend acceptance used `127.0.0.1:18000`.
