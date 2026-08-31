# AI Forge — Agent Instructions

## Commands

| Task | Command (run from repo root) |
|---|---|
| Backend tests | `python -m pytest -q` |
| Single test file | `python -m pytest backend/tests/test_tools.py -q` |
| Frontend typecheck | `cd frontend && npx tsc --noEmit` |
| Frontend build | `cd frontend && npm run build` |
| Frontend tests | `cd frontend && npm run test` (Vitest, no jsdom — pure functions only; config in `vitest.config.ts` reusing the `@` alias) |
| Full dev stack (one command) | `./scripts/dev.sh` — backend :3000 + registry :3010 + Vite :5173; Ctrl-C stops all, logs in `.dev/` |
| Backend dev server | `python backend/cli.py` (uvicorn on 127.0.0.1:3000, auto-reload) |
| Frontend dev server | `cd frontend && npm run dev` (:5173, proxies /api → :3000) |
| After editing `schema/models.py` | `PYTHONPATH=. python scripts/generate_schema.py` (repo root) **and** `cd frontend && npm run generate:types` — the first rewrites `schema/*.json`, the second rewrites `frontend/src/lib/workflowTypes.generated.ts` (DO-NOT-EDIT; hand layer in `workflowTypes.ts` keeps only React Flow wrappers + frontend-only fields) |

No dedicated linter or formatter (eslint/ruff) is configured. The frontend `npm run lint` script just runs `tsc --noEmit`; TypeScript strict mode (`noUnusedLocals`, `noUnusedParameters`) is the only real check.

## Architecture (import roots)

Two separate Python import roots — this trips up imports:
- **Repo root** → `schema` package (Pydantic models in `schema/models.py`)
- **`backend/`** → `app` package (FastAPI, engine, sandbox, API)

`conftest.py` at repo root adds both to `sys.path`. Tests must run from the repo root (`python -m pytest -q`), not from `backend/`.

Key directories:
- `schema/models.py` — all Pydantic models (`Workflow`, NodeConfig variants, ToolDefinition, ModelConfig, RunEvent, etc.)
- `backend/app/engine/` — LangGraph builder, runner, tools, conditions, validation
- `backend/app/api/` — FastAPI routers (workflows, runs, secrets)
- `backend/app/sandbox/` — RestrictedPython execution
- `frontend/src/pages/` — WorkflowEditor (React Flow canvas + editor shell), EmptyState
- `frontend/src/components/layout/` — AppLayout route shell, WorkflowSidebar (list/create/delete/search)
- `frontend/src/components/flow/` — ConfigPanel, RunPanel (+ inline human-input form), ModelsPanel, ToolsPanel, SecretsPanel, FlowNode (custom React Flow node)
- `frontend/src/lib/` — api.ts (axios + WS stream), graphTransform.ts, workflowTypes.ts

## Gotchas

- **FastAPI body params**: parameters typed as `Any` are NOT parsed from JSON bodies. Use explicit `Body(default={})` annotation or the value arrives as `None`.
- **LangGraph `interrupt()`**: raises `GraphInterrupt` which must be re-raised, not caught by generic `except Exception` handlers in `_instrument`.
- **Vite WebSocket proxy**: the `/api` proxy in `vite.config.ts` requires `ws: true` or WebSocket upgrades are silently dropped.
- **Run events over WS**: `POST /run` returns 202 + `run_id` immediately (async). Events stream via `WS /api/runs/{id}/events` with seq-numbered replay-on-connect.
- **Per-agent message isolation**: state uses `messages_by_node: dict[str, list]` keyed by node_id — NOT a shared `messages` list.

## Conventions

- Node types are plain string literals (`"start"`, `"agent"`, `"human_in_loop"`, …), not enums.
- Frontend: React Query for data fetching, TailwindCSS for styling, lucide-react for icons.
- Backend: no DI framework; routers are registered in `app/main.py`.
- Workflow persistence: one JSON file per workflow in `~/.ai-forge/workflows/`.
- Secrets: `~/.ai-forge/secrets.json` (chmod 600), env vars take precedence over file.
