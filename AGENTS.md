# AI Forge — Agent Instructions

## Commands

| Task | Command (run from repo root) |
|---|---|
| Backend tests | `python -m pytest -q` |
| Single test file | `python -m pytest backend/tests/test_tools.py -q` |
| Frontend typecheck | `cd frontend && npx tsc --noEmit` |
| Frontend build | `cd frontend && npm run build` |
| Backend dev server | `ai-forge` (uvicorn on :3000, auto-reload) |
| Frontend dev server | `cd frontend && npm run dev` (:5173, proxies /api → :3000) |

No linter or formatter is configured. TypeScript strict mode (`noUnusedLocals`, `noUnusedParameters`) is the only frontend check.

## Architecture (import roots)

Two separate Python import roots — this trips up imports:
- **Repo root** → `schema` package (Pydantic models in `schema/models.py`)
- **`backend/`** → `app` package (FastAPI, engine, sandbox, API)

`conftest.py` at repo root adds both to `sys.path`. Tests must run from the repo root (`python -m pytest -q`), not from `backend/`.

Key directories:
- `schema/models.py` — all Pydantic models (WorkflowDoc, NodeConfig variants, ToolDefinition, ModelConfig, RunEvent, etc.)
- `backend/app/engine/` — LangGraph builder, runner, tools, conditions, validation
- `backend/app/api/` — FastAPI routers (workflows, runs, secrets)
- `backend/app/sandbox/` — RestrictedPython execution
- `frontend/src/components/flow/` — React Flow canvas, ConfigPanel, RunPanel, panels
- `frontend/src/lib/` — API client, types, graph transforms

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
