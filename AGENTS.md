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

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, use the installed graphify skill or instructions before doing anything else.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
