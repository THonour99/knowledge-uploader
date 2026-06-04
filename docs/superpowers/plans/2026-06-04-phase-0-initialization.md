# Phase 0 Initialization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the runnable phase 0 skeleton for Knowledge Uploader without skipping later project stages.

**Architecture:** Create a modular monorepo scaffold with a minimal FastAPI backend, Vite React frontend, Docker Compose service graph, Invoke task runner, Alembic shell, and design assets copied into `docs/design/`. Long-running capabilities are only represented as Celery worker skeletons in phase 0; business modules stay empty but importable.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy async, Alembic, Celery, RabbitMQ, Redis, MinIO, React 18, TypeScript, Vite, Ant Design 5, Zustand, TanStack Query, ECharts, Docker Compose.

---

### Task 1: Repository Runtime Files

**Files:**
- Create: `.env.example`
- Create: `README.md`
- Create: `pyproject.toml`
- Create: `tasks.py`
- Create: `docs/design/` from `knowledge_platform_design_package/`

- [ ] **Step 1: Add configuration and task-runner files**

Create root environment defaults from `knowledge_uploader_docs/07_DEPLOYMENT_ENV_部署与环境配置.md` and `docs/spark/2026-06-04-p0-implementation-supplement.md`.

- [ ] **Step 2: Add README**

Document the phase 0 purpose, service graph, and commands: `invoke up`, `invoke down`, `invoke lint`, `invoke test`, `invoke check-arm64`, `invoke migrate`.

- [ ] **Step 3: Copy design assets**

Copy `knowledge_platform_design_package/design.md` to `docs/design/design.md` and copy all PNG files to `docs/design/images/`, preserving the original package.

- [ ] **Step 4: Verify root config**

Run: `python -m compileall tasks.py`

Expected: exit 0.

- [ ] **Step 5: Commit**

Commit message: `chore(infra): 添加阶段零根目录配置`

### Task 2: Backend Skeleton

**Files:**
- Create: `backend/requirements.txt`
- Create: `backend/requirements-dev.txt`
- Create: `backend/Dockerfile`
- Create: `backend/alembic.ini`
- Create: `backend/app/main.py`
- Create: `backend/app/core/*.py`
- Create: `backend/app/db/*.py`
- Create: `backend/app/db/migrations/env.py`
- Create: `backend/app/modules/<module>/*.py`
- Create: `backend/app/adapters/<adapter>/*.py`
- Create: `backend/app/workers/celery_app.py`
- Create: `backend/app/utils/*.py`
- Create: `backend/app/tests/conftest.py`
- Create: `backend/app/tests/unit/test_health.py`

- [ ] **Step 1: Write failing health test**

Create a pytest that calls `GET /api/system/health` through `httpx.AsyncClient` and expects `{"status": "ok"}`.

- [ ] **Step 2: Run health test and confirm RED**

Run: `python -m pytest backend/app/tests/unit/test_health.py -q`

Expected before implementation: fail because `backend.app.main` or route is missing.

- [ ] **Step 3: Implement minimal backend**

Add typed config, logging, exceptions, DB session placeholders, Alembic env, module skeleton files, adapter base/mock files, Celery app, and utility placeholders. Implement `/api/system/health`.

- [ ] **Step 4: Run backend test and compile checks**

Run: `python -m pytest backend/app/tests/unit/test_health.py -q`

Expected: pass.

Run: `python -m compileall backend/app`

Expected: exit 0.

- [ ] **Step 5: Commit**

Commit message: `feat(backend): 添加阶段零后端骨架`

### Task 3: Frontend Skeleton

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/index.html`
- Create: `frontend/vite.config.ts`
- Create: `frontend/tsconfig.json`
- Create: `frontend/tsconfig.node.json`
- Create: `frontend/Dockerfile`
- Create: `frontend/nginx.conf`
- Create: `frontend/src/**/*.ts(x)`
- Create: `frontend/src/**/*.css`

- [ ] **Step 1: Add frontend dependencies and Vite config**

Use locked dependency ranges from P0 supplement section 6.3.

- [ ] **Step 2: Add theme and layout**

Implement `theme/tokens.ts`, `theme/antd-theme.ts`, `layouts/AppShell.tsx`, `Sidebar.tsx`, `TopHeader.tsx`, router guards, API client, auth store, `StatusTag`, and placeholder pages for 12 main routes plus 4 auth routes.

- [ ] **Step 3: Add frontend tests**

Create a Vitest test proving `StatusTag` renders known file status text and an unknown status fallback.

- [ ] **Step 4: Run frontend verification**

Run after dependencies are installed: `npm test -- --run`

Expected: pass.

Run: `npm run build`

Expected: pass.

- [ ] **Step 5: Commit**

Commit message: `feat(frontend): 添加阶段零前端骨架`

### Task 4: Docker, Nginx, CI, and ARM64 Check

**Files:**
- Create: `docker-compose.yml`
- Create: `docker-compose.arm64.yml`
- Create: `docker-compose.override.yml.example`
- Create: `nginx/nginx.conf`
- Create: `nginx/default.conf`
- Create: `deploy/ci/github-actions.yml`
- Create: `scripts/check_arm64_wheels.py`

- [ ] **Step 1: Add Docker Compose graph**

Define nginx, frontend, backend-api, outbox-dispatcher, worker-document, worker-ai, worker-ragflow, worker-statistics, worker-notification, scheduler, postgres, rabbitmq, redis, and minio. Use PostgreSQL 16, RabbitMQ 3.13, Redis 7.2, MinIO locked tag, and healthchecks.

- [ ] **Step 2: Add Nginx config**

Proxy `/api/` to backend and serve frontend.

- [ ] **Step 3: Add CI and ARM64 checker**

Implement the P0 ARM64 dependency checker and a CI workflow that runs lint, tests, ARM64 check, and docker build.

- [ ] **Step 4: Verify Compose syntax**

Run: `docker compose config`

Expected: exit 0.

- [ ] **Step 5: Commit**

Commit message: `build(infra): 添加阶段零容器编排`

### Task 5: Phase 0 Acceptance

**Files:**
- Modify only files needed to fix verification failures.

- [ ] **Step 1: Run formatting and lint**

Run: `invoke fmt`

Expected: exit 0.

Run: `invoke lint`

Expected: exit 0.

- [ ] **Step 2: Run tests**

Run: `invoke test`

Expected: backend and frontend tests pass.

- [ ] **Step 3: Run ARM64 check**

Run: `invoke check-arm64`

Expected: no banned dependencies and compatible packages.

- [ ] **Step 4: Run services**

Run: `invoke up`

Expected: all phase 0 services start and healthchecks converge.

- [ ] **Step 5: Verify endpoints**

Run: `Invoke-WebRequest http://localhost:8000/api/system/health`

Expected body: `{"status":"ok"}` or equivalent JSON with `status` equal to `ok`.

Open frontend through the in-app browser or direct URL and confirm the login route renders.

- [ ] **Step 6: Run Alembic**

Run: `docker compose exec backend-api alembic upgrade head`

Expected: exit 0.

- [ ] **Step 7: Commit final fixes if needed**

Use a focused commit message matching `type(scope):中文描述`.

---

### Scope Notes

- Phase 0 only creates runnable scaffolding. Auth, file upload, review, RAGFlow sync, AI analysis, statistics, audit, and E2E behavior remain for phases 1-9.
- The existing untracked `.agents/`, `.codex/`, and `AGENTS.md` files are treated as user/project rule assets and must not be reverted.
- `knowledge_platform_design_package/` remains in place; phase 0 copies it into `docs/design/` for implementation use.
