# Repository Guidelines

## Project Structure & Module Organization

AgentPlane is a single repository with a modular API and a separate worker. Python code lives in `backend/src/agentplane/`: `api/` exposes FastAPI routes, `runtime/` contains framework adapters, and `worker/` consumes queued runs. Database migrations are under `backend/migrations/`; backend tests are in `backend/tests/`. The Vue 3 application lives in `web/src/`, with views, components, stores, and the generated API schema in `web/src/api/schema.d.ts`. Browser tests are in `web/e2e/`. Infrastructure and architecture decisions belong in `deploy/` and `docs/adr/` respectively.

## Build, Test, and Development Commands

- `docker compose --env-file .env -f deploy/compose.yaml up -d`: start PostgreSQL and Redis.
- `cd backend; uv sync --all-groups`: install locked Python dependencies.
- `uv run alembic upgrade head`: apply database migrations.
- `uv run agentplane-api` / `uv run agentplane-worker`: start the API or worker.
- `cd web; pnpm install; pnpm dev`: install frontend dependencies and start Vite.
- `python scripts/check.py`: run contract drift checks, static analysis, tests, and the frontend build.
- `cd backend; $env:RUN_INTEGRATION="1"; uv run pytest -m integration`: run PostgreSQL/Redis integration tests.
- `cd web; pnpm test:e2e`: run the Playwright smoke flow with local services available.

## Coding Style & Naming Conventions

Use four spaces in Python and two spaces in Vue/TypeScript. Ruff owns Python formatting, imports, and linting; Pyright runs in strict mode. Use `snake_case` for Python modules/functions, `PascalCase` for classes and Vue components, and `camelCase` for TypeScript values. Keep tenant filtering in service queries and keep runtime-specific code behind `AgentRuntimeAdapter`. Do not hand-edit generated OpenAPI artifacts.

## Testing Guidelines

Use pytest/pytest-asyncio for backend tests, Vitest for frontend units, and Playwright for browser flows. Name Python files `test_*.py` and frontend tests `*.spec.ts`. Add tests for tenant isolation, state transitions, retries, cancellation, event ordering, and SSE replay whenever those paths change.

## Commit & Pull Request Guidelines

The repository has no commit history yet. Use Conventional Commit-style messages such as `feat(runtime): add tool authorization` or `fix(sse): preserve replay sequence`. Keep commits scoped and do not include `.env`, credentials, generated build output, or unrelated changes. Pull requests should describe behavior and migration impact, link the issue, list verification commands, and include screenshots for UI changes.

## Security & Configuration

Copy `.env.example` to `.env`; never commit secrets. `AUTH_MODE=dev` is local-only. Tool execution must be server-authorized, tenant-scoped, auditable, and deny-by-default for sensitive operations.
