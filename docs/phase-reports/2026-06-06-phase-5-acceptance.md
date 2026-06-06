# Phase 5 Acceptance Report: RAGFlow Integration

Date: 2026-06-06

## Scope

Phase 5 implements the real RAGFlow sync boundary behind the Phase 4 task queue:

- `RagflowClient` supports upload, parse start, status lookup, metadata update, and delete.
- `HttpRagflowClient` wraps the RAGFlow HTTP API and redacts the configured API key from raised errors.
- `worker-ragflow` reads approved files from MinIO, uploads them to the mapped Dataset, writes metadata, starts parsing, queries parse status, and records sync logs.
- Existing `ragflow_document_id` retries query RAGFlow first and do not re-upload duplicate files.
- Existing documents with `UNSTART` parse status update metadata, restart parsing, and query status again.
- Non-terminal parse states such as `RUNNING`, `UNSTART`, and `UNKNOWN` do not mark a task as succeeded; they fail the task with `RagflowParsePendingError` so retry can continue while the file remains `parsing`.
- RAGFlow status responses must contain the exact requested `document_id`; missing or mismatched documents are rejected.
- File state transitions now cover `queued -> syncing -> uploaded_to_ragflow -> parsing -> parsed` and sync failure/retry paths.
- Nginx now uses Docker DNS dynamic resolution so backend container replacement does not leave `/api/*` pointing at a stale IP.

## Configuration

The following RAGFlow settings are defined consistently in `Settings`, `docker-compose.yml`, `.env.example`, and `knowledge_uploader_docs/07_DEPLOYMENT_ENV_部署与环境配置.md`:

```env
RAGFLOW_BASE_URL=http://ragflow:9380
RAGFLOW_API_KEY=
RAGFLOW_ALLOWED_DATASET_IDS=
DEFAULT_DATASET_ID=
RAGFLOW_REQUEST_TIMEOUT=300
RAGFLOW_MAX_RETRY_COUNT=3
```

`RAGFLOW_ALLOWED_DATASET_IDS` is the write boundary. If `RAGFLOW_API_KEY` is configured in any environment, the allowlist must also be configured. Workers reject writes to mapped Dataset ids outside the allowlist before reading MinIO or calling RAGFlow.

## Safety Notes

- RAGFlow API keys stay backend-only and are never returned to the frontend.
- Worker failures store only exception type names, preserving secret-safe failure behavior.
- Sync requires an approved file, an enabled Dataset mapping, matching `ragflow_dataset_id`, and an allowlisted Dataset when the allowlist is set.
- `RAGFLOW_ALLOWED_DATASET_IDS` validation uses normalized non-empty values, so comma-only values such as `,` cannot disable the write boundary.
- The RAGFlow repository updates the `files` table through a local sync record and service-level state-machine methods; it does not import cross-module service or repository objects.
- RAGFlow delete is implemented at the client boundary for Phase 5 capability, but no admin delete API was added in this phase because the acceptance criteria focus on sync, document id, parse status, and retry.

## Verification

### Unit And Integration Tests

```text
docker compose run --rm backend-api pytest app/tests/unit/test_config.py app/tests/unit/test_ragflow_client.py app/tests/unit/test_ragflow_task_api.py -q
34 passed

python -m invoke test
backend: 96 passed, 1 skipped
frontend: 3 passed
```

Covered behavior:

- HTTP adapter calls the upload, metadata, parse, status, and delete endpoints with expected paths and bodies.
- API key redaction is enforced in adapter errors.
- Worker uploads MinIO bytes to RAGFlow, saves `ragflow_document_id`, starts parsing, records `ragflow_parse_status`, and completes only when parse status is terminal success.
- Worker retry with existing `ragflow_document_id` does not read MinIO and does not upload a duplicate document.
- Existing uploaded documents with `UNSTART` parse status are parsed instead of incorrectly being treated as complete.
- Non-terminal parse status leaves the file in `parsing` and fails the task as retryable.
- Worker rejects unapproved files and Dataset ids outside `RAGFLOW_ALLOWED_DATASET_IDS` before external calls.
- `RAGFLOW_MAX_RETRY_COUNT` controls newly created sync task retry budget.
- Comma-only allowlist values fail configuration validation.
- RAGFlow status responses with a mismatched `document_id` are rejected.

### Quality Gates

```text
python -m invoke lint
ruff: passed
module boundary check: passed
mypy: 184 source files passed
frontend eslint: passed

python -m invoke check-arm64
All 31 checked dependencies are ARM64 allowlisted.
```

### Runtime Health

```text
python -m invoke up
docker compose ps
all services healthy

GET http://localhost:18000/api/system/health
{"status":"ok"}

GET http://localhost/api/system/health
{"status":"ok"}

docker compose exec nginx nginx -t
syntax is ok
configuration file test is successful
```

Backend API is bound to `127.0.0.1:18000->8000/tcp`, avoiding host port 8000 conflict.

### Browser Verification

Codex Browser was used against `http://localhost/datasets`.

Evidence:

- Title: `Knowledge Uploader`
- URL: `http://localhost/datasets`
- Console errors: `[]`
- Visible page includes Dataset configuration and RAGFlow mapping content.
- Screenshot captured through the in-app Browser.

### External RAGFlow Safety Check

The provided RAGFlow server was checked only for safe connectivity:

- No real API key was written to files or commands.
- No existing knowledge base was modified or deleted.
- A no-secret connectivity request reached the server and returned HTTP 200.
- Attempting to create a new test Dataset with a dummy token returned RAGFlow business code `109`; no Dataset id was returned.

Authenticated external upload was not run because the API key was provided in chat but not available as a secure runtime environment variable. To run real external acceptance later, configure `RAGFLOW_BASE_URL`, `RAGFLOW_API_KEY`, and `RAGFLOW_ALLOWED_DATASET_IDS` outside the repo and use only a newly-created test Dataset id in the allowlist.

## Files Changed

- `.env.example`
- `backend/app/adapters/minio_client.py`
- `backend/app/adapters/ragflow/base.py`
- `backend/app/adapters/ragflow/http.py`
- `backend/app/adapters/ragflow/mock.py`
- `backend/app/core/config.py`
- `backend/app/core/document_state.py`
- `backend/app/modules/ragflow/records.py`
- `backend/app/modules/ragflow/repository.py`
- `backend/app/modules/ragflow/service.py`
- `backend/app/modules/ragflow/tasks.py`
- `backend/app/tests/unit/test_config.py`
- `backend/app/tests/unit/test_ragflow_client.py`
- `backend/app/tests/unit/test_ragflow_task_api.py`
- `docker-compose.yml`
- `knowledge_uploader_docs/07_DEPLOYMENT_ENV_部署与环境配置.md`
- `nginx/default.conf`

## Residual Risk

- Real authenticated RAGFlow upload remains environment-gated until the key is provided through secure runtime environment variables.
- Phase 5 handles non-terminal parse state as a retryable task failure. A later phase can add scheduled `ragflow_status_check` polling if long-running parses need automatic completion without manual retry.

## Commit And PR

- Commit: pending.
- PR: pending.
