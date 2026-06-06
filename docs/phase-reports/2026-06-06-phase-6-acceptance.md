# Phase 6 Acceptance Report: AI Analysis

Date: 2026-06-06
Branch: `codex/phase-6-ai-analysis`

## Scope

Phase 6 implements the AI analysis boundary for uploaded documents:

- AI configuration tables for providers, feature switches, prompt templates, sensitive rules, document analysis results, and usage logs.
- Alembic migration `c7f1a2b9d6e4_add_ai_analysis_tables.py`.
- `/api/admin/ai/*` configuration APIs for reading config, toggling features, managing providers, testing providers, listing prompts, restoring prompts, and listing sensitive rules.
- Provider API keys are encrypted at rest and never returned as plaintext; responses expose only `has_api_key` and `api_key_masked`.
- OpenAI-compatible provider adapter with connection test and secret-safe failure messages.
- AI worker task `ai.analyze_file` with file text extraction, summary, category suggestion, tag generation, sensitive rule detection, and analysis result persistence.
- Upload outbox dispatch to AI queue only when AI was enabled at upload and the environment switch is enabled.
- File state-machine transitions for `extracting_text`, `analysis_queued`, `analyzing`, `analyzed`, `analysis_failed`, and `sensitive_review_required`.
- Critical sensitive findings block RAGFlow sync in review flow and again at the RAGFlow worker boundary.
- `analysis_failed` files are blocked from RAGFlow sync when `allow_sync_when_analysis_failed` is disabled.
- `/ai-config` frontend page with tabs for feature switches, model providers, prompt templates, and sensitive rules.

## Design Reference

The frontend implementation referenced the local design package under:

```text
docs/design/images
```

The AI configuration page follows the structure of `09_ai_config.png`: header action, tabbed configuration area, compact switch rows, provider table, prompt table, and sensitive rule table.

## Subagents

- Explorer subagent identified the Phase 6 AI skeleton and implementation gaps before backend work.
- Frontend worker implemented the first `/ai-config` page and committed `58d2451 feat(ai): 实现 AI 配置前端页面`.
- Quality reviewer subagent found blocking issues in status rollback, AI disabled behavior, critical sync bypass, external LLM switches, URL parsing, and `analysis_failed` sync policy.
- The blocking and high severity findings were fixed before final verification.

## Safety Notes

- AI provider API keys stay backend-only, are encrypted with the configured Fernet key, and are masked before API responses.
- External model provider tests require both the environment hard switch and the DB feature switch to allow external access.
- External URL detection parses hostnames and IPs; `localhost.evil.example` is treated as external.
- `AI_ANALYSIS_ENABLED=false` is a hard worker precondition even if DB feature rows are enabled.
- Analysis failure handling does not directly write ORM statuses after a state-machine rejection.
- No existing RAGFlow knowledge base was modified or deleted during this phase.

## Verification

### Targeted Tests

```text
docker compose build backend-api
docker compose run --rm backend-api pytest app/tests/unit/test_ai_tasks.py --collect-only -q
6 tests collected

docker compose run --rm backend-api pytest app/tests/unit/test_ai_api.py app/tests/unit/test_ai_tasks.py app/tests/unit/test_review_api.py app/tests/unit/test_ragflow_task_api.py -q
50 passed
```

Covered behavior:

- AI config does not echo plaintext provider keys.
- Provider keys are encrypted in the database and masked in responses.
- Provider connection test respects the DB external-model feature switch.
- `localhost.evil.example` cannot bypass the external provider block.
- AI task generates summary, category suggestion, tags, and sensitive findings.
- AI disabled at upload is a precondition no-op.
- `AI_ANALYSIS_ENABLED=false` is a precondition no-op even when the DB feature is enabled.
- Analysis failure does not revert a file that already moved into review.
- Critical sensitive files cannot be queued for RAGFlow through direct approval, classification update, prebound Dataset mapping, or worker execution.
- `analysis_failed` files cannot sync when the feature switch forbids it.

### Quality Gates

```text
python -m invoke lint
ruff: passed
module boundary check: passed
mypy: 188 source files passed
frontend eslint: passed

python -m invoke check-arm64
All 31 checked dependencies are ARM64 allowlisted.
```

### Full Tests

```text
python -m invoke test
backend: 115 passed, 1 skipped
frontend: 2 test files passed, 7 tests passed

npm --prefix frontend test -- AiConfig --run
1 test file passed, 4 tests passed
```

The local sandbox returned `spawn EPERM` for esbuild on the first direct Vitest run; the same command passed after approved escalation.

### Runtime Health

```text
python -m invoke up
python -m invoke migrate
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

Codex Browser was used against `http://localhost/ai-config`.

Evidence:

- URL: `http://localhost/ai-config?phase6_final=...`
- Visible heading: `AI 文档分析配置`
- Visible tabs: `功能开关`, `模型供应商`, `Prompt 模板`, `敏感规则`
- Sensitive rules tab rendered rule table rows in the browser.
- Console error logs: `[]`

## Files Changed

- `backend/app/modules/ai/*`
- `backend/app/adapters/llm/*`
- `backend/app/db/migrations/versions/c7f1a2b9d6e4_add_ai_analysis_tables.py`
- `backend/app/core/config.py`
- `backend/app/core/document_state.py`
- `backend/app/modules/document/service.py`
- `backend/app/modules/review/*`
- `backend/app/modules/ragflow/*`
- `backend/app/workers/*`
- `backend/app/tests/unit/test_ai_api.py`
- `backend/app/tests/unit/test_ai_tasks.py`
- `backend/app/tests/unit/test_outbox_dispatcher.py`
- `backend/app/tests/unit/test_review_api.py`
- `backend/app/tests/unit/test_ragflow_task_api.py`
- `frontend/src/pages/AiConfig/*`

## Residual Risk

- Real external LLM calls were not run; provider behavior is covered with mock provider and blocked external URL tests.
- OCR, quality score, and similarity detection are exposed as configuration switches but remain reserved behavior for later phases.
- File-detail display of AI analysis results is not yet implemented; Phase 6 focused on analysis execution, admin configuration, and sync safety.

## Commit And PR

- Frontend implementation commit: `58d2451 feat(ai): 实现 AI 配置前端页面`.
- Backend and integration implementation commit: `b197f4b feat(ai): 添加文档分析流程`.
- PR: https://github.com/THonour99/knowledge-uploader/pull/9
